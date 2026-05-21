from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hermes_skill_guard.redaction import (
    SECRET_PATTERNS,
    SENSITIVE_KEYS,
    Redactor,
    stable_hash,
)


def test_redactor_removes_secret_values() -> None:
    payload = {
        "password": "correct-horse-battery-staple",
        "token": "sk-abcdefghijklmnopqrstuvwxyz123456",
        "safe": "hello world",
    }

    summary, _, applied, failed = Redactor().redact(payload)
    encoded = json.dumps(summary)

    assert applied is True
    assert failed is False
    assert "correct-horse" not in encoded
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in encoded
    assert "redacted" in encoded


def test_stable_hash_is_deterministic_and_order_independent() -> None:
    a = stable_hash({"a": 1, "b": 2})
    b = stable_hash({"b": 2, "a": 1})
    assert a == b
    # Different content -> different hash.
    assert stable_hash("hello") != stable_hash("world")


def test_stable_hash_handles_non_json_native_types() -> None:
    # `default=str` lets it stringify exotic types like sets without raising.
    digest = stable_hash({1, 2, 3})
    assert isinstance(digest, str) and len(digest) == 64


def test_secret_patterns_match_known_token_shapes() -> None:
    samples = {
        "openai": "sk-abcdefghijklmnop_ABCDEFGHIJ",
        "github": "ghp_abcdefghijklmnop12345",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature_payload_part",
        "pem": "-----BEGIN RSA PRIVATE KEY-----",
        "aws": "AKIAABCDEFGHIJKLMNOP",
        "hex_blob": "a" * 64,
    }
    for label, value in samples.items():
        assert any(p.search(value) for p in SECRET_PATTERNS), f"{label} not matched"


def test_secret_patterns_have_no_obvious_false_positive() -> None:
    benign = "the quick brown fox jumps over the lazy dog"
    assert not any(p.search(benign) for p in SECRET_PATTERNS)
    # Short prefixes should not trigger.
    assert not any(p.search("sk-too-short") for p in SECRET_PATTERNS)
    assert not any(p.search("ghp_short") for p in SECRET_PATTERNS)


def test_redacts_via_sensitive_key_even_when_value_looks_safe() -> None:
    redactor = Redactor()
    # Hyphenated header name should still normalise to a sensitive key.
    summary, _, applied, failed = redactor.redact({"Authorization": "Bearer xyz"})
    assert applied is True and failed is False
    assert summary["Authorization"]["redacted"] is True
    assert summary["Authorization"]["sha256"]
    # `private_key` variant.
    summary2, *_ = redactor.redact({"private-key": "anything"})
    assert summary2["private-key"]["redacted"] is True


def test_redactor_can_disable_hash_on_redacted_values() -> None:
    redactor = Redactor(hash_redacted_values=False)
    summary, _, _, _ = redactor.redact({"password": "secret"})
    assert summary["password"]["redacted"] is True
    assert "sha256" not in summary["password"]


def test_capture_raw_payloads_truncates_to_max_field_length() -> None:
    redactor = Redactor(capture_raw_payloads=True, max_field_length=10)
    long_value = "x" * 500
    summary, _, _, _ = redactor.redact({"note": long_value})
    assert summary["note"] == "x" * 10


def test_default_mode_summarises_strings_without_leaking_content() -> None:
    redactor = Redactor()
    summary, _, _, _ = redactor.redact({"note": "hello world"})
    note = summary["note"]
    assert note["type"] == "str"
    assert note["length"] == len("hello world")
    assert note["sha256"]
    # Plaintext must not appear.
    assert "hello world" not in json.dumps(summary)


def test_redact_handles_nested_structures_and_caps_lists() -> None:
    redactor = Redactor()
    payload = {
        "outer": {
            "inner": {
                "password": "boom",
                "items": ["a", "b", "c"],
            },
        },
        "long_list": list(range(50)),
    }
    summary, _, applied, failed = redactor.redact(payload)
    assert applied is True and failed is False
    assert summary["outer"]["inner"]["password"]["redacted"] is True
    # Lists are walked recursively.
    assert isinstance(summary["outer"]["inner"]["items"], list)
    assert len(summary["outer"]["inner"]["items"]) == 3
    # Long lists are truncated to 20 entries by design.
    assert len(summary["long_list"]) == 20


def test_redact_wraps_non_dict_top_level_values() -> None:
    # A top-level list is not a dict, so the wrapper `{"value": ...}` branch
    # in `Redactor.redact` is exercised.
    summary, _, applied, failed = Redactor().redact([1, 2, 3])
    assert applied is True and failed is False
    assert "value" in summary
    assert summary["value"] == [1, 2, 3]


def test_redact_returns_failure_envelope_on_exception() -> None:
    redactor = Redactor()

    class Boom:
        def __repr__(self) -> str:  # pragma: no cover - defensive
            return "Boom()"

    # Force `stable_hash` (the first risky call) to explode to exercise the
    # except branch in `redact`.
    with patch(
        "hermes_skill_guard.redaction.stable_hash",
        side_effect=[RuntimeError("boom"), "fallback-hash"],
    ):
        summary, digest, applied, failed = redactor.redact(Boom())

    assert applied is False
    assert failed is True
    assert summary == {"redaction_failed": True}
    assert digest == "fallback-hash"


def test_redact_passes_through_non_string_scalars() -> None:
    summary, _, _, _ = Redactor().redact({"n": 7, "flag": True, "nothing": None})
    # Non-string scalar values fall through the catch-all `return value` branch.
    assert summary["n"] == 7
    assert summary["flag"] is True
    assert summary["nothing"] is None


def test_redact_treats_bytes_as_passthrough_scalar() -> None:
    # The Sequence branch is explicitly guarded against bytes/bytearray, so the
    # value must be returned as-is via the catch-all return.
    raw = b"\x00\x01\x02"
    summary, _, _, _ = Redactor().redact({"blob": raw})
    assert summary["blob"] == raw


def test_redact_top_level_sequence_uses_value_wrapper() -> None:
    summary, _, applied, failed = Redactor().redact(["alpha", "beta"])
    assert applied is True and failed is False
    # Lists at the top level are not dicts, so they get wrapped in {"value": ...}.
    assert "value" in summary
    assert len(summary["value"]) == 2


def test_sensitive_keys_normalisation_is_case_insensitive() -> None:
    # Spot-check a few alias forms. The normaliser lowercases and converts
    # hyphens to underscores, so these all map onto entries in `SENSITIVE_KEYS`.
    redactor = Redactor()
    summary, *_ = redactor.redact(
        {
            "API_KEY": "anything",
            "X-Refresh-Token": "anything",
            "Access_Key": "anything",
        }
    )
    for key in ("API_KEY", "X-Refresh-Token", "Access_Key"):
        assert summary[key]["redacted"] is True


def test_sensitive_keys_constant_contains_expected_names() -> None:
    # Guardrail: changes to this set are intentional.
    for required in ("password", "token", "api_key", "authorization"):
        assert required in SENSITIVE_KEYS


@pytest.mark.parametrize(
    "value",
    [
        "sk-" + "A" * 16,
        "ghp_" + "B" * 20,
        "AKIA" + "C" * 16,
    ],
)
def test_string_values_matching_secret_pattern_are_redacted(value: str) -> None:
    summary, _, _, _ = Redactor().redact({"any_key": value})
    assert summary["any_key"]["redacted"] is True
