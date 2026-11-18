import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app import db
from app.governance.access import access_allowed
from app.governance.audit import record_audit_event
from app.governance.controls import consume_approval, evaluate_control
from app.models.integration_credential import IntegrationCredential
from app.models.integration_sync import IntegrationSync
from app.models.user import User
from app.security.crypto import credential_cipher, decrypt_credential, encrypt_credential

integrations_bp = Blueprint("integrations", __name__)

# These are credential names only. Values are encrypted before they reach the database.
INTEGRATION_FIELDS = {
    "teams": {"required": {"tenant_id", "client_id", "client_secret"}, "optional": set()},
    "zoom": {"required": {"account_id", "client_id", "client_secret"}, "optional": set()},
    "jira": {"required": {"base_url", "email", "api_token"}, "optional": set()},
    "github": {"required": {"organization", "personal_access_token"}, "optional": set()},
    "bamboohr": {"required": {"subdomain", "api_key"}, "optional": set()},
    "frappehr": {"required": {"base_url", "api_key", "api_secret"}, "optional": set()},
    "lucca": {"required": {"base_url", "api_key"}, "optional": set()},
    "factorial": {"required": {"api_key"}, "optional": set()},
    "payfit": {"required": {"api_key"}, "optional": set()},
    "silae": {"required": {"client_id", "client_secret", "subscription_key"}, "optional": set()},
    "skello": {"required": {"access_key", "organization_id"}, "optional": set()},
    "eurecia": {"required": {"api_token"}, "optional": set()},
    "personio": {"required": {"client_id", "client_secret"}, "optional": set()},
    "slack": {"required": {"webhook_url"}, "optional": set()},
    "notion": {"required": {"integration_token"}, "optional": set()},
    "hubspot": {"required": {"private_app_token"}, "optional": set()},
    "asana": {"required": {"personal_access_token"}, "optional": set()},
    "stripe": {"required": {"restricted_api_key"}, "optional": set()},
    "pennylane": {"required": {"company_api_token"}, "optional": set()},
}

INTEGRATION_NAMES = {
    "teams": "Microsoft Teams", "zoom": "Zoom", "jira": "Jira", "github": "GitHub",
    "bamboohr": "BambooHR", "frappehr": "Frappe HR", "lucca": "Lucca", "factorial": "Factorial",
    "payfit": "PayFit", "silae": "Silae", "skello": "Skello", "eurecia": "Eurécia",
    "personio": "Personio", "slack": "Slack", "notion": "Notion", "hubspot": "HubSpot",
    "asana": "Asana", "stripe": "Stripe", "pennylane": "Pennylane",
}
INTEGRATION_CATEGORIES = {
    "teams": "Collaboration", "zoom": "Collaboration", "slack": "Collaboration",
    "bamboohr": "SIRH", "frappehr": "SIRH", "lucca": "SIRH", "factorial": "SIRH",
    "payfit": "SIRH", "skello": "SIRH", "eurecia": "SIRH", "personio": "SIRH",
    "silae": "Paie & finance", "stripe": "Paie & finance", "pennylane": "Paie & finance",
    "jira": "Projets", "github": "Développement", "notion": "Projets", "asana": "Projets",
    "hubspot": "CRM",
}


def _organization_id(permission="manage"):
    claims = get_jwt()
    org_id = claims.get("org_id")
    if org_id is None:
        return None, (jsonify({"error": "organization access required"}), 403)
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None, (jsonify({"error": "authenticated user required"}), 403)
    user = db.session.get(User, user_id)
    if user is None or user.org_id != org_id or not access_allowed(org_id, user.role, "integrations", permission):
        return None, (jsonify({"error": "integration access is denied by an access rule"}), 403)
    return org_id, None


@integrations_bp.get("/integrations")
@jwt_required()
def list_integrations():
    org_id, error = _organization_id("view")
    if error:
        return error
    saved = {
        item.integration_key: item
        for item in IntegrationCredential.query.filter_by(org_id=org_id).all()
    }
    syncs = {
        item.integration_key: item
        for item in IntegrationSync.query.filter_by(org_id=org_id).all()
    }
    return jsonify({"integrations": [
        {
            "key": key,
            "name": INTEGRATION_NAMES.get(key, key),
            "category": INTEGRATION_CATEGORIES.get(key, "Autres"),
            "credentials_saved": key in saved,
            "updated_at": saved[key].updated_at.isoformat() if key in saved else None,
            "connection_status": saved[key].connection_status if key in saved else "not_configured",
            "connection_checked_at": saved[key].connection_checked_at.isoformat() if key in saved and saved[key].connection_checked_at else None,
            "connection_error": saved[key].connection_error if key in saved else None,
            "sync": syncs[key].to_dict() if key in syncs else None,
        }
        for key in INTEGRATION_FIELDS
    ]}), 200


@integrations_bp.put("/integrations/<integration_key>/credentials")
@jwt_required()
def save_credentials(integration_key):
    org_id, error = _organization_id()
    if error:
        return error
    schema = INTEGRATION_FIELDS.get(integration_key)
    if schema is None:
        return jsonify({"error": "Unknown integration"}), 404
    cipher = credential_cipher()
    if cipher is None:
        return jsonify({"error": "Integration credential encryption is not configured"}), 503

    payload = request.get_json(silent=True)
    credentials = payload.get("credentials") if isinstance(payload, dict) else None
    if not isinstance(credentials, dict):
        return jsonify({"error": "credentials must be a JSON object"}), 400
    allowed = schema["required"] | schema["optional"]
    if set(credentials) - allowed:
        return jsonify({"error": "Unsupported credential fields"}), 400
    missing = schema["required"] - credentials.keys()
    if missing:
        return jsonify({"error": "Required credential fields are missing", "fields": sorted(missing)}), 400
    normalized = {}
    for field in allowed:
        value = credentials.get(field, "")
        if not isinstance(value, str) or len(value.strip()) > 4096:
            return jsonify({"error": "Credential values must be text no longer than 4096 characters"}), 400
        value = value.strip()
        if field in schema["required"] and not value:
            return jsonify({"error": f"{field} is required"}), 400
        normalized[field] = value
    if integration_key == "jira":
        parsed_url = urlsplit(normalized["base_url"])
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            return jsonify({"error": "Jira site URL must be a valid HTTPS base URL"}), 400
    if integration_key == "slack":
        parsed_url = urlsplit(normalized["webhook_url"])
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in {"hooks.slack.com", "hooks.slack-gov.com"}
            or parsed_url.username
            or parsed_url.password
            or not parsed_url.path.startswith(("/services/", "/triggers/"))
        ):
            return jsonify({"error": "Enter a valid Slack Incoming Webhook URL"}), 400
    if integration_key == "bamboohr" and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized["subdomain"], re.IGNORECASE):
        return jsonify({"error": "Enter only the BambooHR company domain, without .bamboohr.com"}), 400
    if integration_key == "frappehr":
        parsed_url = urlsplit(normalized["base_url"])
        hostname = (parsed_url.hostname or "").lower()
        local_http_host = hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed_url.scheme not in {"https", "http"}
            or (parsed_url.scheme == "http" and not local_http_host)
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path not in {"", "/"}
            or hostname in {"169.254.169.254", "metadata.google.internal"}
        ):
            return jsonify({"error": "Enter the Frappe site origin (https://… or local http://localhost:port), without a path"}), 400
        normalized["base_url"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
    if integration_key == "lucca":
        parsed_url = urlsplit(normalized["base_url"])
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path not in {"", "/"}
        ):
            return jsonify({"error": "Enter the Lucca tenant origin using HTTPS, without a path"}), 400
        normalized["base_url"] = f"https://{parsed_url.netloc}"
    if integration_key == "stripe" and not normalized["restricted_api_key"].startswith(("rk_test_", "rk_live_")):
        return jsonify({"error": "Use a Stripe restricted API key (rk_test_… or rk_live_…)"}), 400

    actor_id = int(get_jwt_identity())
    approval_id = payload.get("approval_id")
    action_check = evaluate_control(
        org_id, "action_allowlist", {"action": "integration.credentials.save"}, actor_user_id=actor_id,
    )
    if not action_check["allowed"]:
        return jsonify({"error": "this action is not allowed by the organization policy", "reason": action_check.get("reason")}), 403
    approval_check = evaluate_control(
        org_id, "require_approval",
        {"action": "integration.credentials.save", "approval_id": approval_id, "resource_id": integration_key},
        actor_user_id=actor_id,
    )
    if not approval_check["allowed"]:
        return jsonify({"error": "human approval is required for this integration credential change"}), 403
    if approval_check.get("requires_approval") and approval_check.get("mode") == "block" and not consume_approval(
        org_id, approval_id, "integration.credentials.save", actor_user_id=actor_id, resource_id=integration_key,
    ):
        return jsonify({"error": "approval is no longer valid"}), 403

    encrypted = encrypt_credential(json.dumps(normalized, separators=(",", ":")).encode("utf-8"))
    record = IntegrationCredential.query.filter_by(org_id=org_id, integration_key=integration_key).first()
    if record is None:
        record = IntegrationCredential(org_id=org_id, integration_key=integration_key)
        db.session.add(record)
    record.encrypted_credentials = encrypted
    record.updated_by = actor_id
    record.updated_at = datetime.utcnow()
    record.connection_status = "unverified"
    record.connection_checked_at = None
    record.connection_error = None
    record_audit_event(org_id, "integration.credentials_saved", "integration", integration_key, actor_id, details={"updated_at": record.updated_at.isoformat()})
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Integration credentials could not be saved")
        return jsonify({"error": "Integration credentials could not be saved"}), 500
    return jsonify({
        "key": integration_key,
        "credentials_saved": True,
        "connection_verified": False,
        "updated_at": record.updated_at.isoformat(),
    }), 200


@integrations_bp.delete("/integrations/<integration_key>/credentials")
@jwt_required()
def delete_credentials(integration_key):
    org_id, error = _organization_id()
    if error:
        return error
    if integration_key not in INTEGRATION_FIELDS:
        return jsonify({"error": "Unknown integration"}), 404
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    actor_id = int(get_jwt_identity())
    approval_id = payload.get("approval_id")
    action_check = evaluate_control(
        org_id, "action_allowlist", {"action": "integration.credentials.delete"}, actor_user_id=actor_id,
    )
    if not action_check["allowed"]:
        return jsonify({"error": "this action is not allowed by the organization policy", "reason": action_check.get("reason")}), 403
    approval_check = evaluate_control(
        org_id, "require_approval",
        {"action": "integration.credentials.delete", "approval_id": approval_id, "resource_id": integration_key},
        actor_user_id=actor_id,
    )
    if not approval_check["allowed"]:
        return jsonify({"error": "human approval is required for this integration credential change"}), 403
    if approval_check.get("requires_approval") and approval_check.get("mode") == "block" and not consume_approval(
        org_id, approval_id, "integration.credentials.delete", actor_user_id=actor_id, resource_id=integration_key,
    ):
        return jsonify({"error": "approval is no longer valid"}), 403
    record = IntegrationCredential.query.filter_by(org_id=org_id, integration_key=integration_key).first()
    if record:
        db.session.delete(record)
        record_audit_event(org_id, "integration.credentials_deleted", "integration", integration_key, actor_id)
        db.session.commit()
    return jsonify({"key": integration_key, "credentials_saved": False}), 200


def get_decrypted_credentials(org_id, integration_key):
    """Internal connector helper. Never return this data from a public API response."""
    record = IntegrationCredential.query.filter_by(org_id=org_id, integration_key=integration_key).first()
    if credential_cipher() is None or record is None:
        return None
    try:
        return json.loads(decrypt_credential(record.encrypted_credentials).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError, RuntimeError):
        current_app.logger.exception("Stored integration credentials could not be decrypted")
        return None


def _save_connection_result(org_id, integration_key, success, message=None):
    record = IntegrationCredential.query.filter_by(org_id=org_id, integration_key=integration_key).first()
    if record is None:
        return
    record.connection_status = "connected" if success else "failed"
    record.connection_checked_at = datetime.utcnow()
    record.connection_error = None if success else (message or "Connection check failed")[:500]


@integrations_bp.post("/integrations/bamboohr/test")
@jwt_required()
def test_bamboohr_connection():
    org_id, error = _organization_id()
    if error:
        return error
    from app.connectors.bamboohr import BambooHRConnector

    try:
        BambooHRConnector(org_id).connect()
    except Exception as connection_error:
        current_app.logger.warning("BambooHR connection check failed for organization %s", org_id)
        from app.services.bamboohr_sync import _safe_error
        message = _safe_error(connection_error)
        _save_connection_result(org_id, "bamboohr", False, message)
        record_audit_event(
            org_id,
            "integration.connection_test_failed",
            "integration",
            "bamboohr",
            int(get_jwt_identity()),
            outcome="error",
            details={"reason": message},
        )
        db.session.commit()
        return jsonify({"connection_verified": False, "error": message}), 422
    record_audit_event(org_id, "integration.connection_test_succeeded", "integration", "bamboohr", int(get_jwt_identity()))
    _save_connection_result(org_id, "bamboohr", True)
    db.session.commit()
    return jsonify({"connection_verified": True, "message": "BambooHR credentials and employee-read access verified."}), 200


@integrations_bp.post("/integrations/bamboohr/sync")
@jwt_required()
def start_bamboohr_sync():
    org_id, error = _organization_id()
    if error:
        return error
    if get_decrypted_credentials(org_id, "bamboohr") is None:
        return jsonify({"error": "Save BambooHR credentials before starting a sync"}), 409

    sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="bamboohr").with_for_update().first()
    if sync is not None and sync.status in {"queued", "running"}:
        return jsonify({"error": "A BambooHR sync is already in progress", "sync": sync.to_dict()}), 409
    if sync is None:
        sync = IntegrationSync(org_id=org_id, integration_key="bamboohr")
        db.session.add(sync)
    sync.status = "queued"
    sync.records_synced = 0
    sync.last_error = None
    sync.task_id = None
    db.session.commit()

    try:
        from app.services.bamboohr_tasks import sync_bamboohr_employees_task
        queued_task = sync_bamboohr_employees_task.delay(org_id, sync.id)
        sync.task_id = queued_task.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="bamboohr").first()
        if sync:
            sync.status = "failed"
            sync.last_error = "Background queue unavailable. Start the Celery worker and retry."
            db.session.commit()
        current_app.logger.exception("Could not enqueue BambooHR sync for organization %s", org_id)
        return jsonify({"error": "Background queue unavailable. Start the Celery worker and retry."}), 503

    record_audit_event(org_id, "integration.sync_queued", "integration", "bamboohr", int(get_jwt_identity()))
    db.session.commit()
    return jsonify({"sync": sync.to_dict()}), 202


@integrations_bp.get("/integrations/bamboohr/sync")
@jwt_required()
def get_bamboohr_sync_status():
    org_id, error = _organization_id()
    if error:
        return error
    sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="bamboohr").first()
    if sync is None:
        return jsonify({"sync": {
            "integration_key": "bamboohr",
            "status": "idle",
            "records_synced": 0,
            "last_started_at": None,
            "last_success_at": None,
            "last_error": None,
        }}), 200
    return jsonify({"sync": sync.to_dict()}), 200


@integrations_bp.post("/integrations/frappehr/test")
@jwt_required()
def test_frappehr_connection():
    org_id, error = _organization_id()
    if error:
        return error
    from app.connectors.frappehr import FrappeHRConnector
    from app.services.frappehr_sync import safe_error

    try:
        FrappeHRConnector(org_id).connect()
    except Exception as connection_error:
        current_app.logger.warning("Frappe HR connection check failed for organization %s", org_id)
        message = safe_error(connection_error)
        _save_connection_result(org_id, "frappehr", False, message)
        record_audit_event(org_id, "integration.connection_test_failed", "integration", "frappehr", int(get_jwt_identity()), outcome="error", details={"reason": message})
        db.session.commit()
        return jsonify({"connection_verified": False, "error": message}), 422
    record_audit_event(org_id, "integration.connection_test_succeeded", "integration", "frappehr", int(get_jwt_identity()))
    _save_connection_result(org_id, "frappehr", True)
    db.session.commit()
    return jsonify({"connection_verified": True, "message": "Frappe HR credentials and Employee/Leave Application read access verified."}), 200


@integrations_bp.post("/integrations/frappehr/sync")
@jwt_required()
def start_frappehr_sync():
    org_id, error = _organization_id()
    if error:
        return error
    if get_decrypted_credentials(org_id, "frappehr") is None:
        return jsonify({"error": "Save Frappe HR credentials before starting a sync"}), 409
    sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="frappehr").with_for_update().first()
    if sync is not None and sync.status in {"queued", "running"}:
        return jsonify({"error": "A Frappe HR sync is already in progress", "sync": sync.to_dict()}), 409
    if sync is None:
        sync = IntegrationSync(org_id=org_id, integration_key="frappehr")
        db.session.add(sync)
    sync.status, sync.records_synced, sync.last_error, sync.task_id = "queued", 0, None, None
    db.session.commit()
    try:
        from app.services.frappehr_tasks import sync_frappehr_employees_task
        queued_task = sync_frappehr_employees_task.delay(org_id, sync.id)
        sync.task_id = queued_task.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="frappehr").first()
        if sync:
            sync.status, sync.last_error = "failed", "Background queue unavailable. Start the Celery worker and retry."
            db.session.commit()
        current_app.logger.exception("Could not enqueue Frappe HR sync for organization %s", org_id)
        return jsonify({"error": "Background queue unavailable. Start the Celery worker and retry."}), 503
    record_audit_event(org_id, "integration.sync_queued", "integration", "frappehr", int(get_jwt_identity()))
    db.session.commit()
    return jsonify({"sync": sync.to_dict()}), 202


@integrations_bp.get("/integrations/frappehr/sync")
@jwt_required()
def get_frappehr_sync_status():
    org_id, error = _organization_id()
    if error:
        return error
    sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="frappehr").first()
    if sync is None:
        return jsonify({"sync": {
            "integration_key": "frappehr",
            "status": "idle",
            "records_synced": 0,
            "absence_records_synced": 0,
            "data_coverage": None,
            "last_started_at": None,
            "last_success_at": None,
            "last_error": None,
        }}), 200
    return jsonify({"sync": sync.to_dict()}), 200
