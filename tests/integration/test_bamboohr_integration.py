from datetime import datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash


@pytest.fixture
def integration_client(monkeypatch):
    from app import create_app, db

    app = create_app("testing")
    app.config["INTEGRATION_CREDENTIALS_KEY"] = Fernet.generate_key().decode("ascii")
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _organization_token(client, org_id, email):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with client.application.app_context():
        if db.session.get(Organization, org_id) is None:
            db.session.add(Organization(id=org_id, name=f"Org {org_id}"))
            db.session.flush()
        user = User(email=email, password_hash=generate_password_hash("sync-test-password-123"), org_id=org_id, role="admin")
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"org_id": org_id, "role": user.role})
        return {"Authorization": f"Bearer {token}"}


def test_bamboohr_sync_is_idempotent_and_organization_scoped(integration_client, monkeypatch):
    from app import db
    from app.models.employee import Employee
    from app.models.integration_sync import EmployeeExternalId, IntegrationSync
    from app.services.bamboohr_sync import run_bamboohr_sync

    headers_a = _organization_token(integration_client, 501, "sync-a@example.test")
    _organization_token(integration_client, 502, "sync-b@example.test")
    source_page = [{
        "employeeId": "bamboo-77",
        "firstName": "Ariane",
        "lastName": "Martin",
        "jobTitleName": "Analyste",
        "department": "Data",
        "status": "Active",
    }]

    monkeypatch.setattr("app.connectors.bamboohr.BambooHRConnector.iter_employee_pages", lambda _self: iter([source_page]))
    with integration_client.application.app_context():
        sync = IntegrationSync(org_id=501, integration_key="bamboohr", status="queued")
        db.session.add(sync)
        db.session.commit()
        sync_id = sync.id

        first_result = run_bamboohr_sync(501, sync_id)
        first_employee_id = EmployeeExternalId.query.filter_by(org_id=501).one().employee_id
        second_result = run_bamboohr_sync(501, sync_id)
        assert first_result["status"] == "succeeded"
        assert second_result["records_synced"] == 1
        assert Employee.query.filter_by(org_id=501).count() == 1
        assert Employee.query.filter_by(org_id=502).count() == 0
        assert EmployeeExternalId.query.filter_by(org_id=501).one().employee_id == first_employee_id
        assert Employee.query.filter_by(org_id=501).one().team == "Data"
        assert second_result["last_success_at"]

    status = integration_client.get("/v1/integrations/bamboohr/sync", headers=headers_a)
    assert status.status_code == 200
    assert status.get_json()["sync"]["records_synced"] == 1


def test_bamboohr_sync_status_does_not_cross_organizations(integration_client):
    from app import db
    from app.models.integration_sync import IntegrationSync

    headers_a = _organization_token(integration_client, 511, "status-a@example.test")
    headers_b = _organization_token(integration_client, 512, "status-b@example.test")
    with integration_client.application.app_context():
        db.session.add(IntegrationSync(org_id=511, integration_key="bamboohr", status="succeeded", records_synced=15, last_success_at=datetime.utcnow()))
        db.session.commit()

    response = integration_client.get("/v1/integrations/bamboohr/sync", headers=headers_b)
    assert response.status_code == 200
    assert response.get_json()["sync"]["status"] == "idle"
    assert response.get_json()["sync"]["records_synced"] == 0


def test_bamboohr_connection_test_uses_saved_org_credentials(integration_client, monkeypatch):
    class FakeConnector:
        def __init__(self, org_id):
            self.org_id = org_id

        def connect(self):
            assert self.org_id == 521

    monkeypatch.setattr("app.connectors.bamboohr.BambooHRConnector", FakeConnector)
    headers = _organization_token(integration_client, 521, "connection@example.test")
    response = integration_client.post("/v1/integrations/bamboohr/test", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["connection_verified"] is True


def test_bamboohr_sync_endpoint_queues_one_org_scoped_job(integration_client, monkeypatch):
    import json
    from app import db
    from app.models.integration_credential import IntegrationCredential
    from app.security.crypto import encrypt_credential

    headers = _organization_token(integration_client, 531, "queue@example.test")
    with integration_client.application.app_context():
        db.session.add(IntegrationCredential(
            org_id=531,
            integration_key="bamboohr",
            encrypted_credentials=encrypt_credential(json.dumps({"subdomain": "test", "api_key": "x"}).encode()),
        ))
        db.session.commit()
    monkeypatch.setattr(
        "app.services.bamboohr_tasks.sync_bamboohr_employees_task.delay",
        lambda org_id, sync_id: SimpleNamespace(id=f"task-{org_id}-{sync_id}"),
    )

    queued = integration_client.post("/v1/integrations/bamboohr/sync", headers=headers)
    assert queued.status_code == 202
    assert queued.get_json()["sync"]["status"] == "queued"
    duplicate = integration_client.post("/v1/integrations/bamboohr/sync", headers=headers)
    assert duplicate.status_code == 409


def test_bamboohr_connector_follows_cursor_pages(monkeypatch):
    from app.connectors.bamboohr import BambooHRConnector

    calls = []
    responses = [
        {"data": [{"employeeId": "1"}], "meta": {"page": {"nextCursor": "cursor-2"}}},
        {"data": [{"employeeId": "2"}], "meta": {"page": {"nextCursor": None}}},
    ]

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    connector = BambooHRConnector(541)
    connector._organization_credentials = lambda key: {"subdomain": "example", "api_key": "test-key"}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs["params"].copy()))
        return Response(responses[len(calls) - 1])

    monkeypatch.setattr(connector._session, "get", fake_get)
    assert [row["employeeId"] for row in connector.iter_employees()] == ["1", "2"]
    assert calls[0][1]["page[limit]"] == 250
    assert calls[1][1]["page[after]"] == "cursor-2"
