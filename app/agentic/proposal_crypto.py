"""Versioned canonicalization, AES-GCM confidentiality and HMAC integrity."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app

from app.agentic.errors import ProposalConfigurationError, ProposalIntegrityError


CANONICALIZATION_VERSION = "incuva-json-subset-v1"
_HMAC_DOMAIN = b"INCUVA-ACTION-PROPOSAL\x00v1\x00"
_MAX_SAFE_INTEGER = (2 ** 53) - 1


def _validate_json_value(value):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ProposalIntegrityError("A JSON integer is outside the canonical format range")
        return
    if isinstance(value, float):
        raise ProposalIntegrityError("Floating point values are not supported by this canonical format")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ProposalIntegrityError("Canonical object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    raise ProposalIntegrityError("Value is outside the supported canonical JSON domain")


def canonical_json(value) -> bytes:
    """Serialize the strict contract domain deterministically with sorted ASCII keys.

    A3.1 contracts contain only null, booleans, strings, safe integers, arrays and
    string-keyed objects. Dates are normalized by their contract validators to ISO
    strings; floats and arbitrary Python objects are deliberately rejected.
    """
    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProposalIntegrityError("Value cannot be canonicalized") from exc


def _decode_key(value, *, expected_bytes, label):
    if not isinstance(value, str) or not value:
        raise ProposalConfigurationError(f"{label} is not configured")
    try:
        raw = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, TypeError, UnicodeEncodeError):
        raise ProposalConfigurationError(f"{label} has an invalid base64 value") from None
    if len(raw) != expected_bytes:
        raise ProposalConfigurationError(f"{label} has an invalid key length")
    return raw


def _parse_fallbacks(value, *, expected_bytes, label):
    keys = {}
    if not value:
        return keys
    if not isinstance(value, str):
        raise ProposalConfigurationError(f"{label} fallback key list is invalid")
    for entry in value.split(","):
        key_id, separator, encoded = entry.strip().partition("=")
        if not separator or not key_id or not encoded or key_id in keys:
            raise ProposalConfigurationError(f"{label} fallback key list is invalid")
        keys[key_id] = _decode_key(encoded, expected_bytes=expected_bytes, label=label)
    return keys


def _keyring(kind):
    prefix = f"AGENTIC_PROPOSAL_{kind}"
    key_id = current_app.config.get(f"{prefix}_KEY_ID", "")
    current_value = current_app.config.get(f"{prefix}_KEY", "")
    fallback_value = current_app.config.get(f"{prefix}_FALLBACK_KEYS", "")
    if not isinstance(key_id, str) or not key_id.strip():
        raise ProposalConfigurationError(f"{kind} key ID is not configured")
    size = 32
    keys = _parse_fallbacks(fallback_value, expected_bytes=size, label=kind)
    if key_id in keys:
        raise ProposalConfigurationError(f"{kind} current key ID is duplicated")
    keys[key_id] = _decode_key(current_value, expected_bytes=size, label=kind)
    return key_id, keys


def validate_key_configuration():
    encryption_id, encryption_keys = _keyring("ENCRYPTION")
    hmac_id, hmac_keys = _keyring("HMAC")
    if set(encryption_keys.values()) & set(hmac_keys.values()):
        raise ProposalConfigurationError("Encryption and HMAC keys must be independent")
    return encryption_id, encryption_keys, hmac_id, hmac_keys


def _recovery_aad(proposal, key_id):
    return canonical_json({
        "protocol": "incuva.action-proposal-token-recovery",
        "version": 1,
        "proposal_id": proposal.id,
        "org_id": proposal.org_id,
        "initiator_id": proposal.initiator_id,
        "idempotency_key_digest": proposal.idempotency_key_digest,
        "confirmation_token_digest": proposal.confirmation_token_digest,
        "expires_at": _utc_iso(proposal.expires_at),
        "recovery_key_id": key_id,
    })


def validate_recovery_key_configuration():
    recovery_id, recovery_keys = _keyring("RECOVERY")
    _enc_id, encryption_keys, _hmac_id, hmac_keys = validate_key_configuration()
    if set(recovery_keys.values()) & (set(encryption_keys.values()) | set(hmac_keys.values())):
        raise ProposalConfigurationError("Recovery, encryption and HMAC keys must be independent")
    return recovery_id, recovery_keys


def seal_recovery_token(proposal, token):
    recovery_id, recovery_keys = validate_recovery_key_configuration()
    nonce = secrets.token_bytes(12)
    try:
        ciphertext = AESGCM(recovery_keys[recovery_id]).encrypt(
            nonce, token.encode("ascii"), _recovery_aad(proposal, recovery_id),
        )
    except Exception as exc:
        raise ProposalConfigurationError("Proposal token recovery encryption failed") from exc
    proposal.confirmation_token_recovery_ciphertext = ciphertext
    proposal.confirmation_token_recovery_nonce = nonce
    proposal.confirmation_token_recovery_key_id = recovery_id


def open_recovery_token(proposal):
    if proposal.confirmation_token_recovery_ciphertext is None:
        return None
    if proposal.status != "PENDING_CONFIRMATION" or _utc_iso(proposal.expires_at) <= _utc_iso(utc_now()):
        return None
    _recovery_id, recovery_keys = validate_recovery_key_configuration()
    recovery_key = recovery_keys.get(proposal.confirmation_token_recovery_key_id)
    if recovery_key is None:
        raise ProposalIntegrityError("Proposal token recovery key version is unavailable")
    try:
        token = AESGCM(recovery_key).decrypt(
            proposal.confirmation_token_recovery_nonce,
            proposal.confirmation_token_recovery_ciphertext,
            _recovery_aad(proposal, proposal.confirmation_token_recovery_key_id),
        ).decode("ascii")
    except (InvalidTag, ValueError, TypeError, UnicodeDecodeError):
        raise ProposalIntegrityError("Proposal token recovery authentication failed") from None
    if not hmac.compare_digest(token_digest(token), proposal.confirmation_token_digest):
        raise ProposalIntegrityError("Proposal token recovery integrity verification failed")
    return token


def clear_recovery_token(proposal):
    proposal.confirmation_token_recovery_ciphertext = None
    proposal.confirmation_token_recovery_nonce = None
    proposal.confirmation_token_recovery_key_id = None


def _utc_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def proposal_envelope(proposal):
    return {
        "protocol": "incuva.action-proposal",
        "canonicalization_version": proposal.canonicalization_version,
        "proposal_id": proposal.id,
        "org_id": proposal.org_id,
        "initiator_id": proposal.initiator_id,
        "capability": proposal.capability_name,
        "contract_version": proposal.contract_version,
        "hmac_key_id": proposal.hmac_key_id,
        "encryption_key_id": proposal.encryption_key_id,
        "resource_type": proposal.resource_type,
        "resource_id": proposal.resource_id,
        "resource_version": proposal.resource_version,
        "created_at": _utc_iso(proposal.created_at),
        "expires_at": _utc_iso(proposal.expires_at),
        "confirmation_nonce_digest": proposal.confirmation_token_digest,
        "idempotency_key_digest": proposal.idempotency_key_digest,
        "action_digest": proposal.action_digest,
        "snapshot_digest": proposal.snapshot_digest,
    }


def action_digest(*, org_id, initiator_id, capability, version, arguments, resource_type, resource_id):
    value = {
        "protocol": "incuva.action-idempotency",
        "org_id": org_id,
        "initiator_id": initiator_id,
        "capability": capability,
        "version": version,
        "arguments": arguments,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    return hashlib.sha256(canonical_json(value)).hexdigest()


def idempotency_digest(*, org_id, initiator_id, key):
    return hashlib.sha256(canonical_json({
        "scope": "incuva.proposal.idempotency",
        "org_id": org_id,
        "initiator_id": initiator_id,
        "key": key,
    })).hexdigest()


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_confirmation_token():
    # token_urlsafe(32) uses 32 random bytes (256 bits) and is returned only once.
    return secrets.token_urlsafe(32)


def _hmac(key, envelope_bytes):
    return hmac.new(key, _HMAC_DOMAIN + envelope_bytes, hashlib.sha256).hexdigest()


def seal_payload(proposal, payload):
    encryption_id, encryption_keys, hmac_id, hmac_keys = validate_key_configuration()
    plaintext = canonical_json(payload)
    proposal.canonicalization_version = CANONICALIZATION_VERSION
    proposal.snapshot_digest = hashlib.sha256(plaintext).hexdigest()
    proposal.encryption_key_id = encryption_id
    proposal.hmac_key_id = hmac_id
    aad = canonical_json(proposal_envelope(proposal))
    nonce = secrets.token_bytes(12)
    try:
        ciphertext = AESGCM(encryption_keys[encryption_id]).encrypt(nonce, plaintext, aad)
    except Exception as exc:
        raise ProposalConfigurationError("Proposal encryption failed") from exc
    proposal.encryption_nonce = nonce
    proposal.payload_ciphertext = ciphertext
    proposal.hmac_digest = _hmac(hmac_keys[hmac_id], aad)


def open_payload(proposal):
    _current_encryption_id, encryption_keys, _current_hmac_id, hmac_keys = validate_key_configuration()
    hmac_key = hmac_keys.get(proposal.hmac_key_id)
    encryption_key = encryption_keys.get(proposal.encryption_key_id)
    if hmac_key is None or encryption_key is None:
        raise ProposalIntegrityError("Proposal cryptographic key version is unavailable")
    aad = canonical_json(proposal_envelope(proposal))
    expected_hmac = _hmac(hmac_key, aad)
    if not hmac.compare_digest(expected_hmac, proposal.hmac_digest):
        raise ProposalIntegrityError("Proposal integrity verification failed")
    try:
        plaintext = AESGCM(encryption_key).decrypt(proposal.encryption_nonce, proposal.payload_ciphertext, aad)
    except (InvalidTag, ValueError, TypeError):
        raise ProposalIntegrityError("Proposal payload authentication failed") from None
    if not hmac.compare_digest(hashlib.sha256(plaintext).hexdigest(), proposal.snapshot_digest):
        raise ProposalIntegrityError("Proposal snapshot digest verification failed")
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProposalIntegrityError("Proposal payload is unreadable") from None
    if canonical_json(payload) != plaintext:
        raise ProposalIntegrityError("Proposal payload is not in canonical form")
    return payload


def utc_now():
    return datetime.now(timezone.utc)
