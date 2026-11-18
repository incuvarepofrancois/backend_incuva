from app.models.governance import AccessRule

ROLE_DEFAULTS = {
    "viewer": {("employees", "view"), ("risk", "view"), ("meetings", "view"), ("onboarding", "view"), ("integrations", "view")},
    "analyst": {("employees", "view"), ("risk", "view"), ("meetings", "view"), ("meetings", "manage"), ("onboarding", "view"), ("integrations", "view")},
    "manager": {("employees", "view"), ("employees", "manage"), ("risk", "view"), ("meetings", "view"), ("onboarding", "view"), ("onboarding", "manage"), ("integrations", "view")},
}


def access_allowed(org_id, role, resource, action):
    """Evaluate exact role/resource/action rules. Explicit deny wins; admins are the fallback."""
    if role == "owner":
        return True
    rules = AccessRule.query.filter_by(
        org_id=org_id,
        role=role,
        resource=resource,
        action=action,
        enabled=True,
    ).all()
    if any(rule.effect == "deny" for rule in rules):
        return False
    if any(rule.effect == "allow" for rule in rules):
        return True
    if role in {"admin", "owner"}:
        return True
    return (resource, action) in ROLE_DEFAULTS.get(role, set())


def current_user_access(org_id, resource, action):
    """Return the authenticated organization user only when its current role permits an action."""
    from flask import jsonify
    from flask_jwt_extended import get_jwt_identity

    from app import db
    from app.models.user import User

    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None, (jsonify({"error": "authenticated user required"}), 403)
    user = db.session.get(User, user_id)
    if user is None or user.org_id != org_id:
        return None, (jsonify({"error": "organization access required"}), 403)
    if not access_allowed(org_id, user.role, resource, action):
        return None, (jsonify({"error": "permission denied", "resource": resource, "action": action}), 403)
    return user, None
