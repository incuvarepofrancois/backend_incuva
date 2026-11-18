import hashlib
import hmac
import json
import time

import pytest
from flask_jwt_extended import create_access_token
from unittest.mock import patch
from werkzeug.security import generate_password_hash


@pytest.fixture
def client():
    from app import create_app, db
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def token_for(client, org_id=7, email="user@example.com", password="correct horse"):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    with client.application.app_context():
        if not db.session.get(Organization, org_id):
            db.session.add(Organization(id=org_id, name=f"Organization {org_id}"))
            db.session.commit()
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, org_id=org_id, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
        return create_access_token(
            identity=str(user.id), additional_claims={"org_id": org_id, "role": user.role}
        )


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_meeting_endpoints_require_auth(client):
    assert client.post("/v1/meeting-analyze", json={"transcript": "test"}).status_code == 401
    assert client.get("/v1/meetings").status_code == 401
    assert client.get("/v1/meetings/1").status_code == 401
    assert client.patch("/v1/meetings/1/review", json={"reviewed": True}).status_code == 401


def test_analysis_is_validated_persisted_and_listed(client):
    headers = auth(token_for(client))
    with patch("app.api.v1.meetings._analyzer.analyze", return_value={
        "decisions": ["Prioriser le recrutement"],
        "action_items": [{"task": "Préparer le plan", "owner": "Alice", "due_date": None}],
        "participants": ["Alice", "Marc"],
    }) as analyze:
        response = client.post("/v1/meeting-analyze", headers=headers, json={
            "title": "Comité produit", "transcript": "Alice: Priorisons le recrutement."
        })
    assert response.status_code == 201
    meeting = response.get_json()
    assert meeting["title"] == "Comité produit"
    assert meeting["decisions"] == ["Prioriser le recrutement"]
    assert meeting["action_items"][0]["owner"] == "Alice"
    analyze.assert_called_once()

    listed = client.get("/v1/meetings", headers=headers)
    assert listed.status_code == 200
    assert listed.get_json()["meetings"][0]["id"] == meeting["id"]
    detail = client.get(f"/v1/meetings/{meeting['id']}", headers=headers)
    assert detail.get_json()["transcript"] == "Alice: Priorisons le recrutement."


@pytest.mark.parametrize("payload", [{}, {"transcript": "   "}, {"transcript": 123}])
def test_analysis_rejects_missing_or_invalid_transcript(client, payload):
    response = client.post("/v1/meeting-analyze", headers=auth(token_for(client)), json=payload)
    assert response.status_code == 400


def test_analysis_rejects_oversized_transcript(client):
    response = client.post("/v1/meeting-analyze", headers=auth(token_for(client)), json={
        "transcript": "x" * 200_001
    })
    assert response.status_code == 413


def test_meeting_data_is_isolated_by_organization(client):
    headers_org_a = auth(token_for(client, org_id=7))
    headers_org_b = auth(token_for(client, org_id=8, email="other@example.com"))
    with patch("app.api.v1.meetings._analyzer.analyze", return_value={
        "decisions": [], "action_items": [], "participants": []
    }):
        created = client.post("/v1/meeting-analyze", headers=headers_org_a,
                              json={"transcript": "A transcript"}).get_json()

    assert client.get("/v1/meetings", headers=headers_org_b).get_json()["meetings"] == []
    assert client.get(f"/v1/meetings/{created['id']}", headers=headers_org_b).status_code == 404


def test_meeting_review_is_saved_and_scoped_to_organization(client):
    headers_org_a = auth(token_for(client, org_id=7))
    headers_org_b = auth(token_for(client, org_id=8, email="other@example.com"))
    with patch("app.api.v1.meetings._analyzer.analyze", return_value={
        "decisions": [], "action_items": [], "participants": []
    }):
        meeting = client.post("/v1/meeting-analyze", headers=headers_org_a,
                              json={"transcript": "A transcript"}).get_json()

    updated = client.patch(f"/v1/meetings/{meeting['id']}/review", headers=headers_org_a,
                           json={"reviewed": True})
    assert updated.status_code == 200
    assert updated.get_json()["reviewed_at"]
    assert updated.get_json()["reviewed_by"]
    assert client.patch(f"/v1/meetings/{meeting['id']}/review", headers=headers_org_b,
                        json={"reviewed": True}).status_code == 404
    undone = client.patch(f"/v1/meetings/{meeting['id']}/review", headers=headers_org_a,
                          json={"reviewed": False})
    assert undone.get_json()["reviewed_at"] is None


def test_analysis_failure_does_not_create_meeting(client):
    headers = auth(token_for(client))
    with patch("app.api.v1.meetings._analyzer.analyze", side_effect=ValueError("bad model output")):
        response = client.post("/v1/meeting-analyze", headers=headers,
                               json={"transcript": "Transcript"})
    assert response.status_code == 502
    assert client.get("/v1/meetings", headers=headers).get_json()["meetings"] == []


def _zoom_headers(client, body, timestamp=None):
    secret = client.application.config["ZOOM_WEBHOOK_SECRET_TOKEN"]
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    message = f"v0:{timestamp}:".encode() + body
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {"x-zm-request-timestamp": timestamp, "x-zm-signature": f"v0={signature}"}


def test_teams_webhook_requires_client_state_and_accepts_valid_notifications(client):
    invalid = client.post("/v1/webhooks/teams", json={"value": [{"clientState": "wrong"}]})
    assert invalid.status_code == 401

    client_state = client.application.config["TEAMS_WEBHOOK_CLIENT_STATE"]
    valid = client.post("/v1/webhooks/teams", json={"value": [{"clientState": client_state}]})
    assert valid.status_code == 200


def test_teams_webhook_supports_graph_validation_handshake(client):
    response = client.post("/v1/webhooks/teams?validationToken=challenge-token")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == "challenge-token"


def test_teams_webhook_rejects_unmapped_client_state(client):
    client.application.config["TEAMS_WEBHOOK_CLIENT_STATE"] = ""
    response = client.post("/v1/webhooks/teams", json={"clientState": "anything"})
    assert response.status_code == 401


def test_zoom_webhook_rejects_missing_invalid_and_stale_signatures(client):
    body = b'{"event":"ping"}'
    assert client.post("/v1/webhooks/zoom", data=body, content_type="application/json").status_code == 401

    invalid = client.post("/v1/webhooks/zoom", data=body, content_type="application/json", headers={
        "x-zm-request-timestamp": str(int(time.time())), "x-zm-signature": "v0=invalid",
    })
    assert invalid.status_code == 401

    stale_timestamp = int(time.time()) - 301
    stale = client.post("/v1/webhooks/zoom", data=body, content_type="application/json",
                        headers=_zoom_headers(client, body, stale_timestamp))
    assert stale.status_code == 401


def test_zoom_webhook_accepts_valid_signature_and_url_validation(client):
    body = json.dumps({"event": "ping"}, separators=(",", ":")).encode()
    response = client.post("/v1/webhooks/zoom", data=body, content_type="application/json",
                           headers=_zoom_headers(client, body))
    assert response.status_code == 200

    payload = {"event": "endpoint.url_validation", "payload": {"plainToken": "zoom-challenge"}}
    body = json.dumps(payload, separators=(",", ":")).encode()
    response = client.post("/v1/webhooks/zoom", data=body, content_type="application/json",
                           headers=_zoom_headers(client, body))
    assert response.status_code == 200
    result = response.get_json()
    assert result["plainToken"] == "zoom-challenge"
    expected = hmac.new(client.application.config["ZOOM_WEBHOOK_SECRET_TOKEN"].encode(),
                        b"zoom-challenge", hashlib.sha256).hexdigest()
    assert result["encryptedToken"] == expected


def test_zoom_webhook_fails_closed_without_secret_token(client):
    client.application.config["ZOOM_WEBHOOK_SECRET_TOKEN"] = ""
    response = client.post("/v1/webhooks/zoom", data=b'{}', content_type="application/json")
    assert response.status_code == 503


