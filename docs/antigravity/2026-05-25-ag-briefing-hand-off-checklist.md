# Antigravity Briefing — AG-2: HAND-OFF.md Checklist Corrections (L5, L6)
**Date:** 2026-05-25
**Model:** Gemini 3.1 Pro Preview (documentation update, no complex reasoning needed)
**Priority:** LOW — documentation accuracy
**Collision boundary:** Only file: `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`. No code changes.

---

## 1. Context

The HAND-OFF.md production checklist was written before certain fixes landed. A systematic audit has confirmed that two checklist items are already done but remain unchecked, and several new items need to be added based on the audit findings.

---

## 2. Corrections — Items Already Done

### Item to CHECK OFF: "JWKS TTL cache in `verify_token`"

**Evidence of completion:** `lib/a2a/auth.py` on `origin/main` has:
```python
_JWKS_CACHE: cachetools.TTLCache[str, list[dict]] = cachetools.TTLCache(maxsize=1_000, ttl=3_600)
```
This was implemented in PR #130 (Wave 2 server integration). The checklist incorrectly shows this as unchecked.

**Fix:** Find the line in HAND-OFF.md:
```
- [ ] JWKS TTL cache in `verify_token` (5-min TTL keyed on SA email)
```
Change to:
```
- [x] JWKS TTL cache in `verify_token` — DONE: `cachetools.TTLCache(maxsize=1_000, ttl=3_600)` in auth.py (PR #130). Note: TTL is 3600s; a separate fix (PR #138) reduces this to 900s to match Google's Cache-Control: max-age=900 directive.
```

### Item to CHECK OFF: `alert_strategy { auto_close = "1800s" }`

**Evidence of completion:** `terraform/phase-0a-gcp/monitoring.tf` has `alert_strategy { auto_close = "1800s" }` in both alert policies. This was merged in PR #133.

**Fix:** Find the line:
```
- [ ] Add `alert_strategy { auto_close = "1800s" }` to monitoring alert policies
```
Change to:
```
- [x] Add `alert_strategy { auto_close = "1800s" }` to monitoring alert policies — DONE in PR #133.
```

---

## 3. New Items to Add to Production Checklist

Add these NEW items to the production checklist at the end (before the References section):

```markdown
- [ ] `HERMES_A2A_ENABLED` feature flag: currently missing — A2A is always active when the module is imported. Add env var check (default `false`) in `lib/a2a/server.py` before FastAPI app is exposed.
- [ ] Body size limits: add ASGI middleware to reject requests > 1MB on POST /, /stream, /subscribe to prevent memory exhaustion.
- [ ] Negative JWKS caching: cache failed JWKS fetches (429/503 from googleapis.com) for 30s with jitter to prevent unbounded retries.
- [ ] `_call_sign_blob` async: convert from `httpx.post` (sync) to `httpx.AsyncClient.post` (async) to avoid blocking the event loop during AgentCard signing.
- [ ] `HERMES_A2A_SA` validation: validate this env var at startup against the actually-loaded ADC identity from `google.auth.default()`.
- [ ] Redis jti replay cache: replace per-process `cachetools.TTLCache` with Redis-backed atomic SET NX for cross-replica replay prevention (required before multi-replica Cloud Run).
- [ ] Remove unsigned AgentCard fallback: replace `except Exception` fallback in `agent_card_endpoint` with HTTP 503 response — do not serve unsigned cards in production.
- [ ] PHI scrubber on SSE routes: verify `/stream` and `/subscribe` routes call `scrub_inbound_params` (addressed in PR #136).
- [ ] JWT auth on SSE routes: verify `/stream` and `/subscribe` have `Depends(_jwt_guard)` (addressed in PR #136).
```

---

## 4. Execution

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main && git pull
git checkout -b fix/hand-off-checklist-update

# Edit HAND-OFF.md to make corrections
# (two checkmarks + add new items)

git add audit/2026-05-21-a2a-spike-plan/HAND-OFF.md
git commit -m "docs(audit): update HAND-OFF.md — mark completed items + add audit findings

- Mark JWKS TTL cache as DONE (was completed in PR #130, checklist stale)
- Mark alert_strategy auto_close as DONE (completed in PR #133)
- Add 9 new production checklist items from 2026-05-25 systematic audit:
  HERMES_A2A_ENABLED flag, body size limits, negative JWKS caching,
  _call_sign_blob async, SA validation, Redis jti, unsigned card 503,
  SSE scrubber + JWT (being addressed in PR #136)"

git push -u origin fix/hand-off-checklist-update

gh pr create \
  --title "docs(audit): update HAND-OFF.md — mark completed items + add audit findings" \
  --base main \
  --body "Accuracy corrections to the production checklist after systematic 2026-05-25 audit:
- Mark 2 items as done that are already on main (JWKS TTL cache, monitoring auto_close)
- Add 9 new checklist items from audit findings (HERMES_A2A_ENABLED, body size limits, etc.)
No code changes."
```

---

## 5. Acceptance Criteria

- `grep "\[ \] JWKS TTL" HAND-OFF.md` returns empty (item marked done)
- `grep "\[ \] Add.*auto_close" HAND-OFF.md` returns empty (item marked done)
- `grep "HERMES_A2A_ENABLED" HAND-OFF.md` returns the new checklist item
- CI passes (no code changes, only documentation)
