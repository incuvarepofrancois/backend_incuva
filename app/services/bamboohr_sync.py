import logging
from datetime import datetime

import requests

from app import db
from app.connectors.bamboohr import BambooHRConnector
from app.governance.audit import record_audit_event
from app.models.employee import Employee
from app.models.integration_sync import EmployeeExternalId, IntegrationSync

logger = logging.getLogger(__name__)


def _safe_error(error):
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else "unknown"
        if status in (401, 403):
            return f"BambooHR access denied (HTTP {status}). Check the API key and employee-read permissions."
        if status == 429:
            return "BambooHR rate limit reached after retries. Retry the sync later."
        return f"BambooHR request failed (HTTP {status})."
    if isinstance(error, requests.Timeout):
        return "BambooHR request timed out after retries. Retry the sync later."
    if isinstance(error, requests.RequestException):
        return "Could not reach BambooHR after retries. Check network access and retry."
    return str(error)[:500] or "Employee sync failed. Check backend logs."


def _upsert_employee(org_id, source):
    mapped = BambooHRConnector.map_employee(source)
    mapping = EmployeeExternalId.query.filter_by(
        org_id=org_id,
        integration_key="bamboohr",
        external_id=mapped["external_id"],
    ).first()
    employee = db.session.get(Employee, mapping.employee_id) if mapping else None
    if employee is not None and employee.org_id != org_id:
        raise RuntimeError("Employee source mapping violates organization isolation")
    if employee is None:
        employee = Employee(
            org_id=org_id,
            external_id=f"bamboohr:{mapped['external_id']}",
            name=mapped["name"],
            role=mapped["role"],
            team=mapped["team"],
            status=mapped["status"],
        )
        db.session.add(employee)
        db.session.flush()
        db.session.add(EmployeeExternalId(
            org_id=org_id,
            integration_key="bamboohr",
            external_id=mapped["external_id"],
            employee_id=employee.id,
        ))
    employee.name = mapped["name"]
    employee.role = mapped["role"]
    employee.team = mapped["team"]
    employee.status = mapped["status"]
    return employee


def run_bamboohr_sync(org_id, sync_id):
    sync = IntegrationSync.query.filter_by(
        id=sync_id, org_id=org_id, integration_key="bamboohr"
    ).first()
    if sync is None:
        raise ValueError("BambooHR sync job not found for organization")

    sync.status = "running"
    sync.last_started_at = datetime.utcnow()
    sync.last_error = None
    sync.records_synced = 0
    db.session.commit()
    imported_count = 0

    try:
        connector = BambooHRConnector(org_id)
        for page in connector.iter_employee_pages():
            page_count = 0
            for source_employee in page:
                _upsert_employee(org_id, source_employee)
                page_count += 1
            db.session.commit()
            imported_count += page_count
            sync.records_synced = imported_count
            db.session.commit()

        sync.status = "succeeded"
        sync.last_success_at = datetime.utcnow()
        sync.last_error = None
        sync.records_synced = imported_count
        db.session.commit()
        record_audit_event(
            org_id,
            "integration.sync_succeeded",
            "integration",
            "bamboohr",
            details={"records_synced": imported_count},
        )
        db.session.commit()
        return sync.to_dict()
    except Exception as error:
        db.session.rollback()
        current_sync = IntegrationSync.query.filter_by(
            id=sync_id, org_id=org_id, integration_key="bamboohr"
        ).first()
        if current_sync is not None:
            current_sync.status = "failed"
            current_sync.records_synced = imported_count
            current_sync.last_error = _safe_error(error)
            db.session.commit()
            record_audit_event(
                org_id,
                "integration.sync_failed",
                "integration",
                "bamboohr",
                details={"records_synced": imported_count, "reason": current_sync.last_error},
            )
            db.session.commit()
        logger.exception("BambooHR employee sync failed for organization %s", org_id)
        raise
