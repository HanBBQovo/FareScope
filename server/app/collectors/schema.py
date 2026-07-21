"""Utilities for recording provider response shapes without retaining raw values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def schema_shape(value: Any) -> Any:
    """Return a deterministic, value-free description of a JSON-compatible object."""

    if isinstance(value, Mapping):
        return {str(key): schema_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        unique_shapes: dict[str, Any] = {}
        for item in value:
            shape = schema_shape(item)
            encoded = json.dumps(shape, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            unique_shapes.setdefault(encoded, shape)
        return {"list": [unique_shapes[key] for key in sorted(unique_shapes)]}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def schema_fingerprint(value: Any) -> str:
    shape = schema_shape(value)
    encoded = json.dumps(shape, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()
