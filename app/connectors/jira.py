import requests
from requests.auth import HTTPBasicAuth

from app.connectors.base import BaseConnector
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JiraConnector(BaseConnector):
    def __init__(self, org_id):
        super().__init__(org_id)
        self.base_url = ""
        self.email = ""
        self.api_token = ""

    def connect(self):
        credentials = self._organization_credentials("jira")
        self.base_url = credentials["base_url"].rstrip("/")
        self.email = credentials["email"]
        self.api_token = credentials["api_token"]
        resp = requests.get(
            f"{self.base_url}/rest/api/3/myself",
            auth=HTTPBasicAuth(self.email, self.api_token),
            timeout=10,
        )
        resp.raise_for_status()
        self._connected = True

    def fetch_data(self, **kwargs):
        return self.get_user_issues(kwargs.get("account_id", ""))

    def _auth(self):
        return HTTPBasicAuth(self.email, self.api_token)

    def get_user_issues(self, account_id):
        jql = f"assignee={account_id} AND updated >= -30d"
        resp = requests.get(
            f"{self.base_url}/rest/api/3/search",
            params={"jql": jql, "maxResults": 100},
            auth=self._auth(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}

    def get_workload(self, account_id):
        jql = f"assignee={account_id} AND status != Done"
        resp = requests.get(
            f"{self.base_url}/rest/api/3/search",
            params={"jql": jql, "maxResults": 50},
            auth=self._auth(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}

    def get_overdue_tickets(self, account_id):
        jql = f"assignee={account_id} AND due < now() AND status != Done"
        resp = requests.get(
            f"{self.base_url}/rest/api/3/search",
            params={"jql": jql},
            auth=self._auth(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}
