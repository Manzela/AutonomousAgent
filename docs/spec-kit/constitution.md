# Spec-Kit Constitution — Domain Best-Practice Grounding Asset

**SP-25 asset.** Vendored into this repo as a static reference for the SP-03
spec-drafter's `applied_standards[]` field. Not a runtime dependency; no import.

Grounding source for `kind=override` anti-sycophancy challenges (C18) and for
the `applied_standards[].source` field that the drafter emits as overridable
DEFAULTS (R5). The operator's sign-off answer is always authoritative.

---

## Software Engineering Principles

### Correctness over cleverness
Prefer simple, well-understood implementations over clever ones. A correct
algorithm with O(n²) complexity is better than a broken O(n log n) one.

### Fail loudly, fail early
Detect violations at the earliest possible boundary. Silent failures
(swallowed exceptions, ignored return codes, unchecked `None`) propagate
and become hard to diagnose. Raise, log, or signal — never discard.

### Immutable data by default
Prefer frozen/immutable value objects (dataclass(frozen=True), Pydantic
BaseModel with model_config=ConfigDict(frozen=True)) for data that flows
across boundaries. Mutation is a source of aliasing bugs.

### The Boy Scout Rule
Leave the code cleaner than you found it — one small improvement per touch.
Never leave a known broken window (commented-out code, TODO without a ticket,
suppressed linter error) without a plan to close it.

---

## Security Principles (OWASP ASVS 5.0 — abbreviated)

### Input validation at every trust boundary
Validate and sanitise all data entering from external sources (user input,
API responses, file reads, environment variables). Never pass raw user input
to shell commands, SQL queries, or file paths without sanitisation.

### Least privilege
Every component should have only the permissions it needs. Service accounts
should be scoped to the minimum IAM roles; processes should run as
non-root; workspaces should be chroot-isolated when executing untrusted code.

### Secrets out of source control
Credentials, tokens, API keys — never in plaintext in the repo. SOPS-encrypt
before commit. Use WIF/workload-identity where possible to eliminate
long-lived credentials entirely.

### Defence in depth
No single control should be the sole barrier. Layer: input validation +
parameterised queries + WAF + RBAC. A single broken layer must not expose
the system.

---

## API Design Principles (RESTful + gRPC)

### Idempotency for state-changing operations
PUT/PATCH should be idempotent. POST operations that may be retried (e.g.,
payment, dispatch) must carry a client-supplied idempotency key. LangGraph
nodes that write state must be guarded by an exactly-once ledger.

### Backward-compatible versioning
Add fields, never remove or rename without a deprecation cycle. Use semantic
versioning: breaking changes are major bumps. Pydantic models used across
service boundaries should carry a `schema_version` field.

### Error responses carry actionable context
HTTP 4xx/5xx responses and gRPC Status codes must include a structured body
with `{code, message, details[]}` — never a bare string or an empty body.

---

## Distributed Systems Principles

### At-least-once + idempotent consumers
In any queue/pub-sub system assume messages may be delivered more than once.
Consumers must de-duplicate on a stable `(source, origin_id)` key stored in
a persistent ledger.

### Circuit breakers for external dependencies
Calls to external services (LLM APIs, GCS, Pub/Sub) must be wrapped in a
circuit-breaker or retry-with-exponential-backoff. A dead dependency must
degrade gracefully, not crash the process.

### Observability: metrics + traces + logs
Every significant operation should emit a span (OpenTelemetry), a structured
log line, and a metric counter or histogram. The "three pillars" together are
required for production debuggability.

---

## EARS Cheat-Sheet (see also ears.md)

EARS (Easy Approach to Requirements Syntax) pattern reference:

| Pattern | Template |
|---------|----------|
| Ubiquitous | The `<system>` shall `<system response>` |
| State-driven | While `<precondition>`, the `<system>` shall `<system response>` |
| Event-driven | When `[precondition]` `<trigger>`, the `<system>` shall `<system response>` |
| Unwanted behaviour | If `[precondition]` `<trigger>`, then the `<system>` shall `<system response>` |
| Optional feature | Where `<feature>`, the `<system>` shall `<system response>` |
| Complex | While `<precondition>`, when `[precondition]` `<trigger>`, the `<system>` shall `<system response>` |

---

## Test Design Principles

### Red-green discipline
Every new feature or bug-fix must have a test that was RED before the change
and is GREEN after. A test that was never RED proves nothing about the fix.

### Test the mechanism, not the side effect
An oracle that asserts a value that happens to be correct for the wrong reason
is a false positive. Assert the mechanism ran (the callback was called, the
gate was consulted, the ledger was written) plus the exact expected state.

### Hermetic by default
Tests that require a live LLM, network, or GCP service belong in
`tests/integration/`, not `tests/unit/`. Unit tests must pass with
`OTEL_*_EXPORTER=none` and no external tokens.
