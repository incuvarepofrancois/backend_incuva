import json
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.connectors.base import BaseConnector


PAGE_SIZE = 100
MAX_EMPLOYEES_PER_SYNC = 100_000
EMPLOYEE_FIELDS = ["name", "employee_name", "designation", "department", "status", "date_of_joining"]


class FrappeHRConnector(BaseConnector):
    """Read-only Frappe HR employee roster connector, scoped to one INCUVA org."""

    def __init__(self, org_id):
        super().__init__(org_id)
        self._credentials = None
        self._session = requests.Session()
        retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}), respect_retry_after_header=True)
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    def _load_credentials(self):
        if self._credentials is None:
            credentials = self._organization_credentials("frappehr")
            base_url = credentials.get("base_url", "").strip().rstrip("/")
            api_key = credentials.get("api_key", "").strip()
            api_secret = credentials.get("api_secret", "").strip()
            if not base_url or not api_key or not api_secret:
                raise RuntimeError("Frappe HR credentials are incomplete")
            self._credentials = {
                "base_url": base_url,
                "headers": {"Authorization": f"token {api_key}:{api_secret}", "Accept": "application/json"},
            }
        return self._credentials

    def _get_page(self, start=0):
        credentials = self._load_credentials()
        response = self._session.get(
            urljoin(credentials["base_url"] + "/", "api/resource/Employee"),
            params={"fields": json.dumps(EMPLOYEE_FIELDS), "limit_page_length": PAGE_SIZE, "limit_start": start},
            headers=credentials["headers"], timeout=(5, 30), allow_redirects=False,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("Frappe HR returned an invalid response") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Frappe HR returned an unsupported employee response")
        return payload["data"]

    def connect(self):
        # Probe both HR DocTypes needed by the canonical roster + absence import.
        self._get_page(start=0)
        today = date.today()
        self._get_leave_page(0, today - timedelta(days=1), today)
        self._connected = True

    def fetch_data(self, **kwargs):
        return list(self.iter_employees())

    def iter_employees(self):
        for page in self.iter_employee_pages():
            yield from page

    def iter_employee_pages(self):
        start = 0
        total = 0
        while True:
            rows = self._get_page(start=start)
            for employee in rows:
                if not isinstance(employee, dict):
                    raise RuntimeError("Frappe HR returned an invalid employee record")
            total += len(rows)
            if total > MAX_EMPLOYEES_PER_SYNC:
                raise RuntimeError("Frappe HR roster exceeds the supported sync limit")
            yield rows
            if len(rows) < PAGE_SIZE:
                break
            start += len(rows)

    def _get_leave_page(self, start, start_date, end_date):
        credentials = self._load_credentials()
        filters = [
            ["to_date", ">=", start_date.isoformat()],
            ["from_date", "<=", end_date.isoformat()],
        ]
        response = self._session.get(
            urljoin(credentials["base_url"] + "/", "api/resource/Leave Application"),
            params={
                "fields": json.dumps(["name", "employee", "from_date", "to_date", "status", "total_leave_days", "half_day", "modified"]),
                "filters": json.dumps(filters),
                "limit_page_length": PAGE_SIZE,
                "limit_start": start,
                "order_by": "from_date asc, name asc",
            },
            headers=credentials["headers"], timeout=(5, 30), allow_redirects=False,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("Frappe HR returned an invalid leave response") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Frappe HR returned an unsupported leave response")
        return payload["data"]

    def iter_absence_pages(self, start_date, end_date):
        start = 0
        total = 0
        while True:
            rows = self._get_leave_page(start, start_date, end_date)
            for absence in rows:
                if not isinstance(absence, dict):
                    raise RuntimeError("Frappe HR returned an invalid leave record")
            total += len(rows)
            if total > MAX_EMPLOYEES_PER_SYNC * 10:
                raise RuntimeError("Frappe HR leave history exceeds the supported sync limit")
            yield rows
            if len(rows) < PAGE_SIZE:
                break
            start += len(rows)

    @staticmethod
    def map_employee(source):
        source_id = source.get("name")
        if source_id is None or not str(source_id).strip():
            raise ValueError("Frappe HR employee is missing its stable ID")
        name = str(source.get("employee_name") or "").strip()
        if not name:
            raise ValueError("Frappe HR employee is missing a readable name")
        return {
            "external_id": str(source_id).strip()[:120],
            "name": name[:255],
            "role": str(source.get("designation") or "").strip()[:100] or None,
            "team": str(source.get("department") or "").strip()[:100] or None,
            "status": str(source.get("status") or "active").strip().lower()[:50] or "active",
            "hire_date": str(source.get("date_of_joining") or "").strip() or None,
        }

    @staticmethod
    def map_absence(source):
        source_id = source.get("name")
        employee_external_id = source.get("employee")
        start_date = source.get("from_date")
        end_date = source.get("to_date")
        if not source_id or not employee_external_id or not start_date or not end_date:
            raise ValueError("Frappe HR leave record is missing its ID, employee, or date range")
        duration = source.get("total_leave_days")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        half_day_value = source.get("half_day")
        if isinstance(half_day_value, str):
            half_day_value = half_day_value.strip().lower() in {"1", "true", "yes"}
        elif half_day_value is not None:
            half_day_value = bool(half_day_value)
        modified = source.get("modified")
        return {
            "external_id": str(source_id).strip()[:120],
            "employee_external_id": str(employee_external_id).strip()[:120],
            "start_date": str(start_date).strip()[:10],
            "end_date": str(end_date).strip()[:10],
            "status": source.get("status"),
            "duration_days": duration,
            "is_half_day": half_day_value,
            "source_updated_at": str(modified).strip() if modified else None,
        }
