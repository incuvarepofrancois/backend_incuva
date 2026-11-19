from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agentic.capabilities.employee import EmployeeListInput
from app.agentic.contracts import CapabilityContract, ContractModel
from app.agentic.errors import (
    CapabilityDefinitionError,
    CapabilityInputError,
    CapabilityNotFoundError,
    CapabilityResultError,
    CapabilityUnavailableError,
    CapabilityVersionError,
    DuplicateCapabilityError,
)
from app.agentic.registry import CapabilityRegistry, build_default_registry
from app.agentic.contracts import EmptyArguments
from app.agentic.permissions import authorize_capability
from app.agentic.validation import validate_arguments, validate_result


class SampleOutput(ContractModel):
    value: str


def sample_contract(**overrides):
    values = {
        "name": "sample.read.v1",
        "version": "v1",
        "description": "Read a sample resource.",
        "domain": "Sample",
        "operation": "read_sample",
        "access_type": "read",
        "required_permissions": ("employees:view",),
        "input_schema": EmptyArguments,
        "output_schema": SampleOutput,
        "requires_confirmation": False,
        "audit_required": True,
        "enabled": True,
        "limitations": (),
    }
    values.update(overrides)
    return CapabilityContract(**values)


def test_valid_contract_and_metadata_are_typed_and_complete():
    registry = build_default_registry()
    contract = registry.get("employees.list.v1", "v1")
    metadata = contract.metadata()

    assert contract.access_type == "read"
    assert metadata["required_permissions"] == ["employees:view"]
    assert metadata["input_schema"]["additionalProperties"] is False
    assert metadata["audit_required"] is True
    assert metadata["limitations"]


def test_invalid_contract_definitions_are_rejected():
    with pytest.raises(CapabilityDefinitionError):
        sample_contract(name=" ")
    with pytest.raises(CapabilityDefinitionError):
        sample_contract(version="v2")
    with pytest.raises(CapabilityDefinitionError):
        sample_contract(required_permissions=())


def test_registry_returns_new_instance_and_rejects_duplicates_or_unknown_versions():
    contract = sample_contract()
    empty = CapabilityRegistry()
    populated = empty.register(contract)

    assert len(empty) == 0
    assert populated.get("sample.read.v1", "v1") is contract
    with pytest.raises(DuplicateCapabilityError):
        populated.register(contract)
    with pytest.raises(CapabilityNotFoundError):
        populated.get("not.registered.v1")
    with pytest.raises(CapabilityVersionError):
        populated.get("sample.read.v1", "v2")
    with pytest.raises(CapabilityNotFoundError):
        populated.get("sample.unknown.v1", "v1")


def test_registry_lists_enabled_capabilities_by_domain_and_access_category():
    registry = build_default_registry()
    assert {item.name for item in registry.list(domain="Employee")} == {
        "employees.list.v1", "employee.get.v1", "employees.create.v1", "employees.update.v1",
    }
    assert all(item.access_type == "read" for item in registry.list(category="read"))
    assert {item.name for item in registry.list(category="write")} == {
        "employees.create.v1", "employees.update.v1", "onboarding.plan.create.v1",
        "onboarding.task.create.v1", "onboarding.task.status.update.v1",
    }
    disabled = CapabilityRegistry((sample_contract(enabled=False),))
    assert disabled.list() == ()
    assert len(disabled.list(enabled_only=False)) == 1
    with pytest.raises(CapabilityUnavailableError):
        authorize_capability(None, disabled.get("sample.read.v1", "v1"))


def test_registered_contracts_are_read_only_and_have_expected_scope():
    registry = build_default_registry()
    contracts = registry.list(category="read")
    assert {item.name for item in contracts} == {
        "employees.list.v1", "employee.get.v1", "onboarding.plans.list.v1",
    }
    assert all(item.access_type == "read" for item in contracts)
    assert all(not item.requires_confirmation for item in contracts)
    proposable = registry.list(category="write")
    assert {item.name for item in proposable} == {
        "employees.create.v1", "employees.update.v1", "onboarding.plan.create.v1",
        "onboarding.task.create.v1", "onboarding.task.status.update.v1",
    }
    assert all(item.proposable and item.requires_confirmation for item in proposable)
    onboarding = registry.get("onboarding.plans.list.v1", "v1")
    assert onboarding.required_permissions == ("onboarding:view",)
    assert any("200" in limitation for limitation in onboarding.limitations)


def test_employee_list_contract_enforces_api_pagination_bounds():
    assert EmployeeListInput.model_validate({"page": 2, "per_page": 100}).page == 2
    for arguments in ({"page": 0}, {"per_page": 101}, {"per_page": "10"}, {"org_id": 8}):
        with pytest.raises(ValidationError):
            EmployeeListInput.model_validate(arguments)


def test_validation_rejects_missing_wrong_extra_and_tenant_or_permission_arguments():
    contract = sample_contract(input_schema=EmployeeListInput)
    with pytest.raises(CapabilityInputError):
        validate_arguments(contract, {"page": 1, "per_page": 20, "org_id": 99})
    with pytest.raises(CapabilityInputError):
        validate_arguments(contract, {"page": "1"})
    with pytest.raises(CapabilityInputError):
        validate_arguments(contract, {"permission": "employees:manage"})


def test_onboarding_arguments_are_empty_and_reject_registry_or_context_injection():
    contract = build_default_registry().get("onboarding.plans.list.v1", "v1")
    assert validate_arguments(contract, {}).model_dump() == {}
    for injected in ({"org_id": 999}, {"permissions": ["onboarding:manage"]}, {"name": "fake.v1"}):
        with pytest.raises(CapabilityInputError):
            validate_arguments(contract, injected)


def test_output_validation_rejects_results_outside_the_contract():
    with pytest.raises(CapabilityResultError):
        validate_result(sample_contract(), {"unexpected": "value"})
