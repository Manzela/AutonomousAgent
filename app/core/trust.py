"""C16/C17 trust primitive — UntrustedContent wrapper, ActionClass enum, and guard.

This is a PURE LIBRARY module — no sandbox/SP-05 dependency, no ABC collapse.
It is the foundation for:
  - C16: untrusted-read trust boundary (prompt-injection defence)
  - C17: bounded autonomy / action classification

Design anchors (PRD §4.1, SP-01 grounding §6 + Fork-3):
  - ActionClass is a STATIC property of the action type, looked up against the
    §4.1 table.  It is NEVER computed from model introspection or from untrusted
    content (SP-01 Fork-3 Recommendation C, biased to static).
  - The monitor (SP-27) may only ESCALATE (more-restrictive) — never downgrade.
  - UntrustedContent is a frozen wrapper; callers must explicitly request .raw to
    avoid accidental trusted-use of external material.

PRD §4.1 action-class table (lines 141–145, verbatim column headings):

  | Class                     | Actions                                              |
  |---------------------------|------------------------------------------------------|
  | Pre-authorized (standing) | create branch · open/update PR · open/comment/label  |
  |                           | issues · create worktree · draft commits · run CI ·  |
  |                           | read checks                                           |
  | Gated (per-instance appr) | create/delete repo · change visibility (public) ·    |
  |                           | force-push · push to protected branch · edit secrets  |
  |                           | / branch-protection / CODEOWNERS / audit/acceptance/  |
  |                           | · external publish (PyPI/registry) · spend beyond    |
  |                           | per-graph budget (SP-R2)                              |
  | Forbidden                 | edit own gates/rubrics (C10) · act under human        |
  |                           | identity (C13) · disable kill-switch                  |

ActionClass enum values map 1:1 to these three PRD classes (see docstring on each
member for the exact PRD line that grounds it).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# TrustLevel
# ---------------------------------------------------------------------------


class TrustLevel(enum.Enum):
    """Provenance tag for content entering the system.

    TRUSTED   — content sourced from within the harness / locked TaskSpec /
                objective subprocess outputs.
    UNTRUSTED — content from an external channel: issue/PR bodies, CI logs,
                tool/MCP/A2A/web/dependency outputs, any user-supplied text
                not yet sanitised and sealed.  C16.
    """

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


# ---------------------------------------------------------------------------
# UntrustedContent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UntrustedContent:
    """Frozen wrapper that tags external content as UNTRUSTED (C16).

    Wrapping external material (issue bodies, PR text, CI log lines, tool
    outputs, user-supplied intent strings …) forces callers to be explicit
    when they want the raw bytes — they can't accidentally pass an
    UntrustedContent object where a plain str is expected.

    Attributes:
        raw:    The raw external string.  Access is intentionally verbose so
                callers see they are consuming untrusted data.
        source: Human-readable provenance label, e.g. "github_issue_body",
                "telegram_message", "ci_log_line".

    Examples::

        content = UntrustedContent(raw=issue_body, source="github_issue_body")
        # Force-explicit extraction:
        cleaned = sanitiser.scrub(content.raw)
    """

    raw: str
    source: str
    trust: TrustLevel = TrustLevel.UNTRUSTED

    def __post_init__(self) -> None:
        """Force trust to UNTRUSTED regardless of what the caller passed.

        UntrustedContent must ALWAYS be UNTRUSTED — passing trust=TRUSTED at
        construction is a usage error (it would create a trusted-wrapper around
        untrusted material).  We pin the field unconditionally so the type
        invariant "UntrustedContent.trust is always UNTRUSTED" holds even if a
        caller accidentally or maliciously passes trust=TrustLevel.TRUSTED.

        Note: object.__setattr__ bypass is inherent Python and out of scope.
        """
        object.__setattr__(self, "trust", TrustLevel.UNTRUSTED)

    # Prevent accidental __str__ / implicit str() use leaking the tag.
    def __str__(self) -> str:  # pragma: no cover
        return f"<UntrustedContent source={self.source!r} len={len(self.raw)}>"

    def __repr__(self) -> str:
        return f"UntrustedContent(source={self.source!r}, len={len(self.raw)})"


# ---------------------------------------------------------------------------
# ActionClass
# ---------------------------------------------------------------------------


class ActionClass(enum.Enum):
    """Three-tier action classification from PRD §4.1 (lines 141–145).

    Ordering is from LEAST to MOST restrictive so that numeric comparison
    (PRE_AUTHORIZED < GATED < FORBIDDEN) encodes the escalation direction used
    by guard_action_class.

    PRD verbatim column-1 headings → enum member names:

      "Pre-authorized (standing)"   → PRE_AUTHORIZED  (line 143)
      "Gated (per-instance approval)" → GATED          (line 144)
      "Forbidden"                   → FORBIDDEN        (line 145)
    """

    # PRD §4.1 L143: "No approval per action — proceed autonomously"
    # Standing pre-auth: branch/PR/issue/worktree/draft/CI/checks within run repo.
    PRE_AUTHORIZED = 1

    # PRD §4.1 L144: "Operator approves once per instance via Telegram"
    # Gated: create/delete repo, visibility, force-push, protected-branch push,
    # secrets/CODEOWNERS/acceptance edits, external publish, over-budget spend.
    GATED = 2

    # PRD §4.1 L145: "Never" — edit own gates/rubrics, act as human, disable kill-switch.
    FORBIDDEN = 3

    def is_more_restrictive_than(self, other: "ActionClass") -> bool:
        """Return True iff self is strictly more restrictive than other."""
        return self.value > other.value

    def is_at_least_as_restrictive_as(self, other: "ActionClass") -> bool:
        """Return True iff self is at least as restrictive as other."""
        return self.value >= other.value


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class UntrustedEscalationError(ValueError):
    """Raised when untrusted content attempts to set or raise the action class.

    C16: untrusted-read content CANNOT change the action class (PRD §5
    contract + SP-01 Fork-3 static-lookup rule).
    """

    def __init__(self, proposed: ActionClass, source: str) -> None:
        super().__init__(
            f"C16 violation: untrusted content from source={source!r} attempted to "
            f"set action_class={proposed.name}.  ActionClass MUST be derived from "
            f"objective signals (§4.1 table lookup) — never from untrusted reads."
        )
        self.proposed = proposed
        self.source = source


class ActionClassDowngradeError(ValueError):
    """Raised when a proposed action class is less restrictive than the current one.

    SP-01 Fork-3 / SP-27: the monitor may only ESCALATE (more-restrictive);
    a downgrade (PRE_AUTHORIZED after GATED has been set) is rejected.
    """

    def __init__(self, proposed: ActionClass, current: ActionClass) -> None:
        super().__init__(
            f"ActionClass downgrade rejected: cannot move from {current.name} "
            f"(more restrictive) to {proposed.name} (less restrictive).  "
            f"The action_class may only ESCALATE (SP-01 Fork-3 / SP-27 monitor rule)."
        )
        self.proposed = proposed
        self.current = current


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def guard_action_class(
    proposed: ActionClass,
    *,
    derived_from_untrusted: bool,
    untrusted_source: str = "<unknown>",
    current: ActionClass | None = None,
) -> ActionClass:
    """Validate and return a proposed ActionClass, or raise a typed error.

    Enforced invariants (both must pass for the guard to return):

    1. UNTRUSTED-ORIGIN REJECTION (C16):
       If ``derived_from_untrusted=True``, the guard ALWAYS raises
       ``UntrustedEscalationError`` regardless of the proposed class.
       ActionClass MUST come from a static §4.1 table lookup against an
       objective action-type — NEVER from model introspection or untrusted reads.

    2. ESCALATION-ONLY (SP-01 Fork-3 / SP-27 monitor rule):
       If a ``current`` ActionClass is supplied, ``proposed`` must be at least as
       restrictive as ``current``.  A downgrade raises ``ActionClassDowngradeError``.
       This encodes "the monitor may only escalate, never downgrade."

    Caller obligation — ``derived_from_untrusted``:
        This is a TAINT FLAG that the CALLER must set honestly.  The guard
        enforces the rejection given an honest flag; it does NOT itself inspect
        provenance or walk the call stack.  The caller MUST pass
        ``derived_from_untrusted=True`` whenever the proposed action_class was
        computed from, or influenced by, untrusted content (e.g. an issue body,
        PR text, CI log, MCP/A2A response, user-supplied string).  Passing
        ``False`` when the class actually came from untrusted content is a
        violation of the C16 contract (standard taint-propagation posture:
        honest flag → guard enforces; dishonest flag → caller is at fault).

        v1 contract note: a future hardening is to have the guard accept the
        ``UntrustedContent`` / provenance object directly so provenance can be
        verified structurally.  For now the caller flag is the contract.

    Args:
        proposed:               The ActionClass to validate.
        derived_from_untrusted: CALLER OBLIGATION — set True whenever the
                                proposed action_class was computed from, or
                                influenced by, any untrusted content.  See
                                taint-propagation note above.
        untrusted_source:       Provenance label for the error message (the
                                UntrustedContent.source, if available).
        current:                The existing ActionClass for this action in the
                                current state, if one exists.  None means this
                                is the first classification; no escalation check.

    Returns:
        ``proposed`` (unchanged) when both invariants hold.

    Raises:
        UntrustedEscalationError:  invariant 1 violated.
        ActionClassDowngradeError: invariant 2 violated.
    """
    # Invariant 1: reject any classification derived from untrusted content.
    if derived_from_untrusted:
        raise UntrustedEscalationError(proposed=proposed, source=untrusted_source)

    # Invariant 2: escalation-only — proposed must be >= current.
    if current is not None and proposed.value < current.value:
        raise ActionClassDowngradeError(proposed=proposed, current=current)

    return proposed


# ---------------------------------------------------------------------------
# Convenience: tag_untrusted
# ---------------------------------------------------------------------------


def tag_untrusted(raw: Any, *, source: str) -> UntrustedContent:
    """Wrap an external value as UntrustedContent.

    Converts to str if necessary (e.g. bytes decoded utf-8 with replace).
    Convenience function for the first call-site at ingestion boundaries.

    Args:
        raw:    The raw external content.
        source: Human-readable provenance label.

    Returns:
        A frozen UntrustedContent wrapping the content.
    """
    if isinstance(raw, bytes):
        raw_str = raw.decode("utf-8", errors="replace")
    elif not isinstance(raw, str):
        raw_str = str(raw)
    else:
        raw_str = raw
    return UntrustedContent(raw=raw_str, source=source)
