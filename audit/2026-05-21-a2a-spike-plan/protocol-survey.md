# A2A Protocol Surface — Implementer's Survey

**Date:** 2026-05-21
**Spec version surveyed:** A2A v1.0.0 (Linux Foundation / Google), spec downloaded from `https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md` (3,610 lines), normative proto from `https://raw.githubusercontent.com/a2aproject/A2A/main/spec/a2a.proto` (796 lines).
**Source of truth for any disagreement:** the `.proto` file. Per spec §1.4: "the file `spec/a2a.proto` is the single authoritative normative definition." Markdown spec is descriptive; proto is normative.
**Scope:** Just enough of A2A to scope a 2-week bidirectional-call spike. This is a quick reference, not the spec.

---

## 1. Architectural shape

A2A defines THREE layers (spec §1.3 mermaid diagram, lines 47-90):

| Layer | Content | Where it lives |
|-------|---------|----------------|
| **L1 Data Model** | `Task`, `Message`, `AgentCard`, `Part`, `Artifact`, `Extension` | a2a.proto |
| **L2 Operations** | SendMessage, SendStreamingMessage, GetTask, ListTasks, CancelTask, SubscribeToTask, push-notification CRUD, GetExtendedAgentCard | spec §3.1 |
| **L3 Bindings** | JSON-RPC 2.0 over HTTP, gRPC over HTTP/2, HTTP+JSON/REST + SSE | spec §§9-11 |

**Critical: all three bindings are first-class.** Spec §5.1 (lines 1145-1153): "Functional Equivalence Requirements" — every binding MUST implement the same 11 operations with semantic parity. We pick ONE binding for the spike, but MUST NOT design ourselves into a corner that blocks adding a second one later.

**Spike binding choice: JSON-RPC 2.0 over HTTP.**
- Simplest to stand up in Python (existing httpx in tree).
- Compatible with Google's reference SDK (the a2a-python client uses JSON-RPC by default).
- SSE streaming maps cleanly to httpx-sse / Starlette EventSourceResponse.
- gRPC would require generating Python stubs from `a2a.proto` and operating a second listening port — out of scope for a 2-week spike.

---

## 2. The 11 operations (spec §3.1, lines 159-428)

| # | Operation | Spec § | What it does | Hermes equivalent |
|---|-----------|--------|--------------|-------------------|
| 3.1.1 | SendMessage | 159-181 | Send one message, get a Task OR a direct Message response | TaskSpec.create (`lib/anchors/__init__.py:_draft_from_intent`) + agent loop entry |
| 3.1.2 | SendStreamingMessage | 182-228 | SendMessage + SSE stream of TaskStatusUpdateEvent + TaskArtifactUpdateEvent until terminal state | Whole `gateway run` long-poll loop (writes back to Telegram) |
| 3.1.3 | GetTask | 230-253 | Lookup by task_id, returns Task snapshot | `SpecStore.load(spec_id)` (`lib/anchors/spec_store.py:61`) |
| 3.1.4 | ListTasks | 254-279 | Paginated task search by context_id / state | `SpecStore.list_active()` (`lib/anchors/spec_store.py:68`) |
| 3.1.5 | CancelTask | 280-301 | Mark task TASK_STATE_CANCELED | `/cancel` slash command (`lib/anchors/__init__.py:_slash_cancel`) |
| 3.1.6 | SubscribeToTask | 302-331 | RECONNECT to an existing in-progress task and resume SSE stream | NEW SURFACE — closest analogue is checkpoint replay (`lib/durability/checkpoint.py`) |
| 3.1.7 | CreateTaskPushNotificationConfig | 332-356 | Register a webhook for async task updates | NO ANALOGUE — Telegram bridge is hard-coded as the only sink today |
| 3.1.8 | GetTaskPushNotificationConfig | 357-371 | Read back a registered webhook | (same — NEW) |
| 3.1.9 | ListTaskPushNotificationConfigs | 372-389 | List all webhooks on a task | (same — NEW) |
| 3.1.10 | DeleteTaskPushNotificationConfig | 390-404 | Unregister a webhook | (same — NEW) |
| 3.1.11 | GetExtendedAgentCard | 405-428 | Authenticated extended capability disclosure | NEW SURFACE — need a small handler |

**Capability validation (spec §3.3.4):** SubscribeToTask, push-notification ops, and extended-card disclosure are GATED by `AgentCard.capabilities.{streaming, pushNotifications, extendedAgentCard}`. If the server doesn't declare the capability, clients calling those ops MUST receive `UnsupportedOperationError`. Cheap to enforce: a single boolean check at handler dispatch.

---

## 3. Data model essentials (spec §4, proto lines 200-600)

### 3.1 Task lifecycle (TaskState enum, proto lines 380-410)

```
TASK_STATE_SUBMITTED      // accepted, not yet started
   ↓
TASK_STATE_WORKING        // agent is processing
   ↓
TASK_STATE_INPUT_REQUIRED // waiting on user (non-terminal, agent can resume)
TASK_STATE_AUTH_REQUIRED  // waiting on credential (non-terminal, §7.6)
   ↓
TASK_STATE_COMPLETED      // ✅ terminal
TASK_STATE_FAILED         // ❌ terminal
TASK_STATE_CANCELED       // 🛑 terminal (user-initiated)
TASK_STATE_REJECTED       // 🚫 terminal (agent refused)
```

Once a task is in any terminal state, SendMessage to it returns `UnsupportedOperationError` (spec §3.1.1 errors block, lines 173-176).

### 3.2 Message (proto lines 425-450)

```
Message {
  message_id:  string  // client-generated, MUST be unique within a task
  context_id:  string  // groups related tasks (a "conversation")
  task_id:     string  // optional — bound at task creation
  role:        Role    // ROLE_USER or ROLE_AGENT
  parts:       []Part  // at least one
  extensions:  []string // URIs of extensions in use on this message
  metadata:    Struct  // free-form
}
```

### 3.3 Part (proto lines 451-475)

A Part is a tagged union over content types:

| Part type | Use case | Hermes mapping |
|-----------|----------|----------------|
| `text` | Plain prose, the most common case | The intent string in TaskSpec |
| `file` | File reference (URL or inline bytes) | The escalation snapshots (`lib/snapshots/`) |
| `data` | Structured JSON | TaskSpec JSON itself, scrubber redaction reports |

Each part carries an optional `metadata` Struct. The receiver must check `mime_type` before assuming content shape.

### 3.4 Artifact (proto lines 476-500)

Final outputs of a task. Same `parts: []Part` structure. Streamed via `TaskArtifactUpdateEvent` (proto lines 600-620) so a long-running task can emit partial results.

### 3.5 AgentCard (proto lines 200-360)

The most important struct in the protocol. Discovery is just a GET on `/.well-known/agent-card.json` (spec §8.2). The card declares:

```
AgentCard {
  name, description, version, provider, ...
  supported_interfaces: []AgentInterface  // [{ url, protocol_binding: "JSONRPC"|"GRPC"|"HTTP+JSON", protocol_version }]
  capabilities: AgentCapabilities         // { streaming, push_notifications, extended_agent_card, extensions: []AgentExtension }
  skills: []AgentSkill                    // [{ id, name, description, tags, input_modes, output_modes }]
  security_schemes: map<string, SecurityScheme>  // see §6 below
  security: []SecurityRequirement
  default_input_modes, default_output_modes: []string  // MIME types
  signatures: []AgentCardSignature        // optional JWS over the canonical card JSON
}
```

### 3.6 AgentCardSignature (spec §8.4, proto AgentCardSignature)

Optional but **strongly recommended** for production:
- Sign with JWS (RFC 7515)
- Canonicalize via JCS (RFC 8785) BEFORE signing
- Three fields: `protected` (b64url JWS Protected Header with `alg`, `typ=JOSE`, `kid`), `signature` (b64url signature), `header` (optional unprotected header)
- The `signatures` field itself is excluded from the content being signed
- Spec §8.4.3 step 5: clients MUST canonicalize the received card the same way before verifying

**This is the highest-risk interop surface in the entire protocol** — see open-questions.md.

---

## 4. Capability negotiation (spec §3.3.4, §8.3)

Two stacked layers:

1. **Protocol selection** (spec §8.3.2): client parses `supportedInterfaces`, picks the first entry it supports, uses that URL + binding for all subsequent ops. No mid-conversation switching.
2. **Per-operation capability gating** (spec §3.3.4): the client SHOULD check `AgentCard.capabilities.{streaming, pushNotifications, extendedAgentCard}` before invoking SubscribeToTask, push-notif ops, or GetExtendedAgentCard. The server MUST reject with `UnsupportedOperationError` if invoked when the capability is false.

Extensions are negotiated separately (spec §4.6, lines 998-1142):
- AgentExtension is declared in `AgentCard.capabilities.extensions: [{ uri, description, required, params }]`
- If `required: true`, the client MUST declare it in the `A2A-Extensions` header (spec §3.2.6) on every request, OR the server rejects.
- Otherwise the client MAY opt in.

---

## 5. Transport bindings (spec §§9-11) at a glance

### 5.1 JSON-RPC 2.0 over HTTP (spec §9) — **our spike choice**

- Single POST endpoint (no per-operation paths)
- Method names are kebab-case: `message/send`, `message/stream`, `tasks/get`, `tasks/list`, `tasks/cancel`, `tasks/subscribe`, `tasks/pushNotificationConfig/create|get|list|delete`, `agent/getAuthenticatedExtendedCard` (spec §9.4 table)
- Service params (`A2A-Version`, `A2A-Extensions`) MAY be HTTP headers OR JSON-RPC `params._meta` keys (spec §9.2)
- Streaming uses SSE on the same endpoint when method is `message/stream` or `tasks/subscribe` (spec §9.4.2)
- Error mapping (spec §9.5): standard JSON-RPC -32xxx codes plus A2A-specific -32001..-32009

### 5.2 gRPC over HTTP/2 (spec §10) — out of scope for spike

- Generated stubs from `a2a.proto`
- Standard gRPC status codes via `google.rpc.Status`
- Server-streaming RPCs for `SendStreamingMessage` and `SubscribeToTask`
- Service params MAY be gRPC metadata (`A2A-Version`, `A2A-Extensions`)

### 5.3 HTTP+JSON/REST (spec §11) — out of scope for spike

- RESTful resource URLs: `POST /message:send`, `GET /tasks/{id}`, `POST /tasks/{id}:cancel`, `POST /tasks/{id}:subscribe`, `POST /tasks/{id}/pushNotificationConfigs`, etc. (spec §11.3)
- Content-Type SHOULD be `application/a2a+json`
- Service params MUST be HTTP headers (spec §11.2)
- SSE for streaming endpoints (spec §11.7)

**For the spike: implement JSON-RPC only. Leave gRPC + REST handlers as TODO stubs that return `UnsupportedOperationError`** so we can flip them on later without breaking the dispatcher contract.

---

## 6. Security schemes (spec §4.5, proto SecurityScheme oneof, lines 700-790)

The `SecurityScheme` proto oneof allows five flavors (proto line 700+):

| Scheme | Proto tag | Use case |
|--------|-----------|----------|
| `api_key_security_scheme` | 1 | Simple shared secret (header/query/cookie). **Spec §13.4.x: SHOULD NOT use in production** (long-lived, no rotation story). |
| `http_auth_security_scheme` | 2 | HTTP Bearer or Basic, including OAuth2 Bearer tokens. |
| `oauth2_security_scheme` | 3 | Full OAuth2 flows (AuthCode, ClientCredentials, DeviceCode). The standard production choice for human-on-behalf-of. |
| `open_id_connect_security_scheme` | 4 | OIDC discovery URL → JWKS endpoint. Closest match to Google federated identity. |
| `mtls_security_scheme` | 5 | Mutual TLS. Both ends present X.509 certs. **Best for agent-to-agent.** |

These get declared in `AgentCard.securitySchemes` (map<string, SecurityScheme>) and referenced by name in `AgentCard.security` (array of SecurityRequirement). A SecurityRequirement is itself a map<string, []string> where the list is OAuth2 scopes (ignored for non-OAuth schemes).

**Recommendation for spike: declare BOTH mTLS (for agent-to-agent) AND OIDC (for human-on-behalf-of-agent), but only EXERCISE one in the canary test.** See auth-design.md for the full reasoning.

---

## 7. Service parameters (spec §3.2.6, transmitted per binding §§9.2 / 10.2 / 11.2)

Two service params currently exist:

| Param | Purpose | JSON-RPC | gRPC | REST |
|-------|---------|----------|------|------|
| `A2A-Version` | Pin protocol version (e.g. `1.0`). Server MAY refuse mismatched versions. | Header OR `params._meta.A2A-Version` | gRPC metadata | Header |
| `A2A-Extensions` | Comma-separated extension URIs that the client is using on this request. Required extensions on AgentCard MUST appear here. | Same | Same | Header |

**Implementation note:** Spec §11.2.x — service params are CASE-INSENSITIVE per RFC 9110, but the canonical casing is what we MUST emit. The receiver MUST accept any casing.

---

## 8. Push notifications (spec §4.3 + §6.6, proto lines 540-580)

Out-of-band webhook for async task updates. Useful when:
- Caller's connection is too short-lived for SSE (e.g., serverless function client)
- The task is genuinely long-running (hours/days)
- The peer wants delivery-confirmed-or-retried semantics

Wire shape:
1. Caller registers a webhook via `tasks/pushNotificationConfig/create` with `{ url, token: <opaque>, authentication: {…} }`
2. Server stores the config keyed by `(task_id, config_id)`
3. On status/artifact update, server POSTs to the webhook with the event body + an HTTP header `X-A2A-Notification-Token: <token>` (spec §13.2)
4. Caller verifies the token matches what it registered, then processes

**Per spec §13.2 security:**
- The token is the only proof of authenticity — agents SHOULD also use webhook-side auth (HMAC signature, OIDC token, etc.) configured in the `PushNotificationConfig.authentication` block
- The server MUST NOT include credentials of the original caller in the push payload

**Spike scope: skip push notifications.** SSE streaming covers the canary-test loop. Add a stub that returns `PushNotificationNotSupportedError` (-32003) so capability negotiation is correct.

---

## 9. Versioning (spec §3.6, lines 706-746)

- AgentCard carries `protocol_version` (per supported interface) and `version` (the agent's own version)
- Clients declare via `A2A-Version` service param what protocol version they intend to speak
- Server MAY reject with `UnsupportedProtocolVersion` if incompatible
- Spec §3.6.x change-control: proto fields are added with new names, old names stay until next major; SDKs SHOULD ship deprecated aliases

**Spike: declare `A2A-Version: 1.0`. Reject anything else.** Add a feature flag for forward-compat once 1.1 ships.

---

## 10. Common workflows we need to support (spec §§6.1-6.9)

For the spike, we MUST handle:

| Workflow | Spec § | Hermes touchpoint |
|----------|--------|-------------------|
| 6.1 Basic Task Execution (single SendMessage → Task → poll until terminal) | 1308-1348 | TaskSpec.create + observability `turn.start` span |
| 6.2 Streaming Task Execution (SendStreamingMessage → SSE) | 1349-1382 | NEW — bridge SSE writer to Hermes' tool-loop event emission |
| 6.3 Multi-Turn (TASK_STATE_INPUT_REQUIRED → client follow-up SendMessage on same task) | 1383-1441 | Maps to clarification loop (`lib/anchors/clarification_loop.py`) |
| 6.6 Push Notification Setup | 1620-1692 | DEFER — return `PushNotificationNotSupportedError` |
| 6.9 Fetching Extended Agent Card | 1820-1871 | NEW — single endpoint, JWS-signed body |

Defer for post-spike:
- 6.4 Version Negotiation Error (only matters when 1.1 ships)
- 6.5 Task Listing and Management (paging UX — not on critical path)
- 6.7 File Exchange (works via Part type 'file', but our use case is text-heavy)
- 6.8 Structured Data Exchange (Part type 'data' is already supported by the proto — just need to serialize TaskSpec JSON correctly)

---

## 11. Error codes (spec §5.4, lines 1176-1201)

A2A-specific JSON-RPC error codes (in addition to the standard -32600..-32603 range):

| Code | Name | HTTP | Cause |
|------|------|------|-------|
| -32001 | TaskNotFoundError | 404 | task_id unknown |
| -32002 | TaskNotCancelableError | 400 | tried to cancel terminal task |
| -32003 | PushNotificationNotSupportedError | 400 | server doesn't support push |
| -32004 | UnsupportedOperationError | 400 | capability not declared OR terminal-task write |
| -32005 | ContentTypeNotSupportedError | 400 | part MIME type not in default_input_modes |
| -32006 | InvalidAgentResponseError | 500 | server-side bug |
| -32007 | AuthenticatedExtendedCardNotConfiguredError | 404 | extended card capability declared but no extended card configured |
| -32008 | InvalidVersionError | 400 | client A2A-Version unsupported |
| -32009 | DuplicateMessageError | 400 | repeated message_id within a task |

Map all of these to F-codes in `lib/durability/failure_matrix.py` so the trichotomy classifier can route them. See integration-points.md for the proposed F-code mapping.

---

## 12. What we are NOT going to do in the spike

| Out-of-scope | Why |
|--------------|-----|
| gRPC binding | One binding is enough to prove interop. Adding gRPC doubles surface. |
| REST binding | Same. |
| Push notifications | SSE streaming covers our canary. Returning `PushNotificationNotSupportedError` is one line. |
| Custom bindings (spec §12) | Theoretical — no real-world peer would use this with us. |
| Multi-region failover | Phase-2 concern. |
| Extension authoring (spec §4.6.x) | Consume existing extensions, don't publish new ones. |
| Forward-compat to 1.1+ | Pin to 1.0 and reject everything else, fail-loud. |

---

## 13. References (one-click for spike implementers)

- **Spec markdown (read-only)**: <https://github.com/a2aproject/A2A/blob/main/docs/specification.md>
- **Normative proto**: <https://github.com/a2aproject/A2A/blob/main/spec/a2a.proto>
- **Python SDK** (`a2a-python`): <https://github.com/a2aproject/a2a-python> — server + client, JSON-RPC + gRPC, MIT-licensed. Reference implementation we should follow rather than rolling our own.
- **JS SDK** (`a2a-js`): <https://github.com/a2aproject/a2a-js>
- **Sample agents**: <https://github.com/a2aproject/a2a-samples>
- **RFC 7515 JWS**: <https://tools.ietf.org/html/rfc7515>
- **RFC 8785 JCS (canonicalization for AgentCard signing)**: <https://tools.ietf.org/html/rfc8785>
- **W3C TraceContext**: <https://www.w3.org/TR/trace-context/>

---

## 14. The 30-second protocol summary

> A2A is JSON-RPC/gRPC/REST + SSE for AI agents. Discover peers by `GET /.well-known/agent-card.json`. Send a Message → get back either a direct Message (sync) or a Task (async with lifecycle). Tasks can stream updates via SSE or push via webhook. Auth is delegated to standard schemes (mTLS / OIDC / OAuth2) declared in the AgentCard. The whole protocol is opaque-execution by design: peers see capabilities + messages, never internal state.

**Implementer's takeaway:** A2A is not a new RPC framework. It's a thin contract layer over JSON-RPC/gRPC/REST that standardizes (a) how agents discover each other, (b) the Task lifecycle, (c) capability negotiation, and (d) push/stream update delivery. Pick one binding (JSON-RPC), implement the 11 ops behind a thin dispatcher, and align everything else with existing tools.
