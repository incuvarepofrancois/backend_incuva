from datetime import date
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.agentic.contracts import CapabilityContract, ContractModel, EmptyArguments


class OnboardingTaskSummary(ContractModel):
    id: int
    title: str
    status: Literal["pending", "in_progress", "completed"]


class OnboardingPlanSummary(ContractModel):
    id: int
    employee_id: int
    start_date: str
    status: str
    progress: int = Field(ge=0, le=100)
    completed_tasks: int = Field(ge=0)
    total_tasks: int = Field(ge=0)
    tasks: list[OnboardingTaskSummary]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


class OnboardingPlansOutput(ContractModel):
    plans: list[OnboardingPlanSummary]
    coverage_limit: int = 200
    returned: int = Field(ge=0, le=200)
    completeness: Literal["complete", "partial", "unknown"]


def _validate_iso_date(value):
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("must be an ISO date string")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("must be an ISO date string") from exc
    return value


class OnboardingPlanCreateInput(ContractModel):
    employee_id: int = Field(gt=0)
    start_date: Optional[str] = None

    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, value):
        return _validate_iso_date(value)


class OnboardingTaskCreateInput(ContractModel):
    plan_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    owner_type: Literal["hr", "manager", "employee"] = "manager"
    due_date: Optional[str] = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim_strings(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, value):
        return _validate_iso_date(value)

    @model_validator(mode="after")
    def require_non_empty_title(self):
        if not self.title:
            raise ValueError("title is required")
        return self


class OnboardingTaskStatusInput(ContractModel):
    task_id: int = Field(gt=0)
    status: Literal["pending", "in_progress", "completed"]


ONBOARDING_CAPABILITIES = (
    CapabilityContract(
        name="onboarding.plans.list.v1", version="v1",
        description="List onboarding plans visible to the authenticated organization.",
        domain="Onboarding", operation="list_plans", access_type="read",
        required_permissions=("onboarding:view",), input_schema=EmptyArguments,
        output_schema=OnboardingPlansOutput, requires_confirmation=False, audit_required=True,
        enabled=True,
        limitations=(
            "Mirrors GET /v1/onboarding/plans, which returns at most 200 plans and has no pagination or filter parameters.",
            "When 200 plans are returned, completeness is unknown because the endpoint may have reached its cap.",
            "This contract does not define organization-wide aggregates.",
        ),
    ),
    CapabilityContract(
        name="onboarding.plan.create.v1", version="v1",
        description="Create an onboarding plan for an employee in the authenticated organization.",
        domain="Onboarding", operation="create_plan", access_type="write",
        required_permissions=("onboarding:manage",), input_schema=OnboardingPlanCreateInput,
        output_schema=OnboardingPlanSummary, requires_confirmation=True, audit_required=True,
        enabled=True, proposable=True, risk_level="medium", resource_type="onboarding_plan",
        limitations=("Proposal only in A3.1; no plan or task rows are created.",),
    ),
    CapabilityContract(
        name="onboarding.task.create.v1", version="v1",
        description="Add a task to an onboarding plan in the authenticated organization.",
        domain="Onboarding", operation="create_task", access_type="write",
        required_permissions=("onboarding:manage",), input_schema=OnboardingTaskCreateInput,
        output_schema=OnboardingPlanSummary, requires_confirmation=True, audit_required=True,
        enabled=True, proposable=True, risk_level="medium", resource_type="onboarding_plan",
        limitations=("Proposal only in A3.1; no task rows are created.",),
    ),
    CapabilityContract(
        name="onboarding.task.status.update.v1", version="v1",
        description="Change the status of an onboarding task in the authenticated organization.",
        domain="Onboarding", operation="update_task_status", access_type="write",
        required_permissions=("onboarding:manage",), input_schema=OnboardingTaskStatusInput,
        output_schema=OnboardingPlanSummary, requires_confirmation=True, audit_required=True,
        enabled=True, proposable=True, risk_level="medium", resource_type="onboarding_task",
        limitations=("Proposal only in A3.1; no task status is changed.",),
    ),
)
