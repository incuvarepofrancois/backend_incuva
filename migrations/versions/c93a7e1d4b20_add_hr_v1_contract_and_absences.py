"""Add canonical HR v1 data contract and Frappe absence records.

Revision ID: c93a7e1d4b20
Revises: f1a2b6868165
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa


revision = "c93a7e1d4b20"
down_revision = "f1a2b6868165"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("data_contract_version", sa.String(length=20), nullable=False, server_default="hr.v1"))
    op.add_column("integration_syncs", sa.Column("absence_records_synced", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("integration_syncs", sa.Column("data_coverage", sa.JSON(), nullable=True))
    op.add_column("risk_scores", sa.Column("canonical_context", sa.JSON(), nullable=True))
    op.create_table(
        "employee_absences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_days", sa.Float(), nullable=True),
        sa.Column("is_half_day", sa.Boolean(), nullable=True),
        sa.Column("contract_version", sa.String(length=20), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_employee_absence_date_order"),
        sa.CheckConstraint("status IN ('approved', 'pending', 'rejected', 'cancelled', 'unknown')", name="ck_employee_absence_status"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "source_key", "external_id", name="uq_employee_absence_source"),
    )
    op.create_index("ix_employee_absences_org_id", "employee_absences", ["org_id"])
    op.create_index("ix_employee_absences_employee_id", "employee_absences", ["employee_id"])


def downgrade():
    op.drop_index("ix_employee_absences_employee_id", table_name="employee_absences")
    op.drop_index("ix_employee_absences_org_id", table_name="employee_absences")
    op.drop_table("employee_absences")
    op.drop_column("risk_scores", "canonical_context")
    op.drop_column("integration_syncs", "data_coverage")
    op.drop_column("integration_syncs", "absence_records_synced")
    op.drop_column("employees", "data_contract_version")
