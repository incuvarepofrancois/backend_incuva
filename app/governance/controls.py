from app import db
from app.governance.audit import record_audit_event
from app.models.governance import ApprovalRequest, GovernanceControl

CONTROL_KEYS = {"action_allowlist", "export_row_limit", "require_approval"}


def evaluate_control(org_id, control_key, context, *, actor_user_id=None):
    """Evaluate one configured guardrail and write the result to the audit trail."""
    policy = GovernanceControl.query.filter_by(org_id=org_id, control_key=control_key, enabled=True).first()
    if policy is None:
        return {"allowed": True, "matched": False, "control_key": control_key}

    context = context if isinstance(context, dict) else {}
    settings = policy.settings or {}
    violation = False
    reason = None
    audit_context = {}
    if control_key == "action_allowlist":
        action = context.get("action")
        audit_context = {"action": action}
        allowed_actions = settings.get("allowed_actions", [])
        violation = not isinstance(action, str) or action not in allowed_actions
        reason = "action_not_allowed" if violation else None
    elif control_key == "export_row_limit":
        row_count = context.get("row_count")
        audit_context = {"row_count": row_count}
        max_rows = settings.get("max_rows")
        violation = isinstance(row_count, bool) or not isinstance(row_count, int) or row_count > max_rows
        reason = "export_row_limit_exceeded" if violation else None
    elif control_key == "require_approval":
        action = context.get("action")
        approval_id = context.get("approval_id")
        requires_approval = action in settings.get("actions", [])
        approved = False
        if requires_approval and isinstance(approval_id, int) and not isinstance(approval_id, bool):
            query = ApprovalRequest.query.filter_by(
                id=approval_id,
                org_id=org_id,
                action_key=action,
                status="approved",
            )
            resource_id = context.get("resource_id")
            if resource_id is not None:
                query = query.filter_by(resource_id=str(resource_id))
            if actor_user_id is not None:
                query = query.filter_by(requested_by=actor_user_id)
            approved = query.first() is not None
        audit_context = {"action": action, "approval_id": approval_id, "approved": approved}
        violation = requires_approval and not approved
        reason = "approval_required" if violation else None
    else:
        return {"allowed": True, "matched": False, "control_key": control_key}

    blocked = violation and policy.mode == "block"
    result = {"allowed": not blocked, "matched": True, "violation": violation, "reason": reason, "mode": policy.mode}
    if control_key == "require_approval":
        result["requires_approval"] = requires_approval
    record_audit_event(
        org_id, "control.evaluated", "control", resource_id=control_key,
        actor_user_id=actor_user_id, outcome="denied" if blocked else "success",
        details={"control_key": control_key, "result": result, "context": audit_context},
    )
    db.session.commit()
    return result


def consume_approval(org_id, approval_id, action, *, actor_user_id, resource_id=None):
    """Atomically consume a matching, human-approved request before an action executes."""
    if isinstance(approval_id, bool) or not isinstance(approval_id, int):
        return False
    query = ApprovalRequest.query.filter_by(
        id=approval_id,
        org_id=org_id,
        action_key=action,
        requested_by=actor_user_id,
        status="approved",
    ).with_for_update()
    if resource_id is not None:
        query = query.filter_by(resource_id=str(resource_id))
    approval = query.first()
    if approval is None or approval.reviewed_by == actor_user_id:
        db.session.rollback()
        return False
    approval.status = "consumed"
    record_audit_event(
        org_id, "approval.consumed", "approval", approval.id,
        actor_user_id=actor_user_id, details={"action_key": action, "resource_type": approval.resource_type, "resource_id": approval.resource_id},
    )
    db.session.commit()
    return True
