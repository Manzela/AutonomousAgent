# A2A Spike Plan — 10 Working Days to Bidirectional Canary Integration

**Date:** 2026-05-21
**Duration:** 2 calendar weeks = 10 working days (Mon-Fri × 2).
**Owner:** TBD (likely Phase-2-1 implementer).
**Reviewer:** Spike sponsor (architecture lead).
**Definition of "done":** Working bidirectional A2A integration with **one** canary peer (stub agent is fine), built behind a feature flag, observable in Cloud Trace + Phoenix, with signed audit logs and a documented hand-off note describing the gap between the spike and production-ready.

---

## 1. Spike outcomes — what success looks like at end of Day 10

A short-form, end-to-end demo:

1. We start two Hermes-shaped containers locally (`agent-a` and `agent-canary`), each with the A2A plugin enabled and its own GCP service account.
2. From `agent-a`, the user sends an intent: *"Ask canary to compute X."*
3. Hermes mints a JWT (composite identity), POSTs JSON-RPC `message/send` to canary, receives a `Task` back, polls it via `tasks/get`, subscribes to status updates via `tasks/subscribe` (SSE), and reports the result to the user.
4. In the reverse direction, canary calls `agent-a` with `message/send`; `agent-a` accepts the call, creates a TaskSpec, runs a stub workflow, and streams status back via SSE.
5. The whole flow shows up as **one** trace in Cloud Trace; Phoenix shows the LLM spans nested inside the A2A spans.
6. Every request emits exactly one structured Cloud Logging audit entry on the receiver side.
7. Six smoke tests pass: outbound mint → verify, inbound verify, replay rejected, expiry rejected, audience mismatch rejected, scrubber redacts PHI on A2A message bodies.

---

## 2. Day-by-day plan

Each day has: **goal**, **deliverable**, **acceptance gate**, **dependencies**, **failure mode → action**. Days are 6-7 productive hours; assume 1h overhead.

### Day 1 — Scaffolding + spec stamp

- **Goal**: Create `lib/a2a/` plugin shell that registers with Hermes and does literally nothing useful, but loads cleanly. Pin the A2A spec version.
- **Deliverable**:
  - `lib/a2a/__init__.py` with `register(ctx)` entry point (no-op hooks).
  - `lib/a2a/plugin.yaml` manifest.
  - Empty `lib/a2a/{server.py, client.py, agent_card.py, auth.py, task_bridge.py}` with docstring + TODO.
  - `lib/a2a/tests/test_plugin_loads.py` — imports the module, asserts `register` exists.
  - `requirements-a2a.txt` (or pyproject extra `a2a`): `fastapi`, `uvicorn[standard]`, `httpx>=0.27`, `httpx-sse`, `pyjwt[crypto]`, `cachetools`, `python-ulid`, `cryptography`, `python-multipart`.
  - `config/a2a/peers.yaml` template with placeholder canary.
  - `deploy/docker-compose.yml` patch (PR draft) mounting `../lib/a2a` into the Hermes plugin path.
  - `audit/2026-05-21-a2a-spike-plan/SPEC-VERSION.md` recording the A2A spec commit SHA we pinned to.
- **Acceptance gate**: `pytest lib/a2a/tests/test_plugin_loads.py -q` green; `docker compose config` validates; Hermes starts with the empty plugin and logs `register: a2a` once.
- **Dependencies**: none.
- **Failure mode → action**: If Hermes plugin loader rejects our manifest, spend ≤4h debugging; if not resolved by EOD, raise to reviewer for plugin contract clarification. Block: would prevent Day 2 server work, mitigate by stubbing as a standalone uvicorn app for now.

### Day 2 — Receiver side, minimal handler

- **Goal**: Stand up `POST /` JSON-RPC dispatch that accepts `message/send` and returns a hard-coded `Task` with state `SUBMITTED`. No auth yet (allow-all).
- **Deliverable**:
  - `lib/a2a/server.py`: FastAPI app, JSON-RPC envelope parsing, method dispatch table, hard-coded `handle_send_message` returning a synthetic Task.
  - Run the app via `uvicorn lib.a2a.server:app --port 9001` inside the Hermes container alongside the gateway.
  - `lib/a2a/tests/test_server_dispatch.py`: 3 tests — valid request returns 200; unknown method returns `-32601`; malformed JSON returns `-32700`.
  - Compose: expose port 9001 on the canary side only for now.
- **Acceptance gate**: `curl -X POST http://localhost:9001/ -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"USER","parts":[{"text":"hi"}]}}}' -H "Content-Type: application/json"` returns `{"jsonrpc":"2.0","id":1,"result":{"id":"task-...","status":"SUBMITTED"}}`.
- **Dependencies**: Day 1.
- **Failure mode → action**: If FastAPI conflicts with Hermes's existing async runtime, fall back to running A2A server as a separate sidecar container; this is fine for the spike.

### Day 3 — Sender side, minimal client

- **Goal**: Outbound A2A client that calls the Day-2 server.
- **Deliverable**:
  - `lib/a2a/client.py`: `async def send_message(...)` that constructs the JSON-RPC envelope, POSTs via `httpx.AsyncClient`, decodes the response, maps `-32xxx` errors to Python exceptions (`A2AError` hierarchy).
  - `lib/a2a/tests/test_client.py`: round-trip test against an in-process FastAPI TestClient — assert `send_message` returns the Task dict.
  - A throwaway CLI: `python -m lib.a2a.cli send --peer http://localhost:9001/ --text "hi"` for manual exercising.
- **Acceptance gate**: The throwaway CLI prints a `Task` dict. The roundtrip test passes.
- **Dependencies**: Day 2.
- **Failure mode → action**: SSE will be Day 4; if `httpx-sse` proves flaky, fall back to `aiohttp-sse-client` (documented alternative). Decision deadline: end of Day 3.

### Day 4 — SSE streaming (`message/stream` + `tasks/subscribe`)

- **Goal**: Get the streaming sub-protocol working bidirectionally. This is the highest-risk transport item.
- **Deliverable**:
  - Server-side: `handle_stream_message` and `handle_subscribe_task` returning `StreamingResponse` with SSE-formatted events. Emit 3 synthetic events per call: `status: WORKING`, `artifact_added`, `status: COMPLETED`.
  - Client-side: `async def stream_message(...)` using `httpx-sse` to consume the stream, yielding events.
  - `lib/a2a/tests/test_streaming.py`: assert client receives 3 events in the correct order.
- **Acceptance gate**: Streaming test passes; an interactive demo with the CLI prints 3 events.
- **Dependencies**: Day 3.
- **Failure mode → action**: If streaming is broken end-to-end and we can't unblock in 1.5 days, **this triggers the kill-if-blocked criterion** (see §5). SSE is not optional — the A2A protocol's value prop is real-time agent collab. Without it the spike is a polling toy.

### Day 5 — Auth: minter + verifier

- **Goal**: Replace allow-all auth with the JWT pattern from [`auth-design.md`](./auth-design.md).
- **Deliverable**:
  - `lib/a2a/auth.py`: `mint_token`, `verify_token`, `AgentIdentity` dataclass, in-memory TTL caches for both minted JWTs and `jti` replay, `_emit_audit_log` helper.
  - Wire `mint_token` into `client.py`; wire `verify_token` into `server.py`'s middleware.
  - Provision a second SA `agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com` in Terraform (or via gcloud out-of-band for the spike — call it out as a debt item). Grant the Hermes runtime SA `roles/iam.serviceAccountTokenCreator` on both `agent-a` and `agent-canary`.
  - `config/a2a/peers.yaml` populated with the canary SA's email as the issuer.
  - `lib/a2a/tests/test_auth.py`: 7 tests covering the acceptance criteria in `auth-design.md` §11.
- **Acceptance gate**: 7 auth tests pass; a request without `Authorization` returns `-32001` (Unauthenticated); a request from a non-allowlisted SA returns `-32001`.
- **Dependencies**: Days 2-4.
- **Failure mode → action**: If `signJwt` hits quota limits, request a quota bump (rare for spike volume). If JWKS fetching is flaky in CI, mock the JWKS in tests using `pytest-httpx`.

### Day 6 — Telemetry: traceparent + dual-emit

- **Goal**: Get the full trace tree from `telemetry-design.md` §7 visible in Cloud Trace.
- **Deliverable**:
  - `lib/a2a/client.py`: `propagate.inject(headers)` on outbound calls; client span with both `llm.*` and `gen_ai.*` attrs.
  - `lib/a2a/server.py`: `propagate.extract` + `otel_context.attach` in dispatch; server span with both attribute sets.
  - SSE: per-event child spans on the receiver side.
  - `lib/a2a/tests/test_telemetry.py`: use `opentelemetry.sdk.trace.export.in_memory_span_exporter.InMemorySpanExporter` to assert (1) the inbound span's `trace_id` matches the outbound span's `trace_id`, (2) all expected attrs are set, (3) `gen_ai.*` attrs appear only when dual-emit is on.
  - Verify in dev: run the bi-directional demo with `HERMES_DUAL_EMIT_GEN_AI=1` and confirm trace appears in Cloud Trace via the OTel collector at `deploy/otel/collector.prod.yaml`.
- **Acceptance gate**: Telemetry tests pass; Cloud Trace shows one trace spanning both agents.
- **Dependencies**: Day 5.
- **Failure mode → action**: If Cloud Trace ingestion is silent (no trace appears), debug at the collector layer first; if the issue is on the GCP side, fall back to verifying the trace via the in-memory exporter and document Cloud Trace as a follow-up.

### Day 7 — Hermes integration: TaskSpec ↔ A2A Task bridge

- **Goal**: When an A2A inbound `message/send` arrives, create a TaskSpec (anchors plugin); when its state changes, mirror to the A2A `Task.status`.
- **Deliverable**:
  - `lib/a2a/task_bridge.py`: `bridge_inbound_to_taskspec(a2a_task, agent_identity) -> TaskSpec`; `bridge_taskspec_status_to_a2a(spec) -> TaskState`; mapping table from `SpecStatus` (draft/draft_locked/locked/superseded) → `TaskState` (SUBMITTED/WORKING/COMPLETED/CANCELED/FAILED).
  - `server.py` calls into the bridge instead of returning a hard-coded Task.
  - When an inbound A2A `tasks/cancel` arrives, invoke the existing `/cancel` slash command path in `anchors`.
  - `lib/a2a/tests/test_task_bridge.py`: 4 tests covering each state transition.
- **Acceptance gate**: Inbound A2A call creates a TaskSpec visible via `hermes spec list`; cancellation via A2A propagates to `/cancel` semantics.
- **Dependencies**: Day 5 (auth gives us the human_sub to attach to the TaskSpec); Day 6 (trace_id is included in the TaskSpec metadata for audit).
- **Failure mode → action**: If `anchors` doesn't expose a public-ish API for spec creation, do the minimal thing — call the private `_draft_from_intent` directly with a comment noting the layering issue. File a follow-up to surface a public API.

### Day 8 — AgentCard + discovery

- **Goal**: Serve a signed `/.well-known/agent-card.json`; fetch and verify the peer's.
- **Deliverable**:
  - `lib/a2a/agent_card.py`: `build_agent_card()` returns the card dict (id, capabilities=`message_send`+`message_stream`+`task_get`+`task_subscribe`, supported security schemes=oauth2+jwt, JWKS URL pointing to Google).
  - JWS signing using the same agent SA via `signJwt` (the spec allows JWS on the card itself).
  - JCS canonicalization (RFC 8785) before signing — use `jcs` Python package; smoke test the round-trip.
  - `server.py`: route `GET /.well-known/agent-card.json` returns the signed card.
  - `client.py`: `async def discover(peer_base_url) -> AgentCard` fetches the card, verifies JWS, caches result.
  - `lib/a2a/tests/test_agent_card_signing.py`: 3 tests — sign-then-verify round-trip; tampered card rejected; expired card rejected.
- **Acceptance gate**: Both agents publish a signed card; both can discover and verify each other's card; tampering a single byte causes rejection.
- **Dependencies**: Day 5 (signJwt wiring).
- **Failure mode → action**: JCS canonicalization is the highest interop-bug risk surface in the whole spike (default-value pruning rules are subtle). If the spike hits canon round-trip failures, fall back to **not** signing the card (serve unsigned) and document the gap; the auth-on-requests layer remains intact.

### Day 9 — Hardening + scrubber + canary peer hookup

- **Goal**: Patch the known gaps. Wire a real canary peer that lives in a separate compose stack.
- **Deliverable**:
  - **Scrubber bypass fix** ([`integration-points.md`](./integration-points.md) §10): make `lib/a2a/client.py` and `lib/a2a/server.py` call `lib.scrubber.scrub(...)` on outbound message bodies (sender) and inbound message bodies before attaching to spans (receiver). Add `lib/a2a/tests/test_scrubber_integration.py` with a synthetic PHI sample.
  - Canary peer: spin up a second compose stack (`deploy/docker-compose.canary.yml`) with a stub Hermes shaped exactly like ours but with a hard-coded "echo + delay" behavior. This is the *peer* in the demo.
  - Wire the two together over a Docker network; both have JWKS connectivity to Google.
  - Run the end-to-end demo from §1, top to bottom.
- **Acceptance gate**: The Day-10 demo runs end-to-end on a dev box: bidirectional flow, scrubber redacts, trace shows up in Cloud Trace, audit log entries appear in Cloud Logging.
- **Dependencies**: Days 1-8.
- **Failure mode → action**: If the second Hermes container is too heavy to run alongside, the canary can be a minimal Python FastAPI app implementing only the methods we need. Time-box stub canary to 2h.

### Day 10 — Polish, documentation, hand-off

- **Goal**: Make the spike review-ready and write the hand-off note.
- **Deliverable**:
  - All 6 spec deliverables in `audit/2026-05-21-a2a-spike-plan/` updated with any "learned" footnotes (mark each as `## Updates (2026-06-01)` block).
  - `docs/a2a-spike-handoff.md`: 1-2 pages covering (a) what works, (b) what's stubbed, (c) what's broken on purpose, (d) what we'd need to do to ship to prod (Redis-backed replay cache, peer federation, AgentCard discovery hardening, scrubber default-on enforcement).
  - Feature flag: `HERMES_A2A_ENABLED` env var must default to `false`. Plugin's `register` exits early when unset. Spec the flag in `docs/a2a-spike-handoff.md`.
  - Tag the spike commit: `spike/a2a-v0.1`.
  - Half-day demo to spike sponsor.
- **Acceptance gate**: Sponsor signs off OR explicitly accepts gaps as recorded in the hand-off note. Hand-off note merged to main behind feature flag.
- **Dependencies**: Days 1-9.
- **Failure mode → action**: If hand-off is rejected, file a follow-up spike. Do NOT extend the timebox; this is a strict 10-day commitment.

---

## 3. Hard dependencies (need this BEFORE Day 1)

These items MUST be true at spike kickoff. If not, push the start date.

- [ ] GCP project `autonomous-agent-2026` has Cloud Run + IAM Credentials API + Cloud Trace + Cloud Logging APIs enabled (already true per `terraform/phase-0a-gcp/`).
- [ ] A spare SA we can provision as `agent-canary-spike` (can be created day-of, but the OWNER needs `roles/iam.serviceAccountAdmin`).
- [ ] Hermes-agent submodule is populated in this worktree — see [`integration-points.md`](./integration-points.md) §1, currently empty. Submodule init is a 5-minute task but it's a blocker for verifying line references.
- [ ] Spike owner has Cloud Trace + Cloud Logging viewer roles on `autonomous-agent-2026`.
- [ ] Reviewer is available for a 30-min sync at end of each day to unblock.

---

## 4. Stretch goals (only if Days 1-10 land early)

- E2E test against Google's reference A2A server (`a2a-python` SDK's example agent).
- mTLS overlay (transport-layer assurance on top of JWT) — would close the Gemini-flagged "Allow unauthenticated" HIPAA concern more cleanly than the application-layer audit log approach.
- Push notification webhook receiver — A2A's `tasks/pushNotificationConfig/*` methods. We currently return `-32003 PushNotificationNotSupportedError`; a v0.2 spike would implement the webhook path.

These are NOT acceptance criteria; do not slip the core schedule for them.

---

## 5. Kill-if-blocked criterion

**The spike is HALTED for sponsor review the moment ANY of these conditions are true:**

1. **Hard blocker >2 working days on a single Day's deliverable.** Example: Day 4 SSE streaming is still broken at end of Day 6 → halt.
2. **A2A spec is found to require a primitive Hermes structurally cannot provide.** Example: the spec mandates a webhook-only delivery for some method we need; if Hermes runtime can't accept inbound webhooks without major surgery, halt.
3. **Auth pattern (Gemini's signJwt+JWKS) is rejected by sponsor or by a security review.** Example: Privacy office says "no `Allow unauthenticated` Cloud Run, period." Halt and pivot to mTLS-only (which would be a different spike).
4. **Spike owner discovers >2 prerequisites missing from §3 that take >1 day to resolve.** Example: hermes-agent submodule turns out to need 3 days of work to populate cleanly.
5. **Cumulative scope creep adds >2 days of work.** Example: scrubber bypass turns out to require rewriting scrubber.py to be middleware-shaped — that's a separate spike, not in scope.

The halt protocol:

- Implementer creates `audit/2026-05-21-a2a-spike-plan/HALT-{date}.md` describing the blocker, options, and a recommended path.
- Sponsor reviews within 1 working day.
- Decision: (a) extend the spike with a documented scope reduction, (b) end the spike here with what we have, (c) reset and re-plan with the lessons learned.

**Do NOT silently push past the kill criterion.** The point of the kill criterion is that we discover real architectural issues fast, not that we burn the timebox proving Hermes can't host SSE.

---

## 6. Risk register

Ranked by impact × likelihood.

| Risk | L | I | Mitigation |
|---|---|---|---|
| SSE / streaming lifecycle conflicts with Hermes plugin contract (plugin hooks weren't designed for long-lived requests) | M | H | Day 4 is the make-or-break day. If broken, halt per §5. |
| AgentCard JWS canonicalization (JCS RFC 8785) interop bugs with non-Google peers | M | M | Day 8 has a fallback to unsigned card; document and continue. |
| `signJwt` quota limits hit during testing | L | M | Quota bump request takes <2h; in-memory JWT caching (§4 of auth-design) further reduces calls. |
| Scrubber bypass discovered to be deeper than expected — scrubber.py needs refactor | M | H | Day 9 fix is application of scrubber at A2A boundary, NOT scrubber refactor. If refactor is needed, document and defer. |
| `Allow unauthenticated` Cloud Run is rejected by the security review for HIPAA | L | H | Audit-log middleware (per auth-design §3.2) is the answer; if rejected, pivot to mTLS-only is a different spike. |
| Hermes-agent submodule line references (in `lib/durability/*` comments) are stale — implementation lands on wrong hook | L | M | Day 1 includes a verification pass against the live submodule. |
| Canary peer is harder to stand up than expected — too heavy / port conflicts | L | L | Stub canary in §2 Day 9 covers this. |
| Cloud Trace doesn't show A2A traces — collector or exporter misconfig | M | M | Day 6 includes in-memory verification as a fallback acceptance gate. |
| The A2A protocol library we choose (Google `a2a-python` SDK vs. roll our own JSON-RPC wrapper) is missing | L | M | We are NOT using the Google SDK in the spike — we build a minimal JSON-RPC dispatch ourselves. This keeps us independent of SDK bugs and conformant only to what we test. |

---

## 7. What this spike will NOT prove

Be explicit about what's left unproven at end of Day 10, to set sponsor expectations:

- **Performance at scale.** All numbers in `auth-design.md` and `telemetry-design.md` are budgets, not measured. The spike's load is single-digit RPS.
- **Multi-peer federation.** Static allowlist of 1 peer is fine for spike. Real production has dozens of peers, each with its own SA, signing cert rotation, etc.
- **mTLS layer.** Deferred to v2 per `auth-design.md` §7.3.
- **Distributed replay cache.** Single-instance in-memory only. Will fail when we put two Hermes instances behind a load balancer.
- **AgentCard discovery feed / registry.** No central directory. Peers are found by knowing their URL out-of-band.
- **Push notification receiver.** A2A `pushNotificationConfig` methods return `-32003 NotSupported`.
- **Compliance audit.** The spike outputs evidence (audit logs); the actual HIPAA audit is a separate workstream.

---

## 8. Acceptance criteria — overall spike

The spike is **accepted** when ALL of:

- [ ] §1 demo runs end-to-end on a dev box without manual intervention.
- [ ] All daily acceptance gates green.
- [ ] No silent skips on the kill-if-blocked criterion.
- [ ] Hand-off note merged behind feature flag.
- [ ] Sponsor signs off OR explicitly accepts gaps in writing.

The spike is **terminated unsuccessfully** when:

- §5 kill criterion triggers and sponsor decides (b) "end the spike here."
- Sponsor rejects the auth pattern post-implementation.
- Day-10 demo fails and the failure is not a known-debt item.

The spike is **inconclusive** (re-plan) when:

- §5 kill criterion triggers and sponsor decides (c) "reset and re-plan."

---

## 9. References

- [`protocol-survey.md`](./protocol-survey.md) — what we're integrating against.
- [`integration-points.md`](./integration-points.md) — where the code lands.
- [`auth-design.md`](./auth-design.md) — security pattern.
- [`telemetry-design.md`](./telemetry-design.md) — observability pattern.
- [`open-questions.md`](./open-questions.md) — what we cannot decide without sponsor input.
- A2A spec — pinned commit recorded on Day 1 in `SPEC-VERSION.md`.
