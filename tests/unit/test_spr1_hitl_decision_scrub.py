"""SP-R1 (F-4) — the HITL DECISION RECORD's operator free-text (actor + reason) is
scrubbed before it enters EITHER the durable JSONL non-repudiation trail OR the
checkpointed state delta, via the SAME lib/scrubber as every other persisted path.

PRD §6 SP-R1: "assert NONE [of the planted PII/secret shapes] appear verbatim in the
persisted checkpoint bytes ... the serializer routes through the same lib/scrubber.py."
§9 names the decision record as the non-repudiation trail. `_record_decision` builds the
HITL record from the operator's resumed decision dict — `actor` and `reason` are FREE TEXT
the operator types, so they can carry an email / API key / PEM block.

NON-VACUOUS: before F-4, `_record_decision` passed `actor`/`reason` through UNSCRUBBED to
both `append_decision()` (durable trajectories/decision-record.jsonl) and the returned
`{gate: hitl, "decision_record": [hitl]}` state delta — a secret in an operator's reason
landed verbatim on disk and in the checkpoint (RED). GREEN once both fields are scrubbed.

verb (a constrained HitlVerb arbitration enum), interrupt_id (a UUID), and ts (a timestamp)
are NOT free text and are deliberately left unscrubbed — scrubbing verb could corrupt a
valid enum and break C15 arbitration.
"""

from __future__ import annotations

import json

from app.core.graph import _record_decision

# Four distinct shapes the scrubber covers: openai/anthropic key, AWS access-key id, email
# PII (added in #247), and a PEM private-key block. Assembled-from-parts PEM so the file
# source never contains the contiguous header (detect-private-key pre-commit hook); the
# RUNTIME value is a real header, which is what private_key_pem must catch.
_API_KEY = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
_AWS = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
_EMAIL = "alice.operator@example.com"
_PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIBVgIBADANBg\n-----END " + "PRIVATE KEY-----"


def _decision_with_secrets() -> dict:
    return {
        "verb": "REJECT",
        "actor": f"operator {_EMAIL}",
        "reason": f"blocked — leaked key={_API_KEY} aws={_AWS} pem={_PEM}",
        "interrupt_id": "int-abc-123",
    }


def test_secret_absent_from_returned_state_delta():
    delta = _record_decision("ship_gate", _decision_with_secrets())
    blob = json.dumps(delta)
    assert _API_KEY not in blob, "API-key leaked into the checkpointed decision_record delta"
    assert _AWS not in blob, "AWS access-key id leaked into the delta"
    assert _EMAIL not in blob, "operator email leaked into the delta"
    assert _PEM not in blob, "private-key block leaked into the delta"


def test_secret_absent_from_durable_jsonl(tmp_path, monkeypatch):
    # Redirect the durable trail to a tmp file (hermetic — never touch the repo's
    # trajectories/ dir); the path is resolved per-call from SPINE_DECISION_RECORD_PATH.
    dr = tmp_path / "decision-record.jsonl"
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(dr))

    _record_decision("sign_off", _decision_with_secrets())

    on_disk = dr.read_text()
    assert _API_KEY not in on_disk, "API-key leaked verbatim into the durable JSONL trail"
    assert _AWS not in on_disk, "AWS access-key id leaked verbatim into the durable JSONL"
    assert _EMAIL not in on_disk, "operator email leaked verbatim into the durable JSONL"
    assert _PEM not in on_disk, "private-key block leaked verbatim into the durable JSONL"
    # the record IS still written (scrub is not suppression) — readers still get a decision
    rec = json.loads(on_disk.splitlines()[-1])
    assert rec["verb"] == "REJECT" and rec["interrupt_id"] == "int-abc-123"


def test_routes_through_lib_scrubber(monkeypatch):
    # Call-site identity: actor + reason must route through the SAME lib.scrubber.scrub_string
    # the rest of the spine uses (so PII/secret coverage cannot drift between paths).
    import app.core.graph as graph_mod

    seen: list[str] = []
    real = graph_mod.scrub_string

    def _spy(text, *, source="unknown"):
        seen.append(text)
        return real(text, source=source)

    monkeypatch.setattr(graph_mod, "scrub_string", _spy)
    _record_decision("sign_off", _decision_with_secrets())
    assert any(_API_KEY in t for t in seen), "reason did not route through lib.scrubber"
    assert any(_EMAIL in t for t in seen), "actor did not route through lib.scrubber"


def test_clean_decision_preserved():
    # control: benign free text survives intact, and the non-free-text fields are untouched.
    delta = _record_decision(
        "ship_gate",
        {"verb": "APPROVE", "actor": "operator-1", "reason": "ship it", "interrupt_id": "int-9"},
    )
    rec = delta["decision_record"][0]
    assert rec["verb"] == "APPROVE"
    assert rec["actor"] == "operator-1"
    assert rec["reason"] == "ship it"
    assert rec["interrupt_id"] == "int-9"


def test_non_dict_decision_still_records_verb():
    # back-compat: a bare verb string is coerced to {"verb": ...} and still records.
    delta = _record_decision("sign_off", "TIMEOUT")
    assert delta["decision_record"][0]["verb"] == "TIMEOUT"
