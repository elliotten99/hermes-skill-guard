"""CLI integration tests using direct main() calls.

Avoids subprocess overhead (<10ms per test vs 100ms+).
All tests verify behaviour through observable stdout output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_skill_guard.__main__ import main
from hermes_skill_guard.ids import new_candidate_id, new_event_id, new_trace_id
from hermes_skill_guard.schemas import Candidate, CandidateStatus
from hermes_skill_guard.storage.repository import StateStore


class TestCliDoctor:
    def test_doctor_reports_wal_and_empty_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        with pytest.raises(SystemExit) as exc_info:
            main(["doctor"])
        assert exc_info.value.code == 0

        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["ok"] is True
        assert parsed["doctor"]["storage"]["wal_enabled"] is True
        assert parsed["doctor"]["storage"]["summary"]["events"] == 0


class TestCliCandidates:
    def test_list_empty_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "list"])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["candidates"] == []

    def test_approve_existing_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        store = StateStore(tmp_path / "state.db")
        cid = new_candidate_id()
        store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test",
                content_hash="hash",
                status=CandidateStatus.CANDIDATE,
            )
        )

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "approve", cid])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["status"] == "approved"

    def test_reject_existing_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        store = StateStore(tmp_path / "state.db")
        cid = new_candidate_id()
        store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test",
                content_hash="hash",
                status=CandidateStatus.CANDIDATE,
            )
        )

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "reject", cid])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["status"] == "rejected"

    def test_promote_approved_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        store = StateStore(tmp_path / "state.db")
        cid = new_candidate_id()
        store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test",
                content_hash="hash",
                status=CandidateStatus.APPROVED,
            )
        )

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "promote", cid])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["status"] == "pending_promotion"
        assert out["tool_name"] == "skill_manage"
        assert out["tool_args"]["skill_guard_promotion_attempt_id"] == out["attempt_id"]

    def test_promote_nonexistent_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "promote", "nonexistent"])
        assert exc_info.value.code == 1

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_promote_invalid_transition_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        store = StateStore(tmp_path / "state.db")
        cid = new_candidate_id()
        store.create_candidate(
            Candidate(
                candidate_id=cid,
                source_event_id=new_event_id(),
                trace_id=new_trace_id(),
                name="test",
                description="test",
                content_hash="hash",
                status=CandidateStatus.DETECTED,
            )
        )

        with pytest.raises(SystemExit) as exc_info:
            main(["candidates", "promote", cid])
        assert exc_info.value.code == 1

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False
        assert out["error"] == "candidate must be approved before promotion"


class TestCliReport:
    def test_report_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        with pytest.raises(SystemExit) as exc_info:
            main(["report", "--json"])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert "summary" in out


class TestCliStorage:
    def test_storage_rotate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SKILL_GUARD_STATE_DIR", str(tmp_path))

        with pytest.raises(SystemExit) as exc_info:
            main(["storage", "rotate"])
        assert exc_info.value.code == 0

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
