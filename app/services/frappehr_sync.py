import logging
from datetime import date, datetime, timedelta
import math

import requests

from app import db
from app.connectors.frappehr import FrappeHRConnector
from app.data_contracts.hr_v1 import HR_CONTRACT_VERSION, normalize_absence_status
from app.governance.audit import record_audit_event
from app.models.employee import Employee
from app.models.employee_absence import EmployeeAbsence
from app.models.integration_sync import EmployeeExternalId, IntegrationSync

logger = logging.getLogger(__name__)
ABSENCE_HISTORY_DAYS = 180


def safe_error(error):
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else "unknown"
        if status in (401, 403):
            return f"Frappe HR access denied (HTTP {status}). Check API credentials and read permission for Employee and Leave Application."
        if status == 429:
            return "Frappe HR rate limit reached after retries. Retry the sync later."
        return f"Frappe HR request failed (HTTP {status})."
    if isinstance(error, requests.Timeout):
        return "Frappe HR request timed out after retries. Retry the sync later."
    if isinstance(error, requests.RequestException):
        return "Could not reach Frappe HR. Check the instance URL and network access."
    return str(error)[:500] or "HR data sync failed. Check backend logs."


def _parse_date(value, field):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError(f"Frappe HR returned an invalid {field}") from None


def _upsert_employee(org_id, source):
    mapped = FrappeHRConnector.map_employee(source)
    mapping = EmployeeExternalId.query.filter_by(
        org_id=org_id, integration_key="frappehr", external_id=mapped["external_id"],
    ).first()
    employee = db.session.get(Employee, mapping.employee_id) if mapping else None
    if employee is not None and employee.org_id != org_id:
        raise RuntimeError("Employee source mapping violates organization isolation")
    if employee is None:
        employee = Employee(
            org_id=org_id,
            external_id=f"frappehr:{mapped['external_id']}",
            name=mapped["name"],
            role=mapped["role"],
            team=mapped["team"],
            status=mapped["status"],
            data_contract_version=HR_CONTRACT_VERSION,
        )
        db.session.add(employee)
        db.session.flush()
        db.session.add(EmployeeExternalId(
            org_id=org_id, integration_key="frappehr",
            external_id=mapped["external_id"], employee_id=employee.id,
        ))
    employee.name = mapped["name"]
    employee.role = mapped["role"]
    employee.team = mapped["team"]
    employee.status = mapped["status"]
    employee.data_contract_version = HR_CONTRACT_VERSION
    employee.hire_date = _parse_date(mapped["hire_date"], "date_of_joining") if mapped["hire_date"] else None
    return employee


def _upsert_absence(org_id, source):
    mapped = FrappeHRConnector.map_absence(source)
    employee_mapping = EmployeeExternalId.query.filter_by(
        org_id=org_id,
        integration_key="frappehr",
        external_id=mapped["employee_external_id"],
    ).first()
    if employee_mapping is None:
        return False
    employee = db.session.get(Employee, employee_mapping.employee_id)
    if employee is None or employee.org_id != org_id:
        raise RuntimeError("Absence source mapping violates organization isolation")
    start_date = _parse_date(mapped["start_date"], "absence start date")
    end_date = _parse_date(mapped["end_date"], "absence end date")
    if end_date < start_date:
        raise ValueError("Frappe HR returned an absence whose end precedes its start")
    record = EmployeeAbsence.query.filter_by(
        org_id=org_id, source_key="frappehr", external_id=mapped["external_id"],
    ).first()
    if record is None:
        record = EmployeeAbsence(
            org_id=org_id, employee_id=employee.id, source_key="frappehr",
            external_id=mapped["external_id"], start_date=start_date, end_date=end_date,
            status="unknown", contract_version=HR_CONTRACT_VERSION,
        )
        db.session.add(record)
    elif record.employee_id != employee.id:
        raise RuntimeError("An absence source ID was reassigned to a different employee")
    record.start_date = start_date
    record.end_date = end_date
    record.status = normalize_absence_status(mapped["status"])
    duration = mapped["duration_days"]
    record.duration_days = duration if duration is not None and math.isfinite(duration) and duration >= 0 else None
    record.is_half_day = mapped["is_half_day"]
    record.contract_version = HR_CONTRACT_VERSION
    record.source_updated_at = datetime.fromisoformat(mapped["source_updated_at"]) if mapped["source_updated_at"] else None
    record.synced_at = datetime.utcnow()
    return True


def run_frappehr_sync(org_id, sync_id):
    sync = IntegrationSync.query.filter_by(id=sync_id, org_id=org_id, integration_key="frappehr").first()
    if sync is None:
        raise ValueError("Frappe HR sync job not found for organization")
    sync.status = "running"
    sync.last_started_at = datetime.utcnow()
    sync.last_error = None
    sync.records_synced = 0
    sync.absence_records_synced = 0
    db.session.commit()
    imported_employees = 0
    imported_absences = 0
    unmatched_absences = 0
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=ABSENCE_HISTORY_DAYS)

    try:
        connector = FrappeHRConnector(org_id)
        for page in connector.iter_employee_pages():
            for source_employee in page:
                _upsert_employee(org_id, source_employee)
            imported_employees += len(page)
            db.session.commit()
            sync.records_synced = imported_employees
            db.session.commit()

        for page in connector.iter_absence_pages(start_date, end_date):
            for source_absence in page:
                if _upsert_absence(org_id, source_absence):
                    imported_absences += 1
                else:
                    unmatched_absences += 1
            db.session.commit()
            sync.absence_records_synced = imported_absences
            db.session.commit()

        synced_at = datetime.utcnow()
        sync.status = "succeeded"
        sync.last_success_at = synced_at
        sync.last_error = None
        sync.records_synced = imported_employees
        sync.absence_records_synced = imported_absences
        sync.data_coverage = {
            "contract_version": HR_CONTRACT_VERSION,
            "collaborators": {"complete": True, "synced_at": synced_at.isoformat()},
            "absence_history": {
                "complete": unmatched_absences == 0,
                "from": start_date.isoformat(),
                "through": end_date.isoformat(),
                "synced_at": synced_at.isoformat(),
                "unmatched_records": unmatched_absences,
            },
        }
        db.session.commit()
        record_audit_event(
            org_id, "integration.sync_succeeded", "integration", "frappehr",
            details={"contract_version": HR_CONTRACT_VERSION, "collaborators_synced": imported_employees,
                     "absences_synced": imported_absences, "unmatched_absences": unmatched_absences},
        )
        db.session.commit()
        return sync.to_dict()
    except Exception as error:
        db.session.rollback()
        current_sync = IntegrationSync.query.filter_by(id=sync_id, org_id=org_id, integration_key="frappehr").first()
        if current_sync is not None:
            current_sync.status = "failed"
            current_sync.records_synced = imported_employees
            current_sync.absence_records_synced = imported_absences
            current_sync.last_error = safe_error(error)
            db.session.commit()
            record_audit_event(
                org_id, "integration.sync_failed", "integration", "frappehr",
                details={"contract_version": HR_CONTRACT_VERSION, "collaborators_synced": imported_employees,
                         "absences_synced": imported_absences, "reason": current_sync.last_error},
            )
            db.session.commit()
        logger.exception("Frappe HR sync failed for organization %s", org_id)
        raise
