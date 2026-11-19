from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint, event, inspect

from app import db


PROPOSAL_STATES = {
    "PENDING_CONFIRMATION", "EXPIRED", "CANCELLED", "CONFIRMED", "EXECUTING",
    "SUCCEEDED", "FAILED", "STALE", "EXECUTION_UNKNOWN",
}
_A3_1_TRANSITIONS = {
    "PENDING_CONFIRMATION": {"EXPIRED", "CANCELLED"},
}
_IMMUTABLE_FIELDS = (
    "id", "org_id", "initiator_id", "capability_name", "contract_version",
    "payload_ciphertext", "encryption_nonce", "canonicalization_version",
    "snapshot_digest", "action_digest", "hmac_digest", "hmac_key_id",
    "encryption_key_id", "resource_type", "resource_id", "resource_version",
    "created_at", "expires_at", "confirmation_token_digest",
    "idempotency_key_digest", "correlation_id",
)
_RECOVERY_FIELDS = (
    "confirmation_token_recovery_ciphertext",
    "confirmation_token_recovery_nonce",
    "confirmation_token_recovery_key_id",
)
_MUTABLE_FIELDS = {
    "status", "cancelled_at", "terminal_reason", "updated_at", "response_issued_at",
    *_RECOVERY_FIELDS,
}


class AgenticActionProposal(db.Model):
    __tablename__ = "agentic_action_proposals"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "initiator_id", "idempotency_key_digest",
            name="uq_agentic_proposal_org_initiator_idempotency",
        ),
        CheckConstraint(
            "status IN ('PENDING_CONFIRMATION','EXPIRED','CANCELLED','CONFIRMED','EXECUTING','SUCCEEDED','FAILED','STALE','EXECUTION_UNKNOWN')",
            name="ck_agentic_proposal_status",
        ),
        CheckConstraint("expires_at > created_at", name="ck_agentic_proposal_expiry_after_creation"),
        CheckConstraint("length(snapshot_digest) = 64", name="ck_agentic_proposal_snapshot_digest_length"),
        CheckConstraint("length(action_digest) = 64", name="ck_agentic_proposal_action_digest_length"),
        CheckConstraint("length(hmac_digest) = 64", name="ck_agentic_proposal_hmac_digest_length"),
        CheckConstraint("length(confirmation_token_digest) = 64", name="ck_agentic_proposal_token_digest_length"),
        CheckConstraint("length(idempotency_key_digest) = 64", name="ck_agentic_proposal_idempotency_digest_length"),
        CheckConstraint("length(encryption_nonce) = 12", name="ck_agentic_proposal_nonce_length"),
        CheckConstraint(
            "(confirmation_token_recovery_ciphertext IS NULL AND confirmation_token_recovery_nonce IS NULL AND confirmation_token_recovery_key_id IS NULL) OR "
            "(confirmation_token_recovery_ciphertext IS NOT NULL AND length(confirmation_token_recovery_nonce) = 12 AND confirmation_token_recovery_key_id IS NOT NULL)",
            name="ck_agentic_proposal_recovery_fields_complete",
        ),
        CheckConstraint(
            "status = 'PENDING_CONFIRMATION' OR confirmation_token_recovery_ciphertext IS NULL",
            name="ck_agentic_proposal_terminal_has_no_recovery_token",
        ),
        db.Index("ix_agentic_proposals_org_status_expiry", "org_id", "status", "expires_at"),
        db.Index("ix_agentic_proposals_org_created", "org_id", "created_at"),
        db.Index("ix_agentic_proposals_initiator_created", "initiator_id", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    initiator_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    capability_name = db.Column(db.String(120), nullable=False)
    contract_version = db.Column(db.String(30), nullable=False)

    payload_ciphertext = db.Column(db.LargeBinary, nullable=False)
    encryption_nonce = db.Column(db.LargeBinary(12), nullable=False)
    canonicalization_version = db.Column(db.String(40), nullable=False)
    snapshot_digest = db.Column(db.String(64), nullable=False)
    action_digest = db.Column(db.String(64), nullable=False)
    hmac_digest = db.Column(db.String(64), nullable=False)
    hmac_key_id = db.Column(db.String(80), nullable=False)
    encryption_key_id = db.Column(db.String(80), nullable=False)

    resource_type = db.Column(db.String(80), nullable=False)
    resource_id = db.Column(db.String(120), nullable=True)
    resource_version = db.Column(db.String(64), nullable=True)

    status = db.Column(db.String(30), nullable=False, default="PENDING_CONFIRMATION")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    response_issued_at = db.Column(db.DateTime(timezone=True), nullable=True)

    confirmation_token_digest = db.Column(db.String(64), nullable=False)
    confirmation_token_recovery_ciphertext = db.Column(db.LargeBinary, nullable=True)
    confirmation_token_recovery_nonce = db.Column(db.LargeBinary(12), nullable=True)
    confirmation_token_recovery_key_id = db.Column(db.String(80), nullable=True)
    idempotency_key_digest = db.Column(db.String(64), nullable=False)
    correlation_id = db.Column(db.String(128), nullable=False)

    result_code = db.Column(db.String(80), nullable=True)
    terminal_reason = db.Column(db.String(80), nullable=True)
    result_message = db.Column(db.String(200), nullable=True)


@event.listens_for(AgenticActionProposal, "before_update")
def _guard_proposal_updates(_mapper, _connection, proposal):
    state = inspect(proposal)
    changed_immutable = [
        field for field in _IMMUTABLE_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed_immutable:
        raise ValueError("Agentic proposal snapshot fields are immutable")

    changed_fields = {
        attr.key for attr in state.attrs if attr.history.has_changes()
    }
    if changed_fields - _MUTABLE_FIELDS:
        raise ValueError("Only proposal lifecycle metadata may be updated")

    status_history = state.attrs.status.history
    recovery_changed = any(state.attrs[field].history.has_changes() for field in _RECOVERY_FIELDS)
    if recovery_changed:
        if not status_history.has_changes() or proposal.status not in {"CANCELLED", "EXPIRED"}:
            raise ValueError("Recovery token data may only be cleared during a terminal transition")
        if any(getattr(proposal, field) is not None for field in _RECOVERY_FIELDS):
            raise ValueError("Recovery token data may only be cleared, never replaced")
    if status_history.has_changes():
        previous = status_history.deleted[0] if status_history.deleted else None
        current = proposal.status
        if current not in _A3_1_TRANSITIONS.get(previous, set()):
            raise ValueError("Invalid agentic proposal state transition")


@event.listens_for(AgenticActionProposal, "before_insert")
def _guard_proposal_insert(_mapper, _connection, proposal):
    if proposal.status != "PENDING_CONFIRMATION":
        raise ValueError("New proposals must begin in PENDING_CONFIRMATION")
