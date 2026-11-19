from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.agentic.errors import (
    AgenticContextError,
    AgenticError,
    AgenticPermissionError,
    CapabilityInputError,
    CapabilityNotFoundError,
    CapabilityResourceNotFoundError,
    CapabilityUnavailableError,
    CapabilityVersionError,
    ProposalConfigurationError,
    ProposalConflictError,
    ProposalIntegrityError,
    ProposalNotAllowedError,
    ProposalNotFoundError,
    ProposalPersistenceError,
)
from app.agentic.proposal_service import proposal_service
from app.middleware.rate_limiter import rate_limit


agentic_proposals_bp = Blueprint("agentic_proposals", __name__)


def _error_response(error):
    if isinstance(error, (AgenticContextError, AgenticPermissionError, ProposalNotAllowedError)):
        status = 403
    elif isinstance(error, (CapabilityNotFoundError, CapabilityResourceNotFoundError, ProposalNotFoundError)):
        status = 404
    elif isinstance(error, (CapabilityUnavailableError, ProposalConflictError)):
        status = 409
    elif isinstance(error, CapabilityVersionError):
        status = 400
    elif isinstance(error, (CapabilityInputError,)):
        status = 400
    elif isinstance(error, ProposalConfigurationError):
        status = 503
    elif isinstance(error, (ProposalIntegrityError, ProposalPersistenceError)):
        status = 500
    else:
        status = 400
    public_message = str(error)
    if isinstance(error, (ProposalIntegrityError, ProposalPersistenceError, ProposalConfigurationError)):
        public_message = "Action proposal service is unavailable or could not validate the proposal"
    return jsonify({"error": public_message, "error_type": error.__class__.__name__}), status


@agentic_proposals_bp.post("/agentic/proposals")
@jwt_required()
@rate_limit(max_requests=10, window_seconds=60)
def create_proposal():
    payload = request.get_json(silent=True)
    allowed = {"capability", "version", "arguments", "idempotency_key"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        return jsonify({"error": "A JSON object with only proposal fields is required"}), 400
    capability = payload.get("capability")
    version = payload.get("version")
    arguments = payload.get("arguments")
    idempotency_key = payload.get("idempotency_key")
    if not isinstance(capability, str) or not capability.strip():
        return jsonify({"error": "capability is required"}), 400
    if version is not None and not isinstance(version, str):
        return jsonify({"error": "version must be a string"}), 400
    if not isinstance(arguments, dict):
        return jsonify({"error": "arguments must be a JSON object"}), 400
    if not isinstance(idempotency_key, str):
        return jsonify({"error": "idempotency_key is required"}), 400
    try:
        result = proposal_service.propose(
            capability,
            arguments,
            idempotency_key,
            version=version,
        )
    except AgenticError as error:
        return _error_response(error)
    except Exception:
        return jsonify({"error": "Action proposal could not be created"}), 500
    response = jsonify(result)
    response.status_code = 200 if result.get("idempotent_replay") else 201
    return response


@agentic_proposals_bp.get("/agentic/proposals/<proposal_id>")
@jwt_required()
@rate_limit(max_requests=60, window_seconds=60)
def get_proposal(proposal_id):
    try:
        return jsonify(proposal_service.get(proposal_id)), 200
    except AgenticError as error:
        return _error_response(error)
    except Exception:
        return jsonify({"error": "Action proposal could not be retrieved"}), 500


@agentic_proposals_bp.post("/agentic/proposals/<proposal_id>/cancel")
@jwt_required()
@rate_limit(max_requests=20, window_seconds=60)
def cancel_proposal(proposal_id):
    payload = request.get_json(silent=True)
    if payload not in (None, {}):
        return jsonify({"error": "Cancellation does not accept action arguments"}), 400
    try:
        return jsonify(proposal_service.cancel(proposal_id)), 200
    except AgenticError as error:
        return _error_response(error)
    except Exception:
        return jsonify({"error": "Action proposal could not be cancelled"}), 500
