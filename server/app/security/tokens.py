from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretToken:
    value: str
    digest: str


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_secret_token(prefix: str, *, entropy_bytes: int = 32) -> SecretToken:
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("token prefix must contain letters, digits, or underscores")
    if entropy_bytes < 24:
        raise ValueError("secret tokens require at least 192 bits of entropy")

    value = f"{prefix}_{secrets.token_urlsafe(entropy_bytes)}"
    return SecretToken(value=value, digest=token_digest(value))
