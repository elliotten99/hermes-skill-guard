"""Unit tests for the reporting intent (report/doctor handlers).

Targets the ``ReportingIntent`` handlers registered on the Hermes adapter:
``skill_guard_report``, ``skill_guard_doctor``, the corresponding slash
commands, and the internal helpers (``_build_compat_diagnostics``,
``_query_pragma``, ``_detect_config_source``) exercised through the public
tool/slash handlers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.config import EnforcementConfig, GuardConfig
from hermes_skill_guard.context import SkillGuardContext
from hermes_skill_guard.hermes.adapter import HermesAdapter
from hermes_skill_guard.registry import register_intents
from hermes_skill_guard.runtime import TraceCache
from hermes_skill_guard.schemas import ModuleStatus
from hermes_skill_guard.storage.repository import StateStore

# FakeHermesContext fixture (`fake_ctx`) is provided by tests/conftest.py.


def _make_context(
    tmp_path: Path, *, dry_run: bool = True, mode: str = "audit"
) -> SkillGuardContext:
    config = GuardConfig(
        dry_run=dry_run,
        state_dir=tmp_path,
        enforcement=EnforcementConfig(mode=mode),
    )
    return SkillGuardContext(
        config=config,
        store=StateStore(config.state_db, config.events),
        trace_cache=TraceCache(config.trace_cache),
        logger=logging.getLogger("test-reporting"),
    )


def _register(tmp_path: Path, fake_ctx: Any) -> SkillGuardContext:
    ctx = _make_context(tmp_path)
    register_intents(HermesAdapter(fake_ctx), ctx)
    return ctx


# Environment variables that influence ``_detect_config_source``. Keeping the
# list central avoids drift between the three TestDetectConfigSource cases that
# need to scrub the environment before asserting on the detected source.
_CONFIG_ENV_VARS = (
    "SKILL_GUARD_STATE_DIR",
    "SKILL_GUARD_DRY_RUN",
    "SKILL_GUARD_ENFORCEMENT_MODE",
    "SKILL_GUARD_PREFLIGHT_TIMEOUT_MS",
    "SKILL_GUARD_FAIL_OPEN",
    "SKILL_GUARD_CAPTURE_RAW_PAYLOADS",
    "SKILL_GUARD_REDACTION_MODE",
    "SKILL_GUARD_MAX_FIELD_LENGTH",
    "SKILL_GUARD_HASH_REDACTED_VALUES",
    "SKILL_GUARD_TRACE_CACHE_TTL_MINUTES",
    "SKILL_GUARD_TRACE_CACHE_MAX_ENTRIES",
    "SKILL_GUARD_EVENTS_TTL_DAYS",
    "SKILL_GUARD_EVENTS_MAX_ROWS",
    "SKILL_GUARD_EVENTS_MAX_DB_MB",
    "SKILL_GUARD_EVENTS_ROTATE_EVERY",
    "SKILL_GUARD_CONFIG",
)


def _purge_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every config env var so detection falls back to user/default."""
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# skill_guard_report tool handler
# ---------------------------------------------------------------------------


class TestReportToolHandler:
    def test_report_json_default_returns_full_summary(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_report"]["handler"]({})
        parsed = json.loads(raw)

        assert parsed["ok"] is True
        summary = parsed["summary"]
        # Core fields exposed by the report
        assert "events" in summary
        assert "audit_log" in summary
        assert "candidates" in summary
        assert summary["dry_run"] == ctx.config.dry_run
        assert summary["enforcement_mode"] == ctx.config.enforcement.mode
        assert "wal_enabled" in summary
        assert summary["recent_events"] == []
        assert summary["recent_risks"] == []
        # candidate_status_counts always returns the full status->count dict
        assert isinstance(summary["candidate_summary"], dict)
        assert summary["modules"] == []

    def test_report_with_json_true_explicit(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_report"]["handler"]({"json": True})
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        assert "summary" in parsed

    def test_report_text_mode_returns_plain_text(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)

        text = fake_ctx.tools["skill_guard_report"]["handler"]({"json": False})

        assert "hermes-skill-guard report" in text
        assert "events:" in text
        assert "audit_log:" in text
        assert "candidates:" in text
        assert "wal:" in text

    def test_report_handles_store_exception_returns_error_json(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _register(tmp_path, fake_ctx)

        def _boom() -> dict[str, object]:
            raise RuntimeError("boom")

        monkeypatch.setattr(ctx.store, "summary", _boom)

        raw = fake_ctx.tools["skill_guard_report"]["handler"]({})
        parsed = json.loads(raw)
        assert parsed["ok"] is False
        assert parsed["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# /skill-guard report slash handler
# ---------------------------------------------------------------------------


class TestReportSlashHandler:
    def test_slash_default_returns_json(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-report"]["handler"]

        raw = handler("")
        parsed = json.loads(raw)
        assert parsed["ok"] is True

    @pytest.mark.parametrize("raw_arg", ["--text", "text", "-t", "  TEXT  "])
    def test_slash_text_flags_return_plain_text(
        self, tmp_path: Path, fake_ctx: Any, raw_arg: str
    ) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-report"]["handler"]

        out = handler(raw_arg)
        assert "hermes-skill-guard report" in out

    def test_slash_unknown_arg_falls_back_to_json(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-report"]["handler"]

        raw = handler("--json")
        # Unknown raw args default to JSON output
        parsed = json.loads(raw)
        assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# skill_guard_doctor tool handler
# ---------------------------------------------------------------------------


class TestDoctorToolHandler:
    def test_doctor_all_returns_full_diagnostics(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({})
        parsed = json.loads(raw)

        assert parsed["ok"] is True
        diag = parsed["diagnostics"]
        for key in (
            "config_source",
            "storage",
            "candidates",
            "counters",
            "dangling",
            "recent_risks",
            "health_score",
            "compat",
        ):
            assert key in diag, f"missing diagnostic key: {key}"

        # Storage block
        storage = diag["storage"]
        assert "db_path" in storage
        assert "size_mb" in storage
        assert storage["wal_mode"] in ("wal", "off")
        assert isinstance(storage["foreign_keys"], bool)
        assert isinstance(storage["busy_timeout"], int)

        # Candidates block
        assert diag["candidates"]["total"] == 0
        assert isinstance(diag["candidates"]["by_status"], dict)

        # Health score with zero counters should be a perfect 100.
        assert diag["health_score"] == 100
        assert diag["counters"]["sqlite_busy_count"] == 0
        assert diag["counters"]["dropped_write_count"] == 0

        # Compat block: no modules registered -> no warnings.
        compat = diag["compat"]
        assert compat["modules"] == []
        assert compat["warnings"] == []
        assert compat["warning_count"] == 0

    @pytest.mark.parametrize(
        "check, expected_keys",
        [
            ("storage", {"storage"}),
            ("config", {"config_source"}),
            ("candidates", {"candidates", "dangling"}),
            ("counters", {"counters", "health_score"}),
            ("compat", {"compat"}),
        ],
    )
    def test_doctor_filtered_checks(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        check: str,
        expected_keys: set[str],
    ) -> None:
        _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": check})
        parsed = json.loads(raw)

        assert parsed["ok"] is True
        assert set(parsed["diagnostics"].keys()) == expected_keys

    def test_doctor_health_score_drops_with_counters(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _register(tmp_path, fake_ctx)

        def _fake_summary() -> dict[str, object]:
            return {
                "counters": {
                    "sqlite_busy_count": 2,
                    "dropped_write_count": 1,
                    "rotation_failed_count": 1,
                }
            }

        monkeypatch.setattr(ctx.store, "summary", _fake_summary)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "counters"})
        parsed = json.loads(raw)
        # 100 - (2 + 1*5 + 1*10) = 83
        assert parsed["diagnostics"]["health_score"] == 83
        assert parsed["diagnostics"]["counters"]["sqlite_busy_count"] == 2

    def test_doctor_health_score_floor_at_zero(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _register(tmp_path, fake_ctx)

        def _fake_summary() -> dict[str, object]:
            return {
                "counters": {
                    "sqlite_busy_count": 0,
                    "dropped_write_count": 0,
                    "rotation_failed_count": 999,
                }
            }

        monkeypatch.setattr(ctx.store, "summary", _fake_summary)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "counters"})
        parsed = json.loads(raw)
        assert parsed["diagnostics"]["health_score"] == 0

    def test_doctor_handles_non_dict_counters(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If summary().counters is not a dict, doctor should still respond."""
        ctx = _register(tmp_path, fake_ctx)

        def _fake_summary() -> dict[str, object]:
            return {"counters": "not-a-dict"}

        monkeypatch.setattr(ctx.store, "summary", _fake_summary)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "counters"})
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        assert parsed["diagnostics"]["counters"]["sqlite_busy_count"] == 0

    def test_doctor_handles_exception(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _register(tmp_path, fake_ctx)

        def _boom() -> dict[str, object]:
            raise RuntimeError("fail")

        monkeypatch.setattr(ctx.store, "summary", _boom)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "all"})
        parsed = json.loads(raw)
        assert parsed["ok"] is False
        assert parsed["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# /skill-guard doctor slash handler
# ---------------------------------------------------------------------------


class TestDoctorSlashHandler:
    def test_slash_default_runs_all_checks(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-doctor"]["handler"]

        raw = handler("")
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        # All check returns the full diagnostic dict
        assert "storage" in parsed["diagnostics"]
        assert "compat" in parsed["diagnostics"]

    @pytest.mark.parametrize(
        "raw_arg, expected_keys",
        [
            ("storage", {"storage"}),
            ("config", {"config_source"}),
            ("candidates", {"candidates", "dangling"}),
            ("counters", {"counters", "health_score"}),
            ("compat", {"compat"}),
            ("  STORAGE  ", {"storage"}),
        ],
    )
    def test_slash_recognised_check_args(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        raw_arg: str,
        expected_keys: set[str],
    ) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-doctor"]["handler"]

        raw = handler(raw_arg)
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        assert set(parsed["diagnostics"].keys()) == expected_keys

    def test_slash_unknown_arg_falls_back_to_all(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)
        handler = fake_ctx.commands["skill-guard-doctor"]["handler"]

        raw = handler("unknown-check")
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        # Falls through to "all" → keeps every section.
        assert "storage" in parsed["diagnostics"]
        assert "compat" in parsed["diagnostics"]


# ---------------------------------------------------------------------------
# _build_compat_diagnostics (via doctor compat check)
# ---------------------------------------------------------------------------


class TestCompatDiagnostics:
    def test_compat_empty_modules(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "compat"})
        parsed = json.loads(raw)
        compat = parsed["diagnostics"]["compat"]
        assert compat["modules"] == []
        assert compat["warnings"] == []
        assert compat["warning_count"] == 0

    def test_compat_module_without_retirement_status(self, tmp_path: Path, fake_ctx: Any) -> None:
        ctx = _register(tmp_path, fake_ctx)
        ctx.store.record_probe_result(
            intent_id="some-intent",
            status=ModuleStatus.ENABLED.value,
            confidence="high",
            since_version="0.1.0",
            reason="ok",
        )

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "compat"})
        compat = json.loads(raw)["diagnostics"]["compat"]
        assert len(compat["modules"]) == 1
        assert compat["warnings"] == []
        assert compat["warning_count"] == 0

    def test_compat_module_with_retirement_status_produces_warning(
        self, tmp_path: Path, fake_ctx: Any
    ) -> None:
        ctx = _register(tmp_path, fake_ctx)
        ctx.store.record_probe_result(
            intent_id="retiring-intent",
            status=ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
            confidence="medium",
            since_version="0.1.0",
            reason="superseded",
        )

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "compat"})
        compat = json.loads(raw)["diagnostics"]["compat"]
        assert compat["warning_count"] == 1
        assert any("retiring-intent" in w for w in compat["warnings"])


# ---------------------------------------------------------------------------
# _query_pragma (via doctor storage check)
# ---------------------------------------------------------------------------


class TestQueryPragma:
    def test_storage_block_reports_pragma_values(self, tmp_path: Path, fake_ctx: Any) -> None:
        _register(tmp_path, fake_ctx)

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "storage"})
        storage = json.loads(raw)["diagnostics"]["storage"]
        # foreign_keys may be True or False depending on init but must be bool
        assert isinstance(storage["foreign_keys"], bool)
        # busy_timeout is queried via PRAGMA; expect a non-negative int
        assert isinstance(storage["busy_timeout"], int)
        assert storage["busy_timeout"] >= 0

    def test_storage_block_when_pragma_raises(
        self, tmp_path: Path, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the underlying sqlite connection fails, doctor returns an error."""
        ctx = _register(tmp_path, fake_ctx)
        # Point db_path to a directory so sqlite3.connect raises an OperationalError.
        ctx.store.db_path = tmp_path / "does-not-exist" / "nope.db"

        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "storage"})
        parsed = json.loads(raw)
        assert parsed["ok"] is False
        assert "error" in parsed


# ---------------------------------------------------------------------------
# _detect_config_source (via doctor config check)
# ---------------------------------------------------------------------------


class TestDetectConfigSource:
    def test_defaults_when_no_env_or_user_config(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _purge_config_env(monkeypatch)

        # Point the user-config probe at a non-existent path.
        import hermes_skill_guard.config as config_mod

        fake_path = tmp_path / "definitely-not-here.yaml"
        monkeypatch.setattr(config_mod, "_default_user_config_path", lambda: fake_path)

        _register(tmp_path, fake_ctx)
        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "config"})
        parsed = json.loads(raw)
        assert parsed["diagnostics"]["config_source"] == "defaults"

    def test_env_source_when_env_var_set(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        _register(tmp_path, fake_ctx)
        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "config"})
        parsed = json.loads(raw)
        assert parsed["diagnostics"]["config_source"] == "env"

    def test_user_config_when_only_skill_guard_config_set(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Clear env keys that would short-circuit to "env", then set
        # ``SKILL_GUARD_CONFIG`` back to force the user-config branch.
        _purge_config_env(monkeypatch)
        monkeypatch.setenv("SKILL_GUARD_CONFIG", str(tmp_path / "user.yaml"))

        _register(tmp_path, fake_ctx)
        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "config"})
        parsed = json.loads(raw)
        assert parsed["diagnostics"]["config_source"] == "user_config"

    def test_user_config_when_default_path_exists(
        self,
        tmp_path: Path,
        fake_ctx: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _purge_config_env(monkeypatch)

        user_cfg = tmp_path / "config.yaml"
        user_cfg.write_text("dry_run: true\n")

        import hermes_skill_guard.config as config_mod

        monkeypatch.setattr(config_mod, "_default_user_config_path", lambda: user_cfg)

        _register(tmp_path, fake_ctx)
        raw = fake_ctx.tools["skill_guard_doctor"]["handler"]({"check": "config"})
        parsed = json.loads(raw)
        assert parsed["diagnostics"]["config_source"] == "user_config"
