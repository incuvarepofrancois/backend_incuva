from datetime import datetime

from app import db


class IntegrationCredential(db.Model):
    __tablename__ = "integration_credentials"
    __table_args__ = (
        db.UniqueConstraint("org_id", "integration_key", name="uq_integration_credentials_org_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    integration_key = db.Column(db.String(40), nullable=False)
    encrypted_credentials = db.Column(db.Text, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    connection_status = db.Column(db.String(20), nullable=False, default="unverified")
    connection_checked_at = db.Column(db.DateTime, nullable=True)
    connection_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
