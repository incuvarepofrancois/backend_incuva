from pathlib import Path

from flask_migrate import check, stamp, upgrade
from sqlalchemy import inspect
from sqlalchemy import text

from app import create_app, db
from app.models.organization import Organization


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def test_initial_migration_builds_the_full_schema_from_empty_database():
    app = create_app("testing")

    with app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="head")
        upgrade(directory=str(MIGRATIONS_DIR), revision="head")
        check(directory=str(MIGRATIONS_DIR))
        tables = set(inspect(db.engine).get_table_names())
        meeting_columns = {column["name"] for column in inspect(db.engine).get_columns("meetings")}

    assert {
        "organizations",
        "users",
        "auth_sessions",
        "employees",
        "signals",
        "risk_scores",
        "meetings",
        "insights",
        "onboarding_plans",
        "onboarding_tasks",
        "integration_credentials",
        "audit_events",
        "organization_secrets",
        "governance_access_rules",
        "governance_budget_policies",
        "governance_budget_usage",
        "governance_controls",
        "governance_approval_requests",
        "governance_retention_policies",
        "alembic_version",
    }.issubset(tables)
    assert {"action_items", "reviewed_at", "reviewed_by"}.issubset(meeting_columns)


def test_stamping_a_legacy_schema_preserves_existing_rows():
    app = create_app("testing")

    with app.app_context():
        # Simulate the schema created by the former db.create_all() commands.
        db.create_all()
        # These tables belong to a migration added after the legacy schema existed.
        db.session.execute(text("DROP TABLE employee_external_ids"))
        db.session.execute(text("DROP TABLE integration_syncs"))
        db.session.commit()
        organization = Organization(name="Legacy organization")
        db.session.add(organization)
        db.session.commit()
        organization_id = organization.id

        stamp(directory=str(MIGRATIONS_DIR), revision="64275f79d812")
        upgrade(directory=str(MIGRATIONS_DIR), revision="head")

        stored = db.session.get(Organization, organization_id)
        assert stored is not None
        assert stored.name == "Legacy organization"
        assert "organizations" in inspect(db.engine).get_table_names()
