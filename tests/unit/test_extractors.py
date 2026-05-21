"""Unit coverage for ``hermes_skill_guard.intents._extractors``.

The extractors are pure helpers shared by intent handlers.  These tests target
every branch including the early-return / fallthrough paths so the module
reaches >=98% coverage.
"""

from __future__ import annotations

from typing import Any

import pytest

from hermes_skill_guard.intents._extractors import (
    build_skill_manage_create_args,
    extract_content,
    extract_description,
    extract_skill_name,
    extract_target_path,
)


class TestExtractSkillName:
    @pytest.mark.parametrize("key", ["name", "skill_name", "skill", "target"])
    def test_returns_value_for_each_supported_key(self, key: str) -> None:
        assert extract_skill_name({key: "  my-skill  "}) == "my-skill"

    def test_returns_none_when_no_matching_key(self) -> None:
        assert extract_skill_name({"unrelated": "value"}) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert extract_skill_name({"name": "   "}) is None

    def test_returns_none_for_non_string_value(self) -> None:
        # Non-string values must not be returned even if the key is present.
        assert extract_skill_name({"name": 42}) is None
        assert extract_skill_name({"name": None}) is None
        assert extract_skill_name({"name": ["nested"]}) is None

    def test_skips_first_key_when_invalid_and_uses_next(self) -> None:
        # ``name`` is present but blank → falls through to ``skill_name``.
        assert extract_skill_name({"name": "  ", "skill_name": "fallback"}) == "fallback"


class TestExtractDescription:
    @pytest.mark.parametrize("key", ["description", "desc", "summary", "manifest"])
    def test_returns_value_for_each_supported_key(self, key: str) -> None:
        assert extract_description({key: " hello "}) == "hello"

    def test_returns_empty_string_when_missing(self) -> None:
        assert extract_description({"other": "value"}) == ""

    def test_returns_empty_string_for_blank_value(self) -> None:
        assert extract_description({"description": "   "}) == ""

    def test_returns_empty_string_for_non_string_value(self) -> None:
        assert extract_description({"description": 5}) == ""


class TestExtractContent:
    def test_joins_all_present_keys_with_newline(self) -> None:
        args = {
            "content": "a",
            "body": "b",
            "description": "c",
            "manifest": "d",
            "code": "e",
        }
        assert extract_content(args) == "a\nb\nc\nd\ne"

    def test_returns_empty_string_when_no_keys(self) -> None:
        assert extract_content({"unrelated": "x"}) == ""

    def test_skips_non_string_values(self) -> None:
        args: dict[str, Any] = {"content": "keep", "body": 42, "code": None}
        assert extract_content(args) == "keep"

    def test_preserves_empty_string_entries(self) -> None:
        # extract_content only filters by type, not truthiness.
        assert extract_content({"content": "", "body": "x"}) == "\nx"


class TestExtractTargetPath:
    @pytest.mark.parametrize("key", ["path", "target_path", "file_path", "destination"])
    def test_returns_value_for_each_supported_key(self, key: str) -> None:
        assert extract_target_path({key: " /tmp/foo "}) == "/tmp/foo"

    def test_returns_none_when_missing(self) -> None:
        assert extract_target_path({"unrelated": "/x"}) is None

    def test_returns_none_for_blank_value(self) -> None:
        # Covers the trailing ``return None`` (line 42) when value is whitespace.
        assert extract_target_path({"path": "   "}) is None

    def test_returns_none_for_non_string_value(self) -> None:
        assert extract_target_path({"path": 7}) is None

    def test_falls_through_to_next_key_when_first_blank(self) -> None:
        assert extract_target_path({"path": "   ", "target_path": "/data"}) == "/data"


class TestBuildSkillManageCreateArgs:
    def test_required_only_omits_optional_keys(self) -> None:
        result = build_skill_manage_create_args(name="skill", description="desc", content=None)
        assert result == {
            "action": "create",
            "name": "skill",
            "description": "desc",
        }
        assert "content" not in result
        assert "path" not in result

    def test_includes_content_when_provided(self) -> None:
        """Covers line 59 (``args['content'] = content``)."""
        result = build_skill_manage_create_args(name="skill", description="desc", content="body")
        assert result["content"] == "body"
        assert "path" not in result

    def test_includes_path_when_target_path_provided(self) -> None:
        """Covers line 61 (``args['path'] = target_path``)."""
        result = build_skill_manage_create_args(
            name="skill",
            description="desc",
            content=None,
            target_path="/x/y.md",
        )
        assert result["path"] == "/x/y.md"
        assert "content" not in result

    def test_empty_string_content_still_included(self) -> None:
        # The check is ``is not None``, so empty string is included.
        result = build_skill_manage_create_args(name="skill", description="desc", content="")
        assert result["content"] == ""

    def test_includes_both_content_and_path(self) -> None:
        result = build_skill_manage_create_args(
            name="s", description="d", content="c", target_path="/p"
        )
        assert result == {
            "action": "create",
            "name": "s",
            "description": "d",
            "content": "c",
            "path": "/p",
        }
