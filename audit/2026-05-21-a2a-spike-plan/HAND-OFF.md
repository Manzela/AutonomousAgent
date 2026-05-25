# A2A Spike Hand-Off Note

**Spike:** Days 1-10 — A2A bidirectional canary integration
**Date completed:** 2026-05-25
**Waves:** Wave 1 (PRs #126/#127), Wave 2 (PR #130), Wave 3 (PR TBD)

---

## What works (spike scope)

| Feature | Files | Status |
|---------|-------|--------|
| JSON-RPC 2.0 dispatch (`POST /`) | `lib/a2a/server.py` | ✅ `message/send` live; others `-32004` |
| SSE streaming (`POST /stream`, `POST /subscribe`) | `lib/a2a/server.py` | ✅ 3 synthetic events, `text/event-stream` |
| JWT auth guard | `lib/a2a/auth.py`, `server.py` | ✅ `verify_token` + jti replay + HIPAA audit log |
| OTel traceparent propagation | `lib/a2a/client.py`, `server.py` | ✅ W3C extract/inject, InMemorySpanExporter in CI |
| TaskSpec bridge | `lib/a2a/task_bridge.py`, `server.py` | ✅ `bridge_inbound_to_taskspec` wired into `message/send` |
| AgentCard | `lib/a2a/agent_card.py`, `server.py` | ✅ JCS canonicalize + signBlob + verify + `/.well-known/` |
| PHI scrubber | `lib/a2a/scrubber.py` | ✅ 7 patterns, recursive scrubbing |
| E2E demo | `scripts/a2a-e2e-demo.sh` | ✅ Stub-canary fallback, `SKIP_CLOUD_TRACE=1` local |

---

## What is stubbed (known gaps — not bugs)

| Gap | Location | Production fix |
|-----|----------|----------------|
| jti replay cache is per-process | `lib/a2a/auth.py` | Redis-backed `TTLCache` shared across replicas |
| SSE events are synthetic (3 hardcoded frames) | `lib/a2a/server.py` | Wire to `lib.anchors` event bus |
| `tasks/get`, `tasks/cancel` → `-32004` | `lib/a2a/server.py` | Implement via `lib.anchors` queries |
| Unsigned AgentCard fallback | `lib/a2a/server.py` | Return 503 on GCP signBlob error (AG-3) |
| Peer discovery out-of-band | `config/a2a/peers.yaml` | AgentCard discovery feed |

---

## What is broken on purpose

- **Allow-unauthenticated transport**: Cloud Run set to allow-all so we can iterate without IAM churn. JWT guard at the application layer + HIPAA audit log are the compensating controls. mTLS at the transport layer is deferred to v2 per auth-design.md §7.3.
- **Single-instance JWT replay cache**: OOM-proof at spike load but replays across replicas. Documented as `TODO(replay-cache-distributed)` in `auth.py`.
- **Unsigned card fallback**: intentional for dev/CI where GCP signBlob is unavailable. Logs `WARNING` but does not break the server.

---

## Production checklist

- [ ] Redis-backed jti replay cache replacing `cachetools.TTLCache`
- [x] JWKS TTL cache in `verify_token` (5-min TTL keyed on SA email) — PR #130
- [x] Wire `scrub_inbound_params` into `jsonrpc_dispatch` before handler dispatch — done: `lib/a2a/server.py:439`
- [x] Wire `scrub_inbound_params` before OTel span attribute attachment — done / N/A: no `span.set_attribute()` calls expose params in `server.py`; PHI does not reach OTel spans
- [ ] Real SSE event stream from `lib.anchors` event bus (not synthetic 3-frame generator)
- [ ] Implement `tasks/get` and `tasks/cancel` via lib.anchors API
- [x] Wire `mint_token` into `client.py` `send_message` outbound path — done: `_build_auth_headers()` in `lib/a2a/client.py:160-186` calls `mint_token`; wired into `send_message` at line 269
- [x] Add `alert_strategy { auto_close = "1800s" }` to monitoring alert policies — PR #133
- [ ] Hard Cloud Trace assertion in e2e demo (not best-effort warn)
- [ ] Peer federation: move from static `peers.yaml` to AgentCard discovery feed
- [ ] Security review: assess mTLS overlay requirement for HIPAA posture
- [ ] Load test: JWT mint/verify at 100 RPS sustained; SSE hold-open at 50 concurrent
- [x] Tag spike commit: `spike/a2a-v0.1` — tagged on main HEAD f885f4c
- [x] `HERMES_A2A_ENABLED` feature flag: gates `register()` in `lib/a2a/__init__.py`; default=true with deprecation warn (flips to false next release) — PR fix/a2a-audit-h6-h10-l3-l4; **operator: set explicitly to suppress warning**
- [x] Body size limits: add ASGI middleware to reject requests >1MB on `POST /`, `/stream`, `/subscribe` — done PR #142 (M3)
- [x] Negative JWKS caching: cache failed JWKS fetches (429/503) for 30s with jitter — done PR #142 (M6), `lib/a2a/auth.py:_JWKS_FAIL_CACHE`
- [x] `_call_sign_blob` async: convert from `httpx.post` (sync) to `AsyncClient.post` (async) — done: `lib/a2a/agent_card.py:69`
- [x] `HERMES_A2A_SA` validation: format-only validation at startup (GCP SA email regex `^[a-z][a-z0-9-]{4,28}[a-z0-9]@...\.iam\.gserviceaccount\.com$`); raises RuntimeError on bad/missing value — PR fix/a2a-audit-h6-h10-l3-l4; **ADC live-check deferred (breaks CI/test envs)**
- [ ] Remove unsigned AgentCard fallback: return 503 on signBlob error, not unsigned card
- [x] PHI scrubber on SSE routes: wired in PR #139 — confirmed: `server.py:349,377`
- [x] JWT auth on SSE routes: wired in PR #139 — confirmed: `_jwt_guard` Depends on both SSE route handlers
- [x] `a2a.audit` logger: `_emit_audit_log` now emits via `logging.getLogger("a2a.audit")` (NullHandler, propagate=True); **operator: ensure root handler routes INFO to Cloud Logging, or attach dedicated handler to `a2a.audit`** — PR fix/a2a-audit-h6-h10-l3-l4

---

## References

- `audit/2026-05-21-a2a-spike-plan/spike-plan.md` — daily plan and kill criteria
- `audit/2026-05-21-a2a-spike-plan/auth-design.md` — JWT composite identity pattern
- `audit/2026-05-21-a2a-spike-plan/telemetry-design.md` — OTel dual-emit pattern
- `audit/2026-05-21-a2a-spike-plan/integration-points.md` — where each module plugs into Hermes
- `docs/superpowers/plans/2026-05-25-a2a-wave2-server.md` — Wave 2 plan (SA4)
- `docs/superpowers/plans/2026-05-25-a2a-wave3-demo.md` — Wave 3 plan (SA5)
