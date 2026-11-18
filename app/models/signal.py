from datetime import datetime
from app import db


class Signal(db.Model):
    __tablename__ = "signals"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Float, nullable=True)
    weight = db.Column(db.Float, default=1.0)
    captured_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "source": self.source,
            "type": self.type,
            "value": self.value,
            "weight": self.weight,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }
