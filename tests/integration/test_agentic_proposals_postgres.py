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
from sqlalchemy import delete, inspect, text, update
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
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory
    from flask_migrate import check, downgrade, upgrade

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
        tables_before_upgrade = set(inspect(db.engine).get_table_names())
        if "alembic_version" not in tables_before_upgrade and tables_before_upgrade:
            pytest.fail("Refusing to migrate a non-empty PostgreSQL database without Alembic version state")
        if "alembic_version" in tables_before_upgrade:
            revisions = db.session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            migration_config = AlembicConfig()
            migration_config.set_main_option("script_location", str(MIGRATIONS_DIR))
            known_revisions = {
                revision.revision
                for revision in ScriptDirectory.from_config(migration_config).walk_revisions()
            }
            if not revisions or any(revision not in known_revisions for revision in revisions):
                pytest.fail("Refusing to migrate an unknown or inconsistent Alembic state")
        upgrade(directory=str(MIGRATIONS_DIR), revision="head")
        check(directory=str(MIGRATIONS_DIR))
        inspector = inspect(db.engine)
        assert "agentic_action_proposals" in inspector.get_table_names()
        assert {"org_id", "initiator_id"}.issubset(
            {column["name"] for column in inspector.get_columns("agentic_action_proposals")}
        )
        assert {"organizations", "users"} == {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("agentic_action_proposals")
        }
        assert "c72a91fe4b31" == db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        app.extensions["agentic_test_downgrade"] = downgrade
        app.extensions["agentic_test_upgrade"] = upgrade
        app.extensions["agentic_test_migration_check"] = check
        app.extensions["agentic_test_migrations_dir"] = str(MIGRATIONS_DIR)
    app.extensions["agentic_test_org_ids"] = set()
    yield app
    with app.app_context():
        for org_id in app.extensions["agentic_test_org_ids"]:
            _cleanup_test_org(app, org_id)
        db.session.commit()
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
        app.extensions["agentic_test_org_ids"].add(organization.id)
        token = create_access_token(
            identity=str(user.id), additional_claims={"org_id": organization.id},
        )
        return organization.id, user.id, {"Authorization": f"Bearer {token}"}


def _create_test_user(app, org_id, tag):
    from app import db
    from app.models.user import User

    with app.app_context():
        user = User(
            email=f"a31-{tag}@example.test",
            password_hash=generate_password_hash("postgres-proposal-test-password-123"),
            org_id=org_id,
            role="admin",
        )
        db.session.add(user)
        db.session.commit()
        app.extensions["agentic_test_org_ids"].add(org_id)
        token = create_access_token(identity=str(user.id), additional_claims={"org_id": org_id})
        return user.id, {"Authorization": f"Bearer {token}"}


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


def test_postgres_migration_constraints_and_two_session_idempotent_creation(postgres_app, monkeypatch):
    from threading import Barrier

    from app import db
    from app.agentic import proposal_service as proposal_service_module
    from app.models.agentic_action_proposal import AgenticActionProposal

    tag = uuid4().hex
    org_id, user_id, headers = _create_test_actor(postgres_app, tag)
    employee_id = _create_employee(postgres_app, org_id, tag)
    barrier = Barrier(2)
    insert_barrier = Barrier(2)
    seal_payload = proposal_service_module.seal_payload

    def synchronize_before_insert(proposal, payload):
        result = seal_payload(proposal, payload)
        insert_barrier.wait(timeout=20)
        return result

    monkeypatch.setattr(proposal_service_module, "seal_payload", synchronize_before_insert)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_request_proposal, postgres_app, headers, employee_id, f"pg-{tag}", barrier)
            for _ in range(2)
        ]
        responses = [future.result(timeout=45) for future in futures]
    monkeypatch.setattr(proposal_service_module, "seal_payload", seal_payload)

    assert all(status in {200, 201} for status, _body in responses)
    assert responses[0][1]["proposal_id"] == responses[1][1]["proposal_id"]
    assert responses[0][1]["confirmation_token"] == responses[1][1]["confirmation_token"]
    same_action_replay = _request_proposal(
        postgres_app, headers, employee_id, f"pg-{tag}", Barrier(1),
    )
    assert same_action_replay[0] == 200
    assert same_action_replay[1]["proposal_id"] == responses[0][1]["proposal_id"]
    with postgres_app.test_client() as client:
        different_arguments = client.post("/v1/agentic/proposals", headers=headers, json={
            "capability": "employees.update.v1", "version": "v1",
            "arguments": {"employee_id": employee_id, "team": "Different action"},
            "idempotency_key": f"pg-{tag}",
        })
    assert different_arguments.status_code == 409

    _same_org_other_user_id, same_org_other_headers = _create_test_user(postgres_app, org_id, f"same-org-{tag}")
    other_user_result = _request_proposal(
        postgres_app, same_org_other_headers, employee_id, f"pg-{tag}", Barrier(1),
    )
    assert other_user_result[0] == 201
    assert other_user_result[1]["proposal_id"] != responses[0][1]["proposal_id"]
    assert other_user_result[1]["confirmation_token"] != responses[0][1]["confirmation_token"]

    other_org_id, _other_org_user_id, other_org_headers = _create_test_actor(postgres_app, f"other-org-{tag}")
    other_employee_id = _create_employee(postgres_app, other_org_id, tag)
    other_org_result = _request_proposal(
        postgres_app, other_org_headers, other_employee_id, f"pg-{tag}", Barrier(1),
    )
    assert other_org_result[0] == 201
    assert other_org_result[1]["proposal_id"] != responses[0][1]["proposal_id"]
    assert other_org_result[1]["confirmation_token"] != responses[0][1]["confirmation_token"]
    with postgres_app.app_context():
        rows = AgenticActionProposal.query.filter_by(org_id=org_id).all()
        owner_rows = AgenticActionProposal.query.filter_by(org_id=org_id, initiator_id=user_id).all()
        assert len(rows) == 2
        assert len(owner_rows) == 1
        assert all(row.confirmation_token_recovery_ciphertext for row in rows)
        unique_constraints = {
            constraint["name"] for constraint in inspect(db.engine).get_unique_constraints("agentic_action_proposals")
        }
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("agentic_action_proposals")}
        assert "uq_agentic_proposal_org_initiator_idempotency" in unique_constraints
        assert "ix_agentic_proposals_org_status_expiry" in indexes
        _cleanup_test_org(postgres_app, org_id)
        _cleanup_test_org(postgres_app, other_org_id)
        db.session.commit()


def test_postgres_two_session_cancellation_and_tenant_isolation(postgres_app):
    from threading import Barrier

    from app import db
    from app.models.governance import AuditEvent

    tag = uuid4().hex
    org_id, user_id, headers = _create_test_actor(postgres_app, tag)
    _same_org_other_user_id, same_org_other_headers = _create_test_user(postgres_app, org_id, f"same-org-{tag}")
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
        same_org_foreign = client.get(f"/v1/agentic/proposals/{proposal_id}", headers=same_org_other_headers)
        same_org_cancel = client.post(
            f"/v1/agentic/proposals/{proposal_id}/cancel", headers=same_org_other_headers, json={},
        )
        foreign_cancel = client.post(
            f"/v1/agentic/proposals/{proposal_id}/cancel", headers=other_headers, json={},
        )
        assert foreign.status_code == same_org_foreign.status_code == 404
        assert same_org_cancel.status_code == foreign_cancel.status_code == 404

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

    with postgres_app.app_context():
        cancellations = AuditEvent.query.filter_by(
            org_id=org_id, event_type="PROPOSAL_CANCELLED",
        ).all()
        assert sum(event.resource_id == proposal_id for event in cancellations) == 1

    # Remove only rows created by this test; no database/schema/table is dropped.
    with postgres_app.app_context():
        _cleanup_test_org(postgres_app, org_id)
        _cleanup_test_org(postgres_app, other_org_id)
        db.session.commit()


def test_postgres_two_session_expiration_is_idempotent(postgres_app):
    from threading import Barrier
    from datetime import datetime, timedelta, timezone

    from app import db
    from app.agentic.proposal_service import proposal_service
    from app.models.agentic_action_proposal import AgenticActionProposal
    from app.models.governance import AuditEvent

    tag = uuid4().hex
    org_id, _user_id, headers = _create_test_actor(postgres_app, tag)
    employee_id = _create_employee(postgres_app, org_id, tag)
    with postgres_app.test_client() as client:
        created = client.post("/v1/agentic/proposals", headers=headers, json={
            "capability": "employees.update.v1", "version": "v1",
            "arguments": {"employee_id": employee_id, "team": "Expire race"},
            "idempotency_key": f"expire-{tag}",
        })
        assert created.status_code == 201
        proposal_id = created.get_json()["proposal_id"]

    # Keep the DB invariant expires_at > created_at while placing both dates in the past.
    with postgres_app.app_context():
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.execute(
            update(AgenticActionProposal)
            .where(AgenticActionProposal.id == proposal_id)
            .values(created_at=expired_at - timedelta(minutes=1), expires_at=expired_at)
        )
        db.session.commit()

    barrier = Barrier(2)

    def expire_batch():
        with postgres_app.app_context():
            barrier.wait(timeout=20)
            return proposal_service.expire_due(batch_size=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(expire_batch) for _ in range(2)]
        counts = [future.result(timeout=45) for future in futures]
    assert sum(counts) == 1
    with postgres_app.app_context():
        proposal = db.session.get(AgenticActionProposal, proposal_id)
        assert proposal.status == "EXPIRED"
        assert proposal.confirmation_token_recovery_ciphertext is None
        expirations = AuditEvent.query.filter_by(org_id=org_id, event_type="PROPOSAL_EXPIRED").all()
        assert sum(event.resource_id == proposal_id for event in expirations) == 1
        assert proposal_service.expire_due(batch_size=10) == 0
        _cleanup_test_org(postgres_app, org_id)
        db.session.commit()


def test_postgres_recovery_migration_downgrade_upgrade_when_table_is_empty(postgres_app):
    from app import db
    from app.models.agentic_action_proposal import AgenticActionProposal

    with postgres_app.app_context():
        if AgenticActionProposal.query.count() != 0:
            pytest.skip("Refusing PostgreSQL migration rollback while proposal rows exist")
        # Release the read transaction before PostgreSQL's ALTER TABLE lock is requested.
        db.session.commit()
        db.session.remove()
        downgrade = postgres_app.extensions["agentic_test_downgrade"]
        upgrade = postgres_app.extensions["agentic_test_upgrade"]
        migrations_dir = postgres_app.extensions["agentic_test_migrations_dir"]
        downgrade(directory=migrations_dir, revision="a31e8c4d2f90")
        recovery_columns = {
            column["name"] for column in inspect(db.engine).get_columns("agentic_action_proposals")
        }
        assert "confirmation_token_recovery_ciphertext" not in recovery_columns
        upgrade(directory=migrations_dir, revision="head")
        recovery_columns = {
            column["name"] for column in inspect(db.engine).get_columns("agentic_action_proposals")
        }
        assert "confirmation_token_recovery_ciphertext" in recovery_columns
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "c72a91fe4b31"
        postgres_app.extensions["agentic_test_migration_check"](
            directory=postgres_app.extensions["agentic_test_migrations_dir"],
        )
