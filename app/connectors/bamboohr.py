import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.connectors.base import BaseConnector


EMPLOYEE_FIELDS = "department,division,location"
PAGE_SIZE = 250
MAX_EMPLOYEES_PER_SYNC = 100_000


class BambooHRConnector(BaseConnector):
    """Read-only, organization-scoped BambooHR employee roster connector."""

    def __init__(self, org_id):
        super().__init__(org_id)
        self._credentials = None
        self._session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    def _load_credentials(self):
        if self._credentials is None:
            credentials = self._organization_credentials("bamboohr")
            subdomain = credentials.get("subdomain", "").strip().lower()
            api_key = credentials.get("api_key", "").strip()
            if not subdomain or not api_key:
                raise RuntimeError("BambooHR credentials are incomplete")
            self._credentials = {
                "subdomain": subdomain,
                "api_key": api_key,
                "base_url": f"https://{subdomain}.bamboohr.com/api/v1",
            }
        return self._credentials

    def _get_page(self, *, limit=PAGE_SIZE, cursor=None):
        credentials = self._load_credentials()
        params = {"fields": EMPLOYEE_FIELDS, "page[limit]": limit}
        if cursor:
            params["page[after]"] = cursor
        response = self._session.get(
            f"{credentials['base_url']}/employees",
            params=params,
            auth=(credentials["api_key"], "x"),
            headers={"Accept": "application/json"},
            timeout=(5, 30),
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("BambooHR returned an invalid response") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("BambooHR returned an unsupported employee response")
        meta = payload.get("meta") or {}
        page_meta = meta.get("page") or {}
        return payload["data"], page_meta.get("nextCursor"), meta.get("total")

    def connect(self):
        # Reading one row validates credentials and employee-read access without
        # downloading the full roster or exposing any employee data in the API.
        self._get_page(limit=1)
        self._connected = True

    def fetch_data(self, **kwargs):
        return list(self.iter_employees())

    def iter_employees(self):
        for rows in self.iter_employee_pages():
            yield from rows

    def iter_employee_pages(self):
        cursor = None
        seen_cursors = set()
        total_count = 0
        while True:
            rows, next_cursor, _reported_total = self._get_page(cursor=cursor)
            if not rows and next_cursor:
                raise RuntimeError("BambooHR returned an empty page with a continuation cursor")
            for employee in rows:
                if not isinstance(employee, dict):
                    raise RuntimeError("BambooHR returned an invalid employee record")
                total_count += 1
                if total_count > MAX_EMPLOYEES_PER_SYNC:
                    raise RuntimeError("BambooHR roster exceeds the supported sync limit")
            yield rows
            if not next_cursor:
                break
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise RuntimeError("BambooHR returned an invalid pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    @staticmethod
    def map_employee(source):
        source_id = source.get("employeeId", source.get("id"))
        if source_id is None or not str(source_id).strip():
            raise ValueError("BambooHR employee is missing its stable employee ID")
        first_name = source.get("firstName") or ""
        last_name = source.get("lastName") or ""
        preferred_name = source.get("preferredName") or ""
        name = " ".join(str(value).strip() for value in (preferred_name or first_name, last_name) if str(value).strip())
        if not name:
            name = str(source.get("displayName") or "").strip()
        if not name:
            raise ValueError("BambooHR employee is missing a readable name")
        return {
            "external_id": str(source_id).strip()[:120],
            "name": name[:255],
            "role": str(source.get("jobTitleName") or source.get("jobTitle") or "").strip()[:100] or None,
            "team": str(source.get("department") or source.get("division") or source.get("location") or "").strip()[:100] or None,
            "status": str(source.get("status") or "active").strip().lower()[:50] or "active",
        }
