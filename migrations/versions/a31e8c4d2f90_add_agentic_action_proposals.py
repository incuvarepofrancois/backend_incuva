"""Add sealed, persistent agentic action proposals.

Revision ID: a31e8c4d2f90
Revises: d47c8b6e1a20
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa


revision = "a31e8c4d2f90"
down_revision = "d47c8b6e1a20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agentic_action_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("initiator_id", sa.Integer(), nullable=False),
        sa.Column("capability_name", sa.String(length=120), nullable=False),
        sa.Column("contract_version", sa.String(length=30), nullable=False),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("canonicalization_version", sa.String(length=40), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("action_digest", sa.String(length=64), nullable=False),
        sa.Column("hmac_digest", sa.String(length=64), nullable=False),
        sa.Column("hmac_key_id", sa.String(length=80), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=True),
        sa.Column("resource_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_token_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("result_code", sa.String(length=80), nullable=True),
        sa.Column("terminal_reason", sa.String(length=80), nullable=True),
        sa.Column("result_message", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING_CONFIRMATION','EXPIRED','CANCELLED','CONFIRMED','EXECUTING','SUCCEEDED','FAILED','STALE','EXECUTION_UNKNOWN')",
            name="ck_agentic_proposal_status",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_agentic_proposal_expiry_after_creation"),
        sa.CheckConstraint("length(snapshot_digest) = 64", name="ck_agentic_proposal_snapshot_digest_length"),
        sa.CheckConstraint("length(action_digest) = 64", name="ck_agentic_proposal_action_digest_length"),
        sa.CheckConstraint("length(hmac_digest) = 64", name="ck_agentic_proposal_hmac_digest_length"),
        sa.CheckConstraint("length(confirmation_token_digest) = 64", name="ck_agentic_proposal_token_digest_length"),
        sa.CheckConstraint("length(idempotency_key_digest) = 64", name="ck_agentic_proposal_idempotency_digest_length"),
        sa.CheckConstraint("length(encryption_nonce) = 12", name="ck_agentic_proposal_nonce_length"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "initiator_id", "idempotency_key_digest",
            name="uq_agentic_proposal_org_initiator_idempotency",
        ),
    )
    op.create_index(
        "ix_agentic_proposals_org_status_expiry", "agentic_action_proposals",
        ["org_id", "status", "expires_at"], unique=False,
    )
    op.create_index(
        "ix_agentic_proposals_org_created", "agentic_action_proposals",
        ["org_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_agentic_proposals_initiator_created", "agentic_action_proposals",
        ["initiator_id", "created_at"], unique=False,
    )


def downgrade():
    op.drop_index("ix_agentic_proposals_initiator_created", table_name="agentic_action_proposals")
    op.drop_index("ix_agentic_proposals_org_created", table_name="agentic_action_proposals")
    op.drop_index("ix_agentic_proposals_org_status_expiry", table_name="agentic_action_proposals")
    op.drop_table("agentic_action_proposals")
