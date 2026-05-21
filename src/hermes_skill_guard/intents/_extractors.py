"""Shared argument extractors for intent handlers."""

from __future__ import annotations

from typing import Any


def extract_skill_name(args: dict[str, Any]) -> str | None:
    """Extract skill name from tool call arguments."""
    for key in ("name", "skill_name", "skill", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_description(args: dict[str, Any]) -> str:
    """Extract description from tool call arguments."""
    for key in ("description", "desc", "summary", "manifest"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_content(args: dict[str, Any]) -> str:
    """Extract content from tool call arguments."""
    values: list[str] = []
    for key in ("content", "body", "description", "manifest", "code"):
        value = args.get(key)
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def extract_target_path(args: dict[str, Any]) -> str | None:
    """Extract an optional skill target path from tool call arguments."""
    for key in ("path", "target_path", "file_path", "destination"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_skill_manage_create_args(
    *,
    name: str,
    description: str,
    content: str | None,
    target_path: str | None = None,
) -> dict[str, Any]:
    """Build official Hermes skill_manage create arguments from a candidate."""
    args: dict[str, Any] = {
        "action": "create",
        "name": name,
        "description": description,
    }
    if content is not None:
        args["content"] = content
    if target_path is not None:
        args["path"] = target_path
    return args
