from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

MIN_PASSWORD_LENGTH = 1
MAX_PASSWORD_BYTES = 1024

_password_hash = PasswordHash.recommended()


def _validate_password_size(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("password must not be empty")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("password is too long")


def hash_password(password: str) -> str:
    _validate_password_size(password)
    return _password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    try:
        return _password_hash.verify(password, encoded_hash)
    except (PwdlibError, TypeError, ValueError):
        return False


def password_needs_rehash(encoded_hash: str) -> bool:
    for hasher in _password_hash.hashers:
        if hasher.identify(encoded_hash):
            return hasher != _password_hash.current_hasher or hasher.check_needs_rehash(
                encoded_hash
            )
    return True
