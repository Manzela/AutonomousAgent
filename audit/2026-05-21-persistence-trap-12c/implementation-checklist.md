# Persistence Trap (#12.c) — Implementation Checklist

**Purpose:** Step-by-step gating for the J3 trajectory shipper PR. Each `[ ]` item is verifiable; do not check off until the verification command produces evidence.

**Dependency on other tasks:**
- ✅ **F37** wired in `lib/durability/failure_matrix.py` (committed 2026-05-21 in current branch).
- ✅ **Model Armor sub-module** terraform written (`terraform/phase-0a-gcp/model-armor/` — operator must `terraform apply` before P0 items below.)
- ⏳ **Test contract** `test-contract.md` (sibling file) — frozen design; implementation translates this into code.

---

## P0 — Blocking the first GCS bucket binding

These items MUST be done before the J3 shipper has any production credential or any production bucket name in any config.

### P0.1 — Create the module skeleton

- [ ] `mkdir -p lib/trajectory/` + `touch lib/trajectory/__init__.py`
- [ ] Create `lib/trajectory/shipper.py` with this skeleton (NOT a copy of `lib/snapshots/gcs_snapshot.py`):
  ```python
  """J3 trajectory shipper. Tails judge-events JSONL, sanitizes via Model
  Armor, uploads to GCS. Persistence Trap (#12.c) MUST hold: every record
  uploaded MUST have passed templates.sanitize. If sanitize is unavailable,
  dispatch F37 and HALT — do NOT fall back to local-log of the un-redacted
  record. See audit/2026-05-21-persistence-trap-12c/test-contract.md."""

  class ModelArmorSanitizeUnavailable(Exception):
      """Raised when templates.sanitize fails or times out. Triggers F37."""

  class TrajectoryShipper:
      def __init__(self, bucket: str, template: str, sanitize_client=None, gcs_client=None):
          ...
      def ship_one(self, verdict: dict) -> None:
          """Sanitize via Model Armor, upload to GCS, or raise ModelArmorSanitizeUnavailable."""
          ...
      def ship_batch(self, verdicts: list[dict]) -> None:
          """Per-record sanitize + upload. A single record failure halts the batch."""
          ...
  ```

- [ ] Add module to `lib/trajectory/plugin.yaml` (mirror `lib/durability/plugin.yaml` shape — `name`, `version`, `description`, `kind: standalone`, `provides_hooks: []`).

### P0.2 — Wire the sanitize call (per-record, not per-batch)

- [ ] Implement `ship_one` as: `sanitized = self.sanitize_client.sanitize(template=self.template, content=json.dumps(verdict)) ; self.gcs_client.upload(...)`
- [ ] **Critical**: per-record, not per-batch. Per-batch sanitize is tempting (one API call vs N) but it hides single-record failures and makes Test 3 unreliable.
- [ ] On sanitize failure: `raise ModelArmorSanitizeUnavailable(...) from caught_exc`. Do NOT catch the sanitize exception inside the try-block.

### P0.3 — Wire the F37 dispatch

- [ ] Wrap `ship_one` calls in `ship_batch` with:
  ```python
  try:
      self.ship_one(record)
  except ModelArmorSanitizeUnavailable as exc:
      from lib.durability.handlers import dispatch
      dispatch("F37", error=exc, tool_call_id=record["tool_call_id"],
               payload={"shipper": "trajectory", "verdict_id": record["tool_call_id"]})
      raise
  ```
- [ ] **Critical**: re-raise after dispatch. The handler decides "halt" semantics (snapshot + Telegram + BLOCKED state); the shipper's job is to ensure the un-redacted record does not reach GCS. Re-raise stops the per-record loop and prevents the next record from being shipped under a known-unavailable sanitize endpoint.

### P0.4 — Verification gate (DO NOT skip)

Before any GCS bucket name lands in any deployed config:

- [ ] `pytest -m persistence_trap -v` → 3/3 pass with stubbed clients
- [ ] `pytest tests/unit/test_failure_matrix.py -v` → 8/8 still pass (F37 lockstep intact)
- [ ] `grep -rn "GCS_TRAJECTORY_BUCKET\|trajectory_bucket" config/ terraform/` → confirm no production bucket name committed before tests are green

---

## P1 — Before first production upload

### P1.1 — Live nightly verification

- [ ] Add `PERSISTENCE_TRAP_LIVE=1` job to `.github/workflows/nightly.yml` that hits the real Model Armor template after the Phase 0a operator confirms `terraform apply` succeeded for the model-armor sub-module
- [ ] First nightly run must show:
  - Test 1 (Floor-only) PASS with live endpoint
  - Object exists in nightly bucket with zero canary tokens, all 4 markers present
- [ ] Failure of this nightly job triggers Telegram alert (reuse the F37 handler's alert path)

### P1.2 — Operator runbook update

- [ ] Append to `audit/2026-05-20-model-armor-j1-runbook/runbook.md`:
  - §7 "J3 Persistence Trap" — describes the 3 test variants + their meaning
  - §8 "Incident response: F37 fired" — first-3-minute checklist (`gcloud model-armor floorsettings describe`, check sanitize API quota, inspect Telegram alert payload for `tool_call_id`)

### P1.3 — Tail-and-batch loop (the actual shipper)

- [ ] Implement `lib/trajectory/tailer.py` that reads `trajectories/judge-events.jsonl` and yields records since last-shipped offset (offset persisted in `trajectories/.shipper-offset`)
- [ ] Wire to `TrajectoryShipper.ship_batch` with `max_records_per_batch=50`, `batch_interval_s=30`
- [ ] On `ModelArmorSanitizeUnavailable`: stop the tailer, do NOT advance the offset, exit non-zero. Operator restarts after fixing Model Armor.

### P1.4 — Telemetry

- [ ] Emit `trajectory.shipper.records_shipped` counter (OTel) per successful ship_one
- [ ] Emit `trajectory.shipper.records_blocked` counter incremented on F37 dispatch
- [ ] Emit `trajectory.shipper.sanitize_duration_ms` histogram — Model Armor latency is the dominant tail-latency contributor; need visibility for capacity planning

---

## P2 — Defense in depth (post-MVP)

### P2.1 — Inline-sanitize at the writer (gap G1)

- [ ] Add optional `pre_persist_sanitize=True` flag to `lib/evaluators/judge_events.py::_emit_jsonl()`
- [ ] When enabled: pass `verdict` through the same `templates.sanitize` before the local JSONL write
- [ ] Tradeoff: doubles Model Armor call volume (writer + shipper). Off by default. Enable for environments where the local JSONL itself is a persistence boundary (e.g., shared NFS).

### P2.2 — Canary-token gitleaks rule (gap G2)

- [ ] Append to `.gitleaks.toml`:
  ```toml
  [[rules]]
  id = "persistence-trap-canary"
  description = "Persistence Trap test canary PII — only allowed under tests/"
  regex = '''canary\+persistencetrap@example\.test|999-88-7777|4111-1111-1111-1111|\(555\)\s*010-1234'''
  path = '''^(?!tests/).*'''
  ```
- [ ] Verify rule fires by attempting to commit a canary token outside `tests/` and confirming pre-commit blocks the commit.

### P2.3 — Memory-subsystem Persistence Trap (gap G3)

- [ ] Spawn `audit/2026-05-21-memory-persistence-trap/` — same shape as #12.c, scoped to Honcho + Chroma ingest paths.
- [ ] Defer until J3 shipper is live and the J1→J3 path is provably tight (no point gating the memory path against an upstream that may itself be leaky).

---

## Anti-patterns to reject in review

If reviewers see any of these in a `lib/trajectory/` PR, request changes:

| Anti-pattern | Why it's wrong | Correct alternative |
|---|---|---|
| `try: sanitize() except: continue` | Defers the leak; doesn't stop it. | `try: sanitize() except as exc: raise ModelArmorSanitizeUnavailable() from exc` |
| `fallback_local_log` handler for F37 | Backlog grows, operator pressured to drain with redaction disabled. | `halt_alert_snapshot` (already wired). |
| Per-batch sanitize (one API call for N records) | Single-record failure poisons the whole batch's redaction state. | Per-record sanitize. |
| Copy of `lib/snapshots/gcs_snapshot.py` as the starting point | Inherits fail-OPEN posture by default. | Fresh module per skeleton in P0.1. |
| Sanitize call inside `if ENABLE_PII_REDACTION:` flag | Allows ops to disable PII redaction. The contract is unconditional. | No feature flag around sanitize. The whole shipper is the feature flag — either it ships sanitized or it doesn't ship. |
| Catching `ModelArmorSanitizeUnavailable` anywhere except the orchestrator's outermost handler | Defeats F37 dispatch. | Let it propagate; F-dispatch happens in `ship_batch` and the orchestrator's `after_tool_call` hook re-classifies on the way out. |

---

## Done-when

The Persistence Trap contract is satisfied iff:

- ✅ All P0 items checked
- ✅ `pytest -m persistence_trap` green in PR CI
- ✅ Live nightly green for 3 consecutive nights
- ✅ Runbook §7 + §8 written and linked from the F37 handler's docstring

Anything less, the shipper does not get a production bucket binding.
