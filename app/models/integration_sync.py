from datetime import datetime

from app import db


class IntegrationSync(db.Model):
    __tablename__ = "integration_syncs"
    __table_args__ = (
        db.UniqueConstraint("org_id", "integration_key", name="uq_integration_sync_org_key"),
        db.CheckConstraint("status IN ('idle', 'queued', 'running', 'succeeded', 'failed')", name="ck_integration_sync_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    integration_key = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="idle")
    task_id = db.Column(db.String(120), nullable=True)
    records_synced = db.Column(db.Integer, nullable=False, default=0)
    absence_records_synced = db.Column(db.Integer, nullable=False, default=0)
    data_coverage = db.Column(db.JSON, nullable=True)
    last_started_at = db.Column(db.DateTime, nullable=True)
    last_success_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "integration_key": self.integration_key,
            "status": self.status,
            "records_synced": self.records_synced,
            "absence_records_synced": self.absence_records_synced,
            "data_coverage": self.data_coverage,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error": self.last_error,
        }


class EmployeeExternalId(db.Model):
    __tablename__ = "employee_external_ids"
    __table_args__ = (
        db.UniqueConstraint("org_id", "integration_key", "external_id", name="uq_employee_external_source"),
        db.UniqueConstraint("employee_id", "integration_key", name="uq_employee_source_mapping"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    integration_key = db.Column(db.String(40), nullable=False)
    external_id = db.Column(db.String(120), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
