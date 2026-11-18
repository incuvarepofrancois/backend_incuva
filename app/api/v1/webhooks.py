import hashlib
import hmac
import time

from flask import Blueprint, current_app, jsonify, request, Response

from app.modules.meeting_intelligence.connector import MeetingConnector
from app.middleware.rate_limiter import mark_once, rate_limit
from app.models.governance import OrganizationSecret
from app.models.integration_credential import IntegrationCredential
from app.security.crypto import decrypt_credential
from cryptography.fernet import InvalidToken

webhooks_bp = Blueprint("webhooks", __name__)
_connector = MeetingConnector()
ZOOM_SIGNATURE_TOLERANCE_SECONDS = 5 * 60


def _webhook_secret(config_key):
    secret = current_app.config.get(config_key, "")
    if not isinstance(secret, str) or len(secret) < 32:
        return None
    return secret


def _teams_client_states(payload):
    notifications = payload.get("value")
    if isinstance(notifications, list) and notifications:
        states = [item.get("clientState") for item in notifications if isinstance(item, dict)]
        if len(states) != len(notifications):
            return []
    else:
        state = payload.get("clientState")
        states = [state] if isinstance(state, str) else []
    return states if states and all(isinstance(state, str) for state in states) and len(set(states)) == 1 else []


def _teams_notification_org_id(payload):
    states = _teams_client_states(payload)
    if not states:
        return None
    target = states[0]
    for secret in OrganizationSecret.query.filter_by(name="teams.webhook_client_state").all():
        try:
            stored = decrypt_credential(secret.encrypted_value).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError, RuntimeError):
            continue
        if hmac.compare_digest(target, stored):
            return secret.org_id
    # The test configuration uses one fixed token; production requires an org-scoped state.
    test_state = current_app.config.get("TEAMS_WEBHOOK_CLIENT_STATE", "")
    if current_app.testing and len(test_state) >= 32 and hmac.compare_digest(target, test_state):
        return 1
    return None


def _zoom_account_org_id(payload):
    event_payload = payload.get("payload")
    account_id = event_payload.get("account_id") if isinstance(event_payload, dict) else None
    if not isinstance(account_id, str) or not account_id:
        return None
    import json
    for record in IntegrationCredential.query.filter_by(integration_key="zoom").all():
        try:
            credentials = json.loads(decrypt_credential(record.encrypted_credentials).decode("utf-8"))
        except (InvalidToken, ValueError, UnicodeDecodeError, RuntimeError):
            continue
        if hmac.compare_digest(str(credentials.get("account_id", "")), account_id):
            return record.org_id
    return None


def _zoom_signature_is_valid(secret, raw_body):
    timestamp = request.headers.get("x-zm-request-timestamp", "")
    signature = request.headers.get("x-zm-signature", "")
    if len(timestamp) > 12 or not timestamp.isdigit() or not signature.startswith("v0="):
        return False
    if abs(time.time() - int(timestamp)) > ZOOM_SIGNATURE_TOLERANCE_SECONDS:
        return False
    message = b"v0:" + timestamp.encode("ascii") + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@webhooks_bp.route("/teams", methods=["POST"])
@rate_limit(max_requests=120, window_seconds=60)
def teams_webhook():
    validation_token = request.args.get("validationToken")
    if validation_token is not None:
        if not validation_token or len(validation_token) > 4096:
            return jsonify({"error": "Invalid validation token"}), 400
        return Response(validation_token, status=200, mimetype="text/plain")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    org_id = _teams_notification_org_id(payload)
    if org_id is None:
        return jsonify({"error": "Invalid Teams webhook notification"}), 401
    try:
        if not mark_once("teams", request.get_data(cache=True), 300):
            return jsonify({"error": "Duplicate webhook notification"}), 409
    except RuntimeError:
        return jsonify({"error": "Webhook replay protection is unavailable"}), 503
    _connector.handle_teams_webhook(payload, org_id=org_id)
    return jsonify({"status": "received"}), 200


@webhooks_bp.route("/zoom", methods=["POST"])
@rate_limit(max_requests=120, window_seconds=60)
def zoom_webhook():
    secret = _webhook_secret("ZOOM_WEBHOOK_SECRET_TOKEN")
    if secret is None:
        return jsonify({"error": "Zoom webhook verification is not configured"}), 503
    raw_body = request.get_data(cache=True)
    if not _zoom_signature_is_valid(secret, raw_body):
        return jsonify({"error": "Invalid Zoom webhook signature"}), 401
    signature = request.headers.get("x-zm-signature", "")
    try:
        if not mark_once("zoom", signature, ZOOM_SIGNATURE_TOLERANCE_SECONDS):
            return jsonify({"error": "Duplicate webhook notification"}), 409
    except RuntimeError:
        return jsonify({"error": "Webhook replay protection is unavailable"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    if payload.get("event") == "endpoint.url_validation":
        validation_payload = payload.get("payload")
        plain_token = validation_payload.get("plainToken") if isinstance(validation_payload, dict) else None
        if not isinstance(plain_token, str) or not plain_token:
            return jsonify({"error": "Invalid Zoom validation payload"}), 400
        encrypted_token = hmac.new(
            secret.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return jsonify({"plainToken": plain_token, "encryptedToken": encrypted_token}), 200

    org_id = _zoom_account_org_id(payload)
    if payload.get("event") == "recording.completed" and org_id is None:
        return jsonify({"error": "Zoom account is not mapped to an organization"}), 403
    _connector.handle_zoom_webhook(payload, org_id=org_id)
    return jsonify({"status": "received"}), 200
