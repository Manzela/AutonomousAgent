# Persistence Trap (#12.c) — Findings

**Audit date:** 2026-05-21
**Scope:** Application-layer enforcement that the J1 → J3 trajectory pipeline cannot persist un-sanitized PII to GCS, even if Model Armor Floor Settings are bypassed or templates.sanitize is unavailable.
**Cross-refs:**
- `audit/2026-05-20-model-armor-j1-runbook/runbook.md` — Floor Settings + Sanitize API contract
- `terraform/phase-0a-gcp/model-armor/` — Floor Settings + DLP InspectTemplate provisioning
- `lib/durability/failure_matrix.py` — F37 (Model Armor sanitize unavailable, FAIL_LOUD, halt_alert_snapshot)
- `docs/architecture/failure-matrix.md` §"Stream B PII gate (F37)" — rationale for fail-loud posture
- ADR-0008 Q6 disposition (`audit/2026-05-20-architecture-research-gap-analysis/stream-b-open-questions-disposition.md`)

---

## 1. The trap, restated

Model Armor Floor Settings (`google_model_armor_floorsetting`) enforce PII redaction on **every Model Armor API call against the project**. They do NOT enforce anything against a caller that writes to GCS *without* calling Model Armor in the first place. The J3 trajectory shipper, by design, will read judge verdicts from local JSONL and upload them to GCS — if it does so without explicitly invoking `templates.sanitize`, Floor Settings save nothing.

This is the "Persistence Trap": a config-layer defense (Floor Settings) that the operator believes is total but that an application-layer bug can quietly bypass, with the failure mode being *training-substrate PII memorization in Phase 4 RL* — functionally unrecallable after model weights are updated.

## 2. What exists today

### 2.1 Upstream writer — `lib/evaluators/judge_events.py` (219 lines, fail-OPEN)

J1 ships a JSONL recorder for 4-judge consensus + 5th tiebreaker verdicts. Behavior verified by direct read:

- **Path:** `trajectories/judge-events.jsonl` (DEFAULT_PATH, line ~18)
- **Locking:** `fcntl.flock(LOCK_EX)` advisory locking on POSIX (line ~88) — multi-process safe within one host, NOT across hosts
- **Schema:** `schema_version=1`, fields include `verdict` (raw judge text), `consensus`, `tool_call_id`, `tool_name`, `args`, `result_preview`, `judge_responses[]`
- **PII risk surface:** `args` and `result_preview` contain whatever the orchestrator passed to the tool — `args.email`, `args.user_id`, customer-ticket payloads, etc. Any tool call that touches user data leaks into the JSONL.
- **Fail-open at write site:** the `_emit_jsonl()` function wraps the entire write in `try / except Exception: log.warning(...) ; return None`. A write failure (disk full, locked file, schema error) silently drops the record. **This is intentional** — judge events must never crash the orchestrator hot path. But it means we cannot rely on "the writer would have crashed" as a safety net.

### 2.2 Downstream shipper — DOES NOT EXIST AS CODE YET

`lib/trajectory/` does not exist. `grep -rln "trajectory.*ship\|judge_events.jsonl" lib/` returns only `lib/evaluators/judge_events.py` (the producer) and `docs/architecture/runtime-trajectory.md` (the design memo). The J3 shipper described in ADR-0008 / disposition memo § "#12 / J3" is **scaffolding-only**.

This is the most important fact in this audit: **we have a chance to design the shipper correctly the first time, instead of bolting Persistence Trap protection onto an existing pipeline.** The Test Contract (next file) is therefore a *design gate* — code merged for J3 MUST pass these tests before any GCS bucket is wired up.

### 2.3 Closest analog — `lib/snapshots/gcs_snapshot.py` (425 lines, fail-OPEN, NO F-dispatch)

Reviewed because it is the only existing pattern in the repo for "local file → GCS upload":

- Auth: ADC via `google.auth.default()` (line ~74)
- Feature flag: `GCS_SNAPSHOT_BUCKET` env var; absence → no-op (line ~58)
- Idempotency: date-prefixed object names (line ~152) — re-uploads overwrite
- **PII posture: NONE.** Snapshots are operator DR artifacts, intended for the operator's eyes only, and contain no end-user data. The module deliberately omits any Model Armor or DLP call.
- **F-dispatch on upload failure: NONE.** Catches `google.cloud.exceptions.GoogleCloudError`, logs WARNING, returns. **This is the wrong template for J3.**

**Implication.** A junior implementer who copies `gcs_snapshot.py` as the J3 starting point will inherit the fail-OPEN posture by default. The implementation checklist (file 3) must explicitly call out "do not template off `gcs_snapshot.py` for the upload path."

### 2.4 F-code wiring — F37 added 2026-05-21 (commit pending)

`lib/durability/failure_matrix.py` now contains:

```python
"F37": {
    "class": TrichotomyClass.FAIL_LOUD,
    "description": "Model Armor templates.sanitize unavailable (J1 trajectory shipper PII redaction)",
    "handler": "halt_alert_snapshot",
},
```

`lib/durability/handlers.py` `HANDLER_REGISTRY` auto-discovers `halt_alert_snapshot` from the matrix at import time (`_make_stub` + name-keyed dict at module bottom), so F37 picks up the right handler with zero additional code. Verified by `pytest tests/unit/test_failure_matrix.py -v` → 8/8 pass.

`lib/durability/trichotomy.py` `_CLASSIFIERS` table does NOT include an F37 regex. **This is intentional** — F37 is raised by the shipper code via explicit `dispatch("F37", ...)`, not by pattern-matching a caught exception. If a `ModelArmorSanitizeUnavailable` exception ever leaks past the shipper's try-block into a generic tool's `post_tool_call` hook, the classifier falls through to F33 (Fail-Loud, unknown) — which is also halt + alert, so still correct PII posture.

## 3. Why Floor Settings alone are insufficient

Floor Settings enforce sanitize **at the Model Armor API boundary**. They give us:

1. ✅ Every `templates.sanitize` call against the project applies the configured InspectTemplate → no "forgot to set the template" footgun.
2. ✅ Floor Setting drift is observable via `gcloud model-armor floorsettings describe` (runbook §4).
3. ❌ A caller that skips `templates.sanitize` entirely faces no Model Armor gate — Floor Settings cannot intercept what they never see.

The J3 shipper *will* be such a caller by default. Its job is "tail JSONL, batch, upload to GCS." Nothing about that job statement invokes Model Armor. The Persistence Trap is the explicit application-layer contract — codified in tests — that the shipper MUST call sanitize and MUST fail loud when sanitize is unavailable, even if it makes the shipper backlog grow.

## 4. Threat model — three concrete leak paths

These are the leak shapes the test contract must close:

| # | Shape | How it happens | Defense |
|---|-------|----------------|---------|
| T1 | Shipper bypass | Engineer writes `bucket.upload(jsonl_path)` directly. No sanitize call. | **Test contract** — integration test fails if any record in the uploaded JSONL contains a canary PII token. |
| T2 | Sanitize-fail soft-skip | Engineer wraps `sanitize()` in `try/except: continue`. Catches network errors, ships record unredacted. | **F37 fail-loud** — handler is `halt_alert_snapshot`, not `fallback_local_log`. Re-asserted in test 3. |
| T3 | Template drift | Floor Setting points at an InspectTemplate that no longer contains EMAIL_ADDRESS. Sanitize "succeeds" but with empty redaction. | **Canary-payload roundtrip** — test seeds a known-PII verdict, asserts canary tokens absent from uploaded blob. Drift detected automatically when this test fails after a template edit. |

T1 and T3 are caught by the same canary-payload assertion. T2 is caught by the F37 fail-loud assertion + an explicit broken-sanitize test variant.

## 5. Out of scope for #12.c

- **In-flight PII at inference time.** That is Floor Settings' job — covered by `terraform/phase-0a-gcp/model-armor/main.tf`'s `enforcement = true`. We are not re-testing Floor Settings here.
- **Phase 4 RL training-time PII filtering.** That is a Phase 4 concern (post-Unsloth, pre-Vertex AI custom training). #12.c stops PII from *reaching* the training substrate; Phase 4 will need a second-pass scrub at training-data-ingestion time.
- **Honcho / Chroma side-channel persistence.** Judge verdicts also flow into memory subsystems via `lib/memory/`. PII leakage there is a separate concern; tracked as a follow-up gap (see §6).
- **Operator-facing dashboards (Phoenix).** Phoenix shows tool args + judge verdicts to the operator. Operator-side PII viewing is governed by IAP + operator NDA, not Model Armor.

## 6. Gaps surfaced by this audit (follow-ups, not blockers for #12.c)

- **G1.** `judge_events.py` writes raw `args` to JSONL with no in-line redaction. Acceptable today because the JSONL is local-only, but if the shipper goes live the JSONL itself becomes a persistence boundary. Consider an inline-sanitize pass at the writer side too — defense in depth.
- **G2.** No detect-secrets / gitleaks rule for the canary PII tokens. A developer who paste-tests a real customer email in a unit test could check it into git. Add canary token patterns to `.gitleaks.toml` once the contract is locked.
- **G3.** Memory subsystem (Honcho, Chroma) ingests verdicts as part of "skill extraction." No equivalent Persistence Trap exists for the memory ingest path. Track as `audit/2026-05-21-memory-persistence-trap/` (TBD).
- **G4.** Phoenix UI traces include raw `result_preview` strings. If Phoenix is exposed beyond the operator, this is a leak. Out of scope today (Phoenix is IAP-only in Phase 0a).

These do not block the #12.c contract from landing; they expand the threat model for the next iteration.
