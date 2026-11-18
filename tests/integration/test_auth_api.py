import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def client():
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import User
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        db.session.add(Organization(id=12, name="Test organization"))
        db.session.commit()
        db.session.add(User(
            email="admin@example.com",
            password_hash=generate_password_hash("a-long-test-password"),
            org_id=12,
        ))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_login_returns_scoped_tokens_for_valid_credentials(client):
    response = client.post("/v1/auth/login", json={
        "email": "ADMIN@example.com", "password": "a-long-test-password"
    })
    assert response.status_code == 200
    tokens = response.get_json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["user"]["email"] == "admin@example.com"

    protected = client.get("/v1/meetings", headers={
        "Authorization": f"Bearer {tokens['access_token']}"
    })
    assert protected.status_code == 200

    current_user = client.get("/v1/auth/me", headers={
        "Authorization": f"Bearer {tokens['access_token']}"
    })
    assert current_user.status_code == 200
    assert current_user.get_json()["user"]["org_id"] == 12


def test_refresh_renews_access_token_and_logout_revokes_the_session(client):
    login_response = client.post("/v1/auth/login", json={
        "email": "admin@example.com", "password": "a-long-test-password"
    })
    tokens = login_response.get_json()

    refresh_response = client.post("/v1/auth/refresh", headers={
        "Authorization": f"Bearer {tokens['refresh_token']}"
    })
    assert refresh_response.status_code == 200
    refreshed_access_token = refresh_response.get_json()["access_token"]
    assert refreshed_access_token

    logout_response = client.post("/v1/auth/logout", headers={
        "Authorization": f"Bearer {refreshed_access_token}"
    })
    assert logout_response.status_code == 200

    current_user = client.get("/v1/auth/me", headers={
        "Authorization": f"Bearer {refreshed_access_token}"
    })
    assert current_user.status_code == 401

    refresh_after_logout = client.post("/v1/auth/refresh", headers={
        "Authorization": f"Bearer {tokens['refresh_token']}"
    })
    assert refresh_after_logout.status_code == 401


@pytest.mark.parametrize("payload", [
    {"email": "admin@example.com", "password": "wrong"},
    {"email": "unknown@example.com", "password": "a-long-test-password"},
    {},
])
def test_login_rejects_invalid_credentials(client, payload):
    response = client.post("/v1/auth/login", json=payload)
    assert response.status_code == 401


def test_register_creates_organization_admin_and_session(client):
    response = client.post("/v1/auth/register", json={
        "organization_name": "Nouvelle entreprise",
        "email": "  ADMIN@EXAMPLE.ORG ",
        "password": "a-secure-password-long-enough",
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["user"]["email"] == "admin@example.org"
    assert data["user"]["role"] == "admin"

    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    with client.application.app_context():
        organization = db.session.get(Organization, data["user"]["org_id"])
        assert organization.name == "Nouvelle entreprise"
        assert User.query.filter_by(email="admin@example.org").count() == 1


def test_register_rejects_duplicate_email_and_short_password(client):
    duplicate = client.post("/v1/auth/register", json={
        "organization_name": "Other organization",
        "email": "ADMIN@example.com",
        "password": "a-secure-password-long-enough",
    })
    assert duplicate.status_code == 409

    short_password = client.post("/v1/auth/register", json={
        "organization_name": "Other organization",
        "email": "new@example.com",
        "password": "too-short",
    })
    assert short_password.status_code == 400


def test_login_rate_limit_blocks_repeated_password_attempts(client):
    client.application.extensions["redis_client"] = None
    responses = [client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "invalid"}) for _ in range(11)]
    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[-1].status_code == 429
    assert responses[-1].headers["Retry-After"] == "60"
