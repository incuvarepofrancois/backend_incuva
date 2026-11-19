from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app.agentic.errors import ProposalConflictError
from app.agentic.proposal_service import proposal_service
from app.models.agentic_action_proposal import AgenticActionProposal


def _key():
    import base64
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture
def proposal_client():
    from app import create_app, db

    app = create_app("testing")
    app.config.update({
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID": "test-enc-v1",
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY": _key(),
        "AGENTIC_PROPOSAL_HMAC_KEY_ID": "test-hmac-v1",
        "AGENTIC_PROPOSAL_HMAC_KEY": _key(),
        "AGENTIC_PROPOSAL_RECOVERY_KEY_ID": "test-recovery-v1",
        "AGENTIC_PROPOSAL_RECOVERY_KEY": _key(),
        "AGENTIC_PROPOSAL_TTL_SECONDS": "900",
    })
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _actor(client, org_id, email, role="admin"):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with client.application.app_context():
        if db.session.get(Organization, org_id) is None:
            db.session.add(Organization(id=org_id, name=f"Proposal Org {org_id}"))
            db.session.flush()
        user = User(
            email=email,
            password_hash=generate_password_hash("proposal-test-password-123"),
            org_id=org_id,
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"org_id": org_id})
        return user.id, {"Authorization": f"Bearer {token}"}


def _employee(client, org_id, name="Private employee"):
    from app import db
    from app.models.employee import Employee

    with client.application.app_context():
        employee = Employee(org_id=org_id, name=name, role="engineer", team="Platform")
        db.session.add(employee)
        db.session.commit()
        return employee.id


def _plan_and_task(client, org_id):
    from datetime import date
    from app import db
    from app.models.employee import Employee
    from app.models.onboarding import OnboardingPlan, OnboardingTask

    with client.application.app_context():
        employee = Employee(org_id=org_id, name="Plan employee")
        db.session.add(employee)
        db.session.flush()
        plan = OnboardingPlan(org_id=org_id, employee_id=employee.id, start_date=date(2025, 1, 1))
        task = OnboardingTask(title="First task", due_date=date(2025, 1, 2), status="pending")
        plan.tasks.append(task)
        db.session.add(plan)
        db.session.commit()
        return plan.id, task.id


def _create(client, headers, capability, arguments, key="proposal-idempotency-001"):
    return client.post("/v1/agentic/proposals", headers=headers, json={
        "capability": capability,
        "version": "v1",
        "arguments": arguments,
        "idempotency_key": key,
    })


def test_proposal_creation_seals_action_without_mutating_business_rows(proposal_client):
    from app import db
    from app.models.employee import Employee
    from app.models.governance import AuditEvent

    user_id, auth = _actor(proposal_client, 1001, "proposal-owner@example.test")
    employee_id = _employee(proposal_client, 1001)
    before_count = Employee.query.filter_by(org_id=1001).count()

    response = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "People Operations",
    })

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "PENDING_CONFIRMATION"
    assert body["token_issued"] is True
    assert len(body["confirmation_token"]) >= 43
    assert body["summary"]["organization"] == {"id": 1001, "name": "Proposal Org 1001"}
    assert body["summary"]["changes"][0]["before"] == "Platform"
    assert body["summary"]["changes"][0]["after"] == "People Operations"

    with proposal_client.application.app_context():
        employee = db.session.get(Employee, employee_id)
        proposal = db.session.get(AgenticActionProposal, body["proposal_id"])
        assert employee.team == "Platform"
        assert proposal.initiator_id == user_id
        assert proposal.org_id == 1001
        assert proposal.payload_ciphertext
        assert b"People Operations" not in proposal.payload_ciphertext
        assert body["confirmation_token"].encode() not in proposal.payload_ciphertext
        assert proposal.confirmation_token_digest != body["confirmation_token"]
        assert proposal.confirmation_token_recovery_ciphertext
        assert body["confirmation_token"].encode() not in proposal.confirmation_token_recovery_ciphertext
        assert Employee.query.filter_by(org_id=1001).count() == before_count
        events = AuditEvent.query.filter_by(org_id=1001).all()
        assert {event.event_type for event in events} >= {"PROPOSAL_CREATED", "PROPOSAL_RESPONSE_ISSUED"}
        assert body["confirmation_token"] not in str([event.details for event in events])
        assert "People Operations" not in str([event.details for event in events])


def test_only_registered_proposable_write_capabilities_can_be_proposed(proposal_client):
    _user_id, auth = _actor(proposal_client, 1002, "proposal-readonly@example.test")
    read_attempt = _create(proposal_client, auth, "employees.list.v1", {})
    unknown_attempt = _create(proposal_client, auth, "arbitrary.handler.v1", {})
    write_attempt = _create(proposal_client, auth, "employees.create.v1", {
        "name": "New proposal employee", "org_id": 999,
    })
    assert read_attempt.status_code == 403
    assert unknown_attempt.status_code == 404
    assert write_attempt.status_code == 400


def test_permission_is_checked_when_proposal_is_created(proposal_client):
    _user_id, auth = _actor(proposal_client, 1003, "proposal-analyst@example.test", role="analyst")
    response = _create(proposal_client, auth, "employees.create.v1", {"name": "Proposal name"})
    assert response.status_code == 403
    assert response.get_json()["error_type"] == "AgenticPermissionError"


def test_lost_creation_response_can_recover_token_only_with_same_user_and_key(proposal_client):
    _user_id, auth = _actor(proposal_client, 1004, "proposal-idempotent@example.test")
    _other_id, other_auth = _actor(proposal_client, 1004, "proposal-idempotent-other@example.test")
    employee_id = _employee(proposal_client, 1004)
    arguments = {"employee_id": employee_id, "team": "Operations"}
    # Simulate the server committing successfully while the client discards the first response.
    first = _create(proposal_client, auth, "employees.update.v1", arguments, "same-key-0001")
    second = _create(proposal_client, auth, "employees.update.v1", arguments, "same-key-0001")
    conflict = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Finance",
    }, "same-key-0001")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["proposal_id"] == first.get_json()["proposal_id"]
    assert second.get_json()["idempotent_replay"] is True
    assert second.get_json()["confirmation_token"] == first.get_json()["confirmation_token"]
    assert second.get_json()["token_issued"] is True
    ordinary_get = proposal_client.get(
        f"/v1/agentic/proposals/{first.get_json()['proposal_id']}", headers=auth,
    )
    assert ordinary_get.status_code == 200
    assert ordinary_get.get_json()["confirmation_token"] is None

    other = _create(proposal_client, other_auth, "employees.update.v1", arguments, "same-key-0001")
    assert other.status_code == 201
    assert other.get_json()["proposal_id"] != first.get_json()["proposal_id"]
    assert other.get_json()["confirmation_token"] != first.get_json()["confirmation_token"]
    assert conflict.status_code == 409


def test_get_and_cancel_are_owner_scoped_and_cancellation_is_idempotent(proposal_client):
    _user_id, owner_auth = _actor(proposal_client, 1005, "proposal-owner-2@example.test")
    _other_id, other_auth = _actor(proposal_client, 1005, "proposal-other@example.test")
    _foreign_id, foreign_auth = _actor(proposal_client, 1006, "proposal-foreign@example.test")
    employee_id = _employee(proposal_client, 1005)
    created = _create(proposal_client, owner_auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Support",
    }).get_json()
    proposal_id = created["proposal_id"]

    own_get = proposal_client.get(f"/v1/agentic/proposals/{proposal_id}", headers=owner_auth)
    other_get = proposal_client.get(f"/v1/agentic/proposals/{proposal_id}", headers=other_auth)
    foreign_get = proposal_client.get(f"/v1/agentic/proposals/{proposal_id}", headers=foreign_auth)
    other_cancel = proposal_client.post(f"/v1/agentic/proposals/{proposal_id}/cancel", headers=other_auth, json={})
    cancelled = proposal_client.post(f"/v1/agentic/proposals/{proposal_id}/cancel", headers=owner_auth, json={})
    repeated = proposal_client.post(f"/v1/agentic/proposals/{proposal_id}/cancel", headers=owner_auth, json={})

    assert own_get.status_code == 200
    assert own_get.get_json()["confirmation_token"] is None
    assert "arguments" not in own_get.get_json()
    assert other_get.status_code == foreign_get.status_code == other_cancel.status_code == 404
    assert cancelled.status_code == repeated.status_code == 200
    assert cancelled.get_json()["status"] == repeated.get_json()["status"] == "CANCELLED"


def test_terminal_proposal_cannot_be_reactivated_or_reused(proposal_client):
    _user_id, auth = _actor(proposal_client, 1007, "proposal-terminal@example.test")
    employee_id = _employee(proposal_client, 1007)
    first = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Terminal Team",
    }, "terminal-key-0001").get_json()
    proposal_client.post(f"/v1/agentic/proposals/{first['proposal_id']}/cancel", headers=auth, json={})
    from app import db
    with proposal_client.application.app_context():
        cancelled = db.session.get(AgenticActionProposal, first["proposal_id"])
        assert cancelled.confirmation_token_recovery_ciphertext is None
    replay = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Terminal Team",
    }, "terminal-key-0001")
    assert replay.status_code == 409


def test_proposal_expires_lazily_and_expiry_sweep_is_bounded(proposal_client, monkeypatch):
    from app import db
    from app.agentic import proposal_service as service_module

    _user_id, auth = _actor(proposal_client, 1008, "proposal-expiry@example.test")
    employee_id = _employee(proposal_client, 1008)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(service_module, "utc_now", lambda: base)
    created = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Expires",
    }, "expiry-key-00001").get_json()

    monkeypatch.setattr(service_module, "utc_now", lambda: base.replace(minute=16))
    inspected = proposal_client.get(f"/v1/agentic/proposals/{created['proposal_id']}", headers=auth)
    assert inspected.status_code == 200
    assert inspected.get_json()["status"] == "EXPIRED"

    with proposal_client.application.app_context():
        proposal = db.session.get(AgenticActionProposal, created["proposal_id"])
        assert proposal.terminal_reason == "deadline_elapsed"
        assert proposal.confirmation_token_recovery_ciphertext is None
        with pytest.raises(ValueError):
            proposal_service.expire_due(batch_size=1001)


def test_missing_crypto_keys_fail_closed_without_persisting_a_proposal(proposal_client):
    from app import db
    proposal_client.application.config["AGENTIC_PROPOSAL_HMAC_KEY"] = ""
    _user_id, auth = _actor(proposal_client, 1009, "proposal-no-key@example.test")
    response = _create(proposal_client, auth, "employees.create.v1", {"name": "No key"})
    assert response.status_code == 503
    with proposal_client.application.app_context():
        assert AgenticActionProposal.query.filter_by(org_id=1009).count() == 0


def test_missing_recovery_key_fails_closed_without_persisting_a_proposal(proposal_client):
    from app import db
    proposal_client.application.config["AGENTIC_PROPOSAL_RECOVERY_KEY"] = ""
    _user_id, auth = _actor(proposal_client, 1011, "proposal-no-recovery-key@example.test")
    response = _create(proposal_client, auth, "employees.create.v1", {"name": "No recovery key"})
    assert response.status_code == 503
    with proposal_client.application.app_context():
        assert AgenticActionProposal.query.filter_by(org_id=1011).count() == 0


def test_proposal_integrity_tampering_is_detected_and_hidden_from_api(proposal_client):
    from app import db
    from sqlalchemy import update

    _user_id, auth = _actor(proposal_client, 1010, "proposal-tamper@example.test")
    employee_id = _employee(proposal_client, 1010)
    created = _create(proposal_client, auth, "employees.update.v1", {
        "employee_id": employee_id, "team": "Tamper check",
    }).get_json()
    with proposal_client.application.app_context():
        db.session.execute(
            update(AgenticActionProposal)
            .where(AgenticActionProposal.id == created["proposal_id"])
            .values(resource_id="attacker-substitution")
        )
        db.session.commit()

    response = proposal_client.get(
        f"/v1/agentic/proposals/{created['proposal_id']}", headers=auth,
    )
    assert response.status_code == 500
    assert response.get_json() == {
        "error": "Action proposal service is unavailable or could not validate the proposal",
        "error_type": "ProposalIntegrityError",
    }
