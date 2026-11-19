from dataclasses import replace
from datetime import date

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token, verify_jwt_in_request
from werkzeug.security import generate_password_hash

from app.agentic.errors import (
    AgenticContextError,
    AgenticPermissionError,
    CapabilityInputError,
    CapabilityNotFoundError,
    CapabilityResourceNotFoundError,
    CapabilityUnavailableError,
    CapabilityVersionError,
)
from app.agentic.executor import CapabilityExecutor, execute_capability
from app.agentic.registry import CapabilityRegistry, build_default_registry


@pytest.fixture
def execution_app():
    from app import create_app, db

    app = create_app("testing")
    app.config["INTEGRATION_CREDENTIALS_KEY"] = Fernet.generate_key().decode("ascii")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _make_user(app, org_id, role="admin"):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        if db.session.get(Organization, org_id) is None:
            db.session.add(Organization(id=org_id, name=f"Execution organization {org_id}"))
            db.session.flush()
        user = User(
            email=f"execution-{org_id}-{role}@example.test",
            password_hash=generate_password_hash("execution-test-password-123"),
            org_id=org_id,
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"org_id": org_id})
        return user.id, token


def _run(app, token, name, arguments, *, version=None, executor=None):
    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        runner = executor or CapabilityExecutor()
        return runner.execute(name, arguments, version=version)


def _add_employee(app, org_id, name):
    from app import db
    from app.models.employee import Employee

    with app.app_context():
        employee = Employee(org_id=org_id, name=name)
        db.session.add(employee)
        db.session.commit()
        return employee.id


def test_executor_runs_only_registered_backend_handlers_and_returns_validated_result(execution_app):
    _user_id, token = _make_user(execution_app, 901)
    _add_employee(execution_app, 901, "A employee")
    _add_employee(execution_app, 902, "B employee")

    result = _run(execution_app, token, "employees.list.v1", {"page": 1, "per_page": 1})

    assert result.capability == "employees.list.v1"
    assert result.version == "v1"
    assert result.to_dict()["result"] == {
        "employees": [{"id": 1, "name": "A employee", "role": None, "team": None, "status": "active"}],
        "total": 1,
        "page": 1,
        "page_size": 1,
        "returned": 1,
        "has_next_page": False,
        "coverage_complete": True,
    }


def test_executor_rejects_unknown_version_disabled_capability_and_unknown_name(execution_app):
    _user_id, token = _make_user(execution_app, 903)
    executor = CapabilityExecutor()
    with pytest.raises(CapabilityNotFoundError):
        _run(execution_app, token, "missing.capability.v1", {}, executor=executor)
    with pytest.raises(CapabilityVersionError):
        _run(execution_app, token, "employees.list.v1", {}, version="v2", executor=executor)

    default_registry = build_default_registry()
    disabled_contract = replace(default_registry.get("employees.list.v1", "v1"), enabled=False)
    disabled_executor = CapabilityExecutor(CapabilityRegistry((disabled_contract,)))
    with pytest.raises(CapabilityUnavailableError):
        _run(execution_app, token, "employees.list.v1", {}, executor=disabled_executor)

    write_capability = {
        "employee_id": 1,
        "name": "Cannot execute",
    }
    with pytest.raises(CapabilityUnavailableError):
        _run(execution_app, token, "employees.update.v1", write_capability)


def test_executor_rejects_invalid_arguments_and_never_accepts_handler_or_tenant_override(execution_app):
    _user_id, token = _make_user(execution_app, 904)
    for arguments in (
        {"org_id": 999, "page": 1, "per_page": 20},
        {"page": 1, "per_page": 20, "handler": "some_python_function"},
        {"page": 1, "per_page": 101},
    ):
        with pytest.raises(CapabilityInputError):
            _run(execution_app, token, "employees.list.v1", arguments)


def test_executor_rechecks_permission_and_rejects_invalid_context(execution_app):
    from app import db
    from app.models.governance import AccessRule

    _user_id, token = _make_user(execution_app, 905, role="analyst")
    with execution_app.app_context():
        db.session.add(AccessRule(
            org_id=905, role="analyst", resource="employees", action="view",
            effect="deny", enabled=True,
        ))
        db.session.commit()
    with pytest.raises(AgenticPermissionError):
        _run(execution_app, token, "employees.list.v1", {})

    _user_id, token = _make_user(execution_app, 906)
    from flask_jwt_extended import create_access_token
    with execution_app.app_context():
        forged = create_access_token(identity=str(_user_id), additional_claims={"org_id": 907})
    with pytest.raises(AgenticContextError):
        _run(execution_app, forged, "employees.list.v1", {})


def test_employee_list_is_organization_scoped_paginated_and_reports_scoped_total(execution_app):
    _user_id, token_a = _make_user(execution_app, 911)
    _user_id, token_b = _make_user(execution_app, 912)
    for index in range(3):
        _add_employee(execution_app, 911, f"A {index}")
    for index in range(2):
        _add_employee(execution_app, 912, f"B {index}")

    page_a = _run(execution_app, token_a, "employees.list.v1", {"page": 1, "per_page": 2}).to_dict()["result"]
    page_b = _run(execution_app, token_b, "employees.list.v1", {"page": 1, "per_page": 100}).to_dict()["result"]
    page_a_2 = _run(execution_app, token_a, "employees.list.v1", {"page": 2, "per_page": 2}).to_dict()["result"]

    assert page_a["total"] == 3 and page_a["returned"] == 2
    assert page_a["has_next_page"] is True and page_a["coverage_complete"] is False
    assert all(employee["name"].startswith("A ") for employee in page_a["employees"])
    assert page_a_2["total"] == 3 and page_a_2["returned"] == 1
    assert page_b["total"] == 2 and page_b["returned"] == 2
    assert all(employee["name"].startswith("B ") for employee in page_b["employees"])


def test_employee_detail_is_scoped_and_missing_and_cross_tenant_ids_are_indistinguishable(execution_app):
    _user_id, token_a = _make_user(execution_app, 921)
    _user_id, token_b = _make_user(execution_app, 922)
    employee_a_id = _add_employee(execution_app, 921, "Private A")

    detail = _run(execution_app, token_a, "employee.get.v1", {"employee_id": employee_a_id}).to_dict()["result"]
    assert detail["name"] == "Private A"
    with pytest.raises(CapabilityResourceNotFoundError) as missing:
        _run(execution_app, token_a, "employee.get.v1", {"employee_id": 99999})
    with pytest.raises(CapabilityResourceNotFoundError) as cross_tenant:
        _run(execution_app, token_b, "employee.get.v1", {"employee_id": employee_a_id})
    assert str(missing.value) == str(cross_tenant.value)
    with pytest.raises(CapabilityInputError):
        _run(execution_app, token_a, "employee.get.v1", {"employee_id": 0})


def test_employee_and_onboarding_capabilities_require_their_view_permissions(execution_app):
    from app import db
    from app.models.governance import AccessRule

    _user_id, token = _make_user(execution_app, 931, role="analyst")
    with execution_app.app_context():
        db.session.add(AccessRule(
            org_id=931, role="analyst", resource="onboarding", action="view",
            effect="deny", enabled=True,
        ))
        db.session.commit()
    # Analyst retains employees:view but the organization rule denies onboarding:view.
    _run(execution_app, token, "employees.list.v1", {})
    with pytest.raises(AgenticPermissionError):
        _run(execution_app, token, "onboarding.plans.list.v1", {})


def test_onboarding_list_returns_progress_tasks_and_scoped_coverage(execution_app):
    from app import db
    from app.models.employee import Employee
    from app.models.onboarding import OnboardingPlan, OnboardingTask

    _user_id, token_a = _make_user(execution_app, 941)
    _user_id, token_b = _make_user(execution_app, 942)
    with execution_app.app_context():
        employee_a = Employee(org_id=941, name="A employee")
        employee_b = Employee(org_id=942, name="B employee")
        db.session.add_all([employee_a, employee_b])
        db.session.flush()
        plan_a = OnboardingPlan(org_id=941, employee_id=employee_a.id, start_date=date(2025, 1, 1))
        plan_b = OnboardingPlan(org_id=942, employee_id=employee_b.id, start_date=date(2025, 1, 2))
        plan_a.tasks.extend([
            OnboardingTask(title="Done", due_date=date(2025, 1, 1), status="completed"),
            OnboardingTask(title="Next", due_date=date(2025, 1, 2), status="pending"),
        ])
        plan_b.tasks.append(OnboardingTask(title="B only", due_date=date(2025, 1, 2), status="pending"))
        db.session.add_all([plan_a, plan_b])
        db.session.commit()

    plans_a = _run(execution_app, token_a, "onboarding.plans.list.v1", {}).to_dict()["result"]
    plans_b = _run(execution_app, token_b, "onboarding.plans.list.v1", {}).to_dict()["result"]
    assert plans_a["returned"] == 1 and plans_a["completeness"] == "complete"
    assert plans_a["plans"][0]["progress"] == 50
    assert [task["title"] for task in plans_a["plans"][0]["tasks"]] == ["Done", "Next"]
    assert plans_b["returned"] == 1
    assert plans_b["plans"][0]["tasks"][0]["title"] == "B only"


def test_onboarding_empty_list_reports_complete_zero_coverage(execution_app):
    _user_id, token = _make_user(execution_app, 943)
    result = _run(execution_app, token, "onboarding.plans.list.v1", {}).to_dict()["result"]
    assert result["plans"] == []
    assert result["returned"] == 0
    assert result["coverage_limit"] == 200
    assert result["completeness"] == "complete"


def test_onboarding_hides_invalid_cross_tenant_employee_link_and_marks_coverage_partial(execution_app):
    from app import db
    from app.models.employee import Employee
    from app.models.onboarding import OnboardingPlan

    _user_id, token = _make_user(execution_app, 951)
    with execution_app.app_context():
        other_employee = Employee(org_id=952, name="Do not expose")
        db.session.add(other_employee)
        db.session.flush()
        db.session.add(OnboardingPlan(org_id=951, employee_id=other_employee.id, start_date=date.today()))
        db.session.commit()

    result = _run(execution_app, token, "onboarding.plans.list.v1", {}).to_dict()["result"]
    assert result["plans"] == []
    assert result["returned"] == 0
    assert result["completeness"] == "partial"


def test_onboarding_exactly_at_200_records_reports_unknown_completeness(execution_app):
    from app import db
    from app.models.employee import Employee
    from app.models.onboarding import OnboardingPlan

    _user_id, token = _make_user(execution_app, 961)
    with execution_app.app_context():
        employee = Employee(org_id=961, name="Shared employee")
        db.session.add(employee)
        db.session.flush()
        db.session.add_all([
            OnboardingPlan(org_id=961, employee_id=employee.id, start_date=date(2025, 1, 1))
            for _ in range(201)
        ])
        db.session.commit()

    result = _run(execution_app, token, "onboarding.plans.list.v1", {}).to_dict()["result"]
    assert result["returned"] == 200
    assert result["coverage_limit"] == 200
    assert result["completeness"] == "unknown"


def test_successful_execution_audits_metadata_without_employee_payload(execution_app):
    from app import db
    from app.models.governance import AuditEvent

    _user_id, token = _make_user(execution_app, 971)
    _add_employee(execution_app, 971, "Sensitive employee name")
    _run(execution_app, token, "employees.list.v1", {})

    with execution_app.app_context():
        event = AuditEvent.query.filter_by(org_id=971, event_type="agentic.capability.executed").one()
        assert event.outcome == "success"
        assert event.details["capability"] == "employees.list.v1"
        assert "Sensitive employee name" not in str(event.details)
