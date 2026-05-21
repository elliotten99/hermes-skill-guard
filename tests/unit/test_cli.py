"""Unit-level CLI coverage for ``hermes_skill_guard.__main__``.

These tests invoke ``main`` directly and parse the JSON it emits to stdout.
They focus on branches that the existing integration suite leaves uncovered:
doctor sub-checks, the report text/json branches, candidate
``create``/``details``/``status`` commands, every relations action, the
``compat`` lifecycle, ``verify package`` for missing and well-formed
artifacts, and the trivial ``rules test`` command.
"""

from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from hermes_skill_guard.__main__ import main
from hermes_skill_guard.ids import (
    new_candidate_id,
    new_event_id,
    new_promotion_attempt_id,
    new_trace_id,
)
from hermes_skill_guard.schemas import (
    Candidate,
    CandidateStatus,
    EventRecord,
    ModuleStatus,
    PromotionAttempt,
)
from hermes_skill_guard.storage.repository import StateStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Invoke ``main(argv)`` and return ``(exit_code, stdout)``."""
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, capsys.readouterr().out


def _run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code, out = _run(argv, capsys)
    return code, json.loads(out)


def _seed_event(store: StateStore, *, event_id: str | None = None) -> str:
    eid = event_id or new_event_id()
    store.record_event(
        EventRecord(
            event_id=eid,
            trace_id=new_trace_id(),
            parent_event_id=None,
            event_type="pre_tool_use",
            tool_name="skill_manage",
            skill_name=None,
            payload_summary={"summary": "cli-test"},
            payload_hash="hash",
            redaction_applied=True,
            redaction_failed=False,
        )
    )
    return eid


def _seed_candidate(
    store: StateStore,
    *,
    status: CandidateStatus = CandidateStatus.CANDIDATE,
    name: str = "skill-a",
) -> str:
    cid = new_candidate_id()
    store.create_candidate(
        Candidate(
            candidate_id=cid,
            source_event_id=new_event_id(),
            trace_id=new_trace_id(),
            name=name,
            description=f"{name} description",
            content_hash=f"hash-{name}",
            status=status,
        )
    )
    return cid


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(state_dir: Path) -> StateStore:
    return StateStore(state_dir / "state.db")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestCmdDoctor:
    @pytest.mark.parametrize(
        "check, expected_key",
        [
            ("storage", "storage"),
            ("config", "config"),
            ("candidates", "candidates"),
            ("counters", "counters"),
            ("compat", "compat"),
        ],
    )
    def test_doctor_individual_checks_report_their_section(
        self,
        check: str,
        expected_key: str,
        state_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code, out = _run_json(["doctor", "--check", check], capsys)
        assert code == 0
        assert out["check"] == check
        assert expected_key in out["doctor"]

    def test_doctor_all_includes_recent_audit_decisions(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["doctor", "--check", "all"], capsys)
        assert code == 0
        for key in ("storage", "config", "candidates", "counters", "compat"):
            assert key in out["doctor"]
        assert "recent_audit_decisions" in out["doctor"]

    def test_doctor_compat_surfaces_retirement_warnings(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store.record_probe_result(
            intent_id="example_intent",
            status=ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
            confidence="high",
            since_version=None,
            reason="superseded",
        )
        code, out = _run_json(["doctor", "--check", "compat"], capsys)
        assert code == 0
        compat = out["doctor"]["compat"]
        assert compat["warning_count"] >= 1
        assert any("example_intent" in w for w in compat["warnings"])


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


class TestCmdReport:
    def test_report_text_branch_renders_summary(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run(["report", "--limit", "3"], capsys)
        assert code == 0
        assert "hermes-skill-guard report" in out
        assert "events:" in out
        assert "audit_log:" in out
        assert "recent_events (0)" in out

    def test_report_json_branch_with_limit(
        self,
        state_dir: Path,
        store: StateStore,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for _ in range(3):
            _seed_event(store)

        code, out = _run_json(["report", "--json", "--limit", "2"], capsys)
        assert code == 0
        assert out["ok"] is True
        assert "summary" in out
        assert len(out["recent_events"]) <= 2

    def test_report_text_branch_lists_recent_events(
        self,
        state_dir: Path,
        store: StateStore,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Cover the per-event print loop inside the text branch (line 97)."""
        event_ids = [_seed_event(store) for _ in range(2)]
        code, out = _run(["report", "--limit", "5"], capsys)
        assert code == 0
        # The text branch iterates and prints each event id + type
        for eid in event_ids:
            assert eid in out
        assert "pre_tool_use" in out


# ---------------------------------------------------------------------------
# candidates create / details / status
# ---------------------------------------------------------------------------


class TestCmdCandidates:
    def test_candidates_create_attaches_event(
        self,
        state_dir: Path,
        store: StateStore,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        event_id = _seed_event(store)
        code, out = _run_json(
            [
                "candidates",
                "create",
                "--event-id",
                event_id,
                "--name",
                "skill-x",
                "--description",
                "desc",
                "--content-hash",
                "hash-x",
                "--reasons",
                "duplicate",
                "manual",
            ],
            capsys,
        )
        assert code == 0
        assert out["ok"] is True
        assert out["status"] == "detected"

    def test_candidates_create_unknown_event_exits_1(
        self,
        state_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code, out = _run_json(
            [
                "candidates",
                "create",
                "--event-id",
                "evt_missing",
                "--name",
                "n",
                "--description",
                "d",
                "--content-hash",
                "h",
            ],
            capsys,
        )
        assert code == 1
        assert out["ok"] is False
        assert "event not found" in out["error"]

    def test_candidates_details_for_known_candidate(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store, status=CandidateStatus.APPROVED)
        attempt = PromotionAttempt(
            attempt_id=new_promotion_attempt_id(),
            candidate_id=cid,
            trace_id=new_trace_id(),
            tool_call_id=None,
            skill_name="skill-a",
            skill_manage_args={"action": "create"},
        )
        store.create_promotion_attempt(attempt)

        code, out = _run_json(["candidates", "details", cid], capsys)
        assert code == 0
        assert out["ok"] is True
        assert out["candidate"]["candidate_id"] == cid
        assert out["promotion_attempts"]

    def test_candidates_details_for_unknown_candidate_exits_1(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["candidates", "details", "cand_missing"], capsys)
        assert code == 1
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_candidates_stage_transitions_from_detected(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store, status=CandidateStatus.DETECTED)
        code, out = _run_json(["candidates", "stage", cid], capsys)
        assert code == 0
        assert out["status"] == "candidate"

    def test_candidates_stage_invalid_transition_exits_1(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store, status=CandidateStatus.APPROVED)
        code, out = _run_json(["candidates", "stage", cid], capsys)
        assert code == 1
        assert out["ok"] is False

    @pytest.mark.parametrize("action", ["stage", "approve", "reject"])
    def test_candidates_transition_unknown_candidate_exits_1(
        self,
        action: str,
        state_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Cover the KeyError branch (118-121) in cmd_candidates_transition."""
        code, out = _run_json(["candidates", action, "cand_missing"], capsys)
        assert code == 1
        assert out["ok"] is False
        assert "candidate not found" in out["error"]
        assert out["candidate_id"] == "cand_missing"

    def test_candidates_promote_keyerror_during_create_attempt(
        self,
        state_dir: Path,
        store: StateStore,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Cover the KeyError branch (175-179) of cmd_candidates_promote.

        The KeyError is raised by ``create_promotion_attempt`` when the
        candidate row vanishes between ``get_candidate`` and the insert.  We
        simulate that race by monkey-patching the method to raise.
        """
        cid = _seed_candidate(store, status=CandidateStatus.APPROVED)

        def _raise_key_error(self: StateStore, attempt: PromotionAttempt) -> None:
            raise KeyError(attempt.candidate_id)

        monkeypatch.setattr(StateStore, "create_promotion_attempt", _raise_key_error, raising=True)

        code, out = _run_json(["candidates", "promote", cid], capsys)
        assert code == 1
        assert out["ok"] is False
        assert "candidate not found" in out["error"]
        assert out["candidate_id"] == cid

    def test_candidates_archive_known_candidate(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store, status=CandidateStatus.REJECTED)
        code, out = _run_json(["candidates", "archive", cid], capsys)
        assert code == 0
        assert out["status"] == "archived"

    def test_candidates_archive_unknown_exits_1(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["candidates", "archive", "cand_missing"], capsys)
        assert code == 1
        assert out["ok"] is False

    def test_candidates_archive_invalid_transition_exits_1(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store, status=CandidateStatus.CANDIDATE)
        # archive once → archived; second archive is an illegal transition
        first, _ = _run_json(["candidates", "archive", cid], capsys)
        assert first == 0
        code, out = _run_json(["candidates", "archive", cid], capsys)
        assert code == 1
        assert out["ok"] is False

    def test_candidates_status_returns_counts(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_candidate(store, status=CandidateStatus.DETECTED, name="a")
        _seed_candidate(store, status=CandidateStatus.CANDIDATE, name="b")
        code, out = _run_json(["candidates", "status"], capsys)
        assert code == 0
        assert out["ok"] is True
        assert isinstance(out["status_counts"], dict)


# ---------------------------------------------------------------------------
# relations
# ---------------------------------------------------------------------------


class TestCmdRelations:
    def test_relations_add_list_remove_round_trip(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = _seed_candidate(store, name="src")
        dst = _seed_candidate(store, name="dst")

        code, out = _run_json(
            [
                "relations",
                "add",
                src,
                dst,
                "duplicate",
                "--confidence",
                "high",
                "--reasons",
                "identical content",
            ],
            capsys,
        )
        assert code == 0
        relation_id = out["relation_id"]

        code, out = _run_json(
            ["relations", "list", "--source-candidate-id", src, "--relation-type", "duplicate"],
            capsys,
        )
        assert code == 0
        assert any(r["relation_id"] == relation_id for r in out["relations"])

        code, out = _run_json(
            ["relations", "list", "--target-candidate-id", dst],
            capsys,
        )
        assert code == 0
        assert out["ok"] is True

        code, out = _run_json(["relations", "remove", relation_id], capsys)
        assert code == 0
        assert out["relation_id"] == relation_id

    def test_relations_add_self_reference_exits_1(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cid = _seed_candidate(store)
        code, out = _run_json(
            [
                "relations",
                "add",
                cid,
                cid,
                "duplicate",
                "--reasons",
                "self",
            ],
            capsys,
        )
        assert code == 1
        assert out["ok"] is False

    def test_relations_remove_unknown_exits_1(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["relations", "remove", "rel_missing"], capsys)
        assert code == 1
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


class TestCmdStorageRotate:
    def test_storage_rotate_returns_summary(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["storage", "rotate"], capsys)
        assert code == 0
        assert "summary" in out


# ---------------------------------------------------------------------------
# verify package
# ---------------------------------------------------------------------------


REQUIRED_VERIFY_FILES = [
    "hermes_skill_guard/data/default-config.yaml",
    "hermes_skill_guard/data/compat.yaml",
    "hermes_skill_guard/data/default_rules.json",
    "hermes_skill_guard/data/rules.schema.json",
    "hermes_skill_guard/_bundled_skills/skill-guard/SKILL.md",
    "hermes_skill_guard/_bundled_skills/skill-guard/references/workflow.md",
    "hermes_skill_guard/_bundled_skills/skill-guard/references/troubleshooting.md",
]

REQUIRED_SDIST_FILES = [f"src/{name}" for name in REQUIRED_VERIFY_FILES]


def _make_complete_sdist(path: Path) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name in REQUIRED_SDIST_FILES:
            data = b"stub"
            info = tarfile.TarInfo(name=f"hermes_skill_guard-0.0.0/{name}")
            info.size = len(data)
            import io as _io

            tf.addfile(info, _io.BytesIO(data))
    return path


def _make_complete_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in REQUIRED_VERIFY_FILES:
            zf.writestr(name, "stub")
    return path


def _make_incomplete_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(REQUIRED_VERIFY_FILES[0], "stub")
    return path


class TestCmdVerifyPackage:
    def test_verify_package_complete_wheel_exits_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wheel = _make_complete_wheel(tmp_path / "ok.whl")
        code, out = _run_json(["verify", "package", str(wheel)], capsys)
        assert code == 0
        assert out["ok"] is True
        assert out["artifacts"][0]["missing"] == []

    def test_verify_package_incomplete_wheel_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wheel = _make_incomplete_wheel(tmp_path / "broken.whl")
        code, out = _run_json(["verify", "package", str(wheel)], capsys)
        assert code == 1
        assert out["ok"] is False
        assert out["artifacts"][0]["missing"]

    def test_verify_package_handles_tar_gz(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sdist = _make_complete_sdist(tmp_path / "ok.tar.gz")
        code, out = _run_json(["verify", "package", str(sdist)], capsys)
        assert code == 0
        assert out["ok"] is True

    def test_verify_package_unknown_extension_reports_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unknown = tmp_path / "weird.zip"
        unknown.write_bytes(b"not a real archive")
        code, out = _run_json(["verify", "package", str(unknown)], capsys)
        # Unknown extension yields empty name set → all required entries missing.
        assert code == 1
        assert out["artifacts"][0]["missing"]

    def test_verify_package_defaults_to_dist_glob(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Cover the no-args glob fallback (lines 330-332) in cmd_verify_package."""
        dist = tmp_path / "dist"
        dist.mkdir()
        _make_complete_wheel(dist / "ok.whl")
        _make_complete_sdist(dist / "ok.tar.gz")
        monkeypatch.chdir(tmp_path)

        code, out = _run_json(["verify", "package"], capsys)
        assert code == 0
        assert out["ok"] is True
        assert {Path(a["path"]).name for a in out["artifacts"]} == {"ok.whl", "ok.tar.gz"}

    def test_verify_package_defaults_empty_when_no_dist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No artifacts in dist/ still goes through the glob fallback branch."""
        monkeypatch.chdir(tmp_path)
        code, out = _run_json(["verify", "package"], capsys)
        # No paths → all() over empty iterable is True → exit 0
        assert code == 0
        assert out["ok"] is True
        assert out["artifacts"] == []


# ---------------------------------------------------------------------------
# rules test
# ---------------------------------------------------------------------------


class TestCmdRulesTest:
    def test_rules_test_emits_pytest_hint(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run(["rules", "test"], capsys)
        assert code == 0
        assert "pytest" in out


# ---------------------------------------------------------------------------
# compat probe / list / restore
# ---------------------------------------------------------------------------


class TestCmdCompat:
    def test_compat_probe_records_results(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["compat", "probe"], capsys)
        assert code == 0
        assert out["ok"] is True
        assert out["probed"] >= 0
        assert isinstance(out["results"], dict)

    def test_compat_list_returns_modules(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store.record_probe_result(
            intent_id="intent.demo",
            status=ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
            confidence="medium",
            since_version=None,
            reason="for-test",
        )
        code, out = _run_json(["compat", "list"], capsys)
        assert code == 0
        assert any(m.get("intent_id") == "intent.demo" for m in out["modules"])

    def test_compat_restore_re_enables_retirement_candidate(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store.record_probe_result(
            intent_id="intent.restoreme",
            status=ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
            confidence="medium",
            since_version=None,
            reason="needs-restore",
        )
        code, out = _run_json(["compat", "restore", "intent.restoreme"], capsys)
        assert code == 0
        assert out["ok"] is True
        assert out["status"] == ModuleStatus.ENABLED.value

    def test_compat_restore_unknown_intent_exits_1(
        self, state_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = _run_json(["compat", "restore", "intent.nope"], capsys)
        assert code == 1
        assert out["ok"] is False
        assert "module not found" in out["error"]

    def test_compat_restore_rejects_enabled_module(
        self, state_dir: Path, store: StateStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store.record_probe_result(
            intent_id="intent.enabled",
            status=ModuleStatus.CANDIDATE_FOR_RETIREMENT.value,
            confidence="medium",
            since_version=None,
            reason="seed",
        )
        store.update_module_status("intent.enabled", ModuleStatus.ENABLED.value, "seed")
        code, out = _run_json(["compat", "restore", "intent.enabled"], capsys)
        assert code == 1
        assert out["ok"] is False
        assert "cannot restore" in out["error"]


# ---------------------------------------------------------------------------
# __main__ module entrypoint
# ---------------------------------------------------------------------------


class TestModuleEntrypoint:
    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    def test_runpy_invocation_covers_main_guard(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cover the ``if __name__ == '__main__': main()`` guard (line 523).

        Using ``runpy.run_module`` with ``run_name='__main__'`` executes the
        module under the same interpreter so coverage instrumentation sees the
        guard line being executed.
        """
        import runpy
        import sys

        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(sys, "argv", ["hermes_skill_guard", "report", "--json"])

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("hermes_skill_guard", run_name="__main__")
        assert exc_info.value.code == 0
