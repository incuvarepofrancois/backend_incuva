from dataclasses import dataclass
from typing import Literal, Optional, Type

from pydantic import BaseModel, ConfigDict

from app.agentic.errors import CapabilityDefinitionError


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EmptyArguments(ContractModel):
    pass


AccessType = Literal["read", "write"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class CapabilityContract:
    name: str
    version: str
    description: str
    domain: str
    operation: str
    access_type: AccessType
    required_permissions: tuple[str, ...]
    input_schema: Type[ContractModel]
    output_schema: Type[ContractModel]
    requires_confirmation: bool
    audit_required: bool
    enabled: bool
    limitations: tuple[str, ...] = ()
    proposable: bool = False
    risk_level: RiskLevel = "low"
    resource_type: Optional[str] = None

    def __post_init__(self):
        if not self.name or self.name.strip() != self.name:
            raise CapabilityDefinitionError("Capability name must be a non-empty stable identifier")
        if not self.version or self.version.strip() != self.version:
            raise CapabilityDefinitionError("Capability version is required")
        if not self.name.endswith(f".{self.version}"):
            raise CapabilityDefinitionError("Capability name must end with its declared version")
        for field_name in ("description", "domain", "operation"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise CapabilityDefinitionError(f"{field_name} is required")
        if self.access_type not in {"read", "write"}:
            raise CapabilityDefinitionError("access_type must be read or write")
        if not isinstance(self.required_permissions, tuple) or not self.required_permissions:
            raise CapabilityDefinitionError("At least one required permission must be declared")
        if any(
            not isinstance(item, str)
            or item.count(":") != 1
            or not all(part.strip() for part in item.split(":"))
            for item in self.required_permissions
        ):
            raise CapabilityDefinitionError("Permissions must use the resource:action format")
        if not isinstance(self.input_schema, type) or not issubclass(self.input_schema, ContractModel):
            raise CapabilityDefinitionError("input_schema must be a ContractModel")
        if not isinstance(self.output_schema, type) or not issubclass(self.output_schema, ContractModel):
            raise CapabilityDefinitionError("output_schema must be a ContractModel")
        if not all(isinstance(value, bool) for value in (self.requires_confirmation, self.audit_required, self.enabled)):
            raise CapabilityDefinitionError("Capability flags must be booleans")
        if not isinstance(self.proposable, bool):
            raise CapabilityDefinitionError("proposable must be a boolean")
        if self.proposable and (self.access_type != "write" or not self.requires_confirmation):
            raise CapabilityDefinitionError("Only confirmation-required write capabilities may be proposable")
        if self.risk_level not in {"low", "medium", "high"}:
            raise CapabilityDefinitionError("risk_level must be low, medium, or high")
        if self.proposable and (not isinstance(self.resource_type, str) or not self.resource_type.strip()):
            raise CapabilityDefinitionError("A proposable capability must declare a resource_type")
        if not isinstance(self.limitations, tuple) or any(not isinstance(item, str) for item in self.limitations):
            raise CapabilityDefinitionError("limitations must be a tuple of strings")

    @property
    def category(self) -> AccessType:
        return self.access_type

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "domain": self.domain,
            "operation": self.operation,
            "access_type": self.access_type,
            "required_permissions": list(self.required_permissions),
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": self.output_schema.model_json_schema(),
            "requires_confirmation": self.requires_confirmation,
            "audit_required": self.audit_required,
            "enabled": self.enabled,
            "limitations": list(self.limitations),
            "proposable": self.proposable,
            "risk_level": self.risk_level,
            "resource_type": self.resource_type,
        }
