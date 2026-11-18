from datetime import datetime
from app import db


class Meeting(db.Model):
    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    source = db.Column(db.String(50), nullable=True)
    title = db.Column(db.String(255), nullable=True)
    participants = db.Column(db.JSON, nullable=True)
    transcript = db.Column(db.Text, nullable=True)
    decisions = db.Column(db.JSON, nullable=True)
    action_items = db.Column(db.JSON, nullable=True)
    risk_signals = db.Column(db.JSON, nullable=True)
    analyzed_at = db.Column(db.DateTime, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.created_at.date().isoformat() if self.created_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "org_id": self.org_id,
            "source": self.source,
            "title": self.title,
            "participants": self.participants,
            "transcript": self.transcript,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "risk_signals": self.risk_signals,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
        }
