from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.agentic.errors import ProposalConfigurationError, ProposalIntegrityError
from app.agentic.proposal_crypto import (
    CANONICALIZATION_VERSION,
    action_digest,
    canonical_json,
    create_confirmation_token,
    clear_recovery_token,
    open_payload,
    open_recovery_token,
    seal_payload,
    seal_recovery_token,
    token_digest,
    validate_key_configuration,
)


def _key():
    import base64
    import secrets
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture
def crypto_app():
    from app import create_app

    app = create_app("testing")
    app.config.update({
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID": "enc-v1",
        "AGENTIC_PROPOSAL_ENCRYPTION_KEY": _key(),
        "AGENTIC_PROPOSAL_HMAC_KEY_ID": "hmac-v1",
        "AGENTIC_PROPOSAL_HMAC_KEY": _key(),
        "AGENTIC_PROPOSAL_RECOVERY_KEY_ID": "recovery-v1",
        "AGENTIC_PROPOSAL_RECOVERY_KEY": _key(),
    })
    return app


def _proposal():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=str(uuid4()), org_id=41, initiator_id=52,
        capability_name="employees.update.v1", contract_version="v1",
        resource_type="employee", resource_id="77", resource_version="a" * 64,
        created_at=now, expires_at=now + timedelta(minutes=15),
        status="PENDING_CONFIRMATION",
        confirmation_token_digest="b" * 64, idempotency_key_digest="c" * 64,
        action_digest="d" * 64, canonicalization_version=CANONICALIZATION_VERSION,
        hmac_key_id="pending", encryption_key_id="pending",
        snapshot_digest="0" * 64, hmac_digest="0" * 64,
        encryption_nonce=b"", payload_ciphertext=b"",
    )


def test_canonicalization_is_deterministic_and_rejects_unsupported_numbers():
    left = {"name": "Zoé", "changes": {"team": "People", "status": "active"}}
    right = {"changes": {"status": "active", "team": "People"}, "name": "Zoé"}
    assert canonical_json(left) == canonical_json(right)
    assert action_digest(
        org_id=1, initiator_id=2, capability="x.write.v1", version="v1",
        arguments={"value": 1}, resource_type="x", resource_id="1",
    ) != action_digest(
        org_id=1, initiator_id=2, capability="x.write.v1", version="v1",
        arguments={"value": 2}, resource_type="x", resource_id="1",
    )
    with pytest.raises(ProposalIntegrityError):
        canonical_json({"amount": 0.1})


def test_canonicalization_type_boundaries_dates_and_equivalence_rules():
    from pydantic import ValidationError

    from app.agentic.capabilities.onboarding import OnboardingPlanCreateInput

    assert canonical_json({"value": True}) == b'{"value":true}'
    assert canonical_json({"value": 1}) == b'{"value":1}'
    assert canonical_json({"value": None}) == b'{"value":null}'
    assert canonical_json({"items": [1, {"inner": [False, None]}]}) == (
        b'{"items":[1,{"inner":[false,null]}]}'
    )
    assert action_digest(
        org_id=1, initiator_id=2, capability="x.write.v1", version="v1",
        arguments={"value": True}, resource_type="x", resource_id="1",
    ) != action_digest(
        org_id=1, initiator_id=2, capability="x.write.v1", version="v1",
        arguments={"value": 1}, resource_type="x", resource_id="1",
    )
    assert canonical_json({"value": None}) != canonical_json({})
    assert OnboardingPlanCreateInput.model_validate({
        "employee_id": 7, "start_date": "20261119",
    }).model_dump(mode="json") == OnboardingPlanCreateInput.model_validate({
        "employee_id": 7, "start_date": "2026-11-19",
    }).model_dump(mode="json")
    with pytest.raises(ValidationError):
        OnboardingPlanCreateInput.model_validate({"employee_id": 7, "unknown": "field"})
    with pytest.raises(ValidationError):
        OnboardingPlanCreateInput.model_validate({"employee_id": 7, "start_date": "2026-11-19T00:00:00Z"})
    assert canonical_json({"name": "Zo\u00e9"}) != canonical_json({"name": "Zoe\u0301"})
    assert canonical_json({"start_date": "2026-11-19"}) == canonical_json({"start_date": "2026-11-19"})
    for value in (0.0, float("inf"), float("nan")):
        with pytest.raises(ProposalIntegrityError):
            canonical_json({"amount": value})
    with pytest.raises(ProposalIntegrityError):
        canonical_json({"when": datetime(2026, 11, 19, tzinfo=timezone.utc)})
    from decimal import Decimal
    with pytest.raises(ProposalIntegrityError):
        canonical_json({"amount": Decimal("1.25")})


def test_recovery_token_is_independently_encrypted_scoped_and_clearable(crypto_app):
    proposal = _proposal()
    token = create_confirmation_token()
    proposal.confirmation_token_digest = token_digest(token)
    with crypto_app.app_context():
        seal_recovery_token(proposal, token)
        assert token.encode("ascii") not in proposal.confirmation_token_recovery_ciphertext
        assert open_recovery_token(proposal) == token

        proposal.initiator_id += 1
        with pytest.raises(ProposalIntegrityError):
            open_recovery_token(proposal)
        proposal.initiator_id -= 1

        proposal.status = "CANCELLED"
        assert open_recovery_token(proposal) is None
        clear_recovery_token(proposal)
        assert proposal.confirmation_token_recovery_ciphertext is None


def test_recovery_key_rotation_requires_fallback_and_revocation_fails_closed(crypto_app):
    proposal = _proposal()
    token = create_confirmation_token()
    proposal.confirmation_token_digest = token_digest(token)
    old_key = crypto_app.config["AGENTIC_PROPOSAL_RECOVERY_KEY"]
    with crypto_app.app_context():
        seal_recovery_token(proposal, token)
        crypto_app.config.update({
            "AGENTIC_PROPOSAL_RECOVERY_KEY_ID": "recovery-v2",
            "AGENTIC_PROPOSAL_RECOVERY_KEY": _key(),
            "AGENTIC_PROPOSAL_RECOVERY_FALLBACK_KEYS": f"recovery-v1={old_key}",
        })
        assert open_recovery_token(proposal) == token
        crypto_app.config["AGENTIC_PROPOSAL_RECOVERY_FALLBACK_KEYS"] = ""
        with pytest.raises(ProposalIntegrityError):
            open_recovery_token(proposal)


def test_sealing_encrypts_payload_and_verifies_hmac(crypto_app):
    proposal = _proposal()
    payload = {"arguments": {"name": "Sensitive name"}, "preview": {"after": "Sensitive name"}}
    with crypto_app.app_context():
        seal_payload(proposal, payload)
        assert b"Sensitive name" not in proposal.payload_ciphertext
        assert open_payload(proposal) == payload


def test_ciphertext_and_signed_metadata_tampering_are_rejected(crypto_app):
    proposal = _proposal()
    with crypto_app.app_context():
        seal_payload(proposal, {"arguments": {"name": "Sensitive"}, "preview": {}})
        proposal.payload_ciphertext = proposal.payload_ciphertext[:-1] + bytes([proposal.payload_ciphertext[-1] ^ 1])
        with pytest.raises(ProposalIntegrityError):
            open_payload(proposal)

        proposal = _proposal()
        seal_payload(proposal, {"arguments": {"name": "Sensitive"}, "preview": {}})
        proposal.resource_id = "another-resource"
        with pytest.raises(ProposalIntegrityError):
            open_payload(proposal)


def test_confirmation_token_is_256_bit_opaque_and_only_digest_is_reusable(crypto_app):
    token = create_confirmation_token()
    assert len(token) >= 43
    assert token_digest(token) != token


def test_missing_keys_fail_closed_and_old_key_versions_can_verify(crypto_app):
    proposal = _proposal()
    old_encryption = crypto_app.config["AGENTIC_PROPOSAL_ENCRYPTION_KEY"]
    old_hmac = crypto_app.config["AGENTIC_PROPOSAL_HMAC_KEY"]
    with crypto_app.app_context():
        seal_payload(proposal, {"arguments": {"name": "Sensitive"}, "preview": {}})
        crypto_app.config.update({
            "AGENTIC_PROPOSAL_ENCRYPTION_KEY_ID": "enc-v2",
            "AGENTIC_PROPOSAL_ENCRYPTION_KEY": _key(),
            "AGENTIC_PROPOSAL_ENCRYPTION_FALLBACK_KEYS": f"enc-v1={old_encryption}",
            "AGENTIC_PROPOSAL_HMAC_KEY_ID": "hmac-v2",
            "AGENTIC_PROPOSAL_HMAC_KEY": _key(),
            "AGENTIC_PROPOSAL_HMAC_FALLBACK_KEYS": f"hmac-v1={old_hmac}",
        })
        assert open_payload(proposal)["arguments"]["name"] == "Sensitive"
        crypto_app.config["AGENTIC_PROPOSAL_HMAC_KEY"] = ""
        with pytest.raises(ProposalConfigurationError):
            validate_key_configuration()
