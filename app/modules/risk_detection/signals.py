import math
from datetime import datetime, timedelta

from app.utils.logger import get_logger
from app.models.signal import Signal
from app.models.employee import Employee
from app.models.employee_absence import EmployeeAbsence
from app.models.integration_sync import EmployeeExternalId, IntegrationSync
from app.data_contracts.hr_v1 import RISK_MODEL_INPUTS

logger = get_logger(__name__)
SIGNAL_WINDOW_DAYS = 90


class SignalCollector:
    def collect_from_bamboohr(self, employee_id):
        logger.info(f"Collecting BambooHR signals for employee {employee_id}")
        return []

    def collect_from_teams(self, employee_id):
        logger.info(f"Collecting Teams signals for employee {employee_id}")
        return []

    def collect_stored_signals(self, employee_id):
        """Read normalized signals captured during the last 90 days."""
        cutoff = datetime.utcnow() - timedelta(days=SIGNAL_WINDOW_DAYS)
        return [
            {
                "type": signal.type,
                "value": signal.value,
                "weight": signal.weight,
                "source": signal.source,
            }
            for signal in Signal.query.filter_by(employee_id=employee_id)
            .filter(Signal.captured_at >= cutoff)
            .order_by(Signal.captured_at.desc(), Signal.id.desc())
            .all()
            if signal.value is not None
        ]

    def normalize_signals(self, signals):
        normalized = []
        for s in signals:
            try:
                value = float(s.get("value"))
                weight = float(s.get("weight", 1.0))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or not math.isfinite(weight) or not (0 <= value <= 100) or not (weight > 0):
                continue
            normalized.append({**s, "value": value, "weight": weight})
        return normalized

    def aggregate(self, employee_id):
        raw = self.collect_stored_signals(employee_id)
        return self.normalize_signals(raw)

    def collect_canonical_context(self, employee_id):
        """Return contract coverage and non-scored absence context for the risk model."""
        employee = Employee.query.filter_by(id=employee_id).first()
        if employee is None:
            return {"contract_version": None, "domains": {}, "missing_required": ["collaborator"]}

        sync = IntegrationSync.query.filter_by(org_id=employee.org_id, integration_key="frappehr").first()
        coverage = (sync.data_coverage or {}) if sync else {}
        absence_coverage = coverage.get("absence_history") or {}
        covered_from = None
        covered_through = None
        if absence_coverage.get("complete"):
            try:
                covered_from = datetime.fromisoformat(absence_coverage["from"]).date()
                covered_through = datetime.fromisoformat(absence_coverage["through"]).date()
            except (KeyError, TypeError, ValueError):
                covered_from = covered_through = None

        today = datetime.utcnow().date()
        current_start = today - timedelta(days=90)
        previous_start = today - timedelta(days=180)
        records = []
        if covered_from and covered_from <= previous_start and covered_through and covered_through >= today - timedelta(days=1):
            records = EmployeeAbsence.query.filter_by(
                org_id=employee.org_id, employee_id=employee.id, status="approved",
            ).filter(EmployeeAbsence.start_date <= today, EmployeeAbsence.end_date >= previous_start).all()

        current_records = [row for row in records if row.start_date >= current_start]
        previous_records = [row for row in records if previous_start <= row.start_date < current_start]

        def totals(rows):
            return {
                "approved_records": len(rows),
                "reported_days": round(sum(row.duration_days or 0 for row in rows), 2),
            }

        complete = bool(covered_from and covered_from <= previous_start and covered_through and covered_through >= today - timedelta(days=1))
        return {
            "contract_version": coverage.get("contract_version"),
            "domains": {
                "collaborator": {
                    "available": True,
                    "contract_version": employee.data_contract_version,
                    "hire_date_available": employee.hire_date is not None,
                    "source_mappings": [mapping.integration_key for mapping in EmployeeExternalId.query.filter_by(org_id=employee.org_id, employee_id=employee.id).all()],
                },
                "absence_history_180d": {
                    "available": complete,
                    "source": "frappehr" if complete else None,
                    "covered_from": covered_from.isoformat() if complete else None,
                    "covered_through": covered_through.isoformat() if complete else None,
                    "last_90_days": totals(current_records) if complete else None,
                    "previous_90_days": totals(previous_records) if complete else None,
                },
            },
            "model_contract": RISK_MODEL_INPUTS,
            "missing_optional": [] if complete else ["absence_history_180d"],
            "scoring_note": "Absence data is exposed as contextual input and is not used to calculate a risk score in risk.v1.",
        }
