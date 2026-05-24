"""Unit tests for judge-panel JSONL event persistence (J1).

Persistence is fail-open: any I/O or permission error must be logged and
swallowed — never raise into the consensus call path. Each successful append
is one well-formed JSON object on its own line.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from lib.evaluators.consensus import ConsensusResult
from lib.evaluators.judge import JudgeResult
from lib.evaluators.judge_events import (
    SCHEMA_VERSION,
    record_consensus_event,
)


def _judge(axis: str, verdict: str, score: int = 8, model: str | None = None) -> JudgeResult:
    return JudgeResult(
        axis=axis,
        score=score,
        verdict=verdict,
        reasoning=f"{axis} reasoning",
        model=model,
    )


def _result(verdict: str = "accept", *, fifth: JudgeResult | None = None) -> ConsensusResult:
    judges = [
        _judge("code-correctness", "accept" if verdict != "reject" else "reject"),
        _judge("safety", "accept" if verdict != "reject" else "reject"),
        _judge("scope-fit", "accept" if verdict != "reject" else "reject"),
        _judge("completeness", "accept" if verdict != "reject" else "reject"),
    ]
    return ConsensusResult(
        verdict=verdict,
        accept_count=4 if verdict == "accept" else 0,
        reject_count=4 if verdict == "reject" else 0,
        unsure_count=0,
        escalated=fifth is not None,
        rationale="test rationale",
        judges=judges,
        fifth_judge=fifth,
    )


def test_record_writes_one_line_jsonl(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    out = record_consensus_event(
        _result("accept"),
        session_id="sess-1",
        task_spec_id="task-abc",
        worker_action_summary="Edited foo.py",
        path=path,
        enabled=True,
    )
    assert out == path
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["schema_version"] == SCHEMA_VERSION
    assert obj["session_id"] == "sess-1"
    assert obj["task_spec_id"] == "task-abc"
    assert obj["worker_action_summary"] == "Edited foo.py"
    assert obj["consensus"]["verdict"] == "accept"
    assert obj["consensus"]["accept_count"] == 4
    assert len(obj["judges"]) == 4
    assert obj["fifth_judge"] is None


def test_record_appends_without_truncating(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    for i in range(3):
        record_consensus_event(
            _result("accept"),
            session_id=f"sess-{i}",
            task_spec_id="task-x",
            worker_action_summary=f"action {i}",
            path=path,
            enabled=True,
        )
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["session_id"] for line in lines] == ["sess-0", "sess-1", "sess-2"]


def test_record_includes_required_fields(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=True,
    )
    obj = json.loads(path.read_text().splitlines()[0])
    required = {
        "event_id",
        "timestamp_utc",
        "schema_version",
        "session_id",
        "task_spec_id",
        "worker_action_summary",
        "consensus",
        "judges",
        "fifth_judge",
    }
    assert required.issubset(obj.keys())
    # event_id is a uuid4 (36 chars with dashes)
    assert len(obj["event_id"]) == 36
    # ISO8601 with Z suffix, no timezone offset
    assert obj["timestamp_utc"].endswith("Z")


def test_record_serializes_fifth_judge(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    fifth = _judge("tiebreaker", "accept", score=9, model="vertex_ai/claude-opus-4-7")
    record_consensus_event(
        _result("accept", fifth=fifth),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=True,
    )
    obj = json.loads(path.read_text().splitlines()[0])
    assert obj["fifth_judge"] is not None
    assert obj["fifth_judge"]["axis"] == "tiebreaker"
    assert obj["fifth_judge"]["score"] == 9
    assert obj["fifth_judge"]["verdict"] == "accept"
    assert obj["fifth_judge"]["model"] == "vertex_ai/claude-opus-4-7"
    assert obj["consensus"]["escalated"] is True


def test_record_truncates_long_worker_action_summary(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    long_summary = "x" * 2000
    record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary=long_summary,
        path=path,
        enabled=True,
    )
    obj = json.loads(path.read_text().splitlines()[0])
    # Truncated to 500 chars + a "...(truncated N chars)" marker (~25 chars).
    summary = obj["worker_action_summary"]
    assert summary.startswith("x" * 500)
    assert "truncated" in summary
    assert len(summary) < len(long_summary)
    assert len(summary) <= 540


def test_record_disabled_returns_none_no_write(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    out = record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=False,
    )
    assert out is None
    assert not path.exists()


def test_record_creates_missing_parent_dir(tmp_path: Path):
    path = tmp_path / "deep" / "nested" / "judge-events.jsonl"
    out = record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=True,
    )
    assert out == path
    assert path.exists()


def test_record_swallows_permission_error(tmp_path: Path, monkeypatch):
    """A filesystem failure must not raise — consensus must complete."""

    def boom(*_args, **_kwargs):
        raise PermissionError("read-only filesystem (simulated)")

    monkeypatch.setattr("lib.evaluators.judge_events._append_line", boom)
    out = record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=tmp_path / "j.jsonl",
        enabled=True,
    )
    assert out is None


def test_record_concurrent_appends_no_partial_lines(tmp_path: Path):
    """20 threads each writing one event must yield 20 well-formed lines."""
    path = tmp_path / "judge-events.jsonl"

    def writer(i: int) -> None:
        record_consensus_event(
            _result("accept"),
            session_id=f"sess-{i}",
            task_spec_id="t",
            worker_action_summary=f"a{i}",
            path=path,
            enabled=True,
        )

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = path.read_text().splitlines()
    assert len(lines) == 20
    # Every line must parse cleanly — no torn writes.
    for line in lines:
        obj = json.loads(line)
        assert obj["schema_version"] == SCHEMA_VERSION


def test_record_reject_verdict_persists(tmp_path: Path):
    path = tmp_path / "judge-events.jsonl"
    record_consensus_event(
        _result("reject"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=True,
    )
    obj = json.loads(path.read_text().splitlines()[0])
    assert obj["consensus"]["verdict"] == "reject"
    assert obj["consensus"]["reject_count"] == 4
    assert all(j["verdict"] == "reject" for j in obj["judges"])


def test_record_reads_enabled_from_config_when_not_overridden(tmp_path: Path, monkeypatch):
    """If `enabled=None`, fall back to config (default true)."""
    path = tmp_path / "judge-events.jsonl"
    monkeypatch.setattr("lib.evaluators.judge_events._config_enabled", lambda: True)
    out = record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
    )
    assert out == path


def test_record_reads_disabled_from_config_when_not_overridden(tmp_path: Path, monkeypatch):
    path = tmp_path / "judge-events.jsonl"
    monkeypatch.setattr("lib.evaluators.judge_events._config_enabled", lambda: False)
    out = record_consensus_event(
        _result("accept"),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
    )
    assert out is None
    assert not path.exists()


@pytest.mark.parametrize(
    "verdict,expected_a,expected_r",
    [
        ("accept", 4, 0),
        ("reject", 0, 4),
    ],
)
def test_record_counts_match_consensus_result(
    tmp_path: Path, verdict: str, expected_a: int, expected_r: int
):
    path = tmp_path / "judge-events.jsonl"
    record_consensus_event(
        _result(verdict),
        session_id="s",
        task_spec_id="t",
        worker_action_summary="a",
        path=path,
        enabled=True,
    )
    obj = json.loads(path.read_text().splitlines()[0])
    assert obj["consensus"]["accept_count"] == expected_a
    assert obj["consensus"]["reject_count"] == expected_r
