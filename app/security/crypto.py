from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from flask import current_app


def credential_ciphers():
    keys = [current_app.config.get("INTEGRATION_CREDENTIALS_KEY", "")]
    fallback = current_app.config.get("INTEGRATION_CREDENTIALS_FALLBACK_KEYS", "")
    keys.extend(key.strip() for key in fallback.split(",") if key.strip())
    ciphers = []
    for key in keys:
        if not key:
            continue
        try:
            ciphers.append(Fernet(key.encode("ascii")))
        except (ValueError, TypeError, UnicodeEncodeError):
            raise RuntimeError("An integration credentials encryption key is invalid") from None
    return ciphers


def credential_cipher():
    key = current_app.config.get("INTEGRATION_CREDENTIALS_KEY", "")
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return None


def encrypt_credential(value):
    cipher = credential_cipher()
    if cipher is None:
        raise RuntimeError("Integration credential encryption is not configured")
    return cipher.encrypt(value).decode("ascii")


def decrypt_credential(token):
    last_error = None
    for cipher in credential_ciphers():
        try:
            return cipher.decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError, UnicodeEncodeError) as error:
            last_error = error
    if last_error is not None:
        raise InvalidToken from last_error
    raise RuntimeError("Integration credential decryption is not configured")
