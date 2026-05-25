# A2A Audit Findings H6 / H10 / L3 / L4 — Implementation Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four remaining audit findings from the 2026-05-21 A2A spike security review — two HIGH (H6, H10), two LOW (L3, L4) — without breaking existing 45/45 tests, without changing the external API surface, and with full backward compatibility for current deployments.

**Architecture:** Each finding maps to an isolated, surgically targeted change (one file or test file per finding). No cross-finding dependencies exist except H10 must land before H6 so the SA-validation runs only when A2A is enabled. All changes are in `lib/a2a/` except H10's gate which extends `lib/a2a/__init__.py:register()`.

**Tech Stack:** Python 3.11+, FastAPI (server.py), cachetools, pytest, standard `logging`, GCP IAM SA email format constraints.

---

## Finding Summaries

| ID | Severity | What | File |
|----|----------|------|------|
| H6 | HIGH | `HERMES_A2A_SA` env var unvalidated at startup — typo/unset produces silent 403 on first outbound call | `lib/a2a/__init__.py` |
| H10 | HIGH | A2A routes always registered even when operator has not opted in — information disclosure risk | `lib/a2a/__init__.py` |
| L3 | LOW | `test_task_bridge.py` uses split import style (module-level `from` + function-level `import … as tb`) — readability/consistency | `lib/a2a/tests/test_task_bridge.py` |
| L4 | LOW | `_emit_audit_log` uses `print()` — bypasses logging infra, breaks `caplog`, incompatible with test capture | `lib/a2a/auth.py` |

---

## H10 — `HERMES_A2A_ENABLED` feature flag

### Design

Gate all A2A route registration and hook wiring behind `os.getenv("HERMES_A2A_ENABLED", "true").lower() == "true"`.

**Why `default=true`:** Existing deploys call `register(ctx)` unconditionally; flipping the default to `false` now would silently break them on upgrade. Transition plan: ship `default=true` now, log a deprecation WARNING on every startup where the var is unset, flip to `default=false` in the next minor release. Operators who explicitly set `HERMES_A2A_ENABLED=true` are unaffected.

**Gate location:** Inside `register()` in `lib/a2a/__init__.py`. This is the plugin entry point — gating here ensures:
- No FastAPI routes mounted (→ 404, not 503; avoids disclosing A2A surface to scanners)
- No hooks registered
- No JWKS fetches or auth middleware activated
- Import-time side effects are unaffected (module can still be imported safely in test environments)

**Observability:** Two log lines:
- `INFO: "a2a: HERMES_A2A_ENABLED=false; plugin skipped — no routes registered"` — when disabled
- `WARNING: "a2a: HERMES_A2A_ENABLED env var not set; defaulting true (will change in next release)"` — deprecation notice when unset

### File changes

**Modify:** `lib/a2a/__init__.py`

```python
import os

_ENABLED_RAW = os.getenv("HERMES_A2A_ENABLED")

def register(ctx) -> None:
    """Plugin entry point — registers A2A routes and hooks with Hermes."""
    if _ENABLED_RAW is None:
        logger.warning(
            "a2a: HERMES_A2A_ENABLED env var not set; defaulting true "
            "(will change to false in next release — set explicitly to suppress this warning)"
        )
    enabled = (_ENABLED_RAW or "true").lower() == "true"
    if not enabled:
        logger.info("a2a: HERMES_A2A_ENABLED=false; plugin skipped — no routes registered")
        return
    ctx.register_hook("on_session_start", _on_session_start)
    logger.info("a2a: plugin registered")
```

### Tests

**New test file:** `lib/a2a/tests/test_a2a_feature_flag.py`

Three tests:
1. `test_register_skipped_when_disabled`: monkeypatch `os.environ["HERMES_A2A_ENABLED"] = "false"`, create a spy `ctx`, call `register(ctx)`, assert `ctx.register_hook` was never called.
2. `test_register_proceeds_when_enabled`: `HERMES_A2A_ENABLED=true`, assert `register_hook` called once with `"on_session_start"`.
3. `test_deprecation_warning_when_unset`: remove `HERMES_A2A_ENABLED` from env, call `register(ctx)` with `caplog`, assert WARNING message contains `"will change to false in next release"`.

---

## H6 — `HERMES_A2A_SA` startup validation

### Design

Validate `HERMES_A2A_SA` format at startup when A2A is enabled. Emit a clear error and raise `RuntimeError` so the process refuses to start with a mis-configured SA identity.

**Why format-only:** GCP IAM validates identity at first `signJwt` call. Format-only at startup is the industry norm (gcloud, Cloud Run, Cloud Functions all follow this pattern). An ADC live-check at import time would break CI/test environments that have no credentials.

**Validation gate:** Inside the `enabled=true` branch of `register()`, immediately before `ctx.register_hook`. This ensures: (a) validation is skipped when A2A is disabled, (b) it runs once at plugin load, not per-request.

**Regex (GCP SA email constraints):**
- Name: starts with lowercase letter, ends in lowercase letter or digit, body `[a-z0-9-]`, total 6–30 chars
- Project: same constraints
- Suffix: literal `.iam.gserviceaccount.com`

```python
_SA_EMAIL_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
```

**Behavior when invalid/missing:**
```python
sa = os.getenv("HERMES_A2A_SA", "")
if not sa or not _SA_EMAIL_RE.fullmatch(sa):
    raise RuntimeError(
        f"A2A: HERMES_A2A_SA is missing or invalid: {sa!r}. "
        "Must be a GCP service account email "
        "(<name>@<project>.iam.gserviceaccount.com)"
    )
```

### File changes

**Modify:** `lib/a2a/__init__.py` (same file as H10 — same PR)

Add at module top:
```python
import re
_SA_EMAIL_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
```

Add inside `register()` after the `enabled` check:
```python
sa = os.getenv("HERMES_A2A_SA", "")
if not sa or not _SA_EMAIL_RE.fullmatch(sa):
    raise RuntimeError(
        f"A2A: HERMES_A2A_SA is missing or invalid: {sa!r}. "
        "Must be a GCP service account email (<name>@<project>.iam.gserviceaccount.com)"
    )
logger.info("a2a: HERMES_A2A_SA validated: %s", sa)
```

### Tests

Add to `lib/a2a/tests/test_a2a_feature_flag.py`:

4. `test_missing_sa_raises`: `HERMES_A2A_ENABLED=true`, unset `HERMES_A2A_SA`, assert `register(ctx)` raises `RuntimeError` with message containing `"HERMES_A2A_SA"`.
5. `test_malformed_sa_raises`: `HERMES_A2A_SA="not-an-email"`, assert `RuntimeError`.
6. `test_trailing_hyphen_sa_raises`: `HERMES_A2A_SA="bad-name-@proj.iam.gserviceaccount.com"` (trailing hyphen in name), assert `RuntimeError`.
7. `test_valid_sa_does_not_raise`: `HERMES_A2A_SA="agent-a@autonomous-agent-2026.iam.gserviceaccount.com"`, assert no exception.
8. `test_sa_validation_skipped_when_disabled`: `HERMES_A2A_ENABLED=false`, unset `HERMES_A2A_SA`, assert `register(ctx)` does NOT raise (validation gated behind enabled check).

---

## L3 — `test_task_bridge.py` import consistency

### Design

Consolidate to a single module-import form throughout. The current file has a module-level `from lib.a2a.task_bridge import (bridge_inbound_to_taskspec, bridge_taskspec_status_to_a2a, cancel_dispatch)` at line 34 AND a function-level `import lib.a2a.task_bridge as tb` at line 97 inside `test_cancel_dispatch`.

The monkeypatch currently works correctly (patches module globals, not test-module names), but the split style is inconsistent and would mislead future authors about how monkeypatching works.

**Target form:** Single module-level `import lib.a2a.task_bridge as tb`. All call sites reference `tb.<name>`. The `get_spec_metadata_for_test` imports inside `test_bridge_inbound_creates_taskspec` and `test_trace_id_in_taskspec_metadata` are try/except utility imports — leave those unchanged (they're already inside functions and guarded with `ImportError`).

### File changes

**Modify:** `lib/a2a/tests/test_task_bridge.py`

Replace lines 34–38:
```python
from lib.a2a.task_bridge import (  # noqa: E402
    bridge_inbound_to_taskspec,
    bridge_taskspec_status_to_a2a,
    cancel_dispatch,
)
```
With:
```python
import lib.a2a.task_bridge as tb  # noqa: E402
```

Then update every call site:
- `bridge_inbound_to_taskspec(...)` → `tb.bridge_inbound_to_taskspec(...)`
- `bridge_taskspec_status_to_a2a(...)` → `tb.bridge_taskspec_status_to_a2a(...)`
- `cancel_dispatch(...)` → `tb.cancel_dispatch(...)`

Remove the `import lib.a2a.task_bridge as tb` line from inside `test_cancel_dispatch` (line 97) — the module-level import makes it redundant.

### Verification

Run `pytest lib/a2a/tests/test_task_bridge.py -v` — all existing tests must pass. No new tests needed (pure refactor).

---

## L4 — `_emit_audit_log`: `print` → `logging`

### Design

Replace `print(json.dumps(entry), flush=True)` with a dedicated `logging.getLogger("a2a.audit")` logger. The library adds only a `NullHandler` — application startup owns the sink configuration.

**Why NullHandler not StreamHandler:** Python logging HOWTO is explicit: libraries must not add handlers. Adding `StreamHandler(sys.stdout)` at import time causes duplicate output if the application also configures a Cloud Logging handler, and prevents operators from routing `a2a.audit` to a different sink. `NullHandler` suppresses the "no handlers found" warning without taking ownership of routing.

**Backward compatibility:** Cloud Logging ingestion is unaffected. The Hermes startup already configures a root handler that routes `INFO` to stdout; `a2a.audit` propagates to it (propagate stays `True`). Operators who want to isolate the audit channel may configure `logging.getLogger("a2a.audit")` separately in Hermes startup.

**Message shape:** Preserved exactly — `json.dumps(entry)` is passed as the log message string. No format string substitution (avoids log-injection; `json.dumps` produces a safe, quoted string).

### File changes

**Modify:** `lib/a2a/auth.py`

At module top (after the existing `logger = logging.getLogger(__name__)` line — do NOT remove that line):
```python
_audit_logger = logging.getLogger("a2a.audit")
_audit_logger.addHandler(logging.NullHandler())
```

`_audit_logger` is a separate name alongside the existing `logger`; `logger` continues to handle module-level debug/info; `_audit_logger` is dedicated to HIPAA audit events.

In `_emit_audit_log`, replace:
```python
print(json.dumps(entry), flush=True)
```
With:
```python
_audit_logger.info(json.dumps(entry))
```

### Tests

Add `lib/a2a/tests/test_auth_audit_log.py`:

1. `test_emit_audit_log_accepted_uses_logger(caplog)`: Call `_emit_audit_log("accepted", mock_identity, None, None, None)` with `caplog.at_level(logging.INFO, logger="a2a.audit")`. Assert log record is present and the message is valid JSON containing `"decision": "accepted"`.
2. `test_emit_audit_log_rejected_contains_peer(caplog)`: Call with `decision="rejected_not_allowlisted"`, `peer_sa="evil@bad.iam.gserviceaccount.com"`. Assert `"peer_agent_id": "evil@bad.iam.gserviceaccount.com"` in parsed JSON.
3. `test_audit_log_does_not_print_to_stdout(capsys)`: Call `_emit_audit_log(...)`. Assert `capsys.readouterr().out == ""` — no stdout leakage via print.

---

## HAND-OFF.md additions

These three checklist items must be added to `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md` under the production checklist:

```markdown
- [x] **HERMES_A2A_ENABLED** — Set `HERMES_A2A_ENABLED=true` explicitly in Hermes env to suppress deprecation warning; flag default flips to false in next release.
- [x] **HERMES_A2A_SA format validated** — `register()` raises RuntimeError at startup if env var is missing or doesn't match GCP SA email format; deployment will refuse to start with a bad SA identity.
- [x] **`a2a.audit` logger sink** — Library emits HIPAA audit entries to `logging.getLogger("a2a.audit")`; application startup must ensure a handler routes this logger to Cloud Logging at INFO level (root handler suffices if propagate=True).
```

---

## Implementation sequencing

1. **H10 first** — gate in `register()`; includes deprecation warning; new test file scaffold
2. **H6 second** — SA validation inside H10's enabled branch; add remaining tests to same test file
3. **L4** — audit logger refactor; new test file
4. **L3** — test-only refactor; verify 45/45 still pass
5. **HAND-OFF.md** — mark three new items

All four findings can land in a single PR: `fix/a2a-audit-h6-h10-l3-l4`. Conventional commit title: `fix(a2a): SA validation, feature flag gate, audit logger, import cleanup`

---

## Backward-compatibility invariants

| Invariant | Guaranteed by |
|-----------|---------------|
| Existing 45/45 tests pass | L3 is pure refactor; H10/H6/L4 only add new paths; existing test env sets `HERMES_A2A_ENABLED=true` and `HERMES_A2A_SA=<valid>` |
| Current deploys don't break on upgrade | H10 defaults to `true` (same as current behavior) with deprecation warn |
| Cloud Logging still receives audit entries | L4 uses `propagate=True`; root handler routes to stdout → Cloud Logging ingests as before |
| A2A routes still reachable in current deploys | H10 default `true` preserves route registration |
| signJwt SA identity unchanged | H6 validates format only; same SA value used |
