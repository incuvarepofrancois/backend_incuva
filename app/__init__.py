from flask import Flask, abort, request
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from celery import Celery
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import config_map

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
celery = Celery(__name__)
redis_client = None


def create_app(env="development"):
    app = Flask(__name__)
    app.config.from_object(config_map.get(env, config_map["development"]))
    app.config["ENVIRONMENT"] = env
    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if env != "testing" and not database_uri:
        raise RuntimeError("DATABASE_URL must point to the local PostgreSQL database.")
    if env != "testing" and not database_uri.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise RuntimeError("INCUVA requires PostgreSQL outside the test environment.")
    jwt_secret = app.config.get("JWT_SECRET_KEY", "")
    placeholder_markers = ("replace-with", "change-me", "placeholder")
    if env != "testing" and (
        len(jwt_secret) < 32
        or any(marker in jwt_secret.lower() for marker in placeholder_markers)
    ):
        raise RuntimeError("Set JWT_SECRET_KEY to a random value of at least 32 characters.")

    if env == "production":
        from sqlalchemy.engine import make_url
        from cryptography.fernet import Fernet
        database_sslmode = make_url(database_uri).query.get("sslmode")
        if database_sslmode not in {"require", "verify-ca", "verify-full"}:
            raise RuntimeError("Production DATABASE_URL must set sslmode=require, verify-ca, or verify-full.")
        redis_url = app.config.get("REDIS_URL", "")
        celery_url = app.config.get("CELERY_BROKER_URL", "")
        if not redis_url.startswith("rediss://") or not celery_url.startswith("rediss://"):
            raise RuntimeError("Production Redis and Celery broker URLs must use rediss:// TLS connections.")
        encryption_key = app.config.get("INTEGRATION_CREDENTIALS_KEY", "")
        try:
            Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError):
            raise RuntimeError("Production requires a valid INTEGRATION_CREDENTIALS_KEY Fernet key.") from None
        fallback_keys = app.config.get("INTEGRATION_CREDENTIALS_FALLBACK_KEYS", "")
        try:
            for fallback_key in (key.strip() for key in fallback_keys.split(",") if key.strip()):
                Fernet(fallback_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError):
            raise RuntimeError("INTEGRATION_CREDENTIALS_FALLBACK_KEYS contains an invalid Fernet key.") from None
        origins = app.config.get("CORS_ALLOWED_ORIGINS", [])
        if not origins or "*" in origins:
            raise RuntimeError("Production CORS_ALLOWED_ORIGINS must list explicit HTTPS origins.")
        if any(not origin.startswith("https://") for origin in origins):
            raise RuntimeError("Production CORS_ALLOWED_ORIGINS must use HTTPS.")
        proxy_count = max(1, int(app.config.get("TRUSTED_PROXY_COUNT", 1)))
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count, x_proto=proxy_count, x_host=proxy_count)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True, render_as_batch=True)
    jwt.init_app(app)
    CORS(app, origins=app.config.get("CORS_ALLOWED_ORIGINS", []))
    app.extensions["rate_limit_memory"] = {}
    app.extensions["webhook_replay_memory"] = {}

    celery.conf.update(broker_url=app.config.get("CELERY_BROKER_URL", ""))
    # Register background integration jobs in both Flask and Celery worker processes.
    from app.services import bamboohr_tasks, frappehr_tasks  # noqa: F401

    global redis_client
    try:
        import redis as redis_lib
        redis_client = redis_lib.from_url(app.config["REDIS_URL"])
    except Exception:
        pass
    app.extensions["redis_client"] = redis_client

    @app.before_request
    def require_https_in_production():
        if env == "production" and not request.is_secure:
            abort(400, description="HTTPS is required")

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    from app.api.v1.auth import auth_bp
    from app.api.v1.employees import employees_bp
    from app.api.v1.risk import risk_bp
    from app.api.v1.meetings import meetings_bp
    from app.api.v1.insights import insights_bp
    from app.api.v1.webhooks import webhooks_bp
    from app.api.v1.onboarding import onboarding_bp
    from app.api.v1.integrations import integrations_bp
    from app.api.v1.governance import governance_bp
    from app.api.v1.agentic_proposals import agentic_proposals_bp
    from app.models.user import User
    from app.models.organization import Organization
    from werkzeug.security import generate_password_hash
    import click
    import csv
    import math
    from datetime import datetime, timezone
    from pathlib import Path
    from app.models.employee import Employee
    from app.models.signal import Signal
    from app.modules.risk_detection.indicators import INDICATORS

    app.register_blueprint(auth_bp, url_prefix="/v1/auth")
    app.register_blueprint(employees_bp, url_prefix="/v1")
    app.register_blueprint(risk_bp, url_prefix="/v1")
    app.register_blueprint(meetings_bp, url_prefix="/v1")
    app.register_blueprint(insights_bp, url_prefix="/v1")
    app.register_blueprint(webhooks_bp, url_prefix="/v1/webhooks")
    app.register_blueprint(onboarding_bp, url_prefix="/v1")
    app.register_blueprint(integrations_bp, url_prefix="/v1")
    app.register_blueprint(governance_bp, url_prefix="/v1")
    app.register_blueprint(agentic_proposals_bp, url_prefix="/v1")

    @app.cli.command("create-user")
    @click.option("--email", prompt=True)
    @click.option("--org-id", type=int, prompt="Organization ID")
    @click.password_option()
    def create_user_command(email, org_id, password):
        """Provision a dashboard user for an existing organization ID."""
        normalized_email = email.strip().lower()
        if User.query.filter_by(email=normalized_email).first():
            raise click.ClickException("A user with this email already exists.")
        if org_id <= 0:
            raise click.ClickException("Organization ID must be a positive integer.")
        if not Organization.query.get(org_id):
            raise click.ClickException("Organization not found. Run 'flask create-org' first.")
        if len(password) < 12:
            raise click.ClickException("Password must contain at least 12 characters.")
        db.session.add(User(
            email=normalized_email,
            password_hash=generate_password_hash(password),
            org_id=org_id,
        ))
        db.session.commit()
        click.echo(f"Created {normalized_email} in organization {org_id}.")

    @app.cli.command("create-org")
    @click.option("--name", prompt=True)
    def create_org_command(name):
        """Create an organization and print its ID for user provisioning."""
        normalized_name = name.strip()
        if not normalized_name:
            raise click.ClickException("Organization name is required.")
        organization = Organization(name=normalized_name)
        db.session.add(organization)
        db.session.commit()
        click.echo(f"Created {normalized_name} with ID {organization.id}.")

    @app.cli.command("rotate-credential-encryption-key")
    def rotate_credential_encryption_key_command():
        """Re-encrypt integration credentials and organization secrets with the primary key."""
        from app.models.integration_credential import IntegrationCredential
        from app.models.governance import OrganizationSecret
        from app.security.crypto import credential_cipher, decrypt_credential, encrypt_credential
        if credential_cipher() is None:
            raise click.ClickException("INTEGRATION_CREDENTIALS_KEY must contain the new primary Fernet key.")
        integration_count = 0
        secret_count = 0
        try:
            for row in IntegrationCredential.query.yield_per(100):
                row.encrypted_credentials = encrypt_credential(decrypt_credential(row.encrypted_credentials))
                integration_count += 1
            for row in OrganizationSecret.query.yield_per(100):
                row.encrypted_value = encrypt_credential(decrypt_credential(row.encrypted_value))
                secret_count += 1
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            raise click.ClickException(f"Key rotation failed; no changes were committed: {error}") from error
        click.echo(f"Re-encrypted {integration_count} integration credential(s) and {secret_count} organization secret(s).")

    @app.cli.command("purge-expired-data")
    @click.option("--org-id", type=click.IntRange(min=1), default=None, help="Limit the purge to one organization.")
    @click.option("--commit", "commit_changes", is_flag=True, help="Delete expired rows. Without this flag, only preview counts.")
    def purge_expired_data_command(org_id, commit_changes):
        """Preview or execute deletions configured by organization retention policies."""
        from datetime import datetime, timedelta
        from app.models.governance import RetentionPolicy
        from app.models.meeting import Meeting
        from app.models.signal import Signal
        from app.models.employee import Employee
        from app.models.onboarding import OnboardingPlan
        from app.models.risk_score import RiskScore
        from app.models.insight import Insight
        from app.governance.audit import record_audit_event

        policies = RetentionPolicy.query.order_by(RetentionPolicy.org_id.asc())
        if org_id is not None:
            policies = policies.filter_by(org_id=org_id)
        summaries = []
        try:
            for policy in policies.all():
                cutoff_values = {
                    "meetings": datetime.utcnow() - timedelta(days=policy.meetings_days) if policy.meetings_days else None,
                    "signals": datetime.utcnow() - timedelta(days=policy.signals_days) if policy.signals_days else None,
                    "risk_scores": datetime.utcnow() - timedelta(days=policy.risk_scores_days) if policy.risk_scores_days else None,
                    "insights": datetime.utcnow() - timedelta(days=policy.insights_days) if policy.insights_days else None,
                    "onboarding": datetime.utcnow() - timedelta(days=policy.completed_onboarding_days) if policy.completed_onboarding_days else None,
                }
                old_meetings = Meeting.query.filter(Meeting.org_id == policy.org_id, Meeting.created_at < cutoff_values["meetings"]).all() if cutoff_values["meetings"] else []
                signal_query = Signal.query.join(Employee, Signal.employee_id == Employee.id).filter(Employee.org_id == policy.org_id, Signal.captured_at < cutoff_values["signals"]) if cutoff_values["signals"] else None
                old_signals = signal_query.count() if signal_query is not None else 0
                score_query = RiskScore.query.join(Employee, RiskScore.employee_id == Employee.id).filter(Employee.org_id == policy.org_id, RiskScore.created_at < cutoff_values["risk_scores"]) if cutoff_values["risk_scores"] else None
                old_scores = score_query.count() if score_query is not None else 0
                insight_query = Insight.query.filter(Insight.org_id == policy.org_id, Insight.created_at < cutoff_values["insights"]) if cutoff_values["insights"] else None
                old_insights = insight_query.count() if insight_query is not None else 0
                old_plans = OnboardingPlan.query.filter(
                    OnboardingPlan.org_id == policy.org_id,
                    OnboardingPlan.status == "completed",
                    OnboardingPlan.completed_at < cutoff_values["onboarding"],
                ).all() if cutoff_values["onboarding"] else []
                summary = {"org_id": policy.org_id, "meetings": len(old_meetings), "signals": old_signals, "risk_scores": old_scores, "insights": old_insights, "completed_onboarding_plans": len(old_plans)}
                summaries.append(summary)
                if commit_changes:
                    for meeting in old_meetings:
                        db.session.delete(meeting)
                    if signal_query is not None:
                        signal_query.delete(synchronize_session=False)
                    if score_query is not None:
                        score_query.delete(synchronize_session=False)
                    if insight_query is not None:
                        insight_query.delete(synchronize_session=False)
                    for plan in old_plans:
                        db.session.delete(plan)
                    record_audit_event(
                        policy.org_id, "retention.purge_completed", "retention_policy", policy.id,
                        details={"deleted": summary, "policy_days": {
                            "meetings": policy.meetings_days,
                            "signals": policy.signals_days,
                            "risk_scores": policy.risk_scores_days,
                            "insights": policy.insights_days,
                            "completed_onboarding": policy.completed_onboarding_days,
                        }},
                    )
            if commit_changes:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception as error:
            db.session.rollback()
            raise click.ClickException(f"Retention purge failed; transaction rolled back: {error}") from error

        for summary in summaries:
            click.echo(f"Organization {summary['org_id']}: {summary['meetings']} meetings, {summary['signals']} signals, {summary['risk_scores']} risk scores, {summary['insights']} insights, {summary['completed_onboarding_plans']} completed onboarding plans.")
        if not summaries:
            click.echo("No retention policies configured for the selected organization(s).")
        click.echo("Deletions committed." if commit_changes else "Preview only. Add --commit to delete these records.")

    @app.cli.command("import-risk-signals")
    @click.argument("csv_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--org-id", type=click.IntRange(min=1), required=True, help="INCUVA organization ID.")
    @click.option("--commit", "commit_changes", is_flag=True, help="Write validated rows to PostgreSQL. Without this flag, the command only previews the import.")
    def import_risk_signals_command(csv_path, org_id, commit_changes):
        """Preview or import normalized employee signals from a CSV file."""
        indicator_weights = {item["key"]: item["weight"] for item in INDICATORS}
        required_columns = {"employee_id", "indicator", "value", "captured_at"}
        prepared = []
        errors = []
        duplicates = 0
        seen = set()

        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as source_file:
                sample = source_file.read(4096)
                source_file.seek(0)
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter if sample.strip() else ","
                reader = csv.DictReader(source_file, delimiter=delimiter)
                headers = {header.strip() for header in (reader.fieldnames or []) if header}
                missing = required_columns - headers
                if missing:
                    raise click.ClickException("Missing CSV columns: " + ", ".join(sorted(missing)))

                for line_number, row in enumerate(reader, start=2):
                    if line_number > 10002:
                        errors.append("CSV exceeds the 10,000 row limit")
                        break
                    try:
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("row has a different number of columns than the header")
                        row = {(key or "").strip(): (value or "").strip() for key, value in row.items()}
                        employee_id = int(row["employee_id"])
                        if employee_id <= 0:
                            raise ValueError("employee_id must be positive")
                        employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first()
                        if employee is None:
                            raise ValueError("employee does not belong to the selected organization")

                        indicator = row["indicator"]
                        if indicator not in indicator_weights:
                            raise ValueError("unknown indicator: " + indicator)
                        value = float(row["value"])
                        if not math.isfinite(value) or not 0 <= value <= 100:
                            raise ValueError("value must be a number between 0 and 100")

                        captured_at = datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
                        if captured_at.tzinfo is None:
                            raise ValueError("captured_at must include a timezone, for example Z")
                        captured_at = captured_at.astimezone(timezone.utc).replace(tzinfo=None)
                        identity = (employee_id, indicator, value, captured_at)
                        if identity in seen or Signal.query.filter_by(
                            employee_id=employee_id,
                            source="csv_import",
                            type=indicator,
                            value=value,
                            captured_at=captured_at,
                        ).first():
                            duplicates += 1
                            continue
                        seen.add(identity)
                        prepared.append(Signal(
                            employee_id=employee_id,
                            source="csv_import",
                            type=indicator,
                            value=value,
                            weight=indicator_weights[indicator],
                            captured_at=captured_at,
                        ))
                    except (KeyError, TypeError, ValueError) as error:
                        errors.append(f"Line {line_number}: {error}")

        except (OSError, csv.Error) as error:
            raise click.ClickException(f"Could not read CSV: {error}") from error

        if errors:
            db.session.rollback()
            for error in errors[:20]:
                click.echo("ERROR: " + error, err=True)
            if len(errors) > 20:
                click.echo(f"... and {len(errors) - 20} more error(s)", err=True)
            raise click.ClickException("No rows were imported because the file contains errors.")

        click.echo(f"Validated rows: {len(prepared)}; duplicates skipped: {duplicates}.")
        if not commit_changes:
            click.echo("Preview only. Add --commit to write these rows to PostgreSQL.")
            return

        try:
            db.session.add_all(prepared)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            raise click.ClickException(f"Import failed; transaction rolled back: {error}") from error
        click.echo(f"Imported {len(prepared)} risk signal(s) for organization {org_id}.")

    return app
