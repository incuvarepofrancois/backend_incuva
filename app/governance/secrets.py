from cryptography.fernet import InvalidToken

from app.models.governance import OrganizationSecret
from app.security.crypto import credential_cipher, decrypt_credential


def read_secret(org_id, name, default=None):
    """Internal-only secret access. Never expose the result in an API response or log."""
    cipher = credential_cipher()
    item = OrganizationSecret.query.filter_by(org_id=org_id, name=name).first()
    if cipher is None or item is None:
        return default
    try:
        return decrypt_credential(item.encrypted_value).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        from flask import current_app
        current_app.logger.exception("Organization secret could not be decrypted")
        return default
