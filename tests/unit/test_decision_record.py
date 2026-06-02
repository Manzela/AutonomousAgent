"""SP-01 decision-record: plain append-only JSONL keyed by interrupt_id (§9).

Extends the judge_events.py append-only discipline; hash-chain deferred to SP-27.
"""

from __future__ import annotations

import json
import threading

from app.core.decision_record import append_decision


def _decision(verb, iid):
    return {"verb": verb, "actor": "operator", "reason": "ok", "interrupt_id": iid, "ts": "z"}


def test_append_writes_one_jsonl_line_keyed_by_interrupt_id(tmp_path):
    p = tmp_path / "decision-record.jsonl"
    append_decision(_decision("APPROVE", "i1"), path=p)
    append_decision(_decision("REJECT", "i2"), path=p)
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["interrupt_id"] == "i1"
    assert rec0["verb"] == "APPROVE"
    assert rec0["schema_version"] == 1


def test_append_is_append_only_not_truncating(tmp_path):
    p = tmp_path / "dr.jsonl"
    for i in range(5):
        append_decision(_decision("APPROVE", f"i{i}"), path=p)
    assert len(p.read_text().splitlines()) == 5


def test_append_is_fail_open_on_bad_path():
    # a path under a non-creatable parent must not raise (fail-open audit append)
    assert append_decision(_decision("APPROVE", "i"), path="/proc/nonexistent/dr.jsonl") is None


def test_disabled_is_a_noop(tmp_path):
    p = tmp_path / "dr.jsonl"
    assert append_decision(_decision("APPROVE", "i"), path=p, enabled=False) is None
    assert not p.exists()


def test_concurrent_appends_no_torn_lines(tmp_path):
    p = tmp_path / "dr.jsonl"

    def worker(n):
        append_decision(_decision("APPROVE", f"i{n}"), path=p)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = p.read_text().splitlines()
    assert len(lines) == 40
    for line in lines:
        json.loads(line)  # every line is a complete JSON record (no torn writes)
