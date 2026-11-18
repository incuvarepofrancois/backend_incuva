from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import func

from app import db
from app.governance.audit import record_audit_event, redact_audit_details
from app.models.governance import BudgetPolicy, BudgetUsage


def _period_start(period, now):
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _usage_for(policy, now):
    start = _period_start(policy.period, now)
    total = db.session.query(func.coalesce(func.sum(BudgetUsage.amount), 0)).filter(
        BudgetUsage.policy_id == policy.id,
        BudgetUsage.occurred_at >= start,
    ).scalar()
    return Decimal(str(total))


def budget_snapshot(policy, now=None):
    now = now or datetime.utcnow()
    spent = _usage_for(policy, now)
    limit_amount = Decimal(policy.limit_amount)
    return {
        "id": policy.id,
        "name": policy.name,
        "meter": policy.meter,
        "unit": policy.unit,
        "limit": str(limit_amount),
        "spent": str(spent),
        "remaining": str(max(Decimal("0"), limit_amount - spent)),
        "period": policy.period,
        "scope": policy.scope,
        "scope_key": policy.scope_key,
        "hard_limit": policy.hard_limit,
        "enabled": policy.enabled,
        "period_started_at": _period_start(policy.period, now).isoformat() + "Z",
    }


def consume_budget(org_id, amount, unit, meter, *, model_key=None, user_id=None, source="application", idempotency_key=None, metadata=None):
    """Reserve estimated usage against matching budgets before a billable operation.

    The caller supplies a forecast amount. This foundation does not yet reconcile
    the reservation with provider-reported actual usage or refund unused capacity.
    """
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("amount must be a positive decimal number")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be a positive decimal number")
    if amount.adjusted() > 7 or amount != amount.quantize(Decimal("0.000001")):
        raise ValueError("amount must fit the budget precision (up to 8 integer and 6 decimal digits)")
    if not isinstance(unit, str) or not unit.strip() or not isinstance(meter, str) or not meter.strip():
        raise ValueError("unit and meter are required")
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 120):
        raise ValueError("idempotency_key must be at most 120 characters")

    unit = unit.strip().upper()
    meter = meter.strip()
    policies = BudgetPolicy.query.filter_by(org_id=org_id, enabled=True, meter=meter, unit=unit).with_for_update().all()
    applicable = [policy for policy in policies if (
        policy.scope == "organization"
        or (policy.scope == "model" and model_key is not None and policy.scope_key == str(model_key))
        or (policy.scope == "user" and user_id is not None and policy.scope_key == str(user_id))
    )]
    if not applicable:
        return {"allowed": True, "policy_ids": [], "amount": str(amount), "unit": unit}
    now = datetime.utcnow()
    existing = []
    if idempotency_key:
        for policy in applicable:
            previous = BudgetUsage.query.filter_by(policy_id=policy.id, idempotency_key=idempotency_key).first()
            if previous:
                existing.append(previous)
        if existing:
            db.session.rollback()
            return {"allowed": True, "idempotent_replay": True, "policy_ids": [usage.policy_id for usage in existing]}

    exceeded = []
    for policy in applicable:
        spent = _usage_for(policy, now)
        if policy.hard_limit and spent + amount > Decimal(policy.limit_amount):
            exceeded.append(policy)
    if exceeded:
        record_audit_event(
            org_id, "budget.blocked", "budget", actor_user_id=user_id, outcome="denied",
            details={"meter": meter, "unit": unit, "amount": str(amount), "policy_ids": [policy.id for policy in exceeded]},
        )
        db.session.commit()
        return {"allowed": False, "reason": "budget_limit_exceeded", "policy_ids": [policy.id for policy in exceeded]}

    for policy in applicable:
        db.session.add(BudgetUsage(
            policy_id=policy.id,
            org_id=org_id,
            amount=amount,
            occurred_at=now,
            source=str(source)[:80],
            idempotency_key=idempotency_key,
            metadata_json=redact_audit_details(metadata or {}),
        ))
    record_audit_event(
        org_id, "budget.usage_recorded", "budget", actor_user_id=user_id, outcome="success",
        details={"meter": meter, "unit": unit, "amount": str(amount), "policy_ids": [policy.id for policy in applicable]},
    )
    db.session.commit()
    return {"allowed": True, "policy_ids": [policy.id for policy in applicable], "amount": str(amount), "unit": unit}
