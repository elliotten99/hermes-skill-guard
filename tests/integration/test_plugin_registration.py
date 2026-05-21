from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import pytest

from hermes_skill_guard.plugin import register

# FakeHermesContext is defined in tests/conftest.py and auto-imported by pytest.


def test_plugin_registers_tools_hooks_commands_and_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: Any
) -> None:
    monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

    register(fake_ctx)

    assert "skill_guard_preflight" in fake_ctx.tools
    assert "skill_guard_candidates" in fake_ctx.tools
    assert "skill_guard_promote" in fake_ctx.tools
    assert "skill_guard_report" in fake_ctx.tools
    assert "pre_tool_call" in fake_ctx.hooks
    assert "post_tool_call" in fake_ctx.hooks
    assert "skill-guard-report" in fake_ctx.commands
    assert "skill-guard-doctor" in fake_ctx.commands
    assert "cli:skill-guard" in fake_ctx.commands
    assert "skill-guard" in fake_ctx.skills
    assert Path(fake_ctx.skills["skill-guard"]["path"]).name == "skill-guard"

    # Verify schemas were passed
    assert fake_ctx.tools["skill_guard_preflight"]["schema"]["name"] == "skill_guard_preflight"
    assert fake_ctx.tools["skill_guard_candidates"]["schema"]["name"] == "skill_guard_candidates"
    assert fake_ctx.tools["skill_guard_preflight"]["schema"]["parameters"]["required"] == [
        "tool_name",
        "args",
    ]
    candidates_schema = fake_ctx.tools["skill_guard_candidates"]["schema"]
    assert candidates_schema["parameters"]["required"] == ["action"]

    # Verify Hermes CLI registration can populate a parser for `hermes skill-guard ...`.
    cli_parser = argparse.ArgumentParser(prog="hermes skill-guard")
    setup_fn = cast(Any, fake_ctx.commands["cli:skill-guard"]["setup_fn"])
    setup_fn(cli_parser)
    parsed = cli_parser.parse_args(["report", "--json"])
    assert parsed.command == "report"
    assert parsed.json is True


def test_pre_and_post_hook_persist_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: Any
) -> None:
    monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
    register(fake_ctx)

    # Use invoke_hook (public contract) instead of direct handler access.
    fake_ctx.invoke_hook(
        "pre_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "short"},
        task_id="t1",
        session_id="s1",
        tool_call_id="tc-1",
    )
    fake_ctx.invoke_hook(
        "post_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "short"},
        result='{"ok": true}',
        task_id="t1",
        session_id="s1",
        tool_call_id="tc-1",
        duration_ms=42,
    )
    report_raw = fake_ctx.tools["skill_guard_report"]["handler"]({"json": True})
    report = json.loads(report_raw)

    assert report["summary"]["events"] == 1
    assert report["summary"]["audit_log"] == 1


def test_post_hook_records_trace_cache_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: Any
) -> None:
    monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
    register(fake_ctx)

    fake_ctx.invoke_hook(
        "post_tool_call",
        tool_name="skill_manage",
        args={"action": "create", "name": "x", "content": "short"},
        result='{"ok": true}',
        task_id="t1",
        session_id="s1",
        tool_call_id="missing-pre-hook",
        duration_ms=7,
    )
    report_raw = fake_ctx.tools["skill_guard_report"]["handler"]({"json": True})
    report = json.loads(report_raw)

    assert report["summary"]["events"] == 1
    assert report["summary"]["audit_log"] == 0
    assert report["summary"]["counters"]["trace_cache_miss_count"] == 1


def test_registered_tool_handlers_return_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: Any
) -> None:
    monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
    register(fake_ctx)

    preflight_raw = fake_ctx.tools["skill_guard_preflight"]["handler"](
        {
            "tool_name": "skill_manage",
            "args": {"action": "create", "name": "x", "content": "short"},
        }
    )
    candidates_raw = fake_ctx.tools["skill_guard_candidates"]["handler"]({"action": "list"})
    report_raw = fake_ctx.tools["skill_guard_report"]["handler"]({"json": True})

    assert json.loads(preflight_raw)["ok"] is True
    assert json.loads(candidates_raw)["ok"] is True
    assert json.loads(report_raw)["ok"] is True
