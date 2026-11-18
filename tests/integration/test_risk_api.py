import os
import pytest

os.environ.setdefault("FLASK_ENV", "testing")


@pytest.fixture
def client():
    from app import create_app, db as _db
    app = create_app("testing")
    with app.test_client() as c:
        with app.app_context():
            _db.create_all()
            yield c
            _db.drop_all()


def test_predict_turnover_requires_auth(client):
    resp = client.post("/v1/predict-turnover", json={"employee_id": 1})
    assert resp.status_code == 401


def test_signals_requires_auth(client):
    resp = client.get("/v1/employees/1/signals")
    assert resp.status_code == 401


def test_employees_requires_auth(client):
    resp = client.get("/v1/employees")
    assert resp.status_code == 401


def test_employee_and_insight_routes_are_organization_scoped(client):
    from app import db
    from app.models.employee import Employee
    from app.models.insight import Insight
    from app.models.organization import Organization
    from app.models.signal import Signal
    from app.models.user import User
    from flask_jwt_extended import create_access_token
    from werkzeug.security import generate_password_hash

    with client.application.app_context():
        org_a = Organization(id=101, name="Org A")
        org_b = Organization(id=202, name="Org B")
        db.session.add_all([org_a, org_b])
        db.session.flush()
        user_a = User(email="a@example.com", password_hash=generate_password_hash("password-a-123"), org_id=101)
        user_b = User(email="b@example.com", password_hash=generate_password_hash("password-b-123"), org_id=202)
        employee_a = Employee(name="A employee", org_id=101)
        employee_b = Employee(name="B employee", org_id=202)
        db.session.add_all([user_a, user_b, employee_a, employee_b])
        db.session.flush()
        db.session.add_all([
            Insight(org_id=101, type="test", title="A insight"),
            Insight(org_id=202, type="test", title="B insight"),
        ])
        db.session.add_all([
            Signal(employee_id=employee_a.id, source="test", type="a_signal"),
            Signal(employee_id=employee_b.id, source="test", type="b_signal"),
        ])
        db.session.commit()
        employee_b_id = employee_b.id
        token_a = create_access_token(identity=str(user_a.id), additional_claims={"org_id": 101})
    headers = {"Authorization": f"Bearer {token_a}"}

    listed = client.get("/v1/employees", headers=headers).get_json()["employees"]
    assert [employee["name"] for employee in listed] == ["A employee"]
    assert client.get(f"/v1/employees/{employee_b_id}", headers=headers).status_code == 404
    assert client.get(f"/v1/employees/{employee_b_id}/signals", headers=headers).status_code == 404
    assert client.post("/v1/predict-turnover", headers=headers,
                       json={"employee_id": employee_b_id}).status_code == 404
    assert [item["title"] for item in client.get("/v1/insights/latest", headers=headers).get_json()["insights"]] == ["A insight"]


def test_employee_creation_uses_token_organization(client):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from flask_jwt_extended import create_access_token
    from werkzeug.security import generate_password_hash

    with client.application.app_context():
        org = Organization(id=303, name="Org C")
        user = User(email="c@example.com", password_hash=generate_password_hash("password-c-123"), org_id=303)
        db.session.add_all([org, user])
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"org_id": 303})
    response = client.post("/v1/employees", headers={"Authorization": f"Bearer {token}"}, json={
        "name": "New employee", "org_id": 101,
    })
    assert response.status_code == 201
    assert response.get_json()["org_id"] == 303


def test_employee_manager_cannot_cross_organization_boundary(client):
    from app import db
    from app.models.employee import Employee
    from app.models.organization import Organization
    from app.models.user import User
    from flask_jwt_extended import create_access_token
    from werkzeug.security import generate_password_hash

    with client.application.app_context():
        organization_a = Organization(id=401, name="Org A")
        organization_b = Organization(id=402, name="Org B")
        db.session.add_all([organization_a, organization_b])
        db.session.flush()
        user_a = User(email="manager-a@example.com", password_hash=generate_password_hash("password-manager-a"), org_id=401)
        manager_a = Employee(name="Manager A", org_id=401)
        employee_a = Employee(name="Employee A", org_id=401)
        manager_b = Employee(name="Manager B", org_id=402)
        db.session.add_all([user_a, manager_a, employee_a, manager_b])
        db.session.flush()
        employee_a.manager_id = manager_a.id
        db.session.commit()
        employee_a_id = employee_a.id
        manager_a_id = manager_a.id
        manager_b_id = manager_b.id
        token_a = create_access_token(identity=str(user_a.id), additional_claims={"org_id": 401})

    headers = {"Authorization": f"Bearer {token_a}"}
    create_foreign_manager = client.post("/v1/employees", headers=headers, json={
        "name": "New employee", "manager_id": manager_b_id,
    })
    assert create_foreign_manager.status_code == 404

    update_foreign_manager = client.put(f"/v1/employees/{employee_a_id}", headers=headers, json={
        "manager_id": manager_b_id,
    })
    assert update_foreign_manager.status_code == 404

    with client.application.app_context():
        assert db.session.get(Employee, employee_a_id).manager_id == manager_a_id

    assert client.put(f"/v1/employees/{manager_b_id}", headers=headers, json={
        "name": "Changed by Org A",
    }).status_code == 404


def test_jarvis_insight_is_assigned_to_employee_organization(client):
    from app import db
    from app.models.employee import Employee
    from app.models.insight import Insight
    from app.models.organization import Organization
    from app.services.jarvis import JarvisOrchestrator

    with client.application.app_context():
        organization = Organization(name="Insight organization")
        db.session.add(organization)
        db.session.flush()
        employee = Employee(name="At risk employee", org_id=organization.id)
        db.session.add(employee)
        db.session.commit()
        organization_id = organization.id
        employee_id = employee.id

        JarvisOrchestrator()._create_insight(employee_id, {
            "risk_level": "critical", "risk_score": 82, "employee_id": employee_id,
        })

        insight = Insight.query.one()
        assert insight.org_id == organization_id


def test_jarvis_does_not_create_unscoped_insight_for_unassigned_employee(client):
    from app import db
    from app.models.employee import Employee
    from app.models.insight import Insight
    from app.services.jarvis import JarvisOrchestrator

    with client.application.app_context():
        employee = Employee(name="Unassigned employee", org_id=None)
        db.session.add(employee)
        db.session.commit()

        JarvisOrchestrator()._create_insight(employee.id, {
            "risk_level": "critical", "risk_score": 82, "employee_id": employee.id,
        })

        assert Insight.query.count() == 0
