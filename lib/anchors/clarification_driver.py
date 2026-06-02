"""SP-03 clarification driver — wires `decide_next_action` + a spec-drafter.

This is the driver the SP-03 PRD calls for: it replaces the old `lib/anchors/
__init__.py` TODO stub (the `return None` block) with a real round-runner that

  1. tags the operator's free-form goal as UNTRUSTED external text (C16),
  2. calls an ``AbstractSpecDrafter`` to draft TaskSpec fields + typed clarifying
     questions + an ambiguity report (incl. anti-sycophancy challenges, C18),
  3. feeds the drafter's confidence into ``decide_next_action`` (the existing
     hybrid circuit-breaker in lib/anchors/clarification_loop.py) to decide
     whether to ask the next question, lock, draft-lock, or escalate.

Hybrid pattern: the drafter is injected (``InMemorySpecDrafter`` in CI, the
DEFERRED ``VertexSpecDrafter`` in prod). The driver itself is deterministic and
hermetic — it never calls Vertex/LLM directly.

The pre-tool-call HOOK that decides *when* to enter this loop on an inbound
operator message (the session-aware adapter) remains owned by the Hermes
integration layer; this module provides the round-runner it will call. The
``_on_pre_tool_call`` hook keeps its fail-closed HALT-sentinel behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.spec_drafter import (
    AbstractSpecDrafter,
    AmbiguityItem,
    AppliedStandard,
    Assumption,
    ClarifyingQuestion,
    DraftResult,
)
from app.core.trust import tag_untrusted
from lib.anchors.clarification_loop import (
    Action,
    ClarificationState,
    decide_next_action,
)


@dataclass
class ClarificationOutcome:
    """The result of one clarification round (driver output).

    Carries the circuit-breaker ``action`` AND the drafter's surfaced artifacts so
    the caller (the Hermes session adapter / the SP-04 sign-off interrupt) can
    present them to the operator without re-running the drafter.
    """

    action: Action
    draft: DraftResult
    questions: list[ClarifyingQuestion] = field(default_factory=list)
    ambiguities: list[AmbiguityItem] = field(default_factory=list)
    applied_standards: list[AppliedStandard] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    confidence: float = 0.0
    # Provenance tag of the operator intent (C16). Always "untrusted" — the goal
    # enters from an external channel and is tagged before the drafter sees it.
    intent_trust: str = "untrusted"

    @property
    def overrides(self) -> list[AmbiguityItem]:
        """The anti-sycophancy `kind=override` items (C18)."""
        return [a for a in self.ambiguities if a.kind == "override"]

    @property
    def challenges(self) -> list[AmbiguityItem]:
        """False-premise clarifications + SOTA-grounded overrides (C18)."""
        return [a for a in self.ambiguities if a.kind in ("clarification", "override")]


def run_clarification_round(
    intent: str,
    *,
    drafter: AbstractSpecDrafter,
    questions_asked: int = 0,
    last_user_msg_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    is_draft_locked: bool = False,
    answers: Optional[dict[str, str]] = None,
    round_index: int = 0,
) -> ClarificationOutcome:
    """Run one clarification round: draft → confidence → decide_next_action.

    Args:
        intent:           Operator's free-form goal. Tagged UNTRUSTED (C16) before
                          the drafter is called.
        drafter:          The injected ``AbstractSpecDrafter`` (InMemory in CI;
                          the DEFERRED Vertex drafter in prod).
        questions_asked:  Questions asked so far this thread (budget accounting).
        last_user_msg_at: Time of the last operator message (silence accounting).
                          Defaults to ``now`` (no silence).
        now:              Current time (defaults to UTC now). Injectable for tests.
        is_draft_locked:  Whether the spec is already in draft_locked state.
        answers:          Answers gathered so far, keyed by the question token.
        round_index:      0-based clarification round index.

    Returns:
        A ``ClarificationOutcome`` with the circuit-breaker action + the drafter's
        questions / ambiguity report / applied-standards / assumptions.
    """
    now = now or datetime.now(timezone.utc)
    last_user_msg_at = last_user_msg_at or now

    # C16: the operator intent is UNTRUSTED external content. Tag it before the
    # drafter touches it; the .raw is passed through (the drafter performs no
    # action-class derivation, so no guard_action_class call is needed here — the
    # intent cannot influence any action class).
    tagged = tag_untrusted(intent, source="operator_goal")

    draft = drafter.draft(tagged.raw, answers=answers, round_index=round_index)

    state = ClarificationState(
        questions_asked=questions_asked,
        last_user_msg_at=last_user_msg_at,
        confidence=draft.confidence,
        is_draft_locked=is_draft_locked,
    )
    action = decide_next_action(state, now=now)

    return ClarificationOutcome(
        action=action,
        draft=draft,
        questions=list(draft.questions),
        ambiguities=list(draft.ambiguities),
        applied_standards=list(draft.applied_standards),
        assumptions=list(draft.assumptions),
        confidence=draft.confidence,
        intent_trust=tagged.trust.value,
    )
