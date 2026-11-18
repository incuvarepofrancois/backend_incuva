from datetime import datetime
from app import db


class Insight(db.Model):
    __tablename__ = "insights"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, nullable=True)
    type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    data = db.Column(db.JSON, nullable=True)
    severity = db.Column(db.String(20), default="info")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "org_id": self.org_id,
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "data": self.data,
            "severity": self.severity,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
