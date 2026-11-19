"""Opt-in PostgreSQL coverage for migration and multi-session proposal races.

Set AGENTIC_TEST_POSTGRES_URL to a dedicated, disposable database whose name ends
in `_test`. This suite only applies additive Alembic migrations and removes rows
created under its uniquely named test organizations; it never drops the database.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token
from sqlalchemy import inspect
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash


POSTGRES_URL = os.getenv("AGENTIC_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="AGENTIC_TEST_POSTGRES_URL is not configured")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _key():
    import base64
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture(scope="module")
def postgres_app():
    parsed = make_url(POSTGRES_URL)
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("AGENTIC_TEST_POSTGRES_URL must name a dedicated database ending in `_test`")

    from app import create_app, db
    from app.config import TestingConfig
    from flask_migrate import upgrade

    previous_uri = TestingConfig.SQLALCHEMY_DATABASE_URI
    TestingConfig.SQLALCHEMY_DATABASE_URI = POSTGRES_URL
    try:
        app = create_app("testing")
    finally:
        TestingConfig.SQLALCHEMY_DATABASE_URI = previous_uri
    app.config.update({
        "INTEGRATION_CREDENTIALS_KEY": Fernet.generate_key().decode("ascii"),
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID": "pg-test-enc-v1",
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY": _key(),
        "AGENTIC_PROPOSAL_HMAC_KEY_ID": "pg-test-hmac-v1",
        "AGENTIC_PROPOSAL_HMAC_KEY": _key(),
        "AGENTIC_PROPOSAL_RECOVERY_KEY_ID": "pg-test-recovery-v1",
        "AGENTIC_PROPOSAL_RECOVERY_KEY": _key(),
    })
    with app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="head")
        assert "agentic_action_proposals" in inspect(db.engine).get_table_names()
    yield app
    db.session.remove()
    db.engine.dispose()


def _create_test_actor(app, tag):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        organization = Organization(name=f"A31 PostgreSQL test {tag}")
        db.session.add(organization)
        db.session.flush()
        user = User(
            email=f"a31-{tag}@example.test",
            password_hash=generate_password_hash("postgres-proposal-test-password-123"),
            org_id=organization.id,
            role="admin",
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(
            identity=str(user.id), additional_claims={"org_id": organization.id},
        )
        return organization.id, user.id, {"Authorization": f"Bearer {token}"}


def _create_employee(app, org_id, tag):
    from app import db
    from app.models.employee import Employee

    with app.app_context():
        employee = Employee(org_id=org_id, name=f"PG proposal employee {tag}", role="engineer")
        db.session.add(employee)
        db.session.commit()
        return employee.id


def _cleanup_test_org(app, org_id):
    from app import db
    from app.models.agentic_action_proposal import AgenticActionProposal
    from app.models.employee import Employee
    from app.models.governance import AuditEvent
    from app.models.organization import Organization
    from app.models.user import User

    db.session.execute(delete(AuditEvent).where(AuditEvent.org_id == org_id))
    db.session.execute(delete(AgenticActionProposal).where(AgenticActionProposal.org_id == org_id))
    db.session.execute(delete(Employee).where(Employee.org_id == org_id))
    db.session.execute(delete(User).where(User.org_id == org_id))
    db.session.execute(delete(Organization).where(Organization.id == org_id))


def _request_proposal(app, headers, employee_id, key, barrier):
    with app.test_client() as client:
        barrier.wait(timeout=20)
        response = client.post("/v1/agentic/proposals", headers=headers, json={
            "capability": "employees.update.v1",
            "version": "v1",
            "arguments": {"employee_id": employee_id, "team": "Concurrent Operations"},
            "idempotency_key": key,
        })
        return response.status_code, response.get_json()


def test_postgres_migration_constraints_and_two_session_idempotent_creation(postgres_app):
    from threading import Barrier

    from app import db
    from app.models.agentic_action_proposal import AgenticActionProposal

    tag = uuid4().hex
    org_id, _user_id, headers = _create_test_actor(postgres_app, tag)
    employee_id = _create_employee(postgres_app, org_id, tag)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_request_proposal, postgres_app, headers, employee_id, f"pg-{tag}", barrier)
            for _ in range(2)
        ]
        responses = [future.result(timeout=45) for future in futures]

    assert all(status in {200, 201} for status, _body in responses)
    assert responses[0][1]["proposal_id"] == responses[1][1]["proposal_id"]
    assert responses[0][1]["confirmation_token"] == responses[1][1]["confirmation_token"]
    with postgres_app.app_context():
        rows = AgenticActionProposal.query.filter_by(org_id=org_id).all()
        assert len(rows) == 1
        assert rows[0].confirmation_token_recovery_ciphertext
        unique_constraints = {
            constraint["name"] for constraint in inspect(db.engine).get_unique_constraints("agentic_action_proposals")
        }
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("agentic_action_proposals")}
        assert "uq_agentic_proposal_org_initiator_idempotency" in unique_constraints
        assert "ix_agentic_proposals_org_status_expiry" in indexes
        _cleanup_test_org(postgres_app, org_id)
        db.session.commit()


def test_postgres_two_session_cancellation_and_tenant_isolation(postgres_app):
    from threading import Barrier

    from app import db
    from app.models.employee import Employee

    tag = uuid4().hex
    org_id, user_id, headers = _create_test_actor(postgres_app, tag)
    other_org_id, _other_user_id, other_headers = _create_test_actor(postgres_app, f"other-{tag}")
    employee_id = _create_employee(postgres_app, org_id, tag)
    with postgres_app.test_client() as client:
        created = client.post("/v1/agentic/proposals", headers=headers, json={
            "capability": "employees.update.v1",
            "version": "v1",
            "arguments": {"employee_id": employee_id, "team": "Cancel race"},
            "idempotency_key": f"cancel-{tag}",
        })
        proposal_id = created.get_json()["proposal_id"]
        foreign = client.get(f"/v1/agentic/proposals/{proposal_id}", headers=other_headers)
        assert foreign.status_code == 404

    barrier = Barrier(2)

    def cancel_once():
        with postgres_app.test_client() as client:
            barrier.wait(timeout=20)
            response = client.post(f"/v1/agentic/proposals/{proposal_id}/cancel", headers=headers, json={})
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [pool.submit(cancel_once) for _ in range(2)]
        results = [future.result(timeout=45) for future in responses]
    assert all(status == 200 and body["status"] == "CANCELLED" for status, body in results)

    # Remove only rows created by this test; no database/schema/table is dropped.
    with postgres_app.app_context():
        _cleanup_test_org(postgres_app, org_id)
        _cleanup_test_org(postgres_app, other_org_id)
        db.session.commit()
