"""ID helpers for event and trace correlation."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return a compact globally unique ID with a stable prefix."""
    return f"{prefix}_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return new_id("evt")


def new_trace_id() -> str:
    return new_id("trc")


def new_audit_id() -> str:
    return new_id("aud")


def new_candidate_id() -> str:
    return new_id("cand")


def new_promotion_attempt_id() -> str:
    return new_id("pa")
