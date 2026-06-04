"""AbstractSpecDrafter — the SP-03 clarification-driver injection seam.

This is the abstract interface for the *spec-drafter*: the component that, given
an operator goal (free-form intent) plus the answers gathered so far, emits a
draft of the TaskSpec fields, a bounded set of typed clarifying questions, and an
*ambiguity report* (including anti-sycophancy challenges, C18).

Hybrid adapter pattern (CLAUDE.md / 04-gcp-native-adapter-plan.md):

  - app/core/spec_drafter.py          — THIS abstract base class (+ the typed
                                        Pydantic value objects it returns).
  - app/adapters/inmemory/spec_drafter.py
                                      — a DETERMINISTIC, rule-based drafter for
                                        hermetic CI (no live Vertex / LLM calls).
  - app/adapters/gcp/spec_drafter.py  — the Vertex structured-output concretion
                                        (DEFERRED stub: raises NotImplementedError).

The driver that *consumes* this ABC (wiring `decide_next_action` from
`lib/anchors/clarification_loop.py` together with a drafter) lives in
`lib/anchors/clarification_driver.py`.

PRD anchors (audit/2026-05-29-prd-gap-autonomous-sdlc/PRD §6 SP-03, C18):

  * The drafter emits TaskSpec fields + typed clarifying questions + an ambiguity
    report (Vertex structured-output in prod; Pydantic-constrained).
  * v1.4 additions:
      (a) `applied_standards[]` — `{principle, source, why}`; proactive
          highest-credibility domain best-practices (the Spec-Kit "constitution"
          role) grounded ONLY in model knowledge + the vendored SP-25
          `constitution.md` asset (NOT a live web tool — non-goal #6 / C16);
          shown as overridable DEFAULTS (R5), never locks.
      (b) clarifying questions tagged with a `category` enum
          (functional / data_contracts / edge_error / non_functional /
          scope_boundary), capped ≤5/round.
      (c) `assumptions[]` — below-threshold ambiguities the driver auto-resolves
          rather than asking; each references the resolved token.
      (d) anti-sycophancy ambiguity-report items — `kind=clarification` for false
          premises and `kind=override` for SOTA-grounded disagreement (C18).

The ABC is NEVER collapsed (CLAUDE.md builder-agent rule): the GCP subclass is a
sibling, not a replacement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# SP-25: vendored Spec-Kit assets (docs/spec-kit/). These are static template
# files loaded by the GCP Vertex drafter (deferred concretion) — never runtime
# imports of a Spec Kit package. CONSTITUTION_PATH is the grounding reference
# for applied_standards[] and C18 anti-sycophancy challenges.
CONSTITUTION_PATH: Path = Path("docs/spec-kit/constitution.md")
SPEC_KIT_DIR: Path = Path("docs/spec-kit")

# ---------------------------------------------------------------------------
# Typed value objects (Pydantic-constrained — mirrors the prod structured output)
# ---------------------------------------------------------------------------

# PRD §6 SP-03 (1.2): the five clarifying-question categories. Each under-specified
# surface maps to exactly one of these; coverage is asserted per-category.
QuestionCategory = Literal[
    "functional",
    "data_contracts",
    "edge_error",
    "non_functional",
    "scope_boundary",
]

# PRD §6 SP-03 (2.4) + C18: ambiguity-report item kinds.
#   question      — an ordinary gap the drafter wants the operator to fill.
#   clarification — an anti-sycophancy challenge to a FALSE premise (nonexistent
#                   API/flag): surfaced, never silently encoded into the TaskSpec.
#   override      — a SOTA-grounded disagreement with a real-but-deprecated /
#                   suboptimal operator choice; carries a recommended alternative.
# NOTE (C18): this enum is DISTINCT from SteeringEvent.kind (SP-01); it is an
# ambiguity-report-item enum value only.
AmbiguityKind = Literal["question", "clarification", "override"]

# Maximum clarifying questions surfaced per round (PRD §6 SP-03 (b): "capped ≤5/round").
MAX_QUESTIONS_PER_ROUND = 5


class ClarifyingQuestion(BaseModel):
    """A single typed clarifying question (PRD §6 SP-03 (b))."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    category: QuestionCategory
    # The planted-ambiguity token this question is about, when one was detected.
    # PRD oracle: "a question whose text references the planted token (not a fixed
    # string)" — the drafter records which token drove the question.
    references_token: Optional[str] = None


class AppliedStandard(BaseModel):
    """A proactive domain best-practice proposal (PRD §6 SP-03 (a), Spec-Kit
    "constitution" role). Shown as an overridable DEFAULT (R5), never a lock.

    Grounded ONLY in model knowledge + the vendored SP-25 `constitution.md`
    asset — NEVER a live web tool (non-goal #6 / C16).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    principle: str = Field(min_length=1)
    source: str = Field(min_length=1)  # e.g. "OWASP ASVS", "constitution.md#auth"
    why: str = Field(min_length=1)
    # Sign-off disposition (SP-04). Default 'proposed'; the operator may reject →
    # 'overridden'. The C8-locked TaskSpec asserts against status, not a re-draft.
    status: Literal["proposed", "accepted", "overridden"] = "proposed"


class Assumption(BaseModel):
    """A below-threshold ambiguity the driver auto-resolved rather than asking
    (PRD §6 SP-03 (c) / (1.3) anti-over-clarification).

    Each assumption references the resolved token so the post-pass can assert the
    driver logged an assumptions entry INSTEAD of a question.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ambiguity: str = Field(min_length=1)
    chosen_interpretation: str = Field(min_length=1)
    resolved_token: str = Field(min_length=1)
    ts: Optional[str] = None  # ISO-8601 timestamp, set by the driver on resolve.


class AmbiguityItem(BaseModel):
    """One item on the ambiguity report (PRD §6 SP-03 (2.4) + C18).

    For ``kind == "override"`` the SOTA-grounded fields are required:
    ``recommended_alternative`` + ``rationale``. ``cite`` is optional,
    model-asserted, ALWAYS flagged unverified, and NEVER gated on reachability
    (C18 / non-goal #6).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AmbiguityKind
    claim: str = Field(min_length=1)
    # The planted token this item is about (false-premise token, or the
    # deprecated choice). Lets the oracle assert the item cites the token.
    references_token: Optional[str] = None
    # Required for kind="override" (SOTA-grounded disagreement).
    recommended_alternative: Optional[str] = None
    rationale: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Optional, model-asserted, ALWAYS unverified (C18). Never gated on reachability.
    cite: Optional[str] = None
    cite_unverified: bool = True


class DraftResult(BaseModel):
    """The full structured output of one drafter round (PRD §6 SP-03).

    Carries the draft TaskSpec fields, the bounded typed clarifying questions, the
    ambiguity report (incl. anti-sycophancy challenges), the applied-standards
    proposals, the auto-resolved assumptions, and a confidence score the driver
    feeds into ``decide_next_action``.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Draft TaskSpec fields (mutable until U-3 sign-off; §5) ---
    title: str = ""
    intent: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    # --- Clarification driver outputs ---
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    ambiguities: list[AmbiguityItem] = Field(default_factory=list)
    applied_standards: list[AppliedStandard] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)

    # Confidence the spec is complete enough to lock. Feeds decide_next_action
    # (PRD: "rises only when a tracked ambiguity is resolved").
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    def model_post_init(self, _ctx: object) -> None:
        """Enforce the ≤5 questions/round cap (PRD §6 SP-03 (b)) at the type boundary.

        A drafter that tries to surface >5 questions in a single round is a
        contract violation, not a soft preference — the cap is structural.
        """
        if len(self.questions) > MAX_QUESTIONS_PER_ROUND:
            raise ValueError(
                f"clarifying questions capped at {MAX_QUESTIONS_PER_ROUND}/round "
                f"(PRD §6 SP-03 (b)); got {len(self.questions)}"
            )

    @property
    def covered_categories(self) -> set[QuestionCategory]:
        """Distinct question categories surfaced this round (coverage oracle)."""
        return {q.category for q in self.questions}

    @property
    def overrides(self) -> list[AmbiguityItem]:
        """The anti-sycophancy `kind=override` items (C18)."""
        return [a for a in self.ambiguities if a.kind == "override"]

    @property
    def challenges(self) -> list[AmbiguityItem]:
        """All anti-sycophancy items: false-premise clarifications + overrides (C18)."""
        return [a for a in self.ambiguities if a.kind in ("clarification", "override")]


# ---------------------------------------------------------------------------
# AbstractSpecDrafter — the injection seam
# ---------------------------------------------------------------------------


class AbstractSpecDrafter(ABC):
    """Draft TaskSpec fields + typed clarifying questions + an ambiguity report.

    Concrete subclasses:
      - InMemorySpecDrafter (app/adapters/inmemory) — deterministic, rule-based,
        for hermetic CI. No live Vertex.
      - VertexSpecDrafter (app/adapters/gcp) — Vertex structured-output, DEFERRED
        stub.

    The driver (lib/anchors/clarification_driver.py) calls ``draft(...)`` once per
    clarification round, feeds ``DraftResult.confidence`` into ``decide_next_action``,
    and surfaces the questions / ambiguity report to the operator.
    """

    @abstractmethod
    def draft(
        self,
        intent: str,
        *,
        answers: Optional[dict[str, str]] = None,
        round_index: int = 0,
    ) -> DraftResult:
        """Produce one round of draft fields + questions + ambiguity report.

        Args:
            intent:      The operator's free-form goal (UNTRUSTED external text;
                         the driver tags it via app.core.trust before this call).
            answers:     Answers gathered so far, keyed by the question token they
                         resolve. Resolving a tracked ambiguity raises confidence;
                         an irrelevant / non-answer leaves it UNCHANGED (PRD oracle).
            round_index: 0-based clarification round (for budget accounting).

        Returns:
            A DraftResult. MUST satisfy: ≤5 questions/round; a planted-ambiguity
            token surfaces a question referencing that token; a fully-specified
            goal yields ZERO questions; a planted false premise yields a
            kind=clarification item; a planted deprecated choice yields exactly one
            kind=override item.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.draft() must be implemented")
