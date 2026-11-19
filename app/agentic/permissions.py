from app.agentic.context import ExecutionContext
from app.agentic.contracts import CapabilityContract
from app.agentic.errors import AgenticPermissionError, CapabilityUnavailableError


def authorize_capability(context: ExecutionContext, contract: CapabilityContract) -> None:
    """Recheck each declared permission against the current database governance rules."""
    if not contract.enabled:
        raise CapabilityUnavailableError(f"Capability is disabled: {contract.name}")
    from app.governance.access import current_user_access

    for permission in contract.required_permissions:
        resource, action = permission.split(":", 1)
        user, error = current_user_access(context.org_id, resource, action)
        if error is not None or user is None or user.id != context.user_id:
            raise AgenticPermissionError(f"Permission required: {permission}")
