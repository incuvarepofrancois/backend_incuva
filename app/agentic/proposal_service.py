"""Persistent proposal creation, safe inspection, cancellation and expiry."""

import hashlib
from datetime import timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from flask import current_app

from app import db
from app.agentic.context import build_execution_context
from app.agentic.errors import (
    CapabilityInputError,
    ProposalConfigurationError,
    ProposalConflictError,
    ProposalNotAllowedError,
    ProposalNotFoundError,
    ProposalPersistenceError,
)
from app.agentic.permissions import authorize_capability
from app.agentic.proposal_crypto import (
    action_digest,
    canonical_json,
    clear_recovery_token,
    create_confirmation_token,
    idempotency_digest,
    open_recovery_token,
    open_payload,
    seal_recovery_token,
    seal_payload,
    token_digest,
    utc_now,
)
from app.agentic.registry import build_default_registry
from app.agentic.validation import validate_arguments
from app.governance.audit import record_audit_event
from app.governance.controls import evaluate_control
from app.models.agentic_action_proposal import AgenticActionProposal
from app.models.employee import Employee
from app.models.onboarding import OnboardingPlan, OnboardingTask
from app.models.organization import Organization
from app.modules.onboarding.planner import OnboardingPlanner


_registry = build_default_registry()
_STRING_LABELS = {
    "name": "Nom", "external_id": "Identifiant externe", "role": "Poste",
    "team": "Équipe", "manager_id": "Responsable", "hire_date": "Date d'embauche",
    "status": "Statut", "start_date": "Date de début", "title": "Tâche",
    "description": "Description", "owner_type": "Responsable de la tâche",
    "due_date": "Échéance",
}


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _value(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _state_digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _audit(context, event_type, proposal, *, outcome="success", details=None):
    detail_data = {
        "capability": proposal.capability_name,
        "version": proposal.contract_version,
        "snapshot_digest": proposal.snapshot_digest,
        "correlation_id": context.correlation_id if context is not None else proposal.correlation_id,
    }
    if details:
        detail_data.update(details)
    record_audit_event(
        proposal.org_id,
        event_type,
        "agentic_proposal",
        resource_id=proposal.id,
        actor_user_id=context.user_id if context is not None else None,
        outcome=outcome,
        details=detail_data,
    )


def _ttl_seconds():
    raw = current_app.config.get("AGENTIC_PROPOSAL_TTL_SECONDS", "900")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ProposalConfigurationError("Proposal expiration is misconfigured") from None
    if not 60 <= value <= 3600:
        raise ProposalConfigurationError("Proposal expiration must be between 60 and 3600 seconds")
    return value


def _employee_create_preconditions(context, arguments):
    values = arguments.model_dump(mode="json")
    manager_id = values.get("manager_id")
    if manager_id is not None and not Employee.query.filter_by(id=manager_id, org_id=context.org_id).first():
        raise CapabilityInputError("manager_id must identify an employee in this organization")
    preview_changes = [
        {"field": field, "label": _STRING_LABELS.get(field, field), "before": None, "after": value}
        for field, value in values.items()
        if value is not None
    ]
    return values, None, None, {
        "changes": preview_changes,
        "consequences": ["A new employee record will be created."],
    }


def _employee_update_preconditions(context, arguments):
    values = arguments.model_dump(mode="json", exclude_unset=True)
    employee = Employee.query.filter_by(id=values["employee_id"], org_id=context.org_id).first()
    if employee is None:
        raise CapabilityInputError("Employee not found")
    manager_id = values.get("manager_id")
    if manager_id is not None:
        if manager_id == employee.id or not Employee.query.filter_by(id=manager_id, org_id=context.org_id).first():
            raise CapabilityInputError("manager_id must identify a different employee in this organization")
    changes = []
    for field, after in values.items():
        if field == "employee_id":
            continue
        before = _value(getattr(employee, field))
        changes.append({"field": field, "label": _STRING_LABELS.get(field, field), "before": before, "after": after})
    current = {
        "id": employee.id, "name": employee.name, "external_id": employee.external_id,
        "role": employee.role, "team": employee.team, "manager_id": employee.manager_id,
        "hire_date": _value(employee.hire_date), "status": employee.status,
        "data_contract_version": employee.data_contract_version,
    }
    return values, str(employee.id), _state_digest(current), {
        "changes": changes,
        "consequences": ["The existing employee record will be updated."],
    }


def _plan_create_preconditions(context, arguments):
    values = arguments.model_dump(mode="json")
    employee = Employee.query.filter_by(id=values["employee_id"], org_id=context.org_id).first()
    if employee is None:
        raise CapabilityInputError("Employee not found")
    active = OnboardingPlan.query.filter_by(
        org_id=context.org_id, employee_id=employee.id, status="active",
    ).first()
    if active is not None:
        raise ProposalConflictError("An active onboarding plan already exists for this employee")
    if values.get("start_date") is None:
        values["start_date"] = _value(employee.hire_date) or utc_now().date().isoformat()
    from datetime import date
    start_date = date.fromisoformat(values["start_date"])
    proposed_tasks = OnboardingPlanner().build_tasks(employee.role, start_date)
    employee_state = {
        "id": employee.id, "role": employee.role, "hire_date": _value(employee.hire_date),
        "org_id": employee.org_id,
    }
    return values, None, _state_digest(employee_state), {
        "employee": {"id": employee.id, "name": employee.name},
        "changes": [{"field": "onboarding_plan", "label": "Parcours", "before": None, "after": {
            "start_date": values["start_date"],
            "proposed_task_titles": [task["title"] for task in proposed_tasks],
        }}],
        "consequences": ["A plan and its starter tasks will be created for this employee."],
    }


def _task_create_preconditions(context, arguments):
    values = arguments.model_dump(mode="json")
    plan = OnboardingPlan.query.filter_by(id=values["plan_id"], org_id=context.org_id).first()
    if plan is None:
        raise CapabilityInputError("Onboarding plan not found")
    if plan.status == "completed":
        raise ProposalConflictError("Completed plans cannot receive new tasks")
    employee = Employee.query.filter_by(id=plan.employee_id, org_id=context.org_id).first()
    if employee is None:
        raise CapabilityInputError("Onboarding plan is not linked to an employee in this organization")
    if values.get("due_date") is None:
        values["due_date"] = _value(plan.start_date)
    plan_state = {
        "id": plan.id, "org_id": plan.org_id, "employee_id": plan.employee_id,
        "status": plan.status, "start_date": _value(plan.start_date),
        "updated_at": _value(plan.updated_at), "task_count": len(plan.tasks),
    }
    return values, str(plan.id), _state_digest(plan_state), {
        "employee": {"id": employee.id, "name": employee.name},
        "changes": [{"field": "task", "label": "Nouvelle tâche", "before": None, "after": {
            "title": values["title"], "description": values["description"],
            "owner_type": values["owner_type"], "due_date": values["due_date"],
        }}],
        "consequences": ["The task will be added to the selected onboarding plan."],
    }


def _task_status_preconditions(context, arguments):
    values = arguments.model_dump(mode="json")
    task = (
        OnboardingTask.query.join(OnboardingPlan)
        .filter(OnboardingTask.id == values["task_id"], OnboardingPlan.org_id == context.org_id)
        .first()
    )
    if task is None:
        raise CapabilityInputError("Onboarding task not found")
    plan = task.plan
    state = {
        "task_id": task.id, "plan_id": plan.id, "task_status": task.status,
        "plan_status": plan.status, "plan_updated_at": _value(plan.updated_at),
    }
    return values, str(task.id), _state_digest(state), {
        "changes": [{"field": "status", "label": "Statut de la tâche", "before": task.status, "after": values["status"]}],
        "employee": {"id": plan.employee_id},
        "consequences": ["The task status and onboarding progress may change."],
    }


_PRECONDITION_HANDLERS = {
    "employees.create.v1": _employee_create_preconditions,
    "employees.update.v1": _employee_update_preconditions,
    "onboarding.plan.create.v1": _plan_create_preconditions,
    "onboarding.task.create.v1": _task_create_preconditions,
    "onboarding.task.status.update.v1": _task_status_preconditions,
}


def _response_summary(proposal, payload):
    organization = db.session.get(Organization, proposal.org_id)
    return {
        "action": proposal.capability_name,
        "description": _registry.get(proposal.capability_name, proposal.contract_version).description,
        "organization": {"id": proposal.org_id, "name": organization.name if organization else None},
        "resource": {"type": proposal.resource_type, "id": proposal.resource_id},
        "changes": payload["preview"].get("changes", []),
        "employee": payload["preview"].get("employee"),
        "consequences": payload["preview"].get("consequences", []),
    }


def _proposal_response(proposal, payload, *, confirmation_token=None, replay=False):
    return {
        "proposal_id": proposal.id,
        "capability": proposal.capability_name,
        "version": proposal.contract_version,
        "status": proposal.status,
        "created_at": _iso(proposal.created_at),
        "expires_at": _iso(proposal.expires_at),
        "summary": _response_summary(proposal, payload),
        "confirmation_token": confirmation_token,
        "token_issued": confirmation_token is not None,
        "idempotent_replay": replay,
    }


class ActionProposalService:
    """Proposal-only service; it has no business mutation or confirmation method."""

    def propose(self, name, arguments, idempotency_key, *, version=None):
        context = build_execution_context()
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise CapabilityInputError("A non-empty idempotency key of at most 128 characters is required")
        contract = _registry.get(name, version)
        if not contract.proposable or contract.access_type != "write":
            raise ProposalNotAllowedError("This capability is not eligible for action proposals")
        authorize_capability(context, contract)
        action_check = evaluate_control(
            context.org_id,
            "action_allowlist",
            {"action": contract.name},
            actor_user_id=context.user_id,
        )
        if not action_check["allowed"]:
            raise ProposalNotAllowedError("Organization policy does not allow this proposal")
        validated = validate_arguments(contract, arguments)
        if contract.name == "employees.update.v1":
            request_arguments = validated.model_dump(mode="json", exclude_unset=True)
        else:
            request_arguments = validated.model_dump(mode="json")
        request_resource_id = next((
            request_arguments[key]
            for key in ("employee_id", "plan_id", "task_id")
            if key in request_arguments
        ), None)
        key_digest = idempotency_digest(
            org_id=context.org_id, initiator_id=context.user_id, key=idempotency_key.strip(),
        )
        action_hash = action_digest(
            org_id=context.org_id,
            initiator_id=context.user_id,
            capability=contract.name,
            version=contract.version,
            arguments=request_arguments,
            resource_type=contract.resource_type,
            resource_id=str(request_resource_id) if request_resource_id is not None else None,
        )
        existing = AgenticActionProposal.query.filter_by(
            org_id=context.org_id,
            initiator_id=context.user_id,
            idempotency_key_digest=key_digest,
        ).with_for_update().first()
        if existing is not None:
            return self._replay_or_conflict(context, existing, action_hash)

        prepare = _PRECONDITION_HANDLERS.get(contract.name)
        if prepare is None:
            raise ProposalNotAllowedError("No proposal validator is registered for this capability")
        normalized_arguments, resource_id, resource_version, preview = prepare(context, validated)
        resource_id = str(resource_id) if resource_id is not None else None

        now = utc_now()
        ttl_seconds = _ttl_seconds()
        confirmation_token = create_confirmation_token()
        proposal_id = str(uuid4())
        payload = {"arguments": normalized_arguments, "preview": preview}
        proposal = AgenticActionProposal(
            id=proposal_id,
            org_id=context.org_id,
            initiator_id=context.user_id,
            capability_name=contract.name,
            contract_version=contract.version,
            canonicalization_version="incuva-json-subset-v1",
            snapshot_digest="0" * 64,
            action_digest=action_hash,
            hmac_digest="0" * 64,
            hmac_key_id="pending",
            encryption_key_id="pending",
            resource_type=contract.resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            status="PENDING_CONFIRMATION",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            updated_at=now,
            response_issued_at=now,
            confirmation_token_digest=token_digest(confirmation_token),
            idempotency_key_digest=key_digest,
            correlation_id=context.correlation_id,
        )
        seal_payload(proposal, payload)
        seal_recovery_token(proposal, confirmation_token)
        try:
            db.session.add(proposal)
            _audit(context, "PROPOSAL_CREATED", proposal)
            _audit(context, "PROPOSAL_RESPONSE_ISSUED", proposal, details={"token_issued": True})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            concurrent = AgenticActionProposal.query.filter_by(
                org_id=context.org_id,
                initiator_id=context.user_id,
                idempotency_key_digest=key_digest,
            ).first()
            if concurrent is not None:
                return self._replay_or_conflict(context, concurrent, action_hash)
            raise ProposalPersistenceError("Proposal could not be saved") from None
        except ProposalConfigurationError:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise ProposalPersistenceError("Proposal could not be saved") from exc
        return _proposal_response(proposal, payload, confirmation_token=confirmation_token)

    def get(self, proposal_id):
        context = build_execution_context()
        proposal = self._owned_proposal(context, proposal_id, lock=True)
        self._expire_if_due(context, proposal)
        payload = open_payload(proposal)
        self._audit_response(context, proposal, token_issued=False)
        db.session.commit()
        return _proposal_response(proposal, payload)

    def cancel(self, proposal_id):
        context = build_execution_context()
        proposal = self._owned_proposal(context, proposal_id, lock=True)
        if self._expire_if_due(context, proposal):
            payload = open_payload(proposal)
            self._audit_response(context, proposal, token_issued=False)
            db.session.commit()
            return _proposal_response(proposal, payload)
        if proposal.status == "CANCELLED":
            payload = open_payload(proposal)
            self._audit_response(context, proposal, token_issued=False)
            db.session.commit()
            return _proposal_response(proposal, payload)
        if proposal.status != "PENDING_CONFIRMATION":
            raise ProposalConflictError("Only pending proposals can be cancelled")
        payload = open_payload(proposal)
        now = utc_now()
        proposal.status = "CANCELLED"
        proposal.cancelled_at = now
        proposal.updated_at = now
        proposal.terminal_reason = "initiator_cancelled"
        clear_recovery_token(proposal)
        _audit(context, "PROPOSAL_CANCELLED", proposal, details={"reason": "initiator_cancelled"})
        self._audit_response(context, proposal, token_issued=False)
        db.session.commit()
        return _proposal_response(proposal, payload)

    def expire_due(self, *, batch_size=100):
        """Expire a bounded batch; safe to call from a future scheduler or CLI."""
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        now = utc_now()
        rows = (
            AgenticActionProposal.query.filter(
                AgenticActionProposal.status == "PENDING_CONFIRMATION",
                AgenticActionProposal.expires_at <= now,
            )
            .order_by(AgenticActionProposal.expires_at, AgenticActionProposal.id)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
            .all()
        )
        for proposal in rows:
            proposal.status = "EXPIRED"
            proposal.updated_at = now
            proposal.terminal_reason = "deadline_elapsed"
            clear_recovery_token(proposal)
            _audit(None, "PROPOSAL_EXPIRED", proposal, outcome="success", details={
                "reason": "deadline_elapsed", "system": True,
            })
        db.session.commit()
        return len(rows)

    def _replay_or_conflict(self, context, proposal, requested_action_digest):
        if not hmac_compare(proposal.action_digest, requested_action_digest):
            raise ProposalConflictError("Idempotency key was already used for a different action")
        if proposal.status != "PENDING_CONFIRMATION":
            raise ProposalConflictError("Idempotency key belongs to a terminal proposal; use a new key")
        if self._expire_if_due(context, proposal):
            db.session.commit()
            raise ProposalConflictError("The idempotent proposal has expired; use a new key")
        payload = open_payload(proposal)
        confirmation_token = open_recovery_token(proposal)
        self._audit_response(context, proposal, token_issued=confirmation_token is not None, replay=True)
        proposal.response_issued_at = utc_now()
        proposal.updated_at = utc_now()
        db.session.commit()
        return _proposal_response(
            proposal, payload, confirmation_token=confirmation_token, replay=True,
        )

    def _owned_proposal(self, context, proposal_id, *, lock=False):
        if not isinstance(proposal_id, str):
            raise ProposalNotFoundError("Proposal not found")
        query = AgenticActionProposal.query.filter_by(
            id=proposal_id,
            org_id=context.org_id,
            initiator_id=context.user_id,
        )
        if lock:
            query = query.with_for_update()
        proposal = query.first()
        if proposal is None:
            raise ProposalNotFoundError("Proposal not found")
        return proposal

    def _expire_if_due(self, context, proposal):
        if proposal.status != "PENDING_CONFIRMATION":
            return proposal.status == "EXPIRED"
        if _aware(proposal.expires_at) > utc_now():
            return False
        proposal.status = "EXPIRED"
        proposal.updated_at = utc_now()
        proposal.terminal_reason = "deadline_elapsed"
        clear_recovery_token(proposal)
        _audit(context, "PROPOSAL_EXPIRED", proposal, details={"reason": "deadline_elapsed"})
        return True

    @staticmethod
    def _audit_response(context, proposal, *, token_issued, replay=False):
        now = utc_now()
        proposal.response_issued_at = now
        proposal.updated_at = now
        _audit(context, "PROPOSAL_RESPONSE_ISSUED", proposal, details={
            "token_issued": bool(token_issued),
            "idempotent_replay": bool(replay),
        })


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hmac_compare(left, right):
    import hmac
    return hmac.compare_digest(left, right)


proposal_service = ActionProposalService()
