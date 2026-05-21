"""Strict redaction for tool payloads."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "cookie",
    "private_key",
    "access_key",
    "refresh_token",
}

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Fa-f0-9]{48,}\b"),
]


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEYS)


def _looks_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


class Redactor:
    def __init__(
        self,
        *,
        capture_raw_payloads: bool = False,
        max_field_length: int = 512,
        hash_redacted_values: bool = True,
    ) -> None:
        self.capture_raw_payloads = capture_raw_payloads
        self.max_field_length = max_field_length
        self.hash_redacted_values = hash_redacted_values

    def redact(self, payload: Any) -> tuple[dict[str, Any], str, bool, bool]:
        """Return summary, hash, redaction_applied, redaction_failed."""
        try:
            payload_hash = stable_hash(payload)
            summary = self._redact_value(payload, key=None)
            if not isinstance(summary, dict):
                summary = {"value": summary}
            return summary, payload_hash, True, False
        except Exception:
            return {"redaction_failed": True}, stable_hash(str(type(payload))), False, True

    def _redact_value(self, value: Any, key: str | None) -> Any:
        if key and _is_sensitive_key(key):
            return self._redacted(value)
        if isinstance(value, str):
            if _looks_secret(value):
                return self._redacted(value)
            if self.capture_raw_payloads:
                return value[: self.max_field_length]
            return {"type": "str", "length": len(value), "sha256": stable_hash(value)}
        if isinstance(value, Mapping):
            return {
                str(child_key): self._redact_value(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            return [self._redact_value(item, key=None) for item in list(value)[:20]]
        return value

    def _redacted(self, value: Any) -> dict[str, Any]:
        redacted: dict[str, Any] = {"redacted": True, "type": type(value).__name__}
        if self.hash_redacted_values:
            redacted["sha256"] = stable_hash(value)
        return redacted
