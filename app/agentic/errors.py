class AgenticError(Exception):
    """Base class for normalized errors in the internal agentic foundation."""


class AgenticContextError(AgenticError):
    """The authenticated server-side execution context is missing or invalid."""


class AgenticPermissionError(AgenticError):
    """The current authenticated user is not allowed to use a capability."""


class CapabilityNotFoundError(AgenticError):
    """No registered capability matches the requested identifier and version."""


class CapabilityVersionError(AgenticError):
    """The capability exists, but the requested contract version is unsupported."""


class CapabilityUnavailableError(AgenticError):
    """The capability is registered but disabled."""


class DuplicateCapabilityError(AgenticError):
    """A capability identifier and version were registered more than once."""


class CapabilityDefinitionError(AgenticError):
    """A capability contract contains invalid or inconsistent metadata."""


class CapabilityInputError(AgenticError):
    """Arguments do not conform to a capability's strict input schema."""


class CapabilityResourceNotFoundError(AgenticError):
    """A resource is absent from the caller's organization-scoped view."""


class CapabilityResultError(AgenticError):
    """A business handler returned a value outside the declared output contract."""


class CapabilityExecutionError(AgenticError):
    """A capability handler failed without exposing internal details."""


class ProposalError(AgenticError):
    """Base class for normalized action-proposal failures."""


class ProposalConfigurationError(ProposalError):
    """Proposal cryptographic configuration is missing or invalid."""


class ProposalConflictError(ProposalError):
    """An idempotency key or lifecycle state conflicts with the request."""


class ProposalNotFoundError(ProposalError):
    """The proposal is absent from the authenticated user's organization scope."""


class ProposalIntegrityError(ProposalError):
    """The stored proposal failed cryptographic integrity verification."""


class ProposalNotAllowedError(ProposalError):
    """The registered capability is not eligible for action proposals."""


class ProposalPersistenceError(ProposalError):
    """A proposal could not be persisted safely."""
