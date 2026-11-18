from datetime import datetime

from app import db


class EmployeeAbsence(db.Model):
    """Canonical absence record with organization-scoped source provenance."""

    __tablename__ = "employee_absences"
    __table_args__ = (
        db.UniqueConstraint("org_id", "source_key", "external_id", name="uq_employee_absence_source"),
        db.CheckConstraint("end_date >= start_date", name="ck_employee_absence_date_order"),
        db.CheckConstraint("status IN ('approved', 'pending', 'rejected', 'cancelled', 'unknown')", name="ck_employee_absence_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    source_key = db.Column(db.String(40), nullable=False)
    external_id = db.Column(db.String(120), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="unknown")
    duration_days = db.Column(db.Float, nullable=True)
    is_half_day = db.Column(db.Boolean, nullable=True)
    contract_version = db.Column(db.String(20), nullable=False, default="hr.v1")
    source_updated_at = db.Column(db.DateTime, nullable=True)
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    employee = db.relationship("Employee")

    def to_dict(self):
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "source": self.source_key,
            "external_id": self.external_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "status": self.status,
            "duration_days": self.duration_days,
            "is_half_day": self.is_half_day,
            "contract_version": self.contract_version,
            "source_updated_at": self.source_updated_at.isoformat() if self.source_updated_at else None,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }
