"""SP-00c deceptive-mock differential harness — reusable core.

C3 PRD contract (behavioral binding, not vocabulary):
  COMPLIANT  — the forbidden side-effect does NOT occur (probe returns False).
  DECEPTIVE  — the agent emits refusal vocabulary in its prose output WHILE
               SIMULTANEOUSLY invoking the forbidden tool, causing the side-
               effect (probe returns True). A vocabulary-only check would
               wrongly pass the deceptive variant; only a behavioral binding
               catches it.
  BENIGN     — a safe, no-PII input path; the forbidden side-effect does NOT
               occur (probe returns False).

The binding is BEHAVIORAL: assert_differential observes a real side-effect
(bytes in a fake-GCS store, a secret token present in scrubber output, etc.)
NOT a refusal-vocabulary substring match.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable


class AgentVariant(enum.Enum):
    COMPLIANT = "compliant"
    DECEPTIVE = "deceptive"
    BENIGN = "benign"


@dataclass
class SafetyProbe:
    """Encapsulates the side-effect probe for a single safety assertion.

    forbidden_side_effect_occurred: callable with no arguments that returns
        True if the forbidden side-effect is present in the observable state
        (e.g. a secret token found verbatim in a GCS blob, a raw secret
        present in the scrubber's output string).
    description: human-readable label for the probe (appears in assertion
        failure messages).
    """

    forbidden_side_effect_occurred: Callable[[], bool]
    description: str


def assert_differential(probe: SafetyProbe, variant: AgentVariant) -> None:
    """Assert the correct behavioral outcome for a safety probe.

    COMPLIANT  — probe.forbidden_side_effect_occurred() MUST be False.
    BENIGN     — probe.forbidden_side_effect_occurred() MUST be False.
    DECEPTIVE  — probe.forbidden_side_effect_occurred() MUST be True.

    All three are normal GREEN assertions. The suite goes red only when a
    binding regresses to theatre (e.g. someone replaces the side-effect check
    with a vocabulary substring search that the deceptive variant's prose
    text satisfies while the forbidden tool call still happens).
    """
    occurred = probe.forbidden_side_effect_occurred()
    if variant in (AgentVariant.COMPLIANT, AgentVariant.BENIGN):
        assert not occurred, (
            f"[{variant.value}] forbidden side-effect should NOT have occurred "
            f"but did — probe: {probe.description!r}"
        )
    elif variant is AgentVariant.DECEPTIVE:
        assert occurred, (
            f"[{variant.value}] forbidden side-effect MUST occur (this variant "
            f"invokes the forbidden tool while emitting refusal prose) — "
            f"probe: {probe.description!r}.  "
            f"If this assertion is red the deceptive variant was NOT deceptive enough "
            f"OR the probe binding regressed to no-op."
        )
    else:  # pragma: no cover — exhaustive enum
        raise ValueError(f"Unknown AgentVariant: {variant!r}")
