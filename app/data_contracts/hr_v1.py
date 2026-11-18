"""INCUVA HR canonical data contract v1.

Connector adapters map vendor-specific payloads to these fields. Missing source
values stay null; adapters must not infer sensitive or unavailable values.
"""

HR_CONTRACT_VERSION = "hr.v1"
RISK_MODEL_INPUTS = {
    "model_version": "risk.v1",
    "required": ["normalized_signals"],
    "optional": ["collaborator", "absence_history_180d"],
    "absence_scoring": "disabled_until_validated",
}

COLLABORATOR_FIELDS = {
    "external_id": {"required": True, "type": "string"},
    "name": {"required": True, "type": "string"},
    "role": {"required": False, "type": "string"},
    "team": {"required": False, "type": "string"},
    "status": {"required": False, "type": "string"},
    "hire_date": {"required": False, "type": "date"},
}

ABSENCE_FIELDS = {
    "external_id": {"required": True, "type": "string"},
    "employee_external_id": {"required": True, "type": "string"},
    "start_date": {"required": True, "type": "date"},
    "end_date": {"required": True, "type": "date"},
    "status": {"required": True, "type": "enum", "values": ("approved", "pending", "rejected", "cancelled", "unknown")},
    "duration_days": {"required": False, "type": "number"},
    "is_half_day": {"required": False, "type": "boolean"},
}


def normalize_absence_status(value):
    status = str(value or "").strip().casefold()
    if status in {"approved", "accepted", "validé", "validee", "approuvée"}:
        return "approved"
    if status in {"open", "pending", "submitted", "en attente", "requested"}:
        return "pending"
    if status in {"rejected", "refused", "refusé", "refusée"}:
        return "rejected"
    if status in {"cancelled", "canceled", "annulé", "annulée"}:
        return "cancelled"
    return "unknown"
