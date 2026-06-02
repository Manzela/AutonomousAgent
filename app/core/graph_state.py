"""SpineState — the LangGraph control-plane state contract (SP-01).

DOCTRINE (load-bearing — read before editing a node):

- Node bodies are AT-LEAST-ONCE *for external effects*. langgraph 1.2.2 re-runs an
  interrupted node's body from the top on resume (verified empirically). A node's
  RETURNED state-channel delta is transactionally rolled back and re-applied, so
  reducer writes are exactly-once — but any REAL external side-effect (a file write,
  a PR/commit/merge, an HTTP call) placed before/around interrupt() in that body is
  at-least-once. Therefore irreversible external effects live in DISTINCT post-resume
  nodes, never co-located with interrupt().

- The (thread_id, __pregel_task_id, action_kind) LEDGER — NOT the checkpointer — dedups
  irreversible external effects across a node RE-ENTRY whose receipt is already durable
  (loops / fan-out re-dispatch / re-driven resume): on re-entry a present receipt SKIPS the
  effect. This is check-then-act within one node, so a crash STRICTLY between act and the
  node's checkpoint commit is AT-LEAST-ONCE; true exactly-once for that window comes from
  the external op being idempotent (spec §12: git content-addressing + check-then-act;
  seal_spec derives a deterministic spec_id so its save is idempotent). See app.core.graph.apply_once.

- node_id / super_step are HUMAN-READABLE LABELS ONLY. They are NEVER part of the
  exactly-once key. The naive (thread_id, node_id, super_step) key COLLIDES under
  Send fan-out (only the Send idx differs) — cross-validated by Temporal/DBOS
  idempotency doctrine and LangGraph issue #6626.

- State carries ONLY serializable/scrubbable types. NO callables (agent_id refs,
  never AgentCapability.invoke). The serializer routes through lib/scrubber.py
  (SP-R1) and asserts no-callable at serialize time.

Spec reconciliation: spec §4/§6/§11 literally name the OLD key (thread_id, task_id,
action_kind); that wording is SUPERSEDED by spec §4's "Correlation key" paragraph
which deletes it for (thread_id, __pregel_task_id, action_kind). This module reads
__pregel_task_id. `cost_accumulator` is a dict[str,float] per-key accumulator (the
design-grounding L153 contract downstream SP-16/SP-R2 inherit), of which spec §4's
"float" is the per-entry value type.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from typing_extensions import TypedDict

from lib.scrubber import scrub_string

# ── Verbs / kinds ──────────────────────────────────────────────────────────
HitlVerb = Literal["APPROVE", "REJECT", "REPLAN", "TIMEOUT"]
ActionKind = Literal["seal_spec", "ship"]  # the skeleton's two irreversible effect kinds
# PRD §5/§8 (L181) SteeringEvent.kind. `override` is an SP-03 ambiguity-report-item enum
# (C15 L117), DISTINCT from SteeringEvent.kind, so it is NOT a member here.
SteeringKind = Literal["approve", "reject", "steer", "abort", "answer"]

# Deterministic arbitration precedence (C15): higher number wins. REJECT/REPLAN beat
# APPROVE; TIMEOUT maps to the safe default (treated as REJECT-strength).
_VERB_PRECEDENCE: dict[str, int] = {"APPROVE": 0, "TIMEOUT": 2, "REPLAN": 3, "REJECT": 4}


class HitlDecision(TypedDict):
    verb: HitlVerb
    actor: str
    reason: str
    interrupt_id: str
    ts: str


class WorkspaceRef(TypedDict):
    kind: Literal["branch", "gcs"]
    ref: str  # branch name or GCS object path — NEVER inline workspace bytes
    digest: str  # sha256 of workspace content; the DoD-17 byte-equal oracle


class TaskNode(TypedDict):
    id: str
    phase: Literal["research", "draft", "refine", "verify", "ship"]
    summary: str
    depends_on: list[str]
    acceptance_ref: str
    allowed_paths: list[str]


class TaskGraph(TypedDict):
    nodes: list[TaskNode]
    edges: list[tuple[str, str]]  # (from_id, to_id) depends_on edges


class LedgerReceipt(TypedDict):
    thread_id: str
    pregel_task_id: str  # config["configurable"]["__pregel_task_id"]
    action_kind: ActionKind
    node_label: str  # human-readable only — NOT part of the key
    super_step_label: int  # human-readable only — NOT part of the key
    ts: str


class SteeringEvent(TypedDict):
    """PRD §5/§8 (L181/L346) inbound-human-message contract. `thread_id` is the SOLE
    correlation key (§8); `(channel, origin_id)` is the C15 idempotency key. `kind` is the
    PRD-facing verb (approve/reject/steer/abort/answer); `verb` is the legacy C15 arbitration
    verb (HitlVerb) that arbitrate()/_merge_steering read — kept so the SP-01 reducers and
    DoD-4 oracle keep working until SP-17 wires the full bus."""

    thread_id: str  # §8 sole correlation key — every channel adapter reads/writes via this
    channel: str  # "telegram" | "board" | ...
    origin_id: str  # provider message id — the (channel,origin_id) idempotency key
    kind: SteeringKind  # PRD §5 verb: approve|reject|steer|abort|answer
    verb: HitlVerb  # legacy C15 arbitration verb — arbitrate()/_merge_steering read this
    interrupt_id: Optional[
        str
    ]  # PRD-optional: present for approve/reject/answer, absent for free steer/abort
    payload: Optional[
        dict
    ]  # free-form steer/answer content (e.g. {"text": ...}); None for bare approve/reject
    ts: str


# ── Reducers ───────────────────────────────────────────────────────────────
def _append(existing: Optional[list], incoming: Optional[list]) -> list:
    """Append-only list reducer (None-safe)."""
    return (existing or []) + (incoming or [])


def _merge_by_task_id(existing: Optional[list], incoming: Optional[list]) -> list:
    """tasks reducer: last-write-wins per task_id (fan-out idempotency, not append)."""
    by_id: dict[str, Any] = {t["task_id"]: t for t in (existing or [])}
    for t in incoming or []:
        by_id[t["task_id"]] = t
    return list(by_id.values())


def ledger_key(receipt: dict) -> tuple[str, str, str]:
    """THE exactly-once key: (thread_id, __pregel_task_id, action_kind)."""
    return (receipt["thread_id"], receipt["pregel_task_id"], receipt["action_kind"])


def _naive_key(receipt: dict) -> tuple[str, str, int]:
    """The REJECTED key — kept ONLY so the Send-fan-out collision is a runnable
    red-green (it must never be used for real dedup)."""
    return (receipt["thread_id"], receipt["node_id"], receipt["super_step"])


def _key_str(key: tuple) -> str:
    """Stringify a ledger key for use as an execution_counts dict key."""
    return "|".join(str(p) for p in key)


def _ledger_union(existing: Optional[list], incoming: Optional[list]) -> list:
    """Dedup receipts by ledger_key; first write wins (write-before-effect)."""
    seen: dict[tuple, Any] = {}
    for r in (existing or []) + (incoming or []):
        seen.setdefault(ledger_key(r), r)
    return list(seen.values())


def _merge_counts(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    """Additive int merge (the DoD-1 proof counter)."""
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        out[k] = out.get(k, 0) + v
    return out


def _merge_cost(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    """Additive float merge (cost_accumulator; wires to budget_watchdog later)."""
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        out[k] = out.get(k, 0.0) + v
    return out


def _merge_steering(existing: Optional[list], incoming: Optional[list]) -> list:
    """DoD-4 part 1: at-most-once survival, deduped on (channel, origin_id)."""
    by_key: dict[tuple, Any] = {}
    for e in (existing or []) + (incoming or []):
        by_key.setdefault((e["channel"], e["origin_id"]), e)
    return list(by_key.values())


def arbitrate(events: list, interrupt_id: str) -> Optional[str]:
    """DoD-4 part 2: deterministic C15 winner for one interrupt_id.
    REJECT > REPLAN > TIMEOUT > APPROVE. Returns None if no event matches."""
    candidates = [e for e in events if e.get("interrupt_id") == interrupt_id]
    if not candidates:
        return None
    return max(candidates, key=lambda e: _VERB_PRECEDENCE[e["verb"]])["verb"]


# ── Serialize-time guard (SP-R1) ──────────────────────────────────────────
def assert_serializable_state(state: dict) -> None:
    """Raise TypeError if any value (recursively) is a callable. Enforces the
    'no AgentCapability.invoke in state — only agent_id refs' invariant."""

    def _check(v: Any) -> None:
        if callable(v) and not isinstance(v, type):
            raise TypeError(f"non-serializable callable in state: {v!r}")
        if isinstance(v, dict):
            for x in v.values():
                _check(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _check(x)

    for value in state.values():
        _check(value)


def scrub_state(state: dict) -> dict:
    """Scrub every string value through lib/scrubber.py before persistence (SP-R1).
    Returns a deep-cleaned copy; asserts no callables first."""
    assert_serializable_state(state)

    def _scrub(v: Any) -> Any:
        if isinstance(v, str):
            return scrub_string(v, source="spine_state")
        if isinstance(v, dict):
            return {k: _scrub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_scrub(x) for x in v]
        return v

    return {k: _scrub(v) for k, v in state.items()}


def default_spec_store_root():
    """Default SpecStore root (override with SPINE_SPEC_STORE / SPINE_DATA_DIR)."""
    import os
    from pathlib import Path

    root = os.environ.get("SPINE_SPEC_STORE") or os.path.join(
        os.environ.get("SPINE_DATA_DIR", "/tmp/spine-specs"), "specs"
    )
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── The full state contract (designed complete now) ────────────────────────
class SpineState(TypedDict, total=False):
    # correlation / identity
    thread_id: str
    goal: str
    # spec contracts
    clarifications: Annotated[list, _append]
    plan: Optional[TaskGraph]
    spec_sha: Optional[str]
    spec_id: Optional[str]
    # SP-06 PRD-conformance: the agent's diff (list of [status, path]) the eval_gate scores
    # against the locked plan's allowed scope, and the resulting ScopeVerdict (as a dict).
    # Last-write (no reducer): the execute node AUTHORITATIVELY overwrites changed_paths every
    # run (empty in the skeleton; the real git diff arrives with the SP-05 drive). base_ref is
    # the optional base branch for the verdict metadata (defaults to "main").
    changed_paths: Optional[list]
    base_ref: Optional[str]
    eval_verdict: Optional[dict]
    # HITL decisions
    sign_off: Optional[HitlDecision]
    ship: Optional[HitlDecision]
    # work
    tasks: Annotated[list, _merge_by_task_id]
    # exactly-once ledger + proof counter
    ledger: Annotated[list, _ledger_union]
    execution_counts: Annotated[dict, _merge_counts]
    # non-repudiation / audit
    decision_record: Annotated[list, _append]
    audit: Annotated[list, _append]
    # steering (DoD-4) — full bus deferred; reducer + arbitration live now
    steering_events: Annotated[list, _merge_steering]
    # replan (continue-as-new) — reserved now, marked in Task 6
    replan_parent: Optional[str]
    pre_decompose_checkpoint_id: Optional[str]
    # workspace (DoD-17) — content-addressed digest, never inline bytes
    workspace_ref: Optional[WorkspaceRef]
    # cost / caps
    cost_accumulator: Annotated[dict, _merge_cost]
    fix_attempts: int
    # hygiene
    scrubbed: bool
