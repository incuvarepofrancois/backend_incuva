import base64
import requests
from datetime import datetime, timedelta

from app.connectors.base import BaseConnector
from app.utils.logger import get_logger

logger = get_logger(__name__)
ZOOM_BASE = "https://api.zoom.us/v2"


class ZoomConnector(BaseConnector):
    def __init__(self, org_id):
        super().__init__(org_id)
        self._token = None
        self._token_expires_at = datetime.min

    def connect(self):
        credentials = self._organization_credentials("zoom")
        self.account_id = credentials["account_id"]
        self.client_id = credentials["client_id"]
        self.client_secret = credentials["client_secret"]
        creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": self.account_id},
            headers={"Authorization": f"Basic {creds}"},
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        self._token_expires_at = datetime.utcnow() + timedelta(seconds=max(60, int(token_data.get("expires_in", 3600)) - 60))
        self._connected = True

    def fetch_data(self, **kwargs):
        return self.get_meetings(kwargs.get("user_id", "me"))

    def _headers(self):
        if not self._connected or datetime.utcnow() >= self._token_expires_at:
            self.connect()
        return {"Authorization": f"Bearer {self._token}"}

    def get_meetings(self, user_id="me"):
        resp = requests.get(f"{ZOOM_BASE}/users/{user_id}/meetings", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}

    def get_recordings(self, user_id="me"):
        resp = requests.get(f"{ZOOM_BASE}/users/{user_id}/recordings", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}
