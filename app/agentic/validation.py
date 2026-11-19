from pydantic import ValidationError

from app.agentic.contracts import CapabilityContract, ContractModel
from app.agentic.errors import CapabilityInputError, CapabilityResultError


def validate_arguments(contract: CapabilityContract, arguments) -> ContractModel:
    if not isinstance(arguments, dict):
        raise CapabilityInputError("Capability arguments must be an object")
    try:
        return contract.input_schema.model_validate(arguments)
    except ValidationError as exc:
        raise CapabilityInputError("Capability arguments do not match the declared schema") from exc


def validate_result(contract: CapabilityContract, result) -> ContractModel:
    try:
        return contract.output_schema.model_validate(result)
    except (ValidationError, TypeError) as exc:
        raise CapabilityResultError("Capability result does not match the declared output schema") from exc
