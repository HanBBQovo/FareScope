from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class InvalidEncryptionKeyError(ValueError):
    pass


class SecretDecryptionError(ValueError):
    pass


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (TypeError, ValueError) as error:
            raise InvalidEncryptionKeyError(
                "secret encryption key is not a valid Fernet key"
            ) from error

    def encrypt_mapping(self, value: Mapping[str, Any]) -> bytes:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return self._fernet.encrypt(payload)

    def decrypt_mapping(self, ciphertext: bytes) -> dict[str, Any]:
        try:
            payload = self._fernet.decrypt(ciphertext)
            value = json.loads(payload)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SecretDecryptionError("encrypted secret could not be decrypted") from error
        if not isinstance(value, dict):
            raise SecretDecryptionError("encrypted secret is not an object")
        return value
