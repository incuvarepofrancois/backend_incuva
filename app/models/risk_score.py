from datetime import datetime
from app import db


class RiskScore(db.Model):
    __tablename__ = "risk_scores"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    score = db.Column(db.Float, nullable=False)
    level = db.Column(db.String(20), nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    drivers = db.Column(db.JSON, nullable=True)
    canonical_context = db.Column(db.JSON, nullable=True)
    recommendation = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "employee_id": self.employee_id,
            "score": self.score,
            "level": self.level,
            "confidence": self.confidence,
            "drivers": self.drivers,
            "canonical_context": self.canonical_context,
            "recommendation": self.recommendation,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
