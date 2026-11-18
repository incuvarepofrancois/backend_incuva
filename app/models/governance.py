from datetime import datetime

from sqlalchemy import event

from app import db


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    __table_args__ = (
        db.Index("ix_audit_events_org_created", "org_id", "created_at", "id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(80), nullable=False)
    resource_id = db.Column(db.String(120), nullable=True)
    outcome = db.Column(db.String(20), nullable=False, default="success")
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


@event.listens_for(AuditEvent, "before_update")
def _prevent_audit_update(_mapper, _connection, _target):
    raise ValueError("Audit events are append-only")


@event.listens_for(AuditEvent, "before_delete")
def _prevent_audit_delete(_mapper, _connection, _target):
    raise ValueError("Audit events are append-only")


class OrganizationSecret(db.Model):
    __tablename__ = "organization_secrets"
    __table_args__ = (
        db.UniqueConstraint("org_id", "name", name="uq_organization_secrets_org_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    encrypted_value = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AccessRule(db.Model):
    __tablename__ = "governance_access_rules"
    __table_args__ = (
        db.UniqueConstraint("org_id", "role", "resource", "action", name="uq_access_rule_org_role_resource_action"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False)
    resource = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    effect = db.Column(db.String(10), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BudgetPolicy(db.Model):
    __tablename__ = "governance_budget_policies"
    __table_args__ = (
        db.UniqueConstraint("org_id", "name", name="uq_budget_policy_org_name"),
        db.CheckConstraint("period IN ('daily', 'monthly')", name="ck_budget_policy_period"),
        db.CheckConstraint("scope IN ('organization', 'model', 'user')", name="ck_budget_policy_scope"),
        db.CheckConstraint("limit_amount > 0", name="ck_budget_policy_positive_limit"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    meter = db.Column(db.String(80), nullable=False, default="llm_cost")
    unit = db.Column(db.String(20), nullable=False, default="EUR")
    limit_amount = db.Column(db.Numeric(14, 6), nullable=False)
    period = db.Column(db.String(12), nullable=False, default="monthly")
    scope = db.Column(db.String(20), nullable=False, default="organization")
    scope_key = db.Column(db.String(120), nullable=True)
    hard_limit = db.Column(db.Boolean, nullable=False, default=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BudgetUsage(db.Model):
    __tablename__ = "governance_budget_usage"
    __table_args__ = (
        db.UniqueConstraint("policy_id", "idempotency_key", name="uq_budget_usage_policy_idempotency"),
        db.Index("ix_budget_usage_policy_occurred", "policy_id", "occurred_at"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("governance_budget_policies.id", ondelete="CASCADE"), nullable=False)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 6), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    source = db.Column(db.String(80), nullable=False, default="application")
    idempotency_key = db.Column(db.String(120), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)


class GovernanceControl(db.Model):
    __tablename__ = "governance_controls"
    __table_args__ = (
        db.UniqueConstraint("org_id", "control_key", name="uq_governance_control_org_key"),
        db.CheckConstraint("mode IN ('monitor', 'block')", name="ck_governance_control_mode"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    control_key = db.Column(db.String(80), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    mode = db.Column(db.String(10), nullable=False, default="block")
    settings = db.Column(db.JSON, nullable=False, default=dict)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApprovalRequest(db.Model):
    __tablename__ = "governance_approval_requests"
    __table_args__ = (
        db.Index("ix_approval_org_status_created", "org_id", "status", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    action_key = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(80), nullable=False)
    resource_id = db.Column(db.String(120), nullable=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(12), nullable=False, default="pending")
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewer_note = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)


class RetentionPolicy(db.Model):
    __tablename__ = "governance_retention_policies"
    __table_args__ = (db.UniqueConstraint("org_id", name="uq_retention_policy_org"),)

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    meetings_days = db.Column(db.Integer, nullable=True)
    signals_days = db.Column(db.Integer, nullable=True)
    risk_scores_days = db.Column(db.Integer, nullable=True)
    insights_days = db.Column(db.Integer, nullable=True)
    completed_onboarding_days = db.Column(db.Integer, nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
