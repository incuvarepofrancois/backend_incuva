import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token, verify_jwt_in_request
from werkzeug.security import generate_password_hash

from app.agentic.context import build_execution_context
from app.agentic.errors import AgenticContextError, AgenticPermissionError
from app.agentic.permissions import authorize_capability
from app.agentic.registry import build_default_registry


@pytest.fixture
def agentic_app():
    from app import create_app, db

    app = create_app("testing")
    app.config["INTEGRATION_CREDENTIALS_KEY"] = Fernet.generate_key().decode("ascii")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _user_token(app, *, org_id, role="admin"):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        if db.session.get(Organization, org_id) is None:
            db.session.add(Organization(id=org_id, name=f"Test organization {org_id}"))
            db.session.flush()
        user = User(
            email=f"agentic-{org_id}-{role}@example.test",
            password_hash=generate_password_hash("agentic-test-password-123"),
            org_id=org_id,
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(
            identity=str(user.id), additional_claims={"org_id": org_id, "role": role},
        )
        return token


def _context_for_token(app, token):
    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        return build_execution_context()


def test_context_is_derived_from_authenticated_user_and_has_correlation_id(agentic_app):
    token = _user_token(agentic_app, org_id=701)
    context = _context_for_token(agentic_app, token)

    assert context.org_id == 701
    assert context.user_id > 0
    assert context.correlation_id
    assert "employees:view" in context.permissions
    with pytest.raises((AttributeError, TypeError)):
        context.org_id = 702


def test_context_rejects_absent_identity_and_untrusted_organization(agentic_app):
    with agentic_app.test_request_context():
        with pytest.raises(AgenticContextError):
            build_execution_context()

    _user_token(agentic_app, org_id=702)
    from flask_jwt_extended import create_access_token
    with agentic_app.app_context():
        user = _user_token_user_id(agentic_app, 702)
        missing_org = create_access_token(identity=str(user))
        forged = create_access_token(identity=str(user), additional_claims={"org_id": 703})
    with pytest.raises(AgenticContextError):
        _context_for_token(agentic_app, missing_org)
    with pytest.raises(AgenticContextError):
        _context_for_token(agentic_app, forged)


def _user_token_user_id(app, org_id):
    from app.models.user import User
    from app import db
    return db.session.query(User.id).filter_by(org_id=org_id).scalar()


def test_capability_permission_uses_current_governance_rules(agentic_app):
    from app import db
    from app.models.governance import AccessRule

    token = _user_token(agentic_app, org_id=704, role="analyst")
    context = _context_for_token(agentic_app, token)
    registry = build_default_registry()

    with agentic_app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        authorize_capability(context, registry.get("employees.list.v1", "v1"))
        authorize_capability(context, registry.get("onboarding.plans.list.v1", "v1"))

    with agentic_app.app_context():
        db.session.add(AccessRule(
            org_id=704, role="analyst", resource="employees", action="view",
            effect="deny", enabled=True,
        ))
        db.session.commit()
    with agentic_app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        with pytest.raises(AgenticPermissionError):
            authorize_capability(context, registry.get("employees.list.v1", "v1"))


def test_context_cannot_be_rebound_to_another_organization(agentic_app):
    token = _user_token(agentic_app, org_id=705, role="admin")
    context = _context_for_token(agentic_app, token)
    registry = build_default_registry()

    with agentic_app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        from dataclasses import replace
        forged = replace(context, org_id=706)
        with pytest.raises(AgenticPermissionError):
            authorize_capability(forged, registry.get("employees.list.v1", "v1"))


def test_analyst_cannot_gain_manage_access_from_capability_metadata(agentic_app):
    from app.agentic.capabilities.employee import EmployeeListInput
    from app.agentic.contracts import CapabilityContract

    token = _user_token(agentic_app, org_id=707, role="analyst")
    context = _context_for_token(agentic_app, token)
    write_only = CapabilityContract(
        name="employees.synthetic-write.v1", version="v1", description="Synthetic permission check.",
        domain="Employee", operation="synthetic", access_type="write",
        required_permissions=("onboarding:manage",), input_schema=EmployeeListInput,
        output_schema=EmployeeListInput, requires_confirmation=True, audit_required=True,
        enabled=True, limitations=(),
    )
    with agentic_app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        with pytest.raises(AgenticPermissionError):
            authorize_capability(context, write_only)
