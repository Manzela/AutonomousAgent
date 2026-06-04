"""VertexSpecDrafter — DEFERRED GCP/Vertex concretion of AbstractSpecDrafter.

This is the production spec-drafter: a Vertex structured-output (Pydantic-
constrained) drafter that emits TaskSpec fields + typed clarifying questions + an
ambiguity report (PRD §6 SP-03). It is an INTENTIONALLY-UNWIRED stub — the safe
core (the ABC + the deterministic in-memory drafter + the driver) ships first;
the live Vertex concretion is DEFERRED to a follow-up (it needs the Vertex SDK,
WIF credentials per SP-00b, and the vendored SP-25 `constitution.md` asset for the
`applied_standards[]` grounding).

Kept as a sibling per the CLAUDE.md builder-agent rule (do NOT collapse the ABC;
add the GCP subclass alongside the in-memory one). CI runs against
`app.adapters.inmemory.spec_drafter.InMemorySpecDrafter`; staging + prod will run
against this class once implemented.

is_production_grade=False until draft() is real — same posture as
FirecrackerSandbox (H-06): a True flag on a stub would let a production selector
falsely accept this class and then crash on the first call.

GROUNDING RULE (C16 / non-goal #6): the structured-output prompt is grounded ONLY
in model knowledge + the vendored SP-25 `constitution.md` asset. There is NO live
web tool / general-web egress — a web fetch here would breach non-goal #6 and open
a C16 untrusted-read surface. The Vertex call returns Pydantic-validated
`DraftResult` JSON (response_schema-constrained), never free text the harness must
re-parse.
"""

from __future__ import annotations

from typing import Optional

from app.core.spec_drafter import AbstractSpecDrafter, DraftResult


class VertexSpecDrafter(AbstractSpecDrafter):
    """Vertex structured-output spec-drafter.

    NOT YET IMPLEMENTED — SP-03 Vertex concretion (DEFERRED). ``__init__`` and
    ``draft()`` raise NotImplementedError until the Vertex client + response-schema
    binding + constitution.md grounding asset are wired.
    """

    is_production_grade = False

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        location: str = "us-central1",
        model: str = "claude-opus-4-7",
    ) -> None:
        raise NotImplementedError(
            "SP-03 Vertex spec-drafter not yet implemented. Use "
            "app.adapters.inmemory.spec_drafter.InMemorySpecDrafter for CI. The "
            "Vertex concretion needs: the Vertex SDK + WIF creds (SP-00b), a "
            "response_schema-constrained structured-output call returning DraftResult "
            "JSON, and the vendored SP-25 constitution.md asset for applied_standards "
            "grounding (model knowledge + asset ONLY — no live web tool, C16 / non-goal #6)."
        )

    def draft(
        self,
        intent: str,
        *,
        answers: Optional[dict[str, str]] = None,
        round_index: int = 0,
    ) -> DraftResult:
        """Produce one round via Vertex structured output. DEFERRED — stub."""
        raise NotImplementedError("SP-03 Vertex spec-drafter stub")
