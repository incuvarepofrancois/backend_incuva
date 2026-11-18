"""Track the last verified status of each integration credential set.

Revision ID: d47c8b6e1a20
Revises: c93a7e1d4b20
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa


revision = "d47c8b6e1a20"
down_revision = "c93a7e1d4b20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "integration_credentials",
        sa.Column("connection_status", sa.String(length=20), nullable=False, server_default="unverified"),
    )
    op.add_column("integration_credentials", sa.Column("connection_checked_at", sa.DateTime(), nullable=True))
    op.add_column("integration_credentials", sa.Column("connection_error", sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column("integration_credentials", "connection_error")
    op.drop_column("integration_credentials", "connection_checked_at")
    op.drop_column("integration_credentials", "connection_status")
