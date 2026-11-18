import requests

from app.connectors.base import BaseConnector
from app.utils.logger import get_logger

logger = get_logger(__name__)
GH_BASE = "https://api.github.com"


class GitHubConnector(BaseConnector):
    def __init__(self, org_id):
        super().__init__(org_id)
        self.token = ""
        self.org = ""

    def connect(self):
        credentials = self._organization_credentials("github")
        self.token = credentials["personal_access_token"]
        self.org = credentials["organization"]
        resp = requests.get(f"{GH_BASE}/user", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        self._connected = True

    def fetch_data(self, **kwargs):
        return self.get_commit_activity(kwargs.get("username", ""))

    def _headers(self):
        return {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}

    def get_commit_activity(self, username):
        resp = requests.get(
            f"{GH_BASE}/search/commits",
            params={"q": f"author:{username}", "per_page": 100},
            headers=self._headers(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}

    def get_pr_reviews(self, username):
        resp = requests.get(
            f"{GH_BASE}/search/issues",
            params={"q": f"reviewed-by:{username} type:pr", "per_page": 50},
            headers=self._headers(),
            timeout=10,
        )
        return resp.json() if resp.ok else {}

    def get_contribution_stats(self, username):
        resp = requests.get(
            f"{GH_BASE}/users/{username}/events",
            headers=self._headers(),
            timeout=10,
        )
        return resp.json() if resp.ok else []
