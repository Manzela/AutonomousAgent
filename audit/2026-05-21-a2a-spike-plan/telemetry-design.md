# A2A Telemetry Design — W3C tracecontext, OpenInference + OTel GenAI dual-emit

**Date:** 2026-05-21
**Decision authority:** Spike owner accepts/rejects; non-trivial deviations require an ADR.
**Scope:** Cross-process trace correlation between Hermes and any A2A peer, plus alignment with the existing dual-emit semantic-convention strategy in `lib/observability/__init__.py`.

---

## 1. Decision in one sentence

**Propagate W3C `traceparent` (and `tracestate` when present) on every A2A request and SSE event boundary; emit each A2A span TWICE on the receiver side — once with OpenInference `llm.*` / Phoenix-friendly conventions and once with OTel GenAI `gen_ai.*` conventions — exactly matching the existing `HERMES_DUAL_EMIT_GEN_AI` toggle so Phoenix and Cloud Trace see the same call from their respective dialects.**

---

## 2. Why W3C tracecontext and not B3 or proprietary

Three reasons, in order of importance:

1. **OTel Python SDK ships W3C `tracecontext` propagator as the global default.** `opentelemetry.propagate.inject(headers)` and `extract(headers)` round-trip W3C trace headers with no extra wiring. We already depend on the SDK in `lib/observability/__init__.py`, so we get it free.
2. **The A2A spec is propagator-agnostic but the reference implementations (Google's `a2a-python` SDK) ship with W3C propagation enabled by default.** Aligning with the reference means we interop without coordinating on the wire format.
3. **Cloud Trace ingests W3C natively** via the `googlecloud` exporter already configured in `deploy/otel/collector.prod.yaml:38`. B3 would require a translation step.

We do NOT use Google's proprietary `X-Cloud-Trace-Context` header on the A2A boundary. That's an internal Google convention; it does not interop with non-Google peers. We rely on W3C only.

---

## 3. Header shape on the wire

Every A2A HTTP request and every SSE event carries:

```
traceparent: 00-{trace_id_32hex}-{span_id_16hex}-{flags_2hex}
tracestate: vendor1=value,vendor2=value     # optional, pass through verbatim
```

`flags` is `01` when the trace is sampled, `00` when not. The receiver MUST respect the sampled bit; if the caller says "not sampled," the receiver's auto-sampler should also drop the span unless a head-based override fires (e.g. the trace contains an error). Cloud Trace and Phoenix both honor this.

Spec hook: the A2A spec §5.1.1 reserves the header namespace `A2A-*`; `traceparent` and `tracestate` are W3C-reserved and explicitly compatible with that policy.

---

## 4. Sender-side propagation (we as caller)

### 4.1 Where the inject call lives

In `lib/a2a/client.py` (sketched in [`integration-points.md`](./integration-points.md) §7):

```python
# lib/a2a/client.py — sketch

import httpx
from opentelemetry import trace, propagate
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("hermes.a2a.client")

async def send_message(
    *, peer_url: str, agent_sa: str, human_sub: str,
    message: dict, task_id: str | None = None,
) -> dict:
    with tracer.start_as_current_span(
        "a2a.message.send",
        kind=SpanKind.CLIENT,
        attributes={
            "a2a.method": "message/send",
            "a2a.peer_url": peer_url,
            "a2a.task_id": task_id or "",
            "a2a.our_agent_sa": agent_sa,
            # OTel GenAI dual-emit — see §6
            "gen_ai.operation.name": "a2a.send",
            "gen_ai.system": "a2a",
            # OpenInference dual-emit — see §6
            "openinference.span.kind": "AGENT",
        },
    ) as span:
        # 1. Mint JWT (cached) — see auth-design.md §4
        token = mint_token(target_audience=peer_url, agent_sa=agent_sa,
                           human_sub=human_sub, task_id=task_id)

        # 2. Inject traceparent + tracestate into outgoing headers
        headers = {"Authorization": f"Bearer {token}",
                   "A2A-Version": "1.0.0",
                   "Content-Type": "application/json"}
        propagate.inject(headers)   # ← this writes traceparent into headers

        # 3. Make the call
        envelope = {"jsonrpc": "2.0", "id": str(ulid.new()),
                    "method": "message/send",
                    "params": {"message": message, "task_id": task_id}}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(peer_url, headers=headers, json=envelope)
                r.raise_for_status()
                result = r.json()
                if "error" in result:
                    span.set_status(Status(StatusCode.ERROR,
                                           result["error"].get("message", "")))
                    span.set_attribute("a2a.error.code", result["error"]["code"])
                else:
                    span.set_attribute("a2a.task_id_result",
                                       result.get("result", {}).get("id", ""))
                return result
        except httpx.HTTPStatusError as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
```

Key invariants:

- `SpanKind.CLIENT` on the sender — Cloud Trace renders this with the right arrow direction.
- `propagate.inject(headers)` is called BEFORE the POST. Mutate the dict; don't pass a copy.
- Span attributes use BOTH dialects (`gen_ai.operation.name` AND `openinference.span.kind`) per §6.

### 4.2 SSE streaming sender

For `message/stream` and `tasks/subscribe`, the connection is long-lived. Two options:

- **Option A: single span for the whole stream.** Wrap `async with httpx.stream(...)` in a span. Add events per SSE event. Best for short streams.
- **Option B: parent span + child span per event.** Open a parent at connect, close at disconnect. Open a child span when each SSE event arrives, attaching that event's `traceparent` if the peer included one in the event payload. Best for long-lived streams where each event represents a distinct logical step.

**Spike picks Option B.** Long-lived A2A subscriptions are the protocol's headline feature; treating the whole stream as one opaque span hides the per-event causality.

---

## 5. Receiver-side extraction (we as callee)

In `lib/a2a/server.py` (sketched in [`integration-points.md`](./integration-points.md) §3):

```python
# lib/a2a/server.py — sketch (continued from integration-points.md)

from opentelemetry import trace, propagate, context as otel_context

tracer = trace.get_tracer("hermes.a2a.server")

@app.post("/")
async def jsonrpc_dispatch(request: Request, ...):
    # 1. Extract W3C tracecontext from inbound headers
    inbound_ctx = propagate.extract(dict(request.headers))

    # 2. Attach to the current process context; everything dispatched from here
    #    becomes a child of the peer's span
    token = otel_context.attach(inbound_ctx)
    try:
        with tracer.start_as_current_span(
            "a2a.message.receive",
            kind=SpanKind.SERVER,
            attributes={
                "a2a.method": method_name,
                "a2a.peer_agent_id": agent_identity.agent_id,
                "a2a.peer_human_sub": agent_identity.human_sub,
                "a2a.task_id": params.get("task_id") or "",
                "gen_ai.operation.name": "a2a.receive",
                "gen_ai.system": "a2a",
                "openinference.span.kind": "AGENT",
            },
        ) as span:
            # 3. Dispatch into the actual handler
            return await dispatch_method(method_name, params, agent_identity)
    finally:
        otel_context.detach(token)
```

Key invariants:

- `propagate.extract(dict(headers))` BEFORE entering the handler.
- `otel_context.attach` / `detach` pair — this is the safe pattern; an exception in the handler still detaches.
- `SpanKind.SERVER` on the receiver — pairs with the sender's CLIENT to form the canonical span pair Cloud Trace expects.

When peer's `traceparent` is missing (e.g. unsanctioned peer or older SDK), `extract` returns an empty context; the server span becomes a new trace root. That's the right fallback — don't error out.

---

## 6. Dual-emit attribute mapping — OpenInference ↔ OTel GenAI

`lib/observability/__init__.py:87-104` documents the existing dual-emit policy: every LLM span carries BOTH `llm.*` (OpenInference) attrs for Phoenix AND `gen_ai.*` (OTel GenAI semantic conventions) attrs for Cloud Trace / GCP GenAI dashboards. The toggle is `HERMES_DUAL_EMIT_GEN_AI` (off by default, on in prod).

**The A2A spans MUST match this policy.** Otherwise Phoenix shows the LLM call but Cloud Trace shows the A2A wrap, or vice versa, and the trace tree breaks across UIs.

### 6.1 Attribute mapping table

Source convention on the left, our dual-emit pair on the right:

| A2A semantics | OpenInference (`llm.*` / OI) — for Phoenix | OTel GenAI (`gen_ai.*`) — for Cloud Trace |
|---|---|---|
| Span kind (high-level role) | `openinference.span.kind = "AGENT"` | (none — implied by span kind) |
| Operation classifier | (n/a) | `gen_ai.operation.name = "a2a.send"` or `"a2a.receive"` |
| Which "system" we are talking to | (none — A2A is a wire protocol) | `gen_ai.system = "a2a"` |
| The agent we are calling / being called by | `llm.model_name = peer_agent_id`* | `gen_ai.request.model = peer_agent_id`* |
| Task ID | `a2a.task_id = "task-xyz"` (custom — both UIs render verbatim) | (same) |
| Peer's human delegator | `a2a.peer_human_sub = "user:..."` (custom) | (same) |
| Message body (small, redacted) | `input.value` / `output.value` (text-mime) | `gen_ai.prompt` / `gen_ai.completion` (when content capture flag is on) |
| Error code (A2A JSON-RPC) | `error.code = -32xxx` | `error.code = -32xxx`, plus `gen_ai.error_type = "a2a.{error_name}"` |
| Token counts (if A2A carries them) | `llm.token_count.{prompt,completion,total}` | `gen_ai.usage.{input_tokens,output_tokens}` |
| Finish reason (if the spec adds one) | `llm.finish_reason = "..."` | `gen_ai.response.finish_reasons = ("...",)` |

*Note on `model_name`: A2A peers are agents, not models, so we abuse `llm.model_name` to carry the peer agent ID. This is a Phoenix-UI ergonomics choice; Phoenix's LLM filter view uses `llm.model_name` as the principal grouping key. Document this in the plugin docstring so a future reader doesn't think we're confused.

### 6.2 Toggle reuse

```python
# lib/a2a/server.py — reuse existing toggle

from lib.observability import _is_dual_emit_enabled, _set_gen_ai_attrs

def _add_a2a_span_attrs(span, *, method, peer_agent_id, peer_human_sub, task_id):
    # OpenInference attrs — ALWAYS emit, like observability does
    span.set_attribute("openinference.span.kind", "AGENT")
    span.set_attribute("a2a.method", method)
    span.set_attribute("a2a.peer_agent_id", peer_agent_id)
    span.set_attribute("a2a.peer_human_sub", peer_human_sub)
    span.set_attribute("a2a.task_id", task_id or "")
    span.set_attribute("llm.model_name", peer_agent_id)   # Phoenix grouping

    # OTel GenAI attrs — only when dual-emit on
    _set_gen_ai_attrs(span, {
        "gen_ai.operation.name": f"a2a.{method.split('/')[0]}",
        "gen_ai.system": "a2a",
        "gen_ai.request.model": peer_agent_id,
    })
```

This is the SAME `_set_gen_ai_attrs` helper used by `_post_llm_call`. We import it (or factor it to a shared helper in a follow-up). Critical: do NOT introduce a second toggle env var — A2A spans should be on/off in lockstep with LLM spans, otherwise the Phoenix-vs-Cloud-Trace dialect split is incoherent.

---

## 7. Parent-child trace correlation — worked example

Setup:

- User starts a chat session with Hermes.
- Hermes (`agent-a`) decides to delegate a sub-task to peer agent `agent-canary`.
- Canary makes its own internal LLM call (e.g. Gemini via Vertex), then responds.

What the trace tree looks like end-to-end:

```
Trace ID: 4bf92f3577b34da6a3ce929d0e0e4736
│
├─ hermes.session  (server.kind=INTERNAL, agent-a)
│  attrs: session.id, user.sub, llm.model_name=claude-opus-4-7
│  │
│  ├─ model.call  (kind=CLIENT, agent-a's own LLM call)
│  │  attrs: llm.model_name, llm.input_messages.*, gen_ai.operation.name=chat
│  │
│  ├─ a2a.message.send  (kind=CLIENT, agent-a → canary)   ◄── NEW
│  │  attrs: a2a.method=message/send, a2a.peer_agent_id=canary,
│  │         llm.model_name=canary, gen_ai.operation.name=a2a.send,
│  │         gen_ai.system=a2a
│  │  │
│  │  └─ a2a.message.receive  (kind=SERVER, canary side)   ◄── NEW
│  │     attrs: a2a.method=message/send,
│  │            a2a.peer_agent_id=agent-a,
│  │            a2a.peer_human_sub=user:dmanzela,
│  │            gen_ai.operation.name=a2a.receive
│  │     │
│  │     ├─ canary.task.execute  (kind=INTERNAL, canary side)
│  │     │  └─ canary.model.call  (kind=CLIENT, canary's LLM call)
│  │     │     attrs: gen_ai.request.model=gemini-3.1-pro-preview
│  │     │
│  │     └─ a2a.subscribe.stream  (kind=SERVER, SSE long-lived)   ◄── NEW
│  │        ├─ event: Task.status_change → IN_PROGRESS
│  │        ├─ event: Task.artifact_added
│  │        └─ event: Task.status_change → COMPLETED
│  │
│  └─ model.call  (kind=CLIENT, agent-a's reply synthesis)
```

The three NEW spans (marked) are the A2A spike's additions. They MUST chain via `traceparent` propagation — otherwise canary's spans would orphan into a new trace and we lose causality.

In Phoenix (filtering by `llm.model_name`):
- `claude-opus-4-7` rows → agent-a's own LLM calls.
- `canary` rows → the A2A delegations (treated as agent-to-agent calls).
- `gemini-3.1-pro-preview` rows → canary's internal LLM calls (only visible if canary also exports to the same Phoenix).

In Cloud Trace (filtering by `gen_ai.system`):
- `anthropic`, `vertex` → in-process model calls.
- `a2a` → cross-process agent calls.

Both views show the same tree.

---

## 8. Trace export pipeline — no changes needed

`deploy/otel/collector.prod.yaml` already routes:

```
otlp (receivers) → memory_limiter → resource → tail_sampling → batch → googlecloud (exporter)
```

A2A spans flow through this pipeline unchanged. The `resource` processor stamps `deployment.environment=prod`; the `tail_sampling` policy keeps all errors, all slow traces >5s, and 10% probabilistic.

Two things to verify during the spike:

1. **Tail sampling on A2A errors.** The `errors` policy keys on `status_code=ERROR`. Confirm that A2A JSON-RPC `-32xxx` errors get mapped to `Status(StatusCode.ERROR)` by our span-setting code. If not, every A2A error gets sampled away with 90% probability — bad.
2. **Cardinality on `a2a.task_id`.** Task IDs are ULIDs, very high cardinality. Cloud Trace handles this fine, but if we later add metrics with `a2a.task_id` as a label we explode the metric. Don't put task_id on a metric label.

---

## 9. SSE event correlation

Each SSE event the peer sends MAY include its own `traceparent` in the event data (when the event corresponds to a sub-span the peer opened). Spec is silent; reference SDKs don't do it. We propose:

- When opening a stream, the peer attaches the **stream-scope** trace ID to the connection.
- When the peer fires an event from within a sub-span, the peer SHOULD include `_meta: { traceparent: "..." }` in the event JSON.
- On receive, if `_meta.traceparent` is present, we use it as the parent for the per-event child span; otherwise we use the stream-scope parent.

This is a convention we'd want to propose to the A2A WG — see [`open-questions.md`](./open-questions.md) Q7.

---

## 10. PHI handling on spans

Per HIPAA, any span attribute we ship to Cloud Trace must be either non-PHI or sealed. Concretely:

- `a2a.peer_human_sub`: opaque ID if the human is a patient/clinician — see [`auth-design.md`](./auth-design.md) §3.2 and [`open-questions.md`](./open-questions.md) Q5.
- `input.value` / `output.value` (message bodies): redact PHI through the existing `lib/scrubber.py` before assigning to the span. **CRITICAL**: see [`integration-points.md`](./integration-points.md) §10 — the scrubber currently runs on LiteLLM outbound only; A2A messages bypass it. **This is the biggest open scrubber gap the spike will surface.**
- `llm.input_messages.{N}.message.content`: same scrubber pass.
- Truncate per existing `_safe_str` (1024-byte default).

Acceptance: a test that puts a synthetic PHI string into an A2A message body and asserts the span attribute is redacted.

---

## 11. Acceptance criteria for the telemetry layer

- [ ] Outbound A2A call emits a span with `kind=CLIENT`, both `llm.*` AND `gen_ai.*` attrs when dual-emit is on, OpenInference-only when off.
- [ ] Inbound A2A call extracts `traceparent`; the resulting span has the peer's trace_id as its trace_id (not a new one).
- [ ] Cross-process trace verified end-to-end: trigger a call from canary → us → another canary, see all three spans in ONE trace tree in Cloud Trace AND in Phoenix.
- [ ] SSE stream emits one parent span (`SERVER`) plus N child spans (one per event); all share the stream's trace_id.
- [ ] Error path: A2A `-32600` invalid request sets `Status(StatusCode.ERROR)` on the span; tail sampling retains it.
- [ ] Scrubber test: PHI string in message body is redacted on the span attribute.
- [ ] Span attribute cardinality: no `a2a.task_id` exposed as a metric label.

---

## 12. What the telemetry layer explicitly does NOT do in the spike

- **Metrics emission** — spans only. Counts like "A2A calls per second" can come from span-derived metrics in Cloud Trace.
- **Log correlation** — `trace_id` IS on every audit log line (from `auth-design.md` §3.2), but we don't try to correlate logs in Cloud Logging via UI links. Log Explorer supports `trace=...` filters out of the box.
- **Phoenix dashboards** — we ship the spans; building Phoenix dashboards specifically for A2A is out of scope.
- **Cardinality control on `a2a.peer_human_sub`** — we trust the auth allowlist to bound this. If it grows above ~100, revisit.

---

## 13. References

- `lib/observability/__init__.py:85-145` — existing dual-emit toggle and `_set_gen_ai_attrs` helper that A2A reuses.
- `lib/observability/__init__.py:380-466` — existing `llm.*` and `gen_ai.*` attribute placement for LLM spans, the pattern A2A spans MUST mirror.
- `deploy/otel/collector.prod.yaml:22-48` — production export pipeline; A2A traces flow through unchanged.
- `lib/scrubber.py` (referenced from `integration-points.md` §10) — the scrubber bypass that is the single biggest known gap.
- [W3C Trace Context spec](https://www.w3.org/TR/trace-context/).
- [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
- [OpenInference span conventions](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md).
- A2A spec §5.1.1 — header namespace and W3C compatibility.
- [`integration-points.md`](./integration-points.md) — overall plugin map and `lib/a2a/` placement.
- [`auth-design.md`](./auth-design.md) — JWT layer; `trace_id` is included in every audit log.
