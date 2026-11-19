from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from flask_jwt_extended import get_jwt, get_jwt_identity

from app import db
from app.agentic.errors import AgenticContextError
from app.models.user import User


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable server-derived identity; permissions are rechecked on use."""

    user_id: int
    org_id: int
    permissions: tuple[str, ...]
    correlation_id: str
    conversation_id: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise AgenticContextError("A valid authenticated user is required")
        if isinstance(self.org_id, bool) or not isinstance(self.org_id, int) or self.org_id <= 0:
            raise AgenticContextError("A trusted organization is required")
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise AgenticContextError("A correlation ID is required")
        if not isinstance(self.permissions, tuple) or any(not isinstance(item, str) for item in self.permissions):
            raise AgenticContextError("Permissions must be server-derived")
        if self.conversation_id is not None and (not isinstance(self.conversation_id, str) or len(self.conversation_id) > 128):
            raise AgenticContextError("Invalid conversation ID")


def build_execution_context() -> ExecutionContext:
    """Build context from the verified JWT and current database user, never request arguments."""
    try:
        user_id = int(get_jwt_identity())
        claims = get_jwt()
        org_id = claims.get("org_id")
    except (RuntimeError, TypeError, ValueError) as exc:
        raise AgenticContextError("An authenticated user and organization are required") from exc

    if isinstance(org_id, bool) or not isinstance(org_id, int) or org_id <= 0:
        raise AgenticContextError("A trusted organization is required")
    user = db.session.get(User, user_id)
    if user is None or user.org_id != org_id:
        raise AgenticContextError("Authenticated user does not belong to the token organization")

    try:
        from flask import g
        request_id = getattr(g, "request_id", None)
    except RuntimeError:
        request_id = None
    request_id = request_id or str(uuid4())

    # Snapshot for metadata only. authorize_capability reloads the current user and
    # permissions from the governance system each time; this tuple is not authority.
    from app.governance.access import ROLE_DEFAULTS, access_allowed
    permissions = set(ROLE_DEFAULTS.get(user.role, set()))
    from app.models.governance import AccessRule
    rules = AccessRule.query.filter_by(org_id=org_id, role=user.role, enabled=True).all()
    permissions.update((rule.resource, rule.action) for rule in rules if rule.effect == "allow")
    for rule in rules:
        if rule.effect == "deny":
            permissions.discard((rule.resource, rule.action))
    known_permissions = {
        ("employees", "view"), ("employees", "manage"),
        ("risk", "view"), ("risk", "manage"),
        ("meetings", "view"), ("meetings", "manage"),
        ("onboarding", "view"), ("onboarding", "manage"),
        ("integrations", "view"), ("integrations", "manage"),
    }
    candidates = permissions | known_permissions | {(rule.resource, rule.action) for rule in rules}
    permissions = {
        (resource, action)
        for resource, action in candidates
        if access_allowed(org_id, user.role, resource, action)
    }

    return ExecutionContext(
        user_id=user_id,
        org_id=org_id,
        permissions=tuple(sorted(f"{resource}:{action}" for resource, action in permissions)),
        correlation_id=request_id,
        conversation_id=None,
    )
