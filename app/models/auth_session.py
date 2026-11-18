from datetime import datetime, timezone

from app import db


class AuthSession(db.Model):
    __tablename__ = "auth_sessions"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
