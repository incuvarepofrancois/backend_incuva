import requests

from app.connectors.base import BaseConnector
from app.utils.logger import get_logger

logger = get_logger(__name__)
PERSONIO_BASE = "https://api.personio.de/v1"


class PersonioConnector(BaseConnector):
    def __init__(self, org_id):
        super().__init__(org_id)
        self._token = None

    def connect(self):
        credentials = self._organization_credentials("personio")
        resp = requests.post(
            f"{PERSONIO_BASE}/auth",
            json={"client_id": credentials["client_id"], "client_secret": credentials["client_secret"]},
            timeout=10,
        )
        resp.raise_for_status()
        self._token = resp.json().get("data", {}).get("token")
        self._connected = True

    def fetch_data(self, **kwargs):
        return self.get_employees()

    def _headers(self):
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def get_employees(self):
        resp = requests.get(f"{PERSONIO_BASE}/company/employees", headers=self._headers(), timeout=10)
        return resp.json() if resp.ok else {}

    def get_attendances(self, employee_id):
        resp = requests.get(
            f"{PERSONIO_BASE}/company/attendances",
            params={"employees[]": employee_id},
            headers=self._headers(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}

    def get_documents(self, employee_id):
        resp = requests.get(
            f"{PERSONIO_BASE}/company/employees/{employee_id}/documents",
            headers=self._headers(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}
