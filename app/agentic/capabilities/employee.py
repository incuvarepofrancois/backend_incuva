from datetime import date
from typing import Optional

from pydantic import Field, field_validator, model_validator

from app.agentic.contracts import CapabilityContract, ContractModel


class EmployeeListInput(ContractModel):
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class EmployeeSummary(ContractModel):
    id: int
    name: str
    role: Optional[str] = None
    team: Optional[str] = None
    status: Optional[str] = None


class EmployeeListOutput(ContractModel):
    employees: list[EmployeeSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    returned: int = Field(ge=0, le=100)
    has_next_page: bool
    coverage_complete: bool


class EmployeeGetInput(ContractModel):
    employee_id: int = Field(gt=0)


class EmployeeGetOutput(EmployeeSummary):
    external_id: Optional[str] = None
    manager_id: Optional[int] = None
    hire_date: Optional[str] = None
    data_contract_version: str


def _iso_date_or_none(value):
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("must be an ISO date string")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("must be an ISO date string") from exc
    return value


def _trim_optional(value):
    return value.strip() if isinstance(value, str) else value


class EmployeeCreateInput(ContractModel):
    name: str = Field(min_length=1, max_length=255)
    external_id: Optional[str] = Field(default=None, max_length=100)
    role: Optional[str] = Field(default=None, max_length=100)
    team: Optional[str] = Field(default=None, max_length=100)
    manager_id: Optional[int] = Field(default=None, gt=0)
    hire_date: Optional[str] = None
    status: Optional[str] = Field(default="active", max_length=50)

    @field_validator("name", "external_id", "role", "team", "status", mode="before")
    @classmethod
    def trim_strings(cls, value):
        return _trim_optional(value)

    @field_validator("hire_date")
    @classmethod
    def validate_hire_date(cls, value):
        return _iso_date_or_none(value)


class EmployeeUpdateInput(ContractModel):
    employee_id: int = Field(gt=0)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    external_id: Optional[str] = Field(default=None, max_length=100)
    role: Optional[str] = Field(default=None, max_length=100)
    team: Optional[str] = Field(default=None, max_length=100)
    manager_id: Optional[int] = Field(default=None, gt=0)
    hire_date: Optional[str] = None
    status: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name", "external_id", "role", "team", "status", mode="before")
    @classmethod
    def trim_strings(cls, value):
        return _trim_optional(value)

    @field_validator("hire_date")
    @classmethod
    def validate_hire_date(cls, value):
        return _iso_date_or_none(value)

    @model_validator(mode="after")
    def require_change(self):
        changes = self.model_fields_set - {"employee_id"}
        if not changes:
            raise ValueError("at least one employee field must be provided")
        if "name" in changes and self.name is None:
            raise ValueError("name cannot be null")
        return self


EMPLOYEE_CAPABILITIES = (
    CapabilityContract(
        name="employees.list.v1", version="v1",
        description="List employees visible to the authenticated organization.",
        domain="Employee", operation="list", access_type="read",
        required_permissions=("employees:view",), input_schema=EmployeeListInput,
        output_schema=EmployeeListOutput, requires_confirmation=False, audit_required=True,
        enabled=True,
        limitations=("Uses the existing API pagination contract; page size is capped at 100.", "No server-side search or filters are currently supported."),
    ),
    CapabilityContract(
        name="employee.get.v1", version="v1",
        description="Read an employee visible to the authenticated organization.",
        domain="Employee", operation="get", access_type="read",
        required_permissions=("employees:view",), input_schema=EmployeeGetInput,
        output_schema=EmployeeGetOutput, requires_confirmation=False, audit_required=True,
        enabled=True,
        limitations=("The employee identifier is resolved only within the authenticated organization.",),
    ),
    CapabilityContract(
        name="employees.create.v1", version="v1",
        description="Create an employee in the authenticated organization.",
        domain="Employee", operation="create", access_type="write",
        required_permissions=("employees:manage",), input_schema=EmployeeCreateInput,
        output_schema=EmployeeGetOutput, requires_confirmation=True, audit_required=True,
        enabled=True, proposable=True, risk_level="high", resource_type="employee",
        limitations=("Proposal only in A3.1; no employee row is created or changed.",),
    ),
    CapabilityContract(
        name="employees.update.v1", version="v1",
        description="Update an employee in the authenticated organization.",
        domain="Employee", operation="update", access_type="write",
        required_permissions=("employees:manage",), input_schema=EmployeeUpdateInput,
        output_schema=EmployeeGetOutput, requires_confirmation=True, audit_required=True,
        enabled=True, proposable=True, risk_level="high", resource_type="employee",
        limitations=("Proposal only in A3.1; employee changes require a later execution phase.",),
    ),
)
