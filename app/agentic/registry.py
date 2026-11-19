from types import MappingProxyType

from app.agentic.contracts import CapabilityContract, ContractModel, EmptyArguments
from app.agentic.errors import (
    CapabilityDefinitionError,
    CapabilityNotFoundError,
    CapabilityVersionError,
    DuplicateCapabilityError,
)


class CapabilityRegistry:
    """In-memory declarative registry. It stores schemas and metadata, never handlers."""

    def __init__(self, capabilities=()):
        items = {}
        for capability in capabilities:
            if not isinstance(capability, CapabilityContract):
                raise CapabilityDefinitionError("Only CapabilityContract definitions may be registered")
            key = (capability.name, capability.version)
            if key in items:
                raise DuplicateCapabilityError(f"Capability already registered: {capability.name}")
            items[key] = capability
        self._items = MappingProxyType(items)

    def register(self, capability):
        """Validate and return a new registry; existing registries stay immutable."""
        if not isinstance(capability, CapabilityContract):
            raise CapabilityDefinitionError("Only CapabilityContract definitions may be registered")
        key = (capability.name, capability.version)
        if key in self._items:
            raise DuplicateCapabilityError(f"Capability already registered: {capability.name}")
        return CapabilityRegistry((*self._items.values(), capability))

    def get(self, name, version=None):
        if version is not None:
            capability = self._items.get((name, version))
            if capability is None and any(item_name == name for item_name, _ in self._items):
                raise CapabilityVersionError(f"Unsupported capability version: {name} {version}")
        else:
            matches = [item for (item_name, _), item in self._items.items() if item_name == name]
            capability = matches[0] if len(matches) == 1 else None
        if capability is None:
            raise CapabilityNotFoundError(f"Unknown capability: {name}")
        return capability

    def list(self, *, enabled_only=True, category=None, domain=None):
        capabilities = self._items.values()
        if enabled_only:
            capabilities = (item for item in capabilities if item.enabled)
        if category is not None:
            capabilities = (item for item in capabilities if item.category == category)
        if domain is not None:
            capabilities = (item for item in capabilities if item.domain == domain)
        return tuple(sorted(capabilities, key=lambda item: (item.name, item.version)))

    def metadata(self, *, enabled_only=True):
        return tuple(item.metadata() for item in self.list(enabled_only=enabled_only))

    def __len__(self):
        return len(self._items)


from app.agentic.capabilities.employee import EMPLOYEE_CAPABILITIES
from app.agentic.capabilities.onboarding import ONBOARDING_CAPABILITIES


def build_default_registry():
    return CapabilityRegistry((*EMPLOYEE_CAPABILITIES, *ONBOARDING_CAPABILITIES))
