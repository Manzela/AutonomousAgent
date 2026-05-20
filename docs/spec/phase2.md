# Phase 2 — System-of-Record (codified hardened foundation)

> **Status:** Adopted. This document is the architecture description (per
> ISO/IEC/IEEE 42010) of the AutonomousAgent / Hermes deployment as it
> exists on `main` after the Wave-1 and Wave-2 hardening shipped
> 2026-05-19. It is **codify-what-exists only** — no forward-looking
> features, no GCP migration plan, no capability projections.
>
> **Audit anchor:** `audit/2026-05-19-resume-orchestration/audit-plan.md`
> (§P2-1, §1 5-layer model) and `findings.md` (§4 capability surface, §5
> risk register).
>
> **Provenance of the "hardened foundation" claim:** Wave-1 (P0) closed
> via PRs #72–#80. Wave-2 (P1) closed via PRs #81–#87. Every F-code in
> §2 below resolves to a callable handler post-PR #77; every escalation
> path has a working sink post-PRs #83 and #84; every snapshot has an
> executor behind a feature flag post-PR #86.
>
> **What this spec is not.** It is not a roadmap. It is not a list of
> things we wish were true. It records the seams that exist today so
> that any future work — feature, refactor, migration — has accurate
> failure modes and layer-crossing criteria to design against.

---

## Section 1 — System-of-record: the 5-layer orchestration model

### 1.0 Why 5 layers

The deployment combines an interactive developer environment (Claude
Code), an in-session parallelism surface, a multi-terminal coordination
surface, a verification-discipline surface, a long-running autonomous
runtime (Hermes), and a set of external durability services. These are
not architectural style choices — they are six distinct execution
substrates with different cost, latency, and durability characteristics.
Conflating them produces the failure mode the prior audit named
"spec-without-verification": a Phase-1 spec that did not distinguish
"the agent thought it shipped" from "the artifact is durably present in
the layer that needs it" required ten hotfix PRs to converge.

The 5-layer model is a forcing function against that failure. Every
operation belongs to exactly one originating layer; every cross-layer
call is a contract that must be named.

### 1.1 The layers

| Layer | What | Optimizes for | Durability boundary |
|---|---|---|---|
| 0 | Single Claude Code reasoning loop (the conversation in front of the human) | Depth of thought, tight feedback, full tool palette | None beyond the conversation; context survives compaction but not a process restart |
| 1 | Parallel/background within one Claude Code session — `Agent` tool, `Bash run_in_background`, `CronCreate`, `EnterWorktree` | Wall-clock parallelism, context isolation across independent threads | Same as Layer 0; sub-agent results return into the parent's context |
| 2 | Multiple Claude Code terminals on the same workstation | Independent user-facing workstreams; one human-driven, one fully-autonomous Hermes loop | Filesystem + git + Kanban SQLite are the only shared channels; no in-memory coordination |
| 3 | Verification skills (the `superpowers:*` discipline layer) | Honesty — "I think it works" forced to "I verified it works"; defends against the spec-without-verification failure mode | Policies, not features; cross-cut every other layer |
| 4 | Hermes runtime — `lib/kanban`, `lib/anchors`, `lib/evaluators`, `lib/durability`, `lib/memory`, `lib/observability`, `lib/snapshots`, the escalation watcher sidecar | Multi-day autonomous goals; durable task state independent of any single Claude Code session | SQLite Kanban DB at `/home/hermes/.hermes/kanban.db`; checkpoint files at `/data/checkpoints/`; local JSONL fallback at `/data/local_logs/` |
| 5 | External durability services — Vertex AI, Honcho, Chroma Cloud, Phoenix, GCS snapshots, Telegram, SOPS+age | Cross-host persistence, scale, out-of-band human signal | Each service is its own SLO domain; degradation handled per-F-code (see §2) |

### 1.2 What each layer optimizes for

Each layer's optimization criterion is the lens through which a
decision to land work in that layer should be made.

**Layer 0** optimizes for the human-in-the-loop's working memory and
the model's ability to keep a tight reasoning thread on a single
non-trivial task. The cost of staying here is single-thread throughput;
the benefit is that nothing is dropped on the floor between layers.
Default home for any task that fits in one conversation.

**Layer 1** optimizes for wall-clock parallelism *within one human
session*. Sub-agents are dispatched when the work decomposes into
independent threads (the `dispatching-parallel-agents` skill governs
fan-out / fan-in). Backgrounded `Bash` jobs are used for long-running
processes (builds, container starts, integration test suites) whose
output we want to consume on completion rather than block on. `Cron`
inside the session is used for polling that should survive REPL
idleness but does not need to survive a session restart.

**Layer 2** optimizes for separation of concerns at the human-task
level. Two terminals on the same workstation share filesystem and git
but not in-memory state. The pattern that matters here is "one
human-attended terminal + one fully-autonomous Hermes loop" — the
human can iterate on tomorrow's work in their terminal while Hermes
executes today's work in its own terminal without either side
stepping on the other.

**Layer 3** does not produce features. It produces honesty. The
`verification-before-completion` skill, the `test-driven-development`
skill, the `requesting-code-review` skill, and the
`writing-plans` skill collectively turn "I think the change works"
into "I have evidence the change works". Every other layer is
permitted to skip Layer 3 only when the work is purely additive and
trivially reversible (e.g. a docs-only PR). For any layer-crossing
contract change, Layer 3 is required (see §3 ADR-3).

**Layer 4** optimizes for multi-day autonomous execution. The
Hermes runtime is the only layer with a durable task state model:
Kanban cards have explicit lifecycle (PROPOSED → CLAIMED → IN_PROGRESS
→ BLOCKED|DONE), explicit heartbeats (`last_heartbeat_at`), and
explicit escalation (24h Telegram silence → GitHub issue fallback per
PR #83). The runtime is what we lift work *into* when "let's let the
agent run overnight" is the goal.

**Layer 5** optimizes for "what happens when the workstation
restarts". The LLM API (Vertex AI), the long-term memory (Honcho),
the vector store (Chroma Cloud), the trace store (Phoenix), the
snapshot bucket (GCS), the human-channel-of-last-resort (Telegram),
and the secret store (SOPS+age) each carry state across host
boundaries. Layer-5 reachability is a precondition for almost every
Layer-4 contract.

### 1.3 What "in production" means per layer

A layer is "in production" when the seams *into* and *out of* it have
explicit handlers for every classifiable failure. Wave-2 closed the
remaining gaps:

- Layer 4 → Layer 5 seams (Honcho, Chroma, Vertex, Phoenix, GCS,
  Telegram) are each classified by `lib/durability/trichotomy.py` and
  dispatched by `lib/durability/handlers.py`. PR #78 wired MCP errors
  through the same path; PR #81 expanded the regex table from live
  container log data.
- Layer 4 daily budget cap (F21) is enforced by
  `lib/durability/budget_watchdog.py` (PR #84). It polls the LiteLLM
  `LiteLLM_SpendLogs` Postgres table directly because the `/spend/total`
  REST endpoint is not loaded on this deployment.
- Layer 4 disaster-recovery snapshot (R2 mitigation) has an executor —
  `lib/snapshots/gcs_snapshot.py` (PR #86). The executor is
  feature-flagged on `GCS_SNAPSHOT_BUCKET` so the code is in place
  before the GCS bucket and SA key are owner-provisioned.
- Layer 4 → Layer 5 escalation path has a fallback when Telegram itself
  is unreachable: `lib/durability/github_fallback.py` opens an
  `incident/auto`-labelled GitHub issue (PR #83).
- Layer 4 checkpoint integrity is covered by
  `tests/integration/test_snapshot_integrity.py` (PR #79). The test
  fails loudly if the checkpoint file is corrupted.

### 1.4 The super-orchestrator pattern

Claude Code (Layer 0–3) is the *design and implementation* environment
for the orchestrator. Hermes (Layer 4) IS the orchestrator at runtime.
The handoff between them is a set of durable artifacts:

- Source code → Hermes runtime, shipped via PR.
- Kanban cards → durable task state, SQLite-backed.
- Checkpoint files → durable per-step progress at `/data/checkpoints/`.
- Phoenix spans → durable observability with OpenInference attributes
  (`openinference.span.kind` for both `LLM` and `TOOL` spans per PR #70).
- Honcho sessions → durable cross-session memory.
- GCS daily snapshot → durable system-state recovery point (when the
  bucket is provisioned).
- Telegram + GitHub-issue fallback → durable out-of-band human signal.

That handoff set is what justifies calling the deployment a
"super-orchestrator". Layer 0 does design; Layer 4 does execution;
Layer 3 keeps both honest; Layer 5 keeps both durable.

---

## Section 2 — F-code failure modes (every code maps to a real handler)

The failure matrix is the contract between layers: every failure mode
the system has a name for has a real handler post-PR #77 (baseline
handlers + stub registration for the remaining named handlers). The
test `tests/unit/test_handlers.py::test_all_33_codes_dispatch_to_callable`
enforces "every entry resolves to a callable" at CI time.

**Source-of-truth file:** `lib/durability/failure_matrix.py` — the
`FAILURE_MATRIX` dict is the machine-readable form. The companion
human-readable table at `docs/architecture/failure-matrix.md` is kept
in lockstep via the `grep -cE "^\| F[0-9]+ \|" == 33` CI guard.

**Source-of-truth handler file:** `lib/durability/handlers.py` — the
three baseline implementations (`retry_with_backoff`,
`halt_alert_snapshot`, `fallback_local_log`) plus the auto-generated
stub registry that routes every named handler to a baseline based on
the F-code's trichotomy class.

**Classifier:** `lib/durability/trichotomy.py` — the regex table that
turns a raw exception into an F-code; expanded from live MCP error
data in PR #81.

### 2.1 Self-Heal (F1–F11) — transient, retry with backoff

| F-code | Description | Classifier | Handler (file:symbol) | Status |
|---|---|---|---|---|
| F1 | Rate limit (429) | `rate.?limit|429|too many requests` | `handlers.retry_with_backoff` | live |
| F2 | Network timeout | `timed? out|timeout|deadline exceeded` | `handlers.retry_with_backoff` | live |
| F3 | Transient DNS resolution failure | `name or service not known|dns|nxdomain` | `handlers.retry_with_backoff` | live |
| F4 | 5xx from upstream LLM API | `5\d\d|internal server error|bad gateway` | `handlers.retry_with_backoff` | live |
| F5 | Connection reset by peer | `connection reset` | `handlers.retry_with_backoff` | live |
| F6 | Temporary tool sandbox crash | `sandbox.*(crash|exit)` | `handlers.HANDLER_REGISTRY["restart_sandbox_and_retry"]` (stub → self-heal baseline) | live (stub) |
| F7 | Honcho/Chroma temporary unavailable | `chroma.*unavailable` | `handlers.retry_with_backoff` | live |
| F8 | Stale Vertex AI auth token | `vertex.*(auth|credentials)|invalid token` | `handlers.HANDLER_REGISTRY["refresh_adc_and_retry"]` (stub → self-heal baseline) | live (stub) |
| F9 | Race on Kanban claim_lock | `claim.?lock|claim contention` | `handlers.retry_with_backoff` | live |
| F10 | Checkpoint write contention | `checkpoint.*(contention|locked)` | `handlers.retry_with_backoff` | live |
| F11 | Gemini thinking-tokens silent truncation | `max_tokens too low|thinking tokens truncated` | `handlers.HANDLER_REGISTRY["retry_with_higher_max_tokens"]` (stub → self-heal baseline) | live (stub) |

**Backoff formula** (`lib.durability.trichotomy.backoff_delay`):
exponential with jitter, configured by `config/limits.yaml`
`retries.self_heal.*`. Defaults: `base_delay_ms=500`,
`max_delay_ms=30000`, `jitter_range_pct=25`. The same formula is reused
by `handlers.retry_with_backoff` to ensure call sites do not invent
their own backoff curves.

### 2.2 Fail-Soft (F12–F20) — degrade and continue

| F-code | Description | Classifier | Handler (file:symbol) | Status |
|---|---|---|---|---|
| F12 | Chroma vector store down — disable semantic memory | `chroma.*down` | `handlers.HANDLER_REGISTRY["disable_chroma_for_session"]` (stub → fail-soft baseline) | live (stub) |
| F13 | OTel collector unreachable — log spans locally | `otel.*unreachable` | `handlers.fallback_local_log` | live |
| F14 | Github MCP server unavailable — skip github-tagged tools | `github.?mcp.*(unavailable|unauthorized|forbidden)` plus the MCP session-expired family (`session terminated|expired|not found|transport is closed`) | `handlers.HANDLER_REGISTRY["skip_tool_class"]` (stub → fail-soft baseline) | live (stub); PR #78 wired MCP errors through this path; PR #81 expanded the regex from live log data |
| F15 | Skill extractor temporarily failing — defer extraction | (extractor-side) | `handlers.HANDLER_REGISTRY["defer_extraction"]` (stub → fail-soft baseline) | live (stub) |
| F16 | Single evaluator judge timeout — N-1 consensus | (evaluator-side) | `handlers.HANDLER_REGISTRY["drop_judge_continue_consensus"]` (stub → fail-soft baseline) | live (stub) |
| F17 | Phoenix UI down — traces still collected | (Phoenix-side) | `handlers.HANDLER_REGISTRY["log_and_continue"]` (stub → fail-soft baseline) | live (stub) |
| F18 | Honcho metadata API slow — use cached | (Honcho-side) | `handlers.HANDLER_REGISTRY["use_cached"]` (stub → fail-soft baseline) | live (stub) |
| F19 | Per-task token budget exceeded — truncate response | (limits-side) | `handlers.HANDLER_REGISTRY["truncate_and_warn"]` (stub → fail-soft baseline) | live (stub) |
| F20 | MEMORY/REJECTED.md inject would exceed context — skip | (memory-side) | `handlers.HANDLER_REGISTRY["skip_inject"]` (stub → fail-soft baseline) | live (stub) |

**Fail-soft local log target**:
`HERMES_LOCAL_LOG_DIR` env var, default `/data/local_logs/`. Records
are written as JSONL one-per-line under `<date>/<f_code>.jsonl` so an
operator can replay them when the remote target recovers.

### 2.3 Fail-Loud (F21–F33) — halt + alert via Telegram + snapshot

| F-code | Description | Classifier | Handler (file:symbol) | Status |
|---|---|---|---|---|
| F21 | Daily budget cap exceeded | `lib/durability/budget_watchdog.py` polling `LiteLLM_SpendLogs` (not regex) | `handlers.halt_alert_snapshot` | live (PR #84) |
| F22 | Critical secret leak detected by scrubber | (scrubber-side, `lib/scrubber.py`) | `handlers.halt_alert_snapshot` | live |
| F23 | Sandbox escape attempt detected | (sandbox-side) | `handlers.halt_alert_snapshot` | live |
| F24 | Multi-judge consensus failure (split vote, no 5th judge) | (evaluator-side) | `handlers.halt_alert_snapshot` | live |
| F25 | TaskSpec lock-time clarification loop exceeded max questions | (anchor-side) | `handlers.HANDLER_REGISTRY["halt_alert_request_approval"]` (stub → fail-loud baseline) | live (stub) |
| F26 | 3-strike approach rejection (REJECTED.md trigger) | (memory-side) | `handlers.halt_alert_snapshot` | live |
| F27 | Persistent Vertex AI auth failure after retry+refresh | (after F8 retries exhausted) | `handlers.halt_alert_snapshot` | live |
| F28 | Disk full on checkpoint write | `OSError` whose message matches "no space left" | `handlers.halt_alert_snapshot` | live |
| F29 | Hermes Kanban DB corruption / migration failure | (kanban-side) | `handlers.halt_alert_snapshot` | live |
| F30 | Approval-required tool fired without approval (policy violation) | (tool-middleware-side) | `handlers.halt_alert_snapshot` | live |
| F31 | Egress allowlist violation attempt | (sandbox-side) | `handlers.halt_alert_snapshot` | live |
| F32 | 24h Telegram silence on blocked card → escalate | `lib/durability/escalation.py` (scheduled scan, not regex) | `handlers.HANDLER_REGISTRY["alert_user_escalate_kanban"]` (stub → fail-loud baseline); secondary path via `lib/durability/github_fallback.py` when Telegram itself is unreachable | live (PR #83 fallback) |
| F33 | F-code lookup failed (unclassified exception) | catch-all when classifier returns no match | `handlers.halt_alert_snapshot` | live |

**Fail-loud halt side effects** (`handlers.halt_alert_snapshot`):

1. Write a checkpoint via `lib.durability.checkpoint.Checkpoint.maybe_write`
   if a checkpoint instance is in scope.
2. Send a Telegram alert via `lib.kanban.telegram_bridge.send_alert`.
3. Transition the active card to `blocked` via
   `lib.kanban.telegram_bridge.update_card_status`.

Each step is wrapped in its own `try/except` so a partial failure of
one (e.g. Telegram down) does not block the others. The Telegram
failure path itself is covered by F32's GitHub-issue fallback so the
operator always has at least one durable signal.

### 2.4 Dispatch invariants

`lib/durability/handlers.py::dispatch` is the single entry point that
every error-handling call site uses. Three invariants hold:

1. **Unknown F-code → F33.** A call with a code not in the matrix is
   re-dispatched to F33 (which itself maps to `halt_alert_snapshot`).
   A defensive guard handles the case where F33 is also missing.
2. **Every named handler has a callable.** At import time the module
   walks `FAILURE_MATRIX`, discovers every distinct handler name, and
   registers a stub for any name not explicitly implemented. The stub
   logs a WARNING identifying which name to implement next and
   delegates to the appropriate baseline (`retry_with_backoff` for
   self-heal, `fallback_local_log` for fail-soft, `halt_alert_snapshot`
   for fail-loud).
3. **Fail-open on all side effects.** Every handler's side effects are
   wrapped so that a partial-system failure (e.g. Telegram down)
   degrades but does not crash the agent loop. This matches the
   posture used throughout `lib.kanban.telegram_bridge`.

### 2.5 Classifier coverage rule

Every F-code in §2.1–§2.3 is either:

- pattern-matched by `lib/durability/trichotomy.py::_CLASSIFIERS`
  (regexes), or
- raised by a scheduled scanner (`budget_watchdog` for F21,
  `escalation` for F32) directly with the F-code attached, or
- raised by an internal subsystem with the F-code attached (scrubber
  for F22, sandbox middleware for F23/F31, evaluator for F24/F16,
  anchor for F25, memory for F26/F20, retry-exhausted path for F27,
  checkpoint writer for F28, Kanban DB layer for F29, tool middleware
  for F30, MCP wrapper for F14 via PR #78), or
- the explicit catch-all (F33).

Adding a new failure mode requires (in order): a new row in
`FAILURE_MATRIX`, a new row in `docs/architecture/failure-matrix.md`,
either a new regex in `_CLASSIFIERS` or a new direct raise site, and
either a new entry in `HANDLER_REGISTRY` or acceptance that the stub
+ delegated-baseline behavior is sufficient.

---

## Section 3 — ADR appendix: layer-boundary cross criteria

These ADRs codify the existing rules; they are descriptive, not
aspirational. Each ADR names a decision that was already made by the
shipped code; the appendix records it so future contributors do not
re-litigate it without cause.

### ADR-1 — Default home is Layer 0

**Context.** Every conversation begins as a Layer-0 reasoning loop.
The temptation to fan out (Layer 1) or hand off to Hermes (Layer 4)
on first contact is real and expensive. Each promotion adds
coordination cost: sub-agents must be specified, contexts must be
serialized, Kanban cards must be created.

**Decision.** All work starts at Layer 0. A promotion to a higher
layer requires an explicit reason that matches the layer's optimization
criterion (per §1.2). The promotion criteria for each layer are:

- **Layer 0 → Layer 1** when (a) the same Claude Code session has 2+
  truly-independent threads of work, AND (b) the total wall-clock cost
  of running them serially exceeds ~30 minutes. Below that threshold,
  serial Layer-0 execution is cheaper than the fan-out / fan-in
  overhead.
- **Layer 0 → Layer 2** when there are two distinct human-attended
  workstreams that should not share the same context window
  (e.g. design work vs. PR-review work), OR when one stream is
  fully-autonomous Hermes execution that should not consume the
  developer's working context.
- **Layer 0 → Layer 4** when the work is multi-day in scope, requires
  state to survive container restart, OR requires the Kanban lifecycle
  primitives (claim, heartbeat, blocked, escalate). Anything
  multi-hour-but-single-day stays at Layer 0 or 1.
- **Any layer → Layer 5** when the work must persist beyond a
  container restart OR cross a session boundary. Layer-5 reachability
  is a precondition for almost every Layer-4 contract.

**Consequence.** Avoids over-promotion. The cost of staying at Layer 0
is single-thread throughput; the benefit is that nothing is dropped on
the floor between layers.

### ADR-2 — Verification skills (Layer 3) gate every layer-crossing contract change

**Context.** The Phase-1 spec required ten hotfix PRs (#56–#63 series)
to converge because "the agent thought it shipped" was treated as
equivalent to "the artifact is durably present in the target layer".
The Layer-3 discipline skills exist to break that equivalence.

**Decision.** A PR that changes a layer-crossing contract — a
plugin hook signature, a Kanban DB schema, a checkpoint JSON shape, an
OpenInference span attribute, an F-code classifier regex, the
`HANDLER_REGISTRY` keys, a `config/limits.yaml` field — must invoke
the relevant verification skill. The skills are:

- `superpowers:test-driven-development` — for any code change that
  introduces or modifies a contract.
- `superpowers:verification-before-completion` — before claiming
  "done" on any work, before committing, before opening a PR.
- `superpowers:requesting-code-review` — for any code change that
  modifies a layer-crossing contract.
- `superpowers:writing-plans` — for any multi-step change that affects
  more than one file in `lib/durability/` or any file in
  `lib/snapshots/`.

Docs-only PRs and PRs that touch only one file inside a single layer
are exempt.

**Consequence.** The cost of a contract change goes up; the cost of
shipping a broken contract goes down. The Phase-1 hotfix cascade is
the worked example of what happens without this rule.

### ADR-3 — Layer-4 durability is enforced via a single dispatch entry point

**Context.** Prior to PR #77, the failure matrix was documentation: it
listed handler *names* as strings, but the named functions did not
exist. Error-handling call sites either invented their own retry logic
or silently dropped the error. The risk register's R3 ("F33 catch-all
masking real failures") and R4 ("named handlers, no implementation")
both originated in this gap.

**Decision.** `lib/durability/handlers.py::dispatch` is the single
entry point. Every error-handling call site in Layer 4 uses it. No
call site invents its own retry curve (use
`handlers.retry_with_backoff` or the underlying
`trichotomy.backoff_delay`). No call site decides on its own to halt
the agent (use `handlers.halt_alert_snapshot`). No call site logs an
error and continues without classification (call `dispatch` with the
classified F-code; if the code is unknown, F33 routes correctly).

**Consequence.** The 33-mode matrix has uniform semantics. Adding a
new failure mode is a four-step recipe (§2.5). The CI test
`test_all_33_codes_dispatch_to_callable` is the gate that keeps the
invariant.

### ADR-4 — Layer-5 work is feature-flagged until the external resource exists

**Context.** PR #86 shipped `lib/snapshots/gcs_snapshot.py` before the
GCS bucket and service-account key were provisioned. The naive design
(crash if the bucket is missing) would have broken the sidecar startup
on every deployment that does not yet have the bucket. The
historically correct alternative (wait for the bucket, then ship the
code) would have meant the executor was missing the day the bucket
became available.

**Decision.** Layer-5 integrations whose external resource requires
human provisioning ship behind an environment-variable feature flag.
The executor reads the flag at start, logs a one-time INFO if it is
unset, and treats every tick as a no-op. The operator flips the flag
once the resource is provisioned. The pattern is:

```text
if not os.environ.get("FEATURE_FLAG_VAR"):
    logger.info("feature disabled: %s unset", "FEATURE_FLAG_VAR")
    return  # or sleep loop
```

**Consequence.** Code and resource provisioning decouple. The CI
contract is met (the executor is in place); the runtime contract
defers until the resource exists. The same pattern is reused for
any future Layer-5 integration with the same provisioning model.

### ADR-5 — Layer-4 → Layer-5 escalation has a fallback when the primary channel is itself a Layer-5 dependency

**Context.** F32 ("24h Telegram silence on blocked card → escalate")
originally used Telegram as the alert channel. The risk register's R8
named the circularity: when Telegram itself is the failure, the F32
alert cannot reach the operator. PR #83 added the GitHub-issue
fallback to break the cycle.

**Decision.** Every fail-loud handler whose primary channel is itself
a Layer-5 dependency must have a secondary channel that uses a
different Layer-5 dependency. Today's pairing:

- **Primary:** Telegram (`lib/kanban/telegram_bridge.send_alert`).
- **Secondary:** GitHub issue (`lib/durability/github_fallback`),
  invoked via the `gh` CLI already authenticated in every container.

The secondary path is itself fail-open: if `gh` is unavailable or
fails, the watcher logs a WARNING and continues. The card is still
visible in the Kanban UI as the channel-of-last-resort.

**Consequence.** No single Layer-5 outage can silence the F32 path.
Future fail-loud handlers that add new external channels (e.g. email,
PagerDuty) follow the same paired-channel rule.

### ADR-6 — Documentation parity with code is a CI guard, not a convention

**Context.** Prior audits found cases where `docs/architecture/`
described behavior that the code did not implement. The cost of
catching that drift in review is high; the cost of catching it via a
small CI check is near-zero.

**Decision.** Wherever the documentation describes a machine-readable
contract, a CI guard enforces parity. Current guards:

- `grep -cE "^\| F[0-9]+ \|" docs/architecture/failure-matrix.md ==
  33` — row count in the human table matches the code matrix.
- `tests/unit/test_failure_matrix.py::test_all_33_codes_present` —
  every doc row has a code-side entry.
- `tests/unit/test_handlers.py::test_all_33_codes_dispatch_to_callable`
  — every code-side entry resolves to a callable.

Future contracts (the OpenInference span shape, the checkpoint JSON
schema version, the Kanban DB migration version) should follow the
same pattern: a small guard that runs in CI, not a "remember to keep
them in sync" comment.

**Consequence.** Documentation that describes a contract is durable.
Documentation drift is caught at PR time, not at audit time.

---

## Appendix A — Provenance of every claim in this spec

The spec deliberately cites the PRs that shipped each piece of the
foundation. Future contributors should be able to walk from any
statement back to a specific commit on `main`.

| Section | Claim | Provenance |
|---|---|---|
| §1.3 | MCP errors are classified through `trichotomy` | PR #78 |
| §1.3 | Trichotomy regex grown from live log data | PR #81 |
| §1.3 | F21 budget cap is enforced | PR #84 (`lib/durability/budget_watchdog.py`) |
| §1.3 | GCS snapshot executor exists behind a feature flag | PR #86 (`lib/snapshots/gcs_snapshot.py`) |
| §1.3 | F32 has a GitHub-issue fallback | PR #83 (`lib/durability/github_fallback.py`) |
| §1.3 | Snapshot integrity is covered by an integration test | PR #79 (`tests/integration/test_snapshot_integrity.py`) |
| §1.4 | OpenInference span attributes on LLM and TOOL spans | PR #70 (`lib/observability/__init__.py`) |
| §2 | Every F-code resolves to a callable handler | PR #77 (`lib/durability/handlers.py`) |
| §2.1 | Backoff formula reused across all self-heal handlers | `lib.durability.trichotomy.backoff_delay` (pre-Wave-1 baseline, hardened in #77) |
| §2.3 | Fail-loud halt has three isolated side effects | `lib.durability.handlers.halt_alert_snapshot` (PR #77) |
| §2.4 | Single dispatch entry point | `lib.durability.handlers.dispatch` (PR #77) |
| §3 ADR-2 | Phase-1 spec required ten hotfix PRs | PRs #56–#63 series + #67 (handoff doc corrections) |
| §3 ADR-3 | Pre-#77 risk: handlers named but not implemented | `audit/2026-05-19-resume-orchestration/findings.md` §5.1 R4 |
| §3 ADR-5 | R8 circularity (Telegram on Telegram) | `audit/2026-05-19-resume-orchestration/findings.md` §5.1 R8 |
| §3 ADR-6 | Failure-matrix row-count CI guard | `docs/architecture/failure-matrix.md` (header) |

## Appendix B — Out-of-scope, by design

This spec deliberately does **not** describe:

- Any forward-looking feature (Phase 3, multi-tenant, multi-model, etc.).
- Any migration plan (the GCP migration is out of scope per audit
  mandate).
- Any capability projection (what the system *could* do is not what
  the system *does* do).
- Any unimplemented handler whose name appears in `FAILURE_MATRIX` but
  has not yet been replaced from its stub. Those names are recorded in
  §2 as "live (stub)" with explicit pointers to which baseline the
  stub delegates to; the spec does not commit to a timeline for
  replacing them.

The intent of those exclusions is to keep the spec a faithful
description of what runs today. A future Phase-3 spec can be authored
on top of this one; nothing in §1, §2, or §3 should need to change for
that to happen.

## Appendix C — Conformance test list

The following CI tests collectively assert that the system matches
this spec. Any drift between the spec and the code should be caught
by one of these:

- `tests/unit/test_failure_matrix.py::test_all_33_codes_present`
- `tests/unit/test_handlers.py::test_all_33_codes_dispatch_to_callable`
- `tests/unit/test_trichotomy.py::test_mcp_errors_classify_to_f14`
  (PR #81)
- `tests/integration/test_snapshot_integrity.py`
  (PR #79)
- `tests/integration/test_phoenix_span_coverage.py`
  (PR #82)
- CI shell guard:
  `grep -cE "^\| F[0-9]+ \|" docs/architecture/failure-matrix.md`
  must equal `33`.

If the conformance list grows, this appendix should grow with it.
