"""Controlled internal execution for the fixed A2 read-capability set."""

from dataclasses import dataclass
from types import MappingProxyType

from app import db
from app.agentic.context import ExecutionContext, build_execution_context
from app.agentic.contracts import ContractModel
from app.agentic.errors import (
    AgenticError,
    CapabilityExecutionError,
    CapabilityUnavailableError,
)
from app.agentic.handlers import (
    get_employee_handler,
    list_employees_handler,
    list_onboarding_plans_handler,
)
from app.agentic.permissions import authorize_capability
from app.agentic.registry import CapabilityRegistry, build_default_registry
from app.agentic.validation import validate_arguments, validate_result


@dataclass(frozen=True)
class CapabilityExecutionResult:
    capability: str
    version: str
    result: ContractModel

    def to_dict(self):
        return {
            "capability": self.capability,
            "version": self.version,
            "result": self.result.model_dump(mode="json"),
        }


_HANDLERS = MappingProxyType({
    ("employees.list.v1", "v1"): list_employees_handler,
    ("employee.get.v1", "v1"): get_employee_handler,
    ("onboarding.plans.list.v1", "v1"): list_onboarding_plans_handler,
})


class CapabilityExecutor:
    """Resolve contracts and run only handlers compiled into this backend module."""

    def __init__(self, registry: CapabilityRegistry | None = None):
        self._registry = registry if registry is not None else build_default_registry()

    def execute(self, name: str, arguments, *, version: str | None = None) -> CapabilityExecutionResult:
        context = build_execution_context()
        if not isinstance(name, str) or not name.strip():
            from app.agentic.errors import CapabilityNotFoundError
            raise CapabilityNotFoundError("Unknown capability")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            from app.agentic.errors import CapabilityVersionError
            raise CapabilityVersionError("Unsupported capability version")
        contract = self._registry.get(name, version)

        try:
            authorize_capability(context, contract)
            if contract.access_type != "read":
                raise CapabilityUnavailableError("Write-capability execution is disabled in A3.1")
            validated_arguments = validate_arguments(contract, arguments)
            handler = _HANDLERS.get((contract.name, contract.version))
            if handler is None:
                raise CapabilityExecutionError("No backend handler is registered for this capability")
            raw_result = handler(context, validated_arguments)
            validated_result = validate_result(contract, raw_result)
            if contract.audit_required:
                self._record_execution(context, contract, "success")
            return CapabilityExecutionResult(contract.name, contract.version, validated_result)
        except AgenticError as error:
            if contract.audit_required and not isinstance(error, CapabilityExecutionError):
                self._record_execution(context, contract, "denied", error.__class__.__name__)
            raise
        except Exception as exc:
            if contract.audit_required:
                self._record_execution(context, contract, "error", "CapabilityExecutionError")
            db.session.rollback()
            raise CapabilityExecutionError("Capability execution failed") from exc

    @staticmethod
    def _record_execution(context: ExecutionContext, contract, outcome, error_type=None):
        """Audit only execution metadata; never record arguments or HR result data."""
        from app.governance.audit import record_audit_event

        details = {
            "capability": contract.name,
            "version": contract.version,
            "correlation_id": context.correlation_id,
            "access_type": contract.access_type,
        }
        if error_type:
            details["error_type"] = error_type
        try:
            record_audit_event(
                context.org_id,
                "agentic.capability.executed",
                "agentic_capability",
                resource_id=contract.name,
                actor_user_id=context.user_id,
                outcome=outcome,
                details=details,
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise CapabilityExecutionError("Capability audit could not be recorded") from exc


_default_executor = CapabilityExecutor()


def execute_capability(name: str, arguments, *, version: str | None = None) -> CapabilityExecutionResult:
    """Execute a registered capability using the current authenticated request context."""
    return _default_executor.execute(name, arguments, version=version)
