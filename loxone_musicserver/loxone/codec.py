"""Loxone ID encoding and decoding (base64url of [data, offset])."""

from __future__ import annotations

import base64
import json
from typing import Any, Tuple, Union

BASE_DELTA = 1_000_000
BASE_SERVICE = 1 * BASE_DELTA
BASE_FAVORITE_GLOBAL = 2 * BASE_DELTA
BASE_PLAYLIST = 3 * BASE_DELTA
BASE_LIBRARY = 4 * BASE_DELTA
BASE_INPUT = 5 * BASE_DELTA
BASE_FAVORITE_ZONE = 6 * BASE_DELTA


def encode_loxone_id(data: Union[str, int], offset: int) -> str:
    """Encodes a [data, offset] pair into a Loxone URL-safe base64 string."""
    raw = json.dumps([data, offset], separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8")
    return encoded.rstrip("=")


def decode_loxone_id(raw: str) -> Tuple[Union[str, int], int] | None:
    """Decodes a Loxone URL-safe base64 string into (data, offset)."""
    if not raw:
        return None
    try:
        # Add padding if needed
        padding = 4 - (len(raw) % 4)
        if padding and padding != 4:
            raw += "=" * padding
        decoded = base64.urlsafe_b64decode(raw.encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, list) and len(parsed) >= 2:
            return (parsed[0], int(parsed[1]))
        return None
    except Exception:
        return None
