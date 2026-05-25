# A2A Integration Points — Where the Protocol Surface Lands in This Codebase

**Date:** 2026-05-21
**Repo HEAD:** `feat/phase-0a-gcp-migration` branch, recent commit `dcdc5b4`.
**Audience:** Whoever ends up implementing the 2-week spike (likely Phase-2-1 implementer).
**Hermes-agent submodule status:** Empty in this worktree (`hermes-agent/` is unpopulated). All `hermes-agent/...:N` line references below come from comments in `lib/durability/trichotomy.py`, `lib/durability/__init__.py`, and `lib/observability/__init__.py`, which were authored against a known Hermes pin (`ddb8d8f`). Verify them against the live submodule before writing code.

---

## 1. Three integration layers we need to touch

```
┌─────────────────────────────────────────────────────────┐
│  A2A SERVER (we expose handlers for inbound A2A calls)  │
│  Receives: SendMessage, SendStreamingMessage, GetTask,   │
│  ListTasks, CancelTask, SubscribeToTask, AgentCard       │
└──────────────────────┬──────────────────────────────────┘
                       │ dispatches into
                       ▼
┌─────────────────────────────────────────────────────────┐
│  HERMES PLUGIN BOUNDARY (lib/* plugins)                  │
│  Existing: anchors (TaskSpec), durability (trichotomy,   │
│   checkpoint), observability (OTel spans), kanban        │
│  NEW: lib/a2a/ — the spike plugin                        │
└──────────────────────┬──────────────────────────────────┘
                       │ delegates to
                       ▼
┌─────────────────────────────────────────────────────────┐
│  A2A CLIENT (we call out to remote agents as a tool)     │
│  Becomes a new tool registered with toolset_router       │
│  Tier: external_https (or new tier "a2a_peer")           │
└─────────────────────────────────────────────────────────┘
```

Both directions of flow share **one common piece**: the W3C `traceparent` header on the wire, which `lib/observability/__init__.py` already produces (sender side via OTel SDK) and parses (receiver side, but only for in-process calls today). The spike adds the cross-process plumbing.

---

## 2. Plugin scaffold (NEW)

Create a new plugin `lib/a2a/` matching the existing plugin conventions:

```
lib/a2a/
├── __init__.py          # register(ctx) entry point — wires hooks + commands + tools
├── plugin.yaml          # manifest (slug: a2a, depends_on: [observability, anchors, durability])
├── server.py            # JSON-RPC handlers (FastAPI/Starlette app)
├── client.py            # outbound client (httpx + httpx-sse for streaming)
├── agent_card.py        # AgentCard builder + JWS signer
├── task_bridge.py       # bridges A2A Task lifecycle ↔ Hermes TaskSpec lifecycle
├── auth.py              # JWT signer (calls signJwt) + verifier (cached JWKS)
└── tests/
    ├── test_server.py
    ├── test_client.py
    ├── test_agent_card_signing.py  # JCS canonicalization round-trip is the highest-risk test
    └── test_e2e_canary.py          # stands up two copies, calls bidirectionally
```

Mount it in `deploy/docker-compose.yml:404-419` exactly like the existing plugin mounts — alongside `../lib/anchors:/home/hermes/.hermes/plugins/anchors:ro`, add `../lib/a2a:/home/hermes/.hermes/plugins/a2a:ro`.

Enable in `config/hermes/cli-config.yaml` `plugins.enabled` (file not read this session — verify the existing list).

---

## 3. Receiver-side integration (`lib/a2a/server.py`)

### 3.1 Handler entry point

```python
# lib/a2a/server.py — sketch only, NOT to be implemented in this audit pass

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.responses import StreamingResponse
from typing import Optional

app = FastAPI()

@app.post("/")  # JSON-RPC single endpoint, spec §9.4
async def jsonrpc_dispatch(
    request: Request,
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
    a2a_extensions: Optional[str] = Header(None, alias="A2A-Extensions"),
    authorization: Optional[str] = Header(None),
    traceparent: Optional[str] = Header(None),
) -> dict | StreamingResponse:
    """Parse JSON-RPC envelope, validate version, route by method name."""
    # 1. Auth: validate JWT (auth.py:verify_token) → returns AgentIdentity (agent_id + human_sub)
    # 2. Parse OTel traceparent → attach to current span context (otel.propagators.w3c)
    # 3. Parse JSON-RPC envelope: { jsonrpc, id, method, params }
    # 4. Dispatch on method:
    #      "message/send"                        → handle_send_message
    #      "message/stream"                      → handle_stream_message  (SSE response)
    #      "tasks/get"                           → handle_get_task
    #      "tasks/list"                          → handle_list_tasks
    #      "tasks/cancel"                        → handle_cancel_task
    #      "tasks/subscribe"                     → handle_subscribe_task  (SSE response)
    #      "tasks/pushNotificationConfig/*"      → return PushNotificationNotSupportedError (-32003)
    #      "agent/getAuthenticatedExtendedCard"  → handle_extended_card
    # 5. Map exceptions → JSON-RPC error envelope (-32xxx codes from spec §5.4)
    ...
```

**Critical:** the HTTP server is NEW infrastructure for Hermes. Today Hermes is a long-polling Telegram gateway (`hermes gateway run`, `deploy/docker-compose.yml:320`). It does not listen on any port. Options:

1. **Sidecar service** (recommended for spike): new `a2a-server` container in `docker-compose.yml`, runs `uvicorn lib.a2a.server:app --host 0.0.0.0 --port 8004` and shares the same `hermes-data` volume + Python env. Hermes's plugin loader still picks up `lib/a2a/__init__.py` for the IN-PROCESS pieces (TaskSpec bridge, OTel hooks), but the HTTP server runs out-of-process. This decouples A2A uptime from the Hermes long-poll loop.
2. **In-process listener** (rejected): would require Hermes to gain a `serve` subcommand. The `deploy/docker-compose.yml:307-313` block-comment notes "hermes has no `serve` subcommand. The gateway IS the agent." Adding one is a Hermes-upstream change, not a spike change.

### 3.2 Mapping A2A operations → Hermes entry points

| A2A op | Hermes entry point | Notes |
|--------|---------------------|-------|
| `message/send` | `lib/anchors/__init__.py:_draft_from_intent` (line 332) → `SpecStore.save` (line 52) | Create a Task; SendMessage maps cleanly to "operator sends intent". The `Message.parts[0].text` becomes `intent` arg. |
| `tasks/get` | `lib/anchors/spec_store.py:SpecStore.load` (line 61) | Direct mapping. UUID-keyed. |
| `tasks/list` | `lib/anchors/spec_store.py:SpecStore.list_active` (line 68) | Need to add pagination + `context_id` filter (currently returns ALL active specs). |
| `tasks/cancel` | `lib/anchors/__init__.py:_slash_cancel` (line 258) | Existing slash-command handler; reuse it. |
| `message/stream` | NEW — Starlette `EventSourceResponse` writing `TaskStatusUpdateEvent` per Hermes step | Bridge from Hermes' `post_tool_call` hook (already registered by `lib/observability/__init__.py:134`) into an async queue per active SSE stream. |
| `tasks/subscribe` | NEW — same SSE writer as above, but resumes mid-task | Read most-recent checkpoint via `lib/durability/checkpoint.py` to replay history, then attach to the live queue. |
| `agent/getAuthenticatedExtendedCard` | NEW — single handler, returns JWS-signed JSON | See agent_card.py below. |

### 3.3 TaskState mapping

Hermes `TaskSpec.status` (literal: `draft | draft_locked | locked | superseded`) ≠ A2A `TaskState`. We need a mapping table:

| A2A TaskState | Hermes equivalent |
|---------------|-------------------|
| TASK_STATE_SUBMITTED | spec just created, status=`draft` |
| TASK_STATE_WORKING | spec is `locked` AND there's an active session checkpoint |
| TASK_STATE_INPUT_REQUIRED | clarification loop is open (`lib/anchors/clarification_loop.py`, sidecar file `<spec_id>.skips` exists with count < MAX) |
| TASK_STATE_AUTH_REQUIRED | NEW — needs durability handler that emits this state when an MCP tool returns 401 (F8 in `lib/durability/trichotomy.py:29`) |
| TASK_STATE_COMPLETED | spec is `locked` AND latest checkpoint has terminal-success marker |
| TASK_STATE_FAILED | spec is `locked` AND latest checkpoint has F-code that classified as FAIL_LOUD (`lib/durability/trichotomy.py:trichotomy_class`) |
| TASK_STATE_CANCELED | status=`superseded` AND cancel was user-initiated (slash `/cancel`) |
| TASK_STATE_REJECTED | NEW — no Hermes analogue; emit this on JSON-RPC `-32004 UnsupportedOperationError` from server side |

The mapping is best owned by a single function in `task_bridge.py`:

```python
# lib/a2a/task_bridge.py — sketch only
from lib.anchors.task_spec import TaskSpec, SpecStatus

def to_a2a_task_state(spec: TaskSpec, latest_checkpoint: dict | None) -> str:
    if spec.status == "superseded":
        return "TASK_STATE_CANCELED"
    if spec.status == "draft":
        return "TASK_STATE_SUBMITTED"
    if spec.status == "draft_locked":
        # Clarification round-trip
        return "TASK_STATE_INPUT_REQUIRED"
    # status == "locked"
    if latest_checkpoint is None:
        return "TASK_STATE_SUBMITTED"
    if latest_checkpoint.get("terminal") is True:
        if latest_checkpoint.get("error_f_code", "").startswith("F") and \
           latest_checkpoint["error_f_code"] not in {"F1", "F2", ...}:  # FAIL_LOUD set
            return "TASK_STATE_FAILED"
        return "TASK_STATE_COMPLETED"
    return "TASK_STATE_WORKING"
```

---

## 4. AgentCard signing (`lib/a2a/agent_card.py`)

The single most error-prone part of the protocol. Spec §8.4.1 + RFC 8785 canonicalization is unforgiving — one off-by-one in default-value handling and signature verification fails on the peer with no useful error.

```python
# lib/a2a/agent_card.py — sketch only

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.auth import default as google_auth_default
from google.iam.credentials_v1 import IAMCredentialsClient

# Pin: spec §8.4 + RFC 8785 + RFC 7515
# Implementation: use the `jcs` package on PyPI (only ~200 LoC, MIT-licensed, no transitive deps).

def build_agent_card() -> dict:
    """Build the AgentCard JSON for THIS agent. Read once at startup, cache."""
    return {
        "name": "AutonomousAgent Hermes",
        "description": "...",
        "version": "0.1.0-spike",
        "provider": {"organization": "Manzela", "url": "https://github.com/Manzela/AutonomousAgent"},
        "supportedInterfaces": [
            {"url": "https://hermes.example.com/a2a/v1", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {
            "streaming": True,         # we implement SSE
            "pushNotifications": False, # explicit opt-out for spike (spec §3.3.4 gate)
            "extendedAgentCard": True,
            # extensions omitted (default empty list — JCS rule §8.4.1 says omit it)
        },
        "securitySchemes": {
            "google_jwt": {
                "openIdConnectSecurityScheme": {
                    # JWKS hosted automatically by Google for every SA. See auth-design.md §3.
                    "openIdConnectUrl": "https://www.googleapis.com/service_accounts/v1/jwk/agent-runtime@autonomous-agent-2026.iam.gserviceaccount.com"
                }
            }
        },
        "security": [{"google_jwt": []}],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {"id": "task-execute", "name": "Execute coding task", "description": "...",
             "tags": ["coding"], "inputModes": ["text/plain"], "outputModes": ["text/plain", "application/json"]}
        ],
        # signatures field added by sign_agent_card() below
    }


def sign_agent_card(card: dict, sa_email: str) -> dict:
    """JWS-sign per spec §8.4 + RFC 8785 canonicalization."""
    import jcs  # RFC 8785 implementation
    import json
    import base64

    # 1. Strip signatures field (spec §8.4.1 step 3)
    payload = {k: v for k, v in card.items() if k != "signatures"}

    # 2. Apply spec §8.4.1 default-value pruning rules:
    #    - REQUIRED fields: always include
    #    - optional fields explicitly set: include
    #    - optional fields not set: omit
    #    - repeated fields with empty value: omit
    #    This is THE bug-prone step. Reference: the example in spec §8.4.1 (lines 2025-2056).
    payload = _prune_defaults(payload)

    # 3. Canonicalize per RFC 8785
    canonical_bytes = jcs.canonicalize(payload)  # returns UTF-8 bytes, sorted keys, no whitespace

    # 4. Build JWS Protected Header
    protected_header = {
        "alg": "RS256",
        "typ": "JOSE",
        "kid": sa_email,  # SA email is the kid; the JWKS URL is in jku
        "jku": f"https://www.googleapis.com/service_accounts/v1/jwk/{sa_email}",
    }
    protected_b64 = base64.urlsafe_b64encode(
        json.dumps(protected_header, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(canonical_bytes).rstrip(b"=").decode()

    # 5. Sign via IAM Credentials signBlob (NOT signJwt — signJwt would re-encode the payload)
    signing_input = f"{protected_b64}.{payload_b64}"
    client = IAMCredentialsClient()
    sig_response = client.sign_blob(
        name=f"projects/-/serviceAccounts/{sa_email}",
        payload=signing_input.encode(),
    )
    signature_b64 = base64.urlsafe_b64encode(sig_response.signed_blob).rstrip(b"=").decode()

    # 6. Attach to card
    return {
        **card,
        "signatures": [{
            "protected": protected_b64,
            "signature": signature_b64,
        }]
    }
```

**Lurking trap:** the spec's example at §8.4.1 (lines 2042-2056) shows the canonical form for an Agent Card with `capabilities.extensions: []` — and the result `OMITS extensions`. If you naively `json.dumps(card, sort_keys=True)` you will include the empty array, your JWS will be over a different byte string than the verifier reconstructs, and signature verification will fail with no useful error. Spec §8.4.1 lines 2010-2016 spell out the four rules; ignore them at your peril.

Discovery endpoint:

```python
# lib/a2a/server.py
@app.get("/.well-known/agent-card.json")
async def agent_card() -> dict:
    return _CACHED_SIGNED_CARD  # built once at startup, immutable
```

---

## 5. Sender-side integration (`lib/a2a/client.py`)

Outbound A2A is a TOOL CALL from Hermes' point of view. Wire it through the existing toolset_router.

### 5.1 Add a new tier

```python
# lib/toolset_router.py:17-23 — current state:
class Tier(str, Enum):
    IN_PROCESS = "in_process"
    SHELL_SANDBOX = "shell_sandbox"
    BROWSER_SANDBOX = "browser_sandbox"
    EXTERNAL_HTTPS = "external_https"
    CLOUD_SANDBOX = "cloud_sandbox"

# PROPOSED ADDITION (NOT in this audit pass — just sketched):
    A2A_PEER = "a2a_peer"  # outbound A2A call to a discovered peer agent
```

`config/toolsets.yaml` (currently at `/Users/danielmanzela/RX-Research Project/wt-framing-2/config/toolsets.yaml:42-45`) gains a route:

```yaml
  # A2A peer calls (outbound). The tool name is `a2a_call_<peer_slug>`,
  # dynamically registered per AgentCard in the peer registry.
  - match: ["a2a_call_*"]
    tier: a2a_peer
    evaluate_after: true   # peer calls are side-effecting; judge panel justified
```

### 5.2 Outbound client sketch

```python
# lib/a2a/client.py — sketch only

import httpx
import httpx_sse
from opentelemetry import trace, propagators
from opentelemetry.propagators.textmap import DefaultGetter

class A2AClient:
    """One instance per peer agent (cached AgentCard + JWKS + httpx.AsyncClient)."""

    def __init__(self, agent_card: dict, jwt_signer):
        self.card = agent_card
        self.signer = jwt_signer
        self.url = self._select_interface()  # JSON-RPC interface preferred
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    async def send_message(self, message: dict, *, human_sub: str) -> dict:
        """Sync (non-streaming) send. Returns the raw Task or Message response."""
        tracer = trace.get_tracer("hermes.a2a")
        with tracer.start_as_current_span("a2a.send_message") as span:
            span.set_attribute("a2a.peer.name", self.card["name"])
            span.set_attribute("a2a.peer.url", self.url)
            span.set_attribute("a2a.method", "message/send")
            # Inject W3C traceparent into outbound HTTP headers
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.signer.mint(audience=self.url, human_sub=human_sub)}",
                "A2A-Version": "1.0",
            }
            propagators.inject(headers)  # adds traceparent + tracestate
            envelope = {
                "jsonrpc": "2.0",
                "id": _new_id(),
                "method": "message/send",
                "params": {"message": message},
            }
            resp = await self.client.post(self.url, headers=headers, json=envelope)
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise _a2a_error_to_exception(body["error"])
            return body["result"]

    async def stream_message(self, message, *, human_sub):
        """SSE streaming send. Async-iterates StreamResponse events."""
        # Similar to above but use httpx_sse.aconnect_sse with method="message/stream"
        ...
```

### 5.3 Per-peer tool registration

For each peer AgentCard we know about, dynamically register a tool `a2a_call_<peer_slug>(message: str) -> str`. This routes through `ToolsetRouter` → tier `a2a_peer` → invokes `A2AClient.send_message`. From the agent loop's perspective, it's just another tool — same `pre_tool_call` / `post_tool_call` hooks fire (see `lib/durability/__init__.py:54-55`), same OTel `tool.dispatch` span emits (see `lib/observability/__init__.py:_pre_tool_call`).

---

## 6. Telemetry plumbing (already wired, minor extension needed)

The observability layer is the only existing surface that's nearly A2A-ready:

`lib/observability/__init__.py:132-140` registers hooks for `on_session_start`, `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `post_api_request`. Each hook emits OTel spans with both OpenInference (`llm.*`, `openinference.span.kind`) and OTel GenAI (`gen_ai.*`) attributes via `_set_gen_ai_attrs()` (controlled by `HERMES_DUAL_EMIT_GEN_AI` env var, file line ~ 360).

**The A2A spike adds two new spans:**
- `a2a.send_message` (client side, per outbound JSON-RPC call) — kind: `CHAIN` (delegating to a peer)
- `a2a.receive_message` (server side, per inbound JSON-RPC call) — kind: `CHAIN`

Both must carry the W3C `traceparent` from the wire, so spans across two agents stitch into a single Phoenix trace. See telemetry-design.md §3 for the exact propagator wiring.

The OTLP collector (`deploy/otel/collector.prod.yaml:1-57`) already exports to `googlecloud` (Cloud Trace / Cloud Logging) in production — no collector changes needed.

---

## 7. Auth plumbing (`lib/a2a/auth.py`)

Per auth-design.md, the spike uses **custom JWT via `signJwt` + Google-hosted JWKS**.

```python
# lib/a2a/auth.py — sketch only

import time
from google.cloud import iam_credentials_v1
from jose import jwt, jwk  # python-jose

class JWTSigner:
    def __init__(self, sa_email: str, default_audience: str | None = None):
        self.sa_email = sa_email
        self.iam = iam_credentials_v1.IAMCredentialsClient()

    def mint(self, *, audience: str, human_sub: str, ttl_s: int = 300) -> str:
        """Mint a JWT with composite identity (agent + acting-for human)."""
        now = int(time.time())
        payload = {
            "iss": f"https://{self.sa_email}",
            "sub": self.sa_email,            # the agent's SA
            "aud": audience,                  # the peer's URL (per RFC 7519 §4.1.3)
            "acting_for": human_sub,          # the human on whose behalf
            "agent_id": "hermes-prod",        # our agent's stable id
            "iat": now,
            "exp": now + ttl_s,
            "jti": _new_jti(),                # for replay protection on the receiver
        }
        # signJwt encodes + signs in one call. The returned token is a JWS over the payload
        # signed by the SA's currently-active Google-managed key.
        resp = self.iam.sign_jwt(
            name=f"projects/-/serviceAccounts/{self.sa_email}",
            payload=json.dumps(payload),
        )
        return resp.signed_jwt


class JWTVerifier:
    """Validates inbound JWTs using cached JWKS. Thread-safe."""

    def __init__(self, peer_sa_email_allowlist: list[str]):
        self.allowlist = set(peer_sa_email_allowlist)
        self._jwks_cache: dict[str, tuple[float, dict]] = {}  # sa_email -> (fetched_at, jwks)
        self._seen_jtis: set[str] = set()  # bounded LRU; replace with cachetools.TTLCache in real impl

    def verify(self, token: str, expected_audience: str) -> dict:
        """Returns the validated claim dict. Raises on failure."""
        unverified_header = jwt.get_unverified_header(token)
        sa_email = jwt.get_unverified_claims(token)["sub"]
        if sa_email not in self.allowlist:
            raise PermissionError(f"unknown peer SA: {sa_email}")
        jwks = self._get_jwks(sa_email)
        key = _find_kid(jwks, unverified_header["kid"])
        claims = jwt.decode(token, key, audience=expected_audience, algorithms=["RS256"])
        # Replay protection
        if claims["jti"] in self._seen_jtis:
            raise PermissionError("jti replay")
        self._seen_jtis.add(claims["jti"])
        return claims

    def _get_jwks(self, sa_email: str) -> dict:
        # Cache JWKS for 1h. Google rotates SA keys ~every 2 weeks.
        now = time.time()
        if sa_email in self._jwks_cache:
            fetched_at, jwks = self._jwks_cache[sa_email]
            if now - fetched_at < 3600:
                return jwks
        jwks_url = f"https://www.googleapis.com/service_accounts/v1/jwk/{sa_email}"
        jwks = httpx.get(jwks_url, timeout=5).json()
        self._jwks_cache[sa_email] = (now, jwks)
        return jwks
```

**Reuse the existing `autonomousagent-vm-runtime` SA** (`terraform/phase-0a-gcp/iam.tf:25-30`). Grant it `roles/iam.serviceAccountTokenCreator` on itself for signJwt:

```hcl
# terraform/phase-0a-gcp/iam.tf — proposed addition (NOT in this audit pass)
resource "google_service_account_iam_member" "vm_runtime_can_sign_own_jwt" {
  service_account_id = google_service_account.vm_runtime.id
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.vm_runtime.email}"
}
```

---

## 8. F-code mappings for A2A errors

Extend `lib/durability/trichotomy.py:_CLASSIFIERS` (currently at line 21) with patterns for A2A's -32xxx codes:

| Pattern | F-code | Trichotomy class | Rationale |
|---------|--------|-------------------|-----------|
| `task ?not ?found\|-32001` | F40 (NEW) | FAIL_SOFT | Peer claims task doesn't exist; not our fault, surface to user |
| `task ?not ?cancelable\|-32002` | F41 (NEW) | FAIL_SOFT | Tried to cancel a terminal task; surface |
| `push ?notification ?not ?supported\|-32003` | F42 (NEW) | FAIL_SOFT (skip_tool_class) | Peer doesn't support push; skip push-notif paths for that peer |
| `unsupported ?operation\|-32004` | F43 (NEW) | FAIL_LOUD | Capability not declared; AgentCard out of sync; surface loudly |
| `content ?type ?not ?supported\|-32005` | F44 (NEW) | FAIL_SOFT | Part MIME not accepted; could retry with different mode |
| `invalid ?agent ?response\|-32006` | F45 (NEW) | FAIL_LOUD | Peer-side bug; surface loudly |
| `authenticated ?extended ?card.*not ?configured\|-32007` | F46 (NEW) | FAIL_SOFT | Peer declared but didn't configure; downgrade to base card |
| `invalid ?version\|-32008` | F47 (NEW) | FAIL_LOUD | Protocol version mismatch; can't proceed |
| `duplicate ?message\|-32009` | F48 (NEW) | SELF_HEAL | Retry with new message_id (rare but specced) |
| `jws.*verif.*fail\|invalid signature` | F49 (NEW) | FAIL_LOUD | AgentCard JWS broken; peer is untrusted, fail loudly |

The dispatch path (`lib/durability/handlers.py`) automatically picks these up via the F-code → handler table once added.

---

## 9. Configuration touchpoints

- `config/toolsets.yaml` — add `a2a_call_*` route (above §5.1)
- `config/limits.yaml` — add `a2a.*` section: `jwt_ttl_s`, `jwks_cache_ttl_s`, `peer_allowlist: []`, `enabled: false` (gate the whole spike behind a flag)
- `config/hermes/cli-config.yaml` — add `a2a` to `plugins.enabled` (FILE NOT READ THIS SESSION — verify before writing)
- `deploy/docker-compose.yml:404-419` — add `../lib/a2a:/home/hermes/.hermes/plugins/a2a:ro` mount
- `deploy/docker-compose.yml` (top-level services) — NEW service `a2a-server` (uvicorn, exposed port 8004 to internal network only initially)
- `terraform/phase-0a-gcp/iam.tf` — add `serviceAccountTokenCreator` self-binding for `vm_runtime` SA
- `terraform/phase-0a-gcp/compute.tf` — add firewall rule allowing inbound 443 → backend service that fronts the a2a-server container (deferred until day 7 of spike)

---

## 10. What NOT to touch in the spike

- `lib/anchors/task_spec.py` — TaskSpec schema is immutable post-lock; don't add A2A fields to it. Bridge via `task_bridge.py` instead.
- `lib/observability/__init__.py` — existing dual-emit logic is correct for tool spans. The two new A2A spans add ALONGSIDE, not by modifying existing hooks.
- `lib/durability/checkpoint.py` — schema is versioned; bumping to schema_version=2 is a Phase-2 concern, not spike scope.
- `lib/scrubber.py` — scrubber runs on outbound LiteLLM calls today. A2A messages flow through `lib/a2a/client.py`, NOT through LiteLLM, so they bypass the scrubber. **This is a P0 issue** — see open-questions.md Q5.
- The Hermes submodule itself — all integration is through plugin hooks; we never modify `hermes-agent/` source.

---

## 11. The dependency graph at a glance

```
                  config/limits.yaml (a2a section)
                         │
                         ▼
                  lib/a2a/__init__.py (register hooks + commands + tools)
                  │           │            │           │
                  ▼           ▼            ▼           ▼
            server.py    client.py    auth.py    agent_card.py
                  │           │            │
                  │           │            ├─→ IAMCredentialsClient (signJwt, signBlob)
                  │           │            ├─→ httpx GET (Google JWKS endpoint, cached)
                  │           │            └─→ python-jose (verify)
                  │           │
                  │           └─→ httpx + httpx-sse (outbound)
                  │           └─→ OTel propagators (W3C traceparent inject)
                  │
                  ├─→ FastAPI/Starlette (HTTP server)
                  ├─→ OTel propagators (W3C traceparent extract)
                  ├─→ lib.anchors.SpecStore (task CRUD)
                  ├─→ lib.durability.checkpoint (resume on subscribe)
                  └─→ lib.observability tracer (a2a.* spans)
```

This is the surface area to wrap a 2-week budget around. spike-plan.md sequences it day-by-day.
