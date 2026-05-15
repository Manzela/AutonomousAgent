---
title: "Phase 1 — Design Alignment Spec (10× Transformation)"
subtitle: "Locked design decisions for P1-1 through P1-6, ready for implementation planning"
date: 2026-05-15
status: ready-for-writing-plans
predecessors:
  - docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md
  - audit/audit-plan.md
  - audit/findings.md
  - audit/model-mesh-decision.md
  - docs/architecture/failure-matrix.md  (user draft, 16/33 modes)
successor: docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md  (to be created by writing-plans)
---

# Phase 1 design alignment

## Purpose

This document locks the design decisions for P1's six items so that the implementation plan (next step: `superpowers:writing-plans`) can be written without re-litigating shape decisions. It is the output of one `superpowers:brainstorming` session on 2026-05-15.

**Scope:** the 7 open design questions surfaced by the kickoff handoff (`SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md`), plus one implementation-level constraint surfaced during pre-brainstorm reading (Hermes plugin policy).

**Out of scope:** the *order* of P1 items (already locked: `P1-1 → P1-6 → P1-2 → P1-3 → P1-4 → P1-5`); the model-mesh routing for P3 (locked in `audit/model-mesh-decision.md`); the worktree strategy (single `phase/1` worktree per kickoff doc).

---

## Architectural constraint (non-negotiable)

**All P1 code must ship as Hermes plugins, not modifications to Hermes core.** Per `hermes-agent/AGENTS.md:509-513` (Teknium policy, May 2026):

> Plugins MUST NOT modify core files (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`, etc.). If a plugin needs a capability the framework doesn't expose, expand the generic plugin surface (new hook, new ctx method) — never hardcode plugin-specific logic into core.

What this means for P1:

- `lib/anchors/`, `lib/evaluators/`, `lib/durability/`, `lib/memory/`, `lib/kanban/` all wire into Hermes via the public plugin surface only:
  - Lifecycle hooks: `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end` (`hermes-agent/AGENTS.md:477-479`)
  - Tool registration: `ctx.register_tool(...)`
  - CLI subcommands: `ctx.register_cli_command(...)`
  - `register(ctx)` entry point in each plugin's `__init__.py`
- If we discover a missing hook or ctx method, we either work around it OR open an upstream PR. We do NOT patch core files even temporarily.
- The Hermes submodule pin stays at `ddb8d8f` for P1; bumping is a separate decision.

This constraint applies to every design below; "implementation" sections call out the specific hooks each plugin uses.

---

## P1-1 — TaskSpec + clarification loop

### Schema (locked)

`TaskSpec v1` — 6 mandatory + 5 optional + 7 auto-populated metadata fields. Persisted at `/data/specs/{slug}.json`, immutable once `status='locked'`.

```python
# lib/anchors/task_spec.py — Pydantic model

class Scope(BaseModel):
    in_scope: list[str]      # min 1
    out_of_scope: list[str]  # min 1

class TaskSpec(BaseModel):
    # Mandatory (clarification loop must populate all 6)
    title: str
    intent: str                       # 1-3 sentences, the "why"
    acceptance_criteria: list[str]    # min 1, each must be testable
    scope: Scope
    success_metrics: list[str]        # min 1, objective + measurable
    constraints: list[str]            # min 0, must-not-do + dependencies

    # Optional (clarification loop may populate; sensible defaults)
    budget_usd_cap: float | None = None
    deadline_utc: datetime | None = None
    escalation_h: int = 24                                      # default from limits.yaml
    owner_telegram_id: int | None = None
    parent_spec_sha: str | None = None                          # for spec versioning

    # Auto-populated metadata (NOT user-supplied)
    spec_id: UUID                       # uuid4
    spec_sha: str                       # sha256 of normalized JSON (mandatory + optional fields, sorted keys)
    created_at: datetime                # ISO 8601 UTC
    created_by: int                     # telegram user_id
    schema_version: Literal["1"] = "1"
    status: Literal["draft", "draft_locked", "locked", "superseded"] = "draft"
    superseded_by: str | None = None    # spec_sha of the spec that replaces this one
    intent_category: Literal["coding", "audit", "research", "writing", "ops", "data", "unknown"] = "unknown"
                                        # Set once at lock-time by an LLM classification call (Sonnet 4.6).
                                        # Immutable post-lock; consumed by P1-4 REJECTED.md scoping.
```

**Why mandatory vs optional:** The 6 mandatory fields are what every judge in P1-2 needs to score against. `budget_usd_cap` etc. are operational, not evaluated. Putting them in mandatory forces the clarification loop to spend questions on irrelevant data and burns the 6-question budget.

**Spec versioning:** Spec edits create a new spec with `parent_spec_sha` pointing back; old spec gets `status='superseded'` + `superseded_by`. No in-place mutation. The agent's "active" spec is always the latest in the chain.

### Clarification loop (locked)

Hybrid circuit-breaker — lock when ANY:

| Condition | Action | New status |
|---|---|---|
| `confidence ≥ 0.85` (agent self-report) | Lock | `locked` |
| `questions_asked == 6` (budget exhausted) | Stop asking, request user `/confirm` | `draft_locked` |
| `user silent > 4h` | Stop asking, request user `/confirm` | `draft_locked` |
| `user silent > spec.escalation_h` (24h default) | Telegram alert (Fail-Loud per F-matrix) | unchanged |
| User replies `/cancel` | Abandon spec | `draft` deleted |

**State machine** (`lib/anchors/clarification_loop.py`):

```
[start] --user_msg--> drafting --confidence>=0.85--> locked
        --user_msg--> drafting --questions==6----> draft_locked --/confirm--> locked
        --user_msg--> drafting --silent>4h-------> draft_locked --/confirm--> locked
        --any state---/lock-> locked
        --any state---/cancel-> [deleted]
        --draft_locked--silent>24h--> ESCALATE (Fail-Loud)
```

**User commands during loop:**

- `/lock` — force lock with current draft (skips remaining questions)
- `/skip` — skip current question (still counts toward 6-budget)
- `/cancel` — abandon current draft spec entirely (no argument; distinguishes from P1-5's `/cancel <id>` which targets a card)
- `/confirm` — accept the current `draft_locked` spec → transition to `locked` (used when budget exhausted or silence-triggered lock)

### Hermes integration

- **Reuse:** Hermes' `clarify` tool (location to verify at implementation time — handoff doc cited `toolsets.py:126` but the `clarify` registration may be elsewhere; grep for `def clarify` and `name="clarify"` first). The `clarify` tool gives us the question/answer primitive; our state machine wraps it with the lock criteria + persistence.
- **Plugin shape:** `lib/anchors/__init__.py` exports `register(ctx)` which:
  - Registers `on_session_start` hook → load active spec for the session if one exists
  - Registers `pre_tool_call` hook → if no spec locked yet AND incoming user message looks like a project intent (heuristic), drive the clarification loop before letting the tool run
  - Registers a `/new <intent>` CLI subcommand (Hermes CLI, not Telegram) for explicit spec creation from the operator side. The Telegram-side equivalent is implicit: any non-slash inbound message routes through the heuristic above.
- **Persistence:** `lib/anchors/spec_store.py` — atomic write with `os.rename` for crash safety; sha256 over normalized JSON (sorted keys, no whitespace, ISO datetimes).

### Config additions to `limits.yaml`

```yaml
anchors:
  max_clarification_questions: 6
  lock_confidence_threshold: 0.85
  draft_silence_lock_h: 4              # silence triggers draft_locked
  draft_locked_silence_escalate_h: 24  # silence in draft_locked triggers Telegram alert (== agent.telegram_escalation_timeout_h)
  spec_storage_dir: /data/specs
```

---

## P1-2 — Multi-judge evaluator

### Axes (locked)

4 judges, 4 axes — same set as P3-mesh, but P1 routes differently because Qwen + cross-family-Gemini-as-2-judges aren't online yet:

| Axis | Judge | P1 model | P3 mesh model (later swap, no P1 code change) |
|---|---|---|---|
| 1 | code-correctness | `vertex_ai/claude-sonnet-4-6` | `vllm/qwen3-coder-next` |
| 2 | safety | `vertex_ai/claude-opus-4-7` | `vertex_ai/claude-opus-4-7` (unchanged) |
| 3 | scope-fit | `vertex_ai/claude-sonnet-4-6` | `vertex_ai/gemini-3.1-pro` |
| 4 | completeness | `vertex_ai/gemini-3.1-pro` | `vertex_ai/gemini-3.1-pro` (unchanged, uses 1M ctx) |

**Family count in P1: 2 (Anthropic + Google).** Better than the audit-plan's "all-Anthropic" baseline. Resolves the evaluator-collapse risk one tier earlier than originally scheduled.

### Scope addition vs the audit-plan: enable Gemini 3.1 Pro NOW

This pulls one P3 dependency forward into P1 (~+1d). Steps:

1. Vertex AI console: enable Gemini 3.1 Pro in `i-for-ai` project (no cost until called).
2. `deploy/litellm/config.yaml` — add to `model_list`:
   ```yaml
   - model_name: gemini-3.1-pro
     litellm_params:
       model: vertex_ai/gemini-3.1-pro
       vertex_project: i-for-ai
       vertex_location: us-central1   # default; may revisit if latency matters
   ```
3. `scripts/smoke.sh` — add an 8th smoke check: round-trip via LiteLLM → Gemini 3.1 Pro.
4. Document the new dependency in `audit/audit-plan.md` (P3-1 row: "Gemini 3.1 Pro: enabled in P1, not P3").

**Why this is the right deviation:** the audit-plan listed evaluator-collapse as an unresolved risk persisting through P1 → P2 (~2-3 weeks of operating with known-suboptimal judges). Closing the highest-quality gap a week early is worth the +1d.

### Voting + consensus (locked)

Each judge returns `{score: 0..10, verdict: 'accept'|'reject'|'unsure', reasoning: str, axis: str}`.

**Consensus rule:**

- **3+ accept (≥75%)** → accept
- **3+ reject (≥75%)** → reject; record approach for REJECTED.md if consecutive_rejections >= 3
- **otherwise (no 3-of-4 majority for accept or reject; this includes 2/2 splits AND any combination involving 'unsure' votes)** → escalate (failure mode F60 in failure matrix; Fail-Soft → ask a 5th judge using Opus 4.7)

If 5th judge breaks the tie → that becomes the verdict. If still tied → Fail-Loud (Telegram escalation; do not continue).

### Hermes integration

- **Reuse:** `delegate_task` (`hermes-agent/tools/delegate_tool.py:1909`) for the 4-way parallel judge dispatch (batch mode, ThreadPoolExecutor, max 4 children). Each task in the batch sets `delegation.provider` per-call to route to the right model.
- **Plugin shape:** `lib/evaluators/__init__.py` exports `register(ctx)` which:
  - Registers `post_tool_call` hook → for "complete" tool calls (defined as: tool ran successfully AND was on the evaluation-eligible list, see below), dispatch judge panel
  - Registers `on_session_end` hook → flush in-flight judge state to checkpoint
- **Evaluation eligibility list:** in `config/toolsets.yaml`, add `evaluate_after: bool` per toolset. Read-only tools (`Read`, `Grep`) skip evaluation; mutating + dispatch-style tools (`Write`, `Edit`, shell, `delegate_task`, GitHub MCP write paths) trigger judge panel.

### Config additions to `limits.yaml`

```yaml
evaluators:
  axes: [code-correctness, safety, scope-fit, completeness]
  consensus:
    accept_threshold: 0.75      # fraction of judges that must accept
    reject_threshold: 0.75      # fraction that must reject
    on_split: escalate_to_5th   # alternatives: 'reject', 'accept', 'fail_loud'
    fifth_judge_model: vertex_ai/claude-opus-4-7
  rejection_repeat_threshold: 3  # consecutive rejections of same approach → REJECTED.md
  judge_timeout_s: 90
  parallel_judges_max: 4
```

---

## P1-3 — Per-step checkpointing + resume

### Cadence + retention (locked)

| Setting | Value |
|---|---|
| Interval | every 5 tool calls (`post_tool_call` hook) |
| Storage | `/data/checkpoints/{session_id}/step-{N}.json` |
| Retention — recent | last 50 checkpoints kept (uncompressed) |
| Retention — sparse | every 100th checkpoint kept after 50 (long-tail) |
| Compaction | gzip files older than 1h |
| Prune | delete after session marked DONE/ARCHIVED + 7d |

### Checkpoint triggers + contents

Checkpoints fire on TWO triggers:

1. **`post_tool_call` hook** every N=5 tool calls — the normal cadence; `tool_call_in_flight` is always `null` here.
2. **SIGTERM signal handler** for graceful container shutdown (e.g., `docker compose restart`, OS update) — fires immediately regardless of step count; `tool_call_in_flight` may be non-null if a tool was mid-execution.

```json
{
  "schema_version": 1,
  "session_id": "...",
  "step_n": 47,
  "timestamp": "2026-05-15T16:22:11Z",
  "trigger": "post_tool_call",        // or "sigterm"
  "active_taskspec_sha": "a1b2c3d4...",
  "kanban_card_id": "card-7f3a",
  "last_n_messages": [/* last 20 turns */],
  "tool_call_in_flight": null,        // null on post_tool_call; {tool_name, args, started_at} on sigterm if applicable
  "judge_panel_state": null,          // or {axes_completed: [...], pending: [...]} if mid-evaluation
  "rejected_md_known_entries": ["rej-7f3a", "rej-2e88"]   // for de-dup on resume
}
```

### Resume behavior

- `on_session_start` hook scans `/data/checkpoints/` for sessions with no DONE/ARCHIVED status.
- For each, loads latest non-corrupted checkpoint and resumes.
- **Tool re-run policy** — needs a new field in `config/toolsets.yaml`: `replay_safe: idempotent | mutating | refuse`. On resume:
  - `idempotent` (e.g., `Read`, `Grep`, GitHub MCP read paths) — re-run is cheap, just do it
  - `mutating` (e.g., `Write`, `Edit`, shell side-effects, GitHub PR creation) — DO NOT re-run; mark step complete with the previous checkpoint's stored result if available, else emit a Fail-Soft warning and continue
  - `refuse` (e.g., delete operations, force-pushes) — Fail-Loud on resume; require user `/confirm`
- This classification table is part of P1-3 effort (small — ~30min to seed the existing tool list).

### Hermes integration

- **Reuse:** `batch_runner.py`'s `_load_checkpoint` / `_save_checkpoint` / `--resume` flag — same JSON serialization shape. We extend the pattern from batch context to live agent-loop.
- **Plugin shape:** `lib/durability/__init__.py` (shared with P1-6's trichotomy):
  - `post_tool_call` hook → increment step counter, write checkpoint every N
  - `on_session_start` hook → scan + resume
  - Background gzip task scheduled via Hermes' `cronjob` toolset

### Config additions to `limits.yaml`

```yaml
durability:
  checkpoint_interval_steps: 5
  checkpoint_dir: /data/checkpoints
  retention:
    recent_keep: 50
    sparse_keep_every: 100
    gzip_after_h: 1
    delete_after_done_days: 7
  resume:
    enabled: true
    on_corruption: skip_and_warn   # alternatives: 'fail_loud', 'restart_session'
```

---

## P1-4 — `MEMORY/REJECTED.md` institutional memory

### Format + retention (locked)

Per-entry structured Markdown log, scoped by TaskSpec `intent_category`, 30d default TTL.

**Entry shape:**

```markdown
## Entry: 2026-05-15T14:22Z (id: rej-7f3a)
- spec_sha: a1b2c3d4...
- intent_category: coding              # extracted from TaskSpec.intent (LLM classification at lock-time)
- approach_summary: |
    Tried using sed to refactor the JSON parser —
    judge.code-correctness flagged 3 syntactic errors,
    judge.scope-fit said "out of scope for v1".
- failure_axes: [code-correctness, scope-fit]
- consensus_vote: 1 accept / 3 reject
- alternative_directions: |
    1. Use a proper AST visitor (libcst).
    2. Restrict refactor to v2 milestone.
- created_at: 2026-05-15T14:22Z
- expires_at: 2026-06-14T14:22Z   # 30d default; per-entry override allowed
```

**Intent categories (initial set, extensible):** `coding`, `audit`, `research`, `writing`, `ops`, `data`, `unknown`. Classified at TaskSpec lock-time by a single Sonnet 4.6 call (`class:chatter`).

### Loading at session start

`on_session_start` hook (composes with P1-3's resume hook):

1. Load active TaskSpec → read `intent_category`
2. Open `MEMORY/REJECTED.md`, filter entries where `expires_at > now()` AND `intent_category == active_spec.intent_category`
3. Inject filtered entries as a system message: "Past failed approaches for this kind of task — DO NOT repeat:"

### User commands

- `/forget <pattern>` — delete entries where pattern matches `approach_summary` or `id`
- `/forget id:rej-7f3a` — delete by exact id
- `/rejections` — list active entries (truncated to 5 most recent + count)

### Hermes integration

- **Plugin shape:** `lib/memory/__init__.py`:
  - `register(ctx)` registers a Telegram-bridge slash-command handler for `/forget` and `/rejections` (delegates to the Telegram bridge plugin in P1-5)
  - **Does NOT register its own `on_session_start` hook.** P1-3's resume hook and P1-4's REJECTED-inject hook must run in a defined order (resume → load active spec → inject category-filtered REJECTED entries). Both register inside `lib/durability/__init__.py`'s single `register(ctx)` so the order is controlled by call sequence, not Hermes' hook-iteration order (which the plugin docs don't guarantee).
- **Wired into evaluator:** `lib/evaluators/consensus.py` calls `lib/memory/rejected.append_entry(...)` when `consecutive_rejections >= 3` for the same approach.

**"Same approach" definition (programmatic, not LLM-text):**

`approach_fingerprint = sha256(json.dumps([{"tool": tc.tool_name, "first_arg": _truncate(tc.first_arg, 80)} for tc in session.tool_calls_since_last_taskspec_lock], sort_keys=True))`

Two rejected attempts share an `approach_fingerprint` iff their tool-call sequences (tool name + first-arg-truncated-to-80-chars) match. This avoids the trap of two attempts that LLM-summarize differently being treated as different approaches when the underlying behavior is identical. The fingerprint is what the `consecutive_rejections` counter increments against; the human-readable `approach_summary` in the entry is for the user, not for de-dup.

### Config additions to `limits.yaml`

```yaml
memory:
  rejected_md_path: /data/MEMORY/REJECTED.md
  rejected_default_ttl_days: 30
  rejected_max_inject_per_session: 10   # cap context bloat
  intent_categories: [coding, audit, research, writing, ops, data, unknown]
  intent_classifier_model: vertex_ai/claude-sonnet-4-6
```

---

## P1-5 — Kanban → Telegram bridge

### Mapping rule (locked)

**1 user message = 1 Kanban card** (created at TaskSpec lock-time). Sub-cards allowed via Hermes' Kanban Python API; linked to parent via `Task.workflow_template_id`.

### Notification policy (locked)

| Status transition | Notification |
|---|---|
| `triage` → `todo` | silent |
| `todo` → `ready` | "Started: <title>" (1 msg) |
| `ready` → `running` | silent (heartbeat-only via OTel) |
| `running` → `blocked` | **PRIORITY ALERT**: "Blocked on: <reason>. Use `/resume <id>` to unblock" |
| `running` → `done` | "Done: <title>\n\nResult: <summary>" |
| `running` → failure (consecutive_failures++) | **ALERT**: "Card <id> failed: <consecutive_failures>x — <last_failure_error>" |
| any → `archived` | silent |

### User slash commands (locked)

`/cancel` is overloaded by argument: `/cancel` (no arg) cancels the current draft spec (P1-1); `/cancel <id>` cancels the named card (P1-5). The bridge dispatches by argument presence.

| Command | Action |
|---|---|
| `/list` | active cards (status ≠ done/archived), 1-line each |
| `/show <id>` | full card detail + last_heartbeat_at |
| `/cancel <id>` | transition card to archived |
| `/resume <id>` | unblock card + push to ready (also used to resume after manual `limits.yaml` cap raise per F70, or after a `refuse`-tier tool replay confirmation per P1-3) |
| `/board` | column counts (8 statuses × 1-line summary; in P2 this becomes a TMA-launch button) |
| `/history [limit=10]` | last N completed cards |
| `/forget <pattern>` | (from P1-4) REJECTED.md prune |
| `/rejections` | (from P1-4) list active rejections |
| `/lock` | (from P1-1) force-lock current draft spec |
| `/skip` | (from P1-1) skip current clarification question |
| `/cancel` (no arg) | (from P1-1) abandon current draft spec |
| `/confirm` | (from P1-1) accept current `draft_locked` spec; also (from F62) acknowledge mid-task spec drift |

### Hermes integration

- **Reuse:** `hermes-agent/hermes_cli/kanban_db.py:558-672` — `Task` dataclass and the SQLite WAL CAS Python API (`create_task`, `claim_task`, `complete_task`, etc.).
- **Persistent volume:** mount `hermes-data:/root/.hermes/kanban` so the SQLite DB survives container restarts.
- **Plugin shape:** `lib/kanban/__init__.py`:
  - **Does NOT poll Telegram itself.** Hermes' gateway already long-polls Telegram and dispatches user messages to the agent loop. The bridge plugin hooks the gateway's existing dispatch path:
    - `on_session_start` hook → if the inbound message is a slash command (`/list`, `/show`, etc.), short-circuit the agent loop, run the command directly, send the reply via Hermes' `send_message` tool, and end the session early
    - `on_session_start` hook → otherwise (a project intent), let P1-1's clarification loop run; on TaskSpec lock, call `kanban_db.create_task(...)` and store `card_id` in session metadata
  - Registers `post_tool_call` hook → after every `kanban_db.update_status(...)` call (whether issued by this plugin or by Hermes' built-in worker), check whether the changed card belongs to a session in this Hermes instance and emit a Telegram message per the notification table
  - Registers `on_session_end` hook → close session's card if still in `running` (transition to `done` or `blocked` based on exit code)

### Config additions to `limits.yaml`

```yaml
kanban:
  db_path: /root/.hermes/kanban/kanban.db
  notify_on_statuses: [ready, blocked, done]
  notify_on_failure: true
  status_poll_interval_s: 5
  slash_command_prefix: "/"
  default_priority: 3
```

### Forward-looking: P2 will add a Telegram Mini App Kanban view

P2-2 in the audit-plan was originally "lightweight FastAPI/HTMX read-only dashboard at `localhost:7878`". User upgrade (this brainstorm session): **replace the FastAPI dashboard with a Telegram Mini App (TMA) Kanban view** — same goal (visual board for walk-away monitoring), strictly better UX (lives inside Telegram instead of requiring a separate browser tab), accessible from any device with Telegram installed.

This is a P2 deliverable, not P1. But the slash commands designed in this section are forward-compatible — `/board` will become a TMA-launch button in P2 (instead of the text-based 8-status summary), while the rest of the slash commands (`/list`, `/show`, `/cancel`, `/resume`, `/history`) stay useful as quick CLI alternatives even after the TMA ships.

**Design notes captured for the P2-2 implementation:**

- Sandboxed Chromium WebView inside Telegram via the `telegram-apps/sdk` (`@telegram-apps/sdk`)
- Frontend stack TBD (React, Vue, or Svelte all viable per the Telegram TMA spec); decided at P2-2 brainstorm time
- Drag-and-drop via web-based gesture libraries (`@hello-pangea/dnd` for React, equivalent for chosen stack)
- Theme integration via `webApp.themeParams` → automatic light/dark mode mirroring of user's Telegram theme
- Haptic feedback on card-drop into "done" column via `webApp.HapticFeedback.impactOccurred('medium')`
- **iOS gesture-conflict mitigations** (Telegram TMA-specific known issue):
  - Wide horizontal safe-margins on the Kanban board (≥24px) to avoid Apple's swipe-back gesture triggering when dragging cards near screen edge
  - `webApp.expand()` called on app launch to lock viewport to full-screen height
  - `touch-action: pan-y` on individual cards + `overflow-x: auto` on column container to prevent vertical-scroll jolt during horizontal column-swipe
- HTTPS endpoint required by Telegram TMA spec — host on Cloud Run (lifts naturally with the P2 GCP migration); local dev via ngrok or Cloud Run preview URL
- Auth via Telegram InitData verification (HMAC-SHA256 with the bot token) — only the allowlisted user_id (`7217166969`) gets access
- State sync: TMA polls the Kanban SQLite-via-API every 3s (or websocket if polling proves too laggy); writes (drag-drop status changes) go through the same Hermes Kanban Python API that the agent uses
- BotFather configuration: `/newapp` to register the TMA URL; bot menu button set to launch TMA

These notes are captured here so the P2-2 writing-plans run has the full design context when it hits this item.

---

## P1-6 — Failure trichotomy + 33-mode matrix + 24h escalation

### Trichotomy (locked, matches user's draft)

| Tier | Behavior | Examples |
|---|---|---|
| **Fail-Loud** | Telegram alert + halt task; auto-escalate after `escalation_h` | `Model Not Found`, security violation, single-call cost > spec.budget_usd_cap |
| **Fail-Soft** | Log warning; degrade behavior; continue task; surface degradation in next user-visible reply | Chroma down → skip vector memory; non-quorum vote → ask 5th judge |
| **Self-Heal** | Exponential backoff with jitter (per `limits.yaml.retries`); max N attempts then promote to Fail-Loud | 429 rate limits, transient 5xx, malformed LLM JSON |

### Matrix completion (locked: enumerate all 33 in P1)

User's draft has 16 modes (F01–F05, F10–F13, F20–F23, F30–F33). P1-6 adds 17 more (~+0.5d), grouped:

**Container/Compose (F40–F43):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F40 | Image Pull Failed | Self-Heal → Fail-Loud after 3 | retry with backoff; if still failing, alert |
| F41 | Volume Mount Conflict | Fail-Loud | manual intervention; common cause: stale plaintext secret file |
| F42 | Container OOMKilled | Self-Heal → Fail-Loud | restart 1x with smaller batch; if reoccurs, alert |
| F43 | Health-Check Persistent Fail | Fail-Loud | restart cascade risk; pause restart loop and alert |

**Kanban/Workflow (F50–F52):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F50 | Stale Worker Lease (`claim_expires` past) | Self-Heal | reclaim lease; reset worker_pid |
| F51 | Workflow Step Skip (`current_step_key` mismatch) | Fail-Soft | log + advance to next step |
| F52 | Heartbeat Lost > 5 min | Fail-Loud | worker dead; release card to `ready`, alert |

**Evaluator/TaskSpec (F60–F63):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F60 | No-Quorum Vote (2/2 split or any 'unsure') | Fail-Soft | escalate to 5th judge (Opus 4.7); if still tied, Fail-Loud |
| F61 | TaskSpec Schema Validation Fail | Fail-Loud | refuse to lock; ask user to fix |
| F62 | Spec Drift Detected (mid-task scope change) | Fail-Loud | require user `/confirm` of new spec via Telegram |
| F63 | Judge LLM Returned Non-Number Score | Self-Heal | re-prompt 1x with stricter format; if still bad, drop judge from quorum |

**Cost/Budget (F70–F71):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F70 | Single-Call Cost > spec.budget_usd_cap | Fail-Loud | hard stop; Telegram alert with current cap + observed call cost; user manually raises cap in `limits.yaml` (or edits the spec) and uses `/resume <card_id>` to continue. No chat-side cap-mutation command — keeps the budget control surface inside config files, not chat. |
| F71 | Hourly Burn Rate Spike (>3σ of week avg) | Fail-Soft | alert + degrade to cheaper model class for next 1h |

**Snapshot/Backup (F80–F81):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F80 | GCS Snapshot Upload Failed | Self-Heal → Fail-Loud after 3 | retry; if persists >5m, alert |
| F81 | Local Snapshot Disk Full | Fail-Loud | cannot proceed safely; pause all writes, alert |

**RL training (F90–F91, scaffolded but unused in P1):**

| ID | Mode | Tier | Behavior |
|---|---|---|---|
| F90 | RL Trigger Preflight Failed | Fail-Soft | skip cycle, log; cron continues |
| F91 | RL Run Cost Overrun (>50% of estimate per `limits.yaml`) | Fail-Loud | auto-abort run |

### 24h escalation (already partly wired)

`limits.yaml.agent.telegram_escalation_timeout_h: 24` is in place. P1-6 adds the consumer:

- `lib/durability/escalation.py` — background watcher (Hermes `cronjob` toolset, hourly cron) that scans Kanban for cards with `status='blocked' AND last_heartbeat_at older than escalation_h hours` → emit Telegram Fail-Loud.

### Hermes integration

- **Plugin shape:** `lib/durability/trichotomy.py` + `lib/durability/escalation.py`:
  - `register(ctx)` wraps every tool call (via `pre_tool_call` + `post_tool_call`) with the classifier
  - On exception or failure-flag in tool result, classify against the matrix and apply tier behavior
  - Escalation watcher is scheduled via Hermes' built-in `cronjob` toolset (hourly cron) at plugin load — NOT a CLI subcommand. The `cronjob` toolset is the right primitive because the watcher needs to run as a background scheduled job, not on operator invocation.

### Doc deliverable

`docs/architecture/failure-matrix.md` — replace user's 16-mode draft with the full 33-mode version. **User's existing 16 entries are kept verbatim;** the 17 new entries are appended.

### Tests

- Unit: classifier correctness for each tier (3 unit tests per tier × 11 example modes = ~33 tests)
- Integration: 10 representative modes triggered against the live stack; assert correct tier behavior + correct Telegram message

### Config additions to `limits.yaml`

No new sections needed — uses existing `retries:`, `agent.telegram_escalation_timeout_h`, `alerts:`, `notify_channels:`. The matrix is data, not config.

---

## Updated effort + sequence

| # | Item | Audit-plan | New | Notes |
|---|---|---|---|---|
| P1-1 | TaskSpec + clarification | 1.5d | 1.5d | Schema confirmed minimal |
| P1-6 | Failure trichotomy + 33-mode matrix + 24h escalation | 2d | **2.5d** | +0.5d for 17 added modes |
| P1-2 | Multi-judge evaluator | 1.5d | **2.5d** | +1d to enable Gemini 3.1 Pro on Vertex AI + LiteLLM model_list + smoke |
| P1-3 | Per-step checkpoint + resume | 1d | 1d | Includes 30min for tool replay-safety classification table |
| P1-4 | REJECTED.md institutional memory | 0.5d | 0.5d | Category scoping fits in budget |
| P1-5 | Kanban → Telegram bridge | 0.5d | 0.5d | Slash command set is rich but Hermes Kanban is rich too |
| | **Total** | **7d** | **~8.5d** | |

**Sequence (unchanged from audit-plan):** `P1-1 → P1-6 → P1-2 → P1-3 → P1-4 → P1-5`. P1-2 depends on both P1-1 (TaskSpec for judges to score against) AND P1-6 (failure matrix for judges to reference).

---

## Deviations from the audit-plan (call-outs)

1. **Gemini 3.1 Pro pulled forward** — was P3-1 (originally scheduled for after cloud-prod migration), now lands in P1-2. Reason: closes the evaluator-collapse risk a tier earlier. Cost: +1d in P1, but no incremental cost in P3 (the work just moves earlier).
2. **Failure matrix expanded from 16 → 33 modes in P1** — user's draft had 16; audit-plan called for full enumeration. Expansion lands in P1-6 (~+0.5d) so P1-2 evaluators score against a complete matrix, not a partial one.
3. **Tool replay-safety classification (`replay_safe` field in `config/toolsets.yaml`)** — net-new requirement surfaced by the checkpoint resume design. Tiny effort (~30min seeded inside P1-3 budget) but must exist for P1-3 to ship safely.
4. **P2-2 dashboard upgraded from FastAPI/HTMX → Telegram Mini App (TMA)** — *no P1 impact;* P2 effort grows from 1d → ~3-4d (worth it for strictly-better UX during the P1 48h Mac soak and the P2 7-day cloud-prod soak). Design notes captured under P1-5's "Forward-looking" subsection so the P2-2 writing-plans run has the full context. Slash commands designed in P1-5 are forward-compatible — `/board` becomes a TMA-launch button, the other commands stay as CLI quick-paths.

These four deviations are additive within their respective tiers (no removed scope, no changed sequence between P1 and P2). P1 grows ~7d → ~8.5d (deviations 1+2+3); P2 grows ~7d → ~10d (deviation 4). P1 acceptance date is unchanged from the audit-plan.

---

## Open implementation questions (non-blocking, resolve during writing-plans)

1. **`clarify` tool exact location** — handoff doc cites `hermes-agent/toolsets.py:126` but reading that line shows a different toolset. `grep -rn "name=\"clarify\"" hermes-agent/` will pin it down at planning time.
2. **Intent categorization classifier prompt** — the LLM call that maps a TaskSpec.intent string to one of the 7 intent_categories needs prompt engineering. Trivial (~10 lines of prompt + 5 examples) but should be shown in the implementation plan.
3. **Telegram bridge polling vs webhook** — current Hermes uses long-poll. Bridge can stay long-poll. If we hit message-rate limits, switch to webhook in P2.
4. **Hermes upstream PR for missing hook (if needed)** — if `post_tool_call` doesn't carry tool result text we need for evaluator routing, we may need to upstream a tweak. Defer to writing-plans to confirm hook signature is sufficient.

---

## Status & next step

This spec is **approved as of commit `<auto-filled by git>`** and is the input to `superpowers:writing-plans`, which will produce `docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md`. After writing-plans, execution proceeds via `superpowers:subagent-driven-development` per the kickoff doc's "Step 3: Execute" guidance.

---

## Predecessor cross-reference

| Section in this doc | Source |
|---|---|
| TaskSpec mandatory fields | `audit/audit-plan.md` §P1-1 |
| Clarification cap (≤6) | `audit/audit-plan.md` §P1-1 |
| Judge axes (4) | `audit/model-mesh-decision.md` "Locked mesh" table |
| P1 judge routing (2 Sonnet + 1 Opus + 1 Gemini) | This brainstorm session, 2026-05-15 — deviation from audit-plan |
| Checkpoint default N=5 | `audit/audit-plan.md` §P1-3 |
| Checkpoint retention (50 + every 100th + gzip 1h) | `audit/audit-plan.md` §P1-3 mitigation |
| REJECTED.md TTL 30d + `/forget` | `audit/audit-plan.md` "Critical risks" #4 |
| REJECTED.md per-entry + category scoping | This brainstorm session, 2026-05-15 |
| Kanban statuses + Task schema | `hermes-agent/hermes_cli/kanban_db.py:93, 558-672` |
| Failure trichotomy definitions | `docs/architecture/failure-matrix.md` (user draft) |
| 16 enumerated modes | `docs/architecture/failure-matrix.md` (user draft) |
| 17 added modes | This brainstorm session, 2026-05-15 |
| Plugin policy ("no core mods") | `hermes-agent/AGENTS.md:509-513` |
| `delegate_task` for judge dispatch | `hermes-agent/tools/delegate_tool.py:1909` |
| Hermes lifecycle hooks | `hermes-agent/AGENTS.md:477-479` |
| `batch_runner.py` checkpoint pattern (reuse) | `audit/audit-plan.md` §P1-3 + handoff doc |
| TMA Kanban view (P2-2 upgrade, design notes) | This brainstorm session, 2026-05-15; canonical Telegram TMA spec: https://core.telegram.org/bots/webapps |
