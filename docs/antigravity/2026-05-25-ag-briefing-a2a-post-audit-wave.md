# Antigravity Briefing — A2A Post-Audit Wave
**Date:** 2026-05-25
**Priority:** HIGH — completes the A2A security posture and closes stale documentation
**Collision boundary:** You own `lib/a2a/server.py` and `audit/` for AG-1/AG-3. Do NOT touch `lib/a2a/auth.py`, `lib/a2a/client.py`, `lib/a2a/__init__.py`, or any `app/` path.

---

## Background

PR #145 merged today, closing the final four security audit findings (H6, H10, L3, L4) from the 2026-05-21 A2A spike review. All 33 audit findings are now addressed.

However:

1. **`audit/2026-05-21-a2a-spike-plan/HAND-OFF.md` is stale.** Eight checklist items are still marked `[ ]` but were shipped in earlier PRs. Two items are duplicated. The file needs an accuracy pass before it can serve as the production operator reference.

2. **`spike/a2a-v0.1` git tag is missing.** The spike plan requires tagging the spike completion commit. It is still on the checklist.

3. **AgentCard unsigned fallback is the only genuine remaining security gap.** `/.well-known/agent-card.json` currently serves an unsigned card when GCP `signBlob` fails — this is intentional for dev/CI but is documented as "broken on purpose" and must be hardened before production. The fix is: return HTTP 503 instead of serving the unsigned card.

---

## Repo State

- **Main HEAD:** `ea37296` — `fix(a2a): audit findings h6/h10/l3/l4 — sa validation, feature flag, audit logger (#145)`
- **Python env:** `.venv/` at repo root — use `.venv/bin/python -m pytest` for all test runs
- **All tests:** `lib/a2a/tests/` — 94 passing on main
- **Non-negotiable rules:**
  - No `git add -A` or `git add .` — stage specific files only
  - No `--no-verify` on commits
  - No force-push
  - No touching `lib/a2a/auth.py`, `lib/a2a/client.py`, `lib/a2a/__init__.py`
  - Branch naming: `fix/<desc>` with no dots in `<desc>`
  - PR title subject after `type(scope):` must start with **lowercase letter**
  - Squash-merge only (GitHub enforces this)

---

## What Is Actually Already Done (HAND-OFF stale items)

Before writing any code, understand what was shipped so you mark HAND-OFF.md accurately:

| HAND-OFF item | Actual status | Evidence |
|---|---|---|
| `mint_token` into `client.py:send_message` | ✅ **DONE** | `lib/a2a/client.py:_build_auth_headers()` imports and calls `mint_token`; `send_message()` calls `await _build_auth_headers(...)` at line 269 |
| Wire `scrub_inbound_params` into `jsonrpc_dispatch` | ✅ **DONE** | `lib/a2a/server.py:439` — `params = scrub_inbound_params(params)` inside `_jsonrpc_dispatch_inner` |
| Wire `scrub_inbound_params` before OTel span attribute | ✅ **DONE / N/A** | No `span.set_attribute()` calls exist with params in server.py — no PHI leaks into OTel spans |
| Body size limits (>1MB ASGI middleware) | ✅ **DONE** | Shipped in PR #142 (M3) |
| Negative JWKS caching | ✅ **DONE** | `lib/a2a/auth.py:_JWKS_FAIL_CACHE` — shipped in PR #142 (M6) |
| `_call_sign_blob` async | ✅ **DONE** | `lib/a2a/agent_card.py:69` — `async def _call_sign_blob(...)` |
| PHI scrubber on SSE routes | ✅ **DONE** | `lib/a2a/server.py:349` and `:377` — `scrub_inbound_params` in both `/stream` and `/subscribe` handlers |
| JWT auth on SSE routes | ✅ **DONE** | `lib/a2a/server.py` — `_jwt_guard` Depends on both SSE routes; shipped in PR #139 |
| Duplicate Redis jti entry | STALE DUPLICATE | Lines 50 and 69 in HAND-OFF.md both describe Redis jti — dedupe to one open item |
| Duplicate unsigned AgentCard fallback entry | STALE DUPLICATE | Lines 57 and 70 — dedupe to one open item (the 503 circuit-break) |

---

## Task AG-1: HAND-OFF.md Accuracy Pass

**Branch:** `fix/a2a-handoff-accuracy-pass`
**File:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`
**No tests required** — docs-only change.

### What to do

1. Mark these items `[x]` with a brief note pointing to the PR or line where it was done:

```markdown
- [x] Wire `mint_token` into `client.py` `send_message` outbound path — done: `_build_auth_headers()` in `lib/a2a/client.py:160-186` calls `mint_token`; wired into `send_message` at line 269
- [x] Wire `scrub_inbound_params` into `jsonrpc_dispatch` before handler dispatch — done: `lib/a2a/server.py:439`
- [x] Wire `scrub_inbound_params` before OTel span attribute attachment — done / N/A: no `span.set_attribute()` calls expose params in `server.py`; PHI does not reach OTel spans
- [x] Body size limits: add ASGI middleware to reject requests >1MB on `POST /`, `/stream`, `/subscribe` — done PR #142 (M3)
- [x] Negative JWKS caching: cache failed JWKS fetches (429/503) for 30s with jitter — done PR #142 (M6), `lib/a2a/auth.py:_JWKS_FAIL_CACHE`
- [x] `_call_sign_blob` async: convert from `httpx.post` (sync) to `AsyncClient.post` (async) — done: `lib/a2a/agent_card.py:69`
- [x] PHI scrubber on SSE routes: wired in PR #139 — confirmed: `server.py:349,377`
- [x] JWT auth on SSE routes: wired in PR #139 — confirmed: `_jwt_guard` Depends on both SSE route handlers
```

2. **Deduplicate** the two duplicate entries. The HAND-OFF.md has:
   - Two items for Redis jti replay cache (lines 50 and 69) — remove line 69, keep line 50
   - Two items for unsigned AgentCard fallback (lines 57 and 70) — remove line 57, update line 70 to reference the AG-3 fix

3. Update the `Wire `mint_token`` item in the "What is stubbed" table at the top — change its "Production fix" note to reflect it's done.

### Acceptance

```bash
# Verify no false [ ] items remain for done work:
grep "\- \[ \]" audit/2026-05-21-a2a-spike-plan/HAND-OFF.md
# Should show only: Redis jti, real SSE, tasks/get+cancel, Hard Cloud Trace, Peer federation, mTLS, Load test, Tag spike, AgentCard 503
```

### Commit

```bash
git add audit/2026-05-21-a2a-spike-plan/HAND-OFF.md
git commit -m "docs(a2a): hand-off accuracy pass — mark 8 done items, dedupe 2 entries"
```

---

## Task AG-2: Tag `spike/a2a-v0.1`

**No branch needed.** Run against main after AG-1 PR is merged.

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main && git pull
git tag -a spike/a2a-v0.1 -m "A2A spike v0.1 — Days 1-10 complete, all 33 audit findings closed"
git push origin spike/a2a-v0.1
```

Then update the HAND-OFF.md to mark `Tag spike commit: spike/a2a-v0.1` as `[x]`.

---

## Task AG-3: AgentCard unsigned fallback → 503 circuit-break

**Branch:** `fix/a2a-agent-card-503`
**Files:**
- Modify: `lib/a2a/server.py` — change fallback in `agent_card_endpoint()`
- Modify: `lib/a2a/tests/test_agent_card_signing.py` — update the test that asserts unsigned fallback

### Current behavior (the gap)

`lib/a2a/server.py`, the `agent_card_endpoint()` handler (around line 300-322):

```python
try:
    signed = await _sign_card(card, agent_sa)
except Exception as exc:
    logger.warning(
        "a2a: sign_card failed (%s) — serving unsigned card (dev fallback)", type(exc).__name__
    )
    signed = card          # ← BUG: exposes unsigned card to callers
return JSONResponse(content=signed)
```

### Required behavior

When `_sign_card` raises, return HTTP 503 with a JSON error body. Do NOT serve the unsigned card.

```python
try:
    signed = await _sign_card(card, agent_sa)
except Exception as exc:
    logger.warning(
        "a2a: sign_card failed (%s) — returning 503; unsigned card not served", type(exc).__name__
    )
    return JSONResponse(
        status_code=503,
        content={"error": "agent_card_signing_unavailable", "detail": type(exc).__name__},
    )
return JSONResponse(content=signed)
```

### Test that needs updating

`lib/a2a/tests/test_agent_card_signing.py` has a test that asserts the fallback serves an unsigned card. Find that test (search for "fallback" or "unsigned") and update it to assert:
- HTTP 503 status code when `_sign_card` raises
- Response body contains `{"error": "agent_card_signing_unavailable", ...}`
- The unsigned card is NOT served (response body does not contain `"signature"`)

**Do not delete the test — update its assertions.**

### Write the updated test FIRST (TDD)

Before changing server.py, update the test to expect 503. Confirm it fails. Then apply the server.py fix. Confirm it passes.

### Run the full suite after

```bash
.venv/bin/python -m pytest lib/a2a/tests/ -q --no-header 2>&1 | tail -5
# Expected: 94 passed (or 95 if the test now exercises a new path)
```

### Commit

```bash
git add lib/a2a/server.py lib/a2a/tests/test_agent_card_signing.py
git commit -m "fix(a2a): return 503 on signBlob failure instead of unsigned AgentCard"
```

### PR title

```
fix(a2a): return 503 on signBlob failure instead of unsigned agent card
```

### Update HAND-OFF.md after merge

Once the PR merges, mark the AgentCard 503 item `[x]` in HAND-OFF.md.

---

## Execution Order

1. **AG-1** (HAND-OFF accuracy) → PR → merge → mark `[x]` in local state
2. **AG-2** (git tag) → run immediately after AG-1 merges; update HAND-OFF.md and commit
3. **AG-3** (AgentCard 503) → PR → CI green → merge

AG-1 and AG-3 can be worked in parallel on separate branches if desired.

---

## What Is NOT In Scope For This Wave

The following items remain open and are deferred to a future sprint (require infrastructure or architectural decisions beyond a single Antigravity wave):

| Item | Why deferred |
|------|-------------|
| Redis jti replay cache | Requires Cloud Memorystore provisioning in Terraform + Redis client dependency; not a code-only change |
| Real SSE event stream | Requires `lib.anchors` event bus design decisions |
| `tasks/get`, `tasks/cancel` implementation | Requires `lib.anchors` query API design |
| Hard Cloud Trace assertion in e2e | Requires live GCP trace infrastructure in CI |
| Peer federation (AgentCard discovery feed) | Requires AgentCard service discovery protocol design |
| mTLS security review | External security review process |
| Load test | Requires load testing infrastructure setup |

---

## Acceptance Criteria Summary

| Task | Gate |
|------|------|
| AG-1 | No `[ ]` remains for any item confirmed done above; no duplicate entries |
| AG-2 | `git tag spike/a2a-v0.1` exists on origin; HAND-OFF item marked `[x]` |
| AG-3 | `/.well-known/agent-card.json` returns 503 when signBlob fails; no unsigned card served; existing tests pass; 94+ tests green |

---

## References

- Spike plan: `audit/2026-05-21-a2a-spike-plan/spike-plan.md`
- AgentCard spec: `audit/2026-05-21-a2a-spike-plan/DEFAULTS-ACCEPTED.md`
- HAND-OFF: `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`
- server.py: `lib/a2a/server.py` (~line 300 for AgentCard endpoint)
- agent_card.py: `lib/a2a/agent_card.py` (sign_card, _call_sign_blob)
- Test file: `lib/a2a/tests/test_agent_card_signing.py`
