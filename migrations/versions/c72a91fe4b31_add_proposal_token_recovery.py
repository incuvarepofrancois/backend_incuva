"""Add encrypted, expiring recovery material for proposal response retries.

Revision ID: c72a91fe4b31
Revises: a31e8c4d2f90
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa


revision = "c72a91fe4b31"
down_revision = "a31e8c4d2f90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agentic_action_proposals",
        sa.Column("confirmation_token_recovery_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "agentic_action_proposals",
        sa.Column("confirmation_token_recovery_nonce", sa.LargeBinary(length=12), nullable=True),
    )
    op.add_column(
        "agentic_action_proposals",
        sa.Column("confirmation_token_recovery_key_id", sa.String(length=80), nullable=True),
    )
    with op.batch_alter_table("agentic_action_proposals") as batch_op:
        batch_op.create_check_constraint(
            "ck_agentic_proposal_recovery_fields_complete",
            "(confirmation_token_recovery_ciphertext IS NULL AND confirmation_token_recovery_nonce IS NULL AND confirmation_token_recovery_key_id IS NULL) OR "
            "(confirmation_token_recovery_ciphertext IS NOT NULL AND length(confirmation_token_recovery_nonce) = 12 AND confirmation_token_recovery_key_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_agentic_proposal_terminal_has_no_recovery_token",
            "status = 'PENDING_CONFIRMATION' OR confirmation_token_recovery_ciphertext IS NULL",
        )


def downgrade():
    with op.batch_alter_table("agentic_action_proposals") as batch_op:
        batch_op.drop_constraint("ck_agentic_proposal_terminal_has_no_recovery_token", type_="check")
        batch_op.drop_constraint("ck_agentic_proposal_recovery_fields_complete", type_="check")
        batch_op.drop_column("confirmation_token_recovery_key_id")
        batch_op.drop_column("confirmation_token_recovery_nonce")
        batch_op.drop_column("confirmation_token_recovery_ciphertext")
