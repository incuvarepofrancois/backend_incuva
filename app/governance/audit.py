import re

from app import db
from app.models.governance import AuditEvent

_SENSITIVE_KEY = re.compile(r"secret|token|password|credential|authorization|api[_-]?key", re.IGNORECASE)


def redact_audit_details(value):
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact_audit_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_audit_details(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)[:500]


def record_audit_event(org_id, event_type, resource_type, resource_id=None, actor_user_id=None, outcome="success", details=None):
    event = AuditEvent(
        org_id=org_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        details=redact_audit_details(details or {}),
    )
    db.session.add(event)
    return event
