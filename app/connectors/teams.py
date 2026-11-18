import requests
from datetime import datetime, timedelta
from secrets import token_urlsafe
from flask import current_app

from app.connectors.base import BaseConnector
from app.utils.logger import get_logger

logger = get_logger(__name__)
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class TeamsConnector(BaseConnector):
    def __init__(self, org_id):
        super().__init__(org_id)
        self._token = None
        self._token_expires_at = datetime.min

    def connect(self):
        credentials = self._organization_credentials("teams")
        self.client_id = credentials["client_id"]
        self.client_secret = credentials["client_secret"]
        self.tenant_id = credentials["tenant_id"]
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        self._token_expires_at = datetime.utcnow() + timedelta(seconds=max(60, int(token_data.get("expires_in", 3600)) - 60))
        self._connected = True

    def fetch_data(self, **kwargs):
        return self.get_user_activity(kwargs.get("user_id", ""))

    def _headers(self):
        if not self._connected or datetime.utcnow() >= self._token_expires_at:
            self.connect()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def get_user_activity(self, user_id):
        resp = requests.get(f"{GRAPH_BASE}/users/{user_id}/activities", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}

    def get_meetings(self, user_id):
        resp = requests.get(f"{GRAPH_BASE}/users/{user_id}/onlineMeetings", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}

    def get_presence(self, user_id):
        resp = requests.get(f"{GRAPH_BASE}/users/{user_id}/presence", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}

    def subscribe_to_webhooks(self, notification_url):
        from app import db
        from app.governance.audit import record_audit_event
        from app.models.governance import OrganizationSecret
        from app.security.crypto import credential_cipher, decrypt_credential, encrypt_credential

        if credential_cipher() is None:
            raise RuntimeError("Integration credential encryption is required for Teams webhook state")
        secret_name = "teams.webhook_client_state"
        secret = OrganizationSecret.query.filter_by(org_id=self.org_id, name=secret_name).first()
        if secret:
            client_state = decrypt_credential(secret.encrypted_value).decode("utf-8")
        else:
            client_state = token_urlsafe(32)
            secret = OrganizationSecret(org_id=self.org_id, name=secret_name, encrypted_value=encrypt_credential(client_state.encode("utf-8")))
            db.session.add(secret)
            record_audit_event(self.org_id, "secret.created", "secret", secret_name, details={"source": "teams_webhook_subscription"})
            db.session.commit()
        body = {
            "changeType": "created,updated",
            "notificationUrl": notification_url,
            "resource": "/communications/callRecords",
            "expirationDateTime": "2026-12-31T00:00:00Z",
            "clientState": client_state,
        }
        resp = requests.post(f"{GRAPH_BASE}/subscriptions", json=body, headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}
