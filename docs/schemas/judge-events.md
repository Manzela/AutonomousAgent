# Judge-Panel Event Log (JSONL) — Schema v1

**File:** `trajectories/judge-events.jsonl` (path configurable via
`config/limits.yaml → evaluators.judge_events.path`)
**Producer:** `lib/evaluators/judge_events.record_consensus_event()` — called
by the evaluators plugin after each 4-judge consensus decision (Task 21 wires
the live dispatch).
**Consumer:** the RL trajectory shipper (J3) tails this file and forwards
events to the GCP-side trajectory bucket for reward shaping.

## Why JSONL, not OTel spans

Judge events feed the RL pipeline as **training data**, not as observability
spans. OTel spans (J2/J11) cover the LLM call telemetry; the judge-events log
is the structured record of *decisions* the consensus reached — schemas evolve
on a different cadence than otel-genai semantic conventions, and the
trajectory shipper needs random-access to a fixed schema, not free-form
attribute bags.

## File format

Append-only UTF-8 JSONL. One well-formed JSON object per line, no trailing
comma, no schema preamble. Writes are `fcntl.flock`-guarded so multiple judge
threads in the same process do not interleave partial lines.

The file is **fail-open**: a write failure is logged at WARNING and swallowed
— it must never break the consensus call path.

## Event schema (v1)

```json
{
  "event_id": "5f47e2b1-...-...-...-...",
  "timestamp_utc": "2026-05-20T16:42:01.123456Z",
  "schema_version": 1,
  "session_id": "sess-abc123",
  "task_spec_id": "task-9f8e7d",
  "worker_action_summary": "Edited lib/foo/bar.py: added retry_with_backoff",
  "consensus": {
    "verdict": "accept",
    "accept_count": 4,
    "reject_count": 0,
    "unsure_count": 0,
    "escalated": false,
    "rationale": "4/4 accept >= 75%"
  },
  "judges": [
    {
      "axis": "code-correctness",
      "score": 9,
      "verdict": "accept",
      "reasoning": "Implementation is correct and idiomatic.",
      "model": "vertex_ai/claude-sonnet-4-6"
    }
  ],
  "fifth_judge": null
}
```

### Field reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `event_id` | str (uuid4) | yes | Stable id for downstream dedup |
| `timestamp_utc` | str (ISO 8601, Z) | yes | Microsecond resolution |
| `schema_version` | int | yes | Bumped only via ADR |
| `session_id` | str | yes | Correlates with checkpoints + REJECTED.md |
| `task_spec_id` | str | yes | Correlates with anchors / TaskSpec |
| `worker_action_summary` | str | yes | Truncated to ≤500 chars + truncation marker |
| `consensus.verdict` | str | yes | `accept` \| `reject` \| `needs_5th_judge` \| `fail_loud` |
| `consensus.accept_count` | int | yes | Includes 5th-judge vote when escalated |
| `consensus.reject_count` | int | yes | Includes 5th-judge vote when escalated |
| `consensus.unsure_count` | int | yes | Original 4 judges' unsure votes |
| `consensus.escalated` | bool | yes | True iff 5th judge was dispatched |
| `consensus.rationale` | str | yes | Short human-readable explanation |
| `judges` | list (length 4) | yes | One entry per axis |
| `judges[].axis` | str | yes | `code-correctness` \| `safety` \| `scope-fit` \| `completeness` |
| `judges[].score` | int (0..10) | yes | Per-axis rubric score |
| `judges[].verdict` | str | yes | `accept` \| `reject` \| `unsure` |
| `judges[].reasoning` | str | yes | Truncated to ≤1000 chars |
| `judges[].model` | str \| null | no | Provider/model id (per `config/limits.yaml`) |
| `fifth_judge` | obj \| null | yes | Null unless escalated; same shape as `judges[]` |

## Versioning policy

- **Additive**: new optional fields may appear without bumping `schema_version`.
  J3 consumers must ignore unknown fields.
- **Breaking** (rename, type change, semantic shift): bump `schema_version`,
  write an ADR, update both this doc and `lib/evaluators/judge_events.py`.

## Operator notes

- File is in `.gitignore` (`trajectories/judge-events.jsonl*`). Treat it
  like runtime telemetry: rotation, archival, and backup are the
  trajectory shipper's responsibility (J3).
- To disable persistence (e.g., for a noisy bench run), set
  `evaluators.judge_events.enabled: false` in `config/limits.yaml`.
- Truncation markers in long fields look like `...(truncated 1500 chars)` so
  downstream tools can detect and re-fetch from upstream if needed.

## Related

- `lib/evaluators/consensus.py` — produces `ConsensusResult`
- `lib/evaluators/judge.py` — produces `JudgeResult`
- `docs/decisions/0005-self-rl-pipeline-architecture.md` — RL pipeline ADR
- `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md` §P1-2 —
  judge-panel spec
