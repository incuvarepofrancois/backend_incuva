from datetime import datetime
from app import db


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, nullable=True)
    external_id = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(100), nullable=True)
    team = db.Column(db.String(100), nullable=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    hire_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(50), default="active")
    data_contract_version = db.Column(db.String(20), nullable=False, default="hr.v1")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "org_id": self.org_id,
            "external_id": self.external_id,
            "name": self.name,
            "role": self.role,
            "team": self.team,
            "manager_id": self.manager_id,
            "hire_date": self.hire_date.isoformat() if self.hire_date else None,
            "status": self.status,
            "data_contract_version": self.data_contract_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
