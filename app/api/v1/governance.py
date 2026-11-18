import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app import db
from app.governance.access import access_allowed, current_user_access
from app.governance.audit import record_audit_event
from app.governance.budgets import budget_snapshot
from app.governance.controls import CONTROL_KEYS
from app.models.governance import AccessRule, ApprovalRequest, AuditEvent, BudgetPolicy, GovernanceControl, OrganizationSecret, RetentionPolicy
from app.models.auth_session import AuthSession
from app.models.user import User
from app.models.employee import Employee
from app.models.meeting import Meeting
from app.models.signal import Signal
from app.models.risk_score import RiskScore
from app.models.insight import Insight
from app.models.onboarding import OnboardingPlan
from app.security.crypto import credential_cipher, decrypt_credential, encrypt_credential
from app.middleware.rate_limiter import rate_limit

governance_bp = Blueprint("governance", __name__)
SECRET_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
SLUG = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")
ROLES = {"admin", "owner", "analyst", "manager", "viewer"}
CONTROL_SETTINGS = {
    "action_allowlist": ("allowed_actions",),
    "export_row_limit": ("max_rows",),
    "require_approval": ("actions",),
}


def _admin_context():
    claims = get_jwt()
    org_id = claims.get("org_id")
    if org_id is None:
        return None, None, (jsonify({"error": "organization access required"}), 403)
    try:
        actor_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        actor_id = None
    actor = db.session.get(User, actor_id) if actor_id is not None else None
    role = actor.role if actor and actor.org_id == org_id else None
    if role not in {"admin", "owner"} or not access_allowed(org_id, role, "governance", "admin"):
        return None, None, (jsonify({"error": "organization administrator access required"}), 403)
    return org_id, actor_id, None


def _approval_dict(item):
    return {
        "id": item.id, "action_key": item.action_key, "resource_type": item.resource_type,
        "resource_id": item.resource_id, "requested_by": item.requested_by,
        "status": item.status, "reviewed_by": item.reviewed_by,
        "reviewer_note": item.reviewer_note,
        "created_at": item.created_at.isoformat() + "Z",
        "reviewed_at": item.reviewed_at.isoformat() + "Z" if item.reviewed_at else None,
    }


@governance_bp.get("/governance/approvals")
@jwt_required()
def list_approval_requests():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    status = request.args.get("status", "pending")
    if status not in {"pending", "approved", "rejected", "consumed", "all"}:
        return jsonify({"error": "Invalid approval status"}), 400
    query = ApprovalRequest.query.filter_by(org_id=org_id)
    if status != "all":
        query = query.filter_by(status=status)
    items = query.order_by(ApprovalRequest.created_at.desc()).limit(100).all()
    return jsonify({"approvals": [_approval_dict(item) for item in items]}), 200


@governance_bp.post("/governance/approvals")
@jwt_required()
@rate_limit(max_requests=20, window_seconds=60)
def request_approval():
    claims = get_jwt()
    org_id = claims.get("org_id")
    if org_id is None:
        return jsonify({"error": "organization access required"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    action_key, resource_type = payload.get("action_key"), payload.get("resource_type")
    resource_id = payload.get("resource_id")
    if not isinstance(action_key, str) or not SLUG.fullmatch(action_key) or not isinstance(resource_type, str) or not SLUG.fullmatch(resource_type):
        return jsonify({"error": "action_key and resource_type are invalid"}), 400
    if resource_id is not None and (not isinstance(resource_id, (str, int)) or isinstance(resource_id, bool) or len(str(resource_id)) > 120):
        return jsonify({"error": "resource_id must be text or an integer of at most 120 characters"}), 400
    try:
        actor_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "authenticated user required"}), 403
    actor, error = current_user_access(org_id, resource_type, "manage")
    if error:
        return error
    item = ApprovalRequest(
        org_id=org_id, action_key=action_key, resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        requested_by=actor.id, status="pending",
    )
    db.session.add(item)
    db.session.flush()
    _audit(org_id, actor_id, "approval.requested", "approval", item.id, details={"action_key": action_key, "resource_type": resource_type, "resource_id": item.resource_id})
    commit_error = _commit_or_error("Approval request could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"approval": _approval_dict(item)}), 201


@governance_bp.patch("/governance/approvals/<int:approval_id>")
@jwt_required()
@rate_limit(max_requests=30, window_seconds=60)
def review_approval_request(approval_id):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    decision = payload.get("decision") if isinstance(payload, dict) else None
    note = payload.get("note", "") if isinstance(payload, dict) else ""
    if not isinstance(decision, str) or decision not in {"approve", "reject"}:
        return jsonify({"error": "decision must be approve or reject"}), 400
    if not isinstance(note, str) or len(note) > 500:
        return jsonify({"error": "note must be at most 500 characters"}), 400
    item = ApprovalRequest.query.filter_by(org_id=org_id, id=approval_id).with_for_update().first()
    if item is None:
        return jsonify({"error": "Approval request not found"}), 404
    if item.status != "pending":
        return jsonify({"error": "Only pending requests can be reviewed"}), 409
    if item.requested_by == actor_id:
        return jsonify({"error": "A requester cannot approve their own action"}), 403
    item.status = "approved" if decision == "approve" else "rejected"
    item.reviewed_by = actor_id
    item.reviewer_note = note.strip() or None
    item.reviewed_at = datetime.utcnow()
    _audit(org_id, actor_id, f"approval.{item.status}", "approval", item.id, details={"action_key": item.action_key, "resource_type": item.resource_type, "resource_id": item.resource_id})
    commit_error = _commit_or_error("Approval decision could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"approval": _approval_dict(item)}), 200


@governance_bp.get("/governance/retention")
@jwt_required()
def get_retention_policy():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    policy = RetentionPolicy.query.filter_by(org_id=org_id).first()
    return jsonify({"retention": {
        "meetings_days": policy.meetings_days if policy else None,
        "signals_days": policy.signals_days if policy else None,
        "risk_scores_days": policy.risk_scores_days if policy else None,
        "insights_days": policy.insights_days if policy else None,
        "completed_onboarding_days": policy.completed_onboarding_days if policy else None,
        "updated_at": policy.updated_at.isoformat() + "Z" if policy else None,
    }}), 200


@governance_bp.put("/governance/retention")
@jwt_required()
@rate_limit(max_requests=10, window_seconds=3600)
def save_retention_policy():
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    fields = {"meetings_days", "signals_days", "risk_scores_days", "insights_days", "completed_onboarding_days"}
    if not isinstance(payload, dict) or set(payload) - fields or not fields.issubset(payload):
        return jsonify({"error": "Provide meetings_days, signals_days, risk_scores_days, insights_days, and completed_onboarding_days"}), 400
    for key, value in payload.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650):
            return jsonify({"error": f"{key} must be null or an integer between 1 and 3650"}), 400
    policy = RetentionPolicy.query.filter_by(org_id=org_id).first()
    if policy is None:
        policy = RetentionPolicy(org_id=org_id)
        db.session.add(policy)
    policy.meetings_days = payload["meetings_days"]
    policy.signals_days = payload["signals_days"]
    policy.risk_scores_days = payload["risk_scores_days"]
    policy.insights_days = payload["insights_days"]
    policy.completed_onboarding_days = payload["completed_onboarding_days"]
    policy.updated_by = actor_id
    _audit(org_id, actor_id, "retention.policy_updated", "retention_policy", details=payload)
    commit_error = _commit_or_error("Retention policy could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"retention": payload}), 200


@governance_bp.get("/governance/retention/preview")
@jwt_required()
def preview_retention_purge():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    policy = RetentionPolicy.query.filter_by(org_id=org_id).first()
    fields = {
        "meetings_days": "meetings",
        "signals_days": "signals",
        "risk_scores_days": "risk_scores",
        "insights_days": "insights",
        "completed_onboarding_days": "completed_onboarding",
    }
    result = {"counts": {}, "as_of": datetime.utcnow().isoformat() + "Z"}
    for field, name in fields.items():
        days = getattr(policy, field, None) if policy else None
        count = 0
        if days is not None:
            cutoff = datetime.utcnow() - timedelta(days=days)
            if name == "meetings":
                count = Meeting.query.filter(Meeting.org_id == org_id, Meeting.created_at < cutoff).count()
            elif name == "signals":
                count = Signal.query.join(Employee, Signal.employee_id == Employee.id).filter(Employee.org_id == org_id, Signal.captured_at < cutoff).count()
            elif name == "risk_scores":
                count = RiskScore.query.join(Employee, RiskScore.employee_id == Employee.id).filter(Employee.org_id == org_id, RiskScore.created_at < cutoff).count()
            elif name == "insights":
                count = Insight.query.filter(Insight.org_id == org_id, Insight.created_at < cutoff).count()
            else:
                count = OnboardingPlan.query.filter(OnboardingPlan.org_id == org_id, OnboardingPlan.status == "completed", OnboardingPlan.completed_at.isnot(None), OnboardingPlan.completed_at < cutoff).count()
        result["counts"][name] = {"days": days, "eligible": count}
    last_purge = AuditEvent.query.filter_by(org_id=org_id, event_type="retention.purge_completed").order_by(AuditEvent.id.desc()).first()
    result["last_purge"] = ({"created_at": last_purge.created_at.isoformat() + "Z", "details": last_purge.details} if last_purge else None)
    return jsonify(result), 200


@governance_bp.get("/governance/users")
@jwt_required()
def list_organization_users():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    users = User.query.filter_by(org_id=org_id).order_by(User.email.asc()).all()
    return jsonify({"users": [user.to_dict() for user in users]}), 200


@governance_bp.post("/governance/users")
@jwt_required()
@rate_limit(max_requests=10, window_seconds=3600)
def create_organization_user():
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    email, password, role = payload.get("email"), payload.get("password"), payload.get("role", "viewer")
    if not isinstance(email, str) or not email.strip() or len(email.strip()) > 255 or "@" not in email or any(char.isspace() for char in email.strip()):
        return jsonify({"error": "A valid email address is required"}), 400
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        return jsonify({"error": "Temporary password must contain between 12 and 1024 characters"}), 400
    if not isinstance(role, str) or role not in {"admin", "analyst", "manager", "viewer"}:
        return jsonify({"error": "role must be admin, analyst, manager, or viewer"}), 400
    normalized_email = email.strip().lower()
    if User.query.filter_by(email=normalized_email).first():
        return jsonify({"error": "An account already exists for this email"}), 409
    user = User(email=normalized_email, password_hash=generate_password_hash(password), org_id=org_id, role=role)
    db.session.add(user)
    try:
        db.session.flush()
        _audit(org_id, actor_id, "user.created", "user", user.id, details={"role": role})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "An account already exists for this email"}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Organization user could not be created")
        return jsonify({"error": "User could not be created"}), 500
    return jsonify({"user": user.to_dict()}), 201


@governance_bp.patch("/governance/users/<int:user_id>/role")
@jwt_required()
def update_organization_user_role(user_id):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    role = payload.get("role") if isinstance(payload, dict) else None
    if not isinstance(role, str) or role not in {"admin", "analyst", "manager", "viewer"}:
        return jsonify({"error": "role must be admin, analyst, manager, or viewer"}), 400
    user = User.query.filter_by(id=user_id, org_id=org_id).with_for_update().first()
    if user is None:
        return jsonify({"error": "User not found"}), 404
    if user.role == "owner":
        return jsonify({"error": "The organization owner role cannot be changed here"}), 403
    if user.id == actor_id and role != "admin":
        return jsonify({"error": "You cannot remove your own administrator access"}), 400
    if user.role == "admin" and role != "admin" and User.query.filter_by(org_id=org_id, role="admin").count() <= 1:
        return jsonify({"error": "The organization must keep at least one administrator"}), 409
    previous_role = user.role
    user.role = role
    AuthSession.query.filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)
    _audit(org_id, actor_id, "user.role_changed", "user", user.id, details={"from_role": previous_role, "to_role": role})
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Organization user role could not be updated")
        return jsonify({"error": "User role could not be updated"}), 500
    return jsonify({"user": user.to_dict(), "sessions_revoked": True}), 200


def _audit(org_id, actor_id, event_type, resource_type, resource_id=None, outcome="success", details=None):
    record_audit_event(org_id, event_type, resource_type, resource_id, actor_id, outcome, details)


def _commit_or_error(message):
    try:
        db.session.commit()
        return None
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Governance change could not be committed")
        return jsonify({"error": message}), 500


@governance_bp.get("/governance/audit-events")
@jwt_required()
def list_audit_events():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 100)
        before_id = int(request.args["before_id"]) if request.args.get("before_id") else None
    except ValueError:
        return jsonify({"error": "limit and before_id must be integers"}), 400
    query = AuditEvent.query.filter_by(org_id=org_id)
    event_type = request.args.get("event_type", "").strip()
    outcome = request.args.get("outcome", "").strip()
    actor_id = request.args.get("actor_user_id", "").strip()
    if event_type:
        query = query.filter(AuditEvent.event_type.ilike(f"%{event_type[:100]}%"))
    if outcome:
        if outcome not in {"success", "denied", "error"}:
            return jsonify({"error": "outcome must be success, denied, or error"}), 400
        query = query.filter_by(outcome=outcome)
    if actor_id:
        try:
            query = query.filter_by(actor_user_id=int(actor_id))
        except ValueError:
            return jsonify({"error": "actor_user_id must be an integer"}), 400
    if before_id:
        query = query.filter(AuditEvent.id < before_id)
    events = query.order_by(AuditEvent.id.desc()).limit(limit).all()
    return jsonify({"events": [{
        "id": event.id,
        "actor_user_id": event.actor_user_id,
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "details": event.details,
        "created_at": event.created_at.isoformat() + "Z",
    } for event in events], "next_before_id": events[-1].id if len(events) == limit else None}), 200


@governance_bp.get("/governance/secrets")
@jwt_required()
def list_secrets():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    secrets = OrganizationSecret.query.filter_by(org_id=org_id).order_by(OrganizationSecret.name.asc()).all()
    return jsonify({"secrets": [{
        "name": item.name, "description": item.description, "version": item.version,
        "created_at": item.created_at.isoformat() + "Z", "updated_at": item.updated_at.isoformat() + "Z",
    } for item in secrets]}), 200


@governance_bp.put("/governance/secrets/<secret_name>")
@jwt_required()
def write_secret(secret_name):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    if not SECRET_NAME.fullmatch(secret_name):
        return jsonify({"error": "Secret name must use letters, digits, dots, underscores, or hyphens"}), 400
    cipher = credential_cipher()
    if cipher is None:
        return jsonify({"error": "Secret encryption is not configured"}), 503
    payload = request.get_json(silent=True)
    value = payload.get("value") if isinstance(payload, dict) else None
    description = payload.get("description", "") if isinstance(payload, dict) else ""
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        return jsonify({"error": "value must contain between 1 and 8192 characters"}), 400
    if not isinstance(description, str) or len(description) > 255:
        return jsonify({"error": "description must be at most 255 characters"}), 400
    item = OrganizationSecret.query.filter_by(org_id=org_id, name=secret_name).first()
    is_new = item is None
    if is_new:
        item = OrganizationSecret(org_id=org_id, name=secret_name, created_by=actor_id, version=1)
        db.session.add(item)
    else:
        item.version += 1
    item.description = description.strip() or None
    item.encrypted_value = encrypt_credential(value.encode("utf-8"))
    item.updated_by = actor_id
    _audit(org_id, actor_id, "secret.created" if is_new else "secret.rotated", "secret", secret_name, details={"version": item.version})
    commit_error = _commit_or_error("Secret could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"name": item.name, "version": item.version, "created": is_new, "updated_at": item.updated_at.isoformat() + "Z"}), 200 if not is_new else 201


@governance_bp.delete("/governance/secrets/<secret_name>")
@jwt_required()
def delete_secret(secret_name):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    item = OrganizationSecret.query.filter_by(org_id=org_id, name=secret_name).first()
    if item is None:
        return jsonify({"error": "Secret not found"}), 404
    db.session.delete(item)
    _audit(org_id, actor_id, "secret.deleted", "secret", secret_name)
    commit_error = _commit_or_error("Secret could not be deleted")
    if commit_error:
        return commit_error
    return jsonify({"name": secret_name, "deleted": True}), 200


@governance_bp.get("/governance/access-rules")
@jwt_required()
def list_access_rules():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    rules = AccessRule.query.filter_by(org_id=org_id).order_by(AccessRule.role, AccessRule.resource, AccessRule.action).all()
    return jsonify({"rules": [{
        "id": rule.id, "role": rule.role, "resource": rule.resource, "action": rule.action,
        "effect": rule.effect, "enabled": rule.enabled, "updated_at": rule.updated_at.isoformat() + "Z",
    } for rule in rules]}), 200


@governance_bp.post("/governance/access-rules")
@jwt_required()
def save_access_rule():
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    role, resource, action, effect = (payload.get(key) for key in ("role", "resource", "action", "effect"))
    if not isinstance(role, str) or role not in ROLES or not isinstance(resource, str) or not SLUG.fullmatch(resource) or not isinstance(action, str) or not SLUG.fullmatch(action) or not isinstance(effect, str) or effect not in {"allow", "deny"}:
        return jsonify({"error": "role, resource, action, or effect is invalid"}), 400
    if role == "admin" and resource == "governance" and action == "admin" and effect == "deny":
        return jsonify({"error": "An administrator cannot remove their own governance access"}), 400
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    rule = AccessRule.query.filter_by(org_id=org_id, role=role, resource=resource, action=action).first()
    created = rule is None
    if created:
        rule = AccessRule(org_id=org_id, role=role, resource=resource, action=action, created_by=actor_id)
        db.session.add(rule)
    rule.effect = effect
    rule.enabled = enabled
    _audit(org_id, actor_id, "access_rule.created" if created else "access_rule.updated", "access_rule", f"{role}:{resource}:{action}", details={"effect": effect, "enabled": enabled})
    commit_error = _commit_or_error("Access rule could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"id": rule.id, "role": role, "resource": resource, "action": action, "effect": effect, "enabled": enabled}), 201 if created else 200


@governance_bp.delete("/governance/access-rules/<int:rule_id>")
@jwt_required()
def delete_access_rule(rule_id):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    rule = AccessRule.query.filter_by(org_id=org_id, id=rule_id).first()
    if rule is None:
        return jsonify({"error": "Access rule not found"}), 404
    resource_id = f"{rule.role}:{rule.resource}:{rule.action}"
    db.session.delete(rule)
    _audit(org_id, actor_id, "access_rule.deleted", "access_rule", resource_id)
    commit_error = _commit_or_error("Access rule could not be deleted")
    if commit_error:
        return commit_error
    return jsonify({"id": rule_id, "deleted": True}), 200


@governance_bp.get("/governance/budgets")
@jwt_required()
def list_budgets():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    policies = BudgetPolicy.query.filter_by(org_id=org_id).order_by(BudgetPolicy.name.asc()).all()
    return jsonify({"budgets": [budget_snapshot(policy) for policy in policies]}), 200


@governance_bp.post("/governance/budgets")
@jwt_required()
def create_budget():
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    name, meter, unit = payload.get("name"), payload.get("meter", "llm_cost"), payload.get("unit", "EUR")
    period, scope = payload.get("period", "monthly"), payload.get("scope", "organization")
    scope_key = payload.get("scope_key")
    try:
        limit_amount = Decimal(str(payload.get("limit")))
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({"error": "limit must be a positive decimal number"}), 400
    if not limit_amount.is_finite() or limit_amount <= 0 or limit_amount.adjusted() > 7:
        return jsonify({"error": "limit must be a positive value within the supported range"}), 400
    if limit_amount != limit_amount.quantize(Decimal("0.000001")):
        return jsonify({"error": "limit supports at most 6 decimal places"}), 400
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        return jsonify({"error": "name is required (maximum 100 characters)"}), 400
    if not isinstance(meter, str) or not SLUG.fullmatch(meter) or not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,19}", unit):
        return jsonify({"error": "meter or unit is invalid"}), 400
    if not isinstance(period, str) or period not in {"daily", "monthly"} or not isinstance(scope, str) or scope not in {"organization", "model", "user"}:
        return jsonify({"error": "period or scope is invalid"}), 400
    if scope == "organization":
        scope_key = None
    elif not isinstance(scope_key, str) or not scope_key.strip() or len(scope_key.strip()) > 120:
        return jsonify({"error": "scope_key is required for model and user budgets"}), 400
    hard_limit = payload.get("hard_limit", True)
    if not isinstance(hard_limit, bool):
        return jsonify({"error": "hard_limit must be a boolean"}), 400
    policy = BudgetPolicy(
        org_id=org_id, name=name.strip(), meter=meter, unit=unit.upper(), limit_amount=limit_amount,
        period=period, scope=scope, scope_key=scope_key, hard_limit=hard_limit, created_by=actor_id,
    )
    db.session.add(policy)
    db.session.flush()
    _audit(org_id, actor_id, "budget.created", "budget", policy.id, details={"name": policy.name, "meter": meter, "unit": unit.upper(), "limit": str(limit_amount), "period": period, "scope": scope})
    commit_error = _commit_or_error("Budget could not be saved")
    if commit_error:
        return commit_error
    return jsonify(budget_snapshot(policy)), 201


@governance_bp.patch("/governance/budgets/<int:policy_id>")
@jwt_required()
def update_budget(policy_id):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    policy = BudgetPolicy.query.filter_by(org_id=org_id, id=policy_id).first()
    if policy is None:
        return jsonify({"error": "Budget not found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    if "limit" in payload:
        try:
            value = Decimal(str(payload["limit"]))
        except (InvalidOperation, TypeError, ValueError):
            return jsonify({"error": "limit must be a positive decimal number"}), 400
        if not value.is_finite() or value <= 0 or value.adjusted() > 7:
            return jsonify({"error": "limit must be a positive value within the supported range"}), 400
        if value != value.quantize(Decimal("0.000001")):
            return jsonify({"error": "limit supports at most 6 decimal places"}), 400
        policy.limit_amount = value
    for key in ("enabled", "hard_limit"):
        if key in payload:
            if not isinstance(payload[key], bool):
                return jsonify({"error": f"{key} must be a boolean"}), 400
            setattr(policy, key, payload[key])
    _audit(org_id, actor_id, "budget.updated", "budget", policy.id, details={"enabled": policy.enabled, "hard_limit": policy.hard_limit, "limit": str(policy.limit_amount)})
    commit_error = _commit_or_error("Budget could not be updated")
    if commit_error:
        return commit_error
    return jsonify(budget_snapshot(policy)), 200


@governance_bp.get("/governance/controls")
@jwt_required()
def list_controls():
    org_id, _actor_id, error = _admin_context()
    if error:
        return error
    controls = GovernanceControl.query.filter_by(org_id=org_id).order_by(GovernanceControl.control_key.asc()).all()
    return jsonify({"supported_controls": sorted(CONTROL_KEYS), "controls": [{
        "key": control.control_key, "enabled": control.enabled, "mode": control.mode,
        "settings": control.settings, "updated_at": control.updated_at.isoformat() + "Z",
    } for control in controls]}), 200


@governance_bp.put("/governance/controls/<control_key>")
@jwt_required()
def save_control(control_key):
    org_id, actor_id, error = _admin_context()
    if error:
        return error
    if control_key not in CONTROL_SETTINGS:
        return jsonify({"error": "Unsupported control key", "supported_controls": sorted(CONTROL_KEYS)}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    settings = payload.get("settings", {})
    if not isinstance(settings, dict) or set(settings) != set(CONTROL_SETTINGS[control_key]):
        return jsonify({"error": "settings must contain the fields documented for this control", "required": CONTROL_SETTINGS[control_key]}), 400
    list_key = CONTROL_SETTINGS[control_key][0]
    if control_key in {"action_allowlist", "require_approval"}:
        values = settings.get(list_key)
        if not isinstance(values, list) or len(values) > 100 or any(not isinstance(item, str) or not SLUG.fullmatch(item) for item in values):
            return jsonify({"error": f"{list_key} must be a list of at most 100 action names"}), 400
        settings = {list_key: sorted(set(values))}
    else:
        max_rows = settings.get("max_rows")
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 1000000:
            return jsonify({"error": "max_rows must be between 1 and 1000000"}), 400
    enabled, mode = payload.get("enabled", True), payload.get("mode", "block")
    if not isinstance(enabled, bool) or not isinstance(mode, str) or mode not in {"monitor", "block"}:
        return jsonify({"error": "enabled or mode is invalid"}), 400
    control = GovernanceControl.query.filter_by(org_id=org_id, control_key=control_key).first()
    created = control is None
    if created:
        control = GovernanceControl(org_id=org_id, control_key=control_key)
        db.session.add(control)
    control.enabled, control.mode, control.settings, control.updated_by = enabled, mode, settings, actor_id
    _audit(org_id, actor_id, "control.created" if created else "control.updated", "control", control_key, details={"enabled": enabled, "mode": mode, "settings": settings})
    commit_error = _commit_or_error("Control could not be saved")
    if commit_error:
        return commit_error
    return jsonify({"key": control_key, "enabled": enabled, "mode": mode, "settings": settings}), 201 if created else 200
