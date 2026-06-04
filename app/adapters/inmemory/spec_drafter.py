"""InMemorySpecDrafter — deterministic, rule-based SP-03 drafter for hermetic CI.

This is the in-memory sibling of ``app.core.spec_drafter.AbstractSpecDrafter``.
It performs ZERO live Vertex / LLM calls — CI must be hermetic (CLAUDE.md /
04-gcp-native-adapter-plan.md). The Vertex structured-output concretion is the
DEFERRED stub at ``app.adapters.gcp.spec_drafter.VertexSpecDrafter``.

The drafter is driven by PLANTED TOKENS embedded in the goal string. This makes
the SP-03 acceptance oracles deterministic and red-green (the PRD requires the
question text to reference the planted token "not a fixed string", so the token
is parsed out and woven into the generated question):

  AMBIG:<token>            an above-threshold ambiguity → a clarifying question
                           whose text references <token>.
  AMBIG:<token>@<category> pin the question's category (functional /
                           data_contracts / edge_error / non_functional /
                           scope_boundary). Default category: functional.
  MINOR:<token>            a BELOW-threshold ambiguity → auto-resolved into an
                           assumptions[] entry (NOT a question) — PRD (1.3).
  FALSE:<token>            a planted false premise (nonexistent API/flag) → a
                           kind=clarification challenge citing <token> (C18).
                           The false token is NEVER encoded into the TaskSpec.
  DEPRECATED:<token>       a real-but-deprecated/suboptimal choice → exactly one
                           kind=override item with a recommended alternative (C18).
  STDDOMAIN:<domain>       a known-standard domain → ≥1 cited applied_standards
                           entry (the Spec-Kit "constitution" role) — PRD (1.1).

A goal with NONE of these tokens is treated as "fully specified": ZERO questions,
ZERO challenges, ZERO overrides, confidence == 1.0 (the false-positive control).

Confidence model (PRD oracle): each OPEN above-threshold ambiguity lowers
confidence; resolving a tracked ambiguity (an ``answers`` key matching its token)
raises it. An irrelevant / non-answer (a key that matches no tracked token) leaves
confidence UNCHANGED.

The known-standard registry below is a tiny vendored stand-in for the SP-25
``constitution.md`` asset (which is DEFERRED). It is intentionally model-knowledge
+ asset only — NEVER a live web tool (non-goal #6 / C16).
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.spec_drafter import (
    MAX_QUESTIONS_PER_ROUND,
    AbstractSpecDrafter,
    AmbiguityItem,
    AppliedStandard,
    Assumption,
    ClarifyingQuestion,
    DraftResult,
    QuestionCategory,
)

# Confidence each open above-threshold ambiguity subtracts from a perfect 1.0.
_AMBIGUITY_PENALTY = 0.2

_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"functional", "data_contracts", "edge_error", "non_functional", "scope_boundary"}
)

# Vendored stand-in for the SP-25 constitution.md asset (DEFERRED). Maps a known
# domain to the highest-credibility best-practice the drafter proactively proposes
# as an overridable DEFAULT (R5). Model-knowledge + asset only (non-goal #6 / C16).
_KNOWN_STANDARDS: dict[str, AppliedStandard] = {
    "auth": AppliedStandard(
        principle="Hash credentials with a memory-hard KDF (argon2id) and per-user salt.",
        source="OWASP ASVS v4 §2.4 / constitution.md#auth",
        why="Defends against offline credential cracking; the current SOTA password store.",
    ),
    "crypto": AppliedStandard(
        principle="Use AEAD ciphers (AES-GCM / ChaCha20-Poly1305); never raw ECB/CBC.",
        source="NIST SP 800-38D / constitution.md#crypto",
        why="Authenticated encryption prevents tamper + padding-oracle classes of attack.",
    ),
    "http_api": AppliedStandard(
        principle="Version the API and return RFC 7807 problem+json on error.",
        source="RFC 7807 / constitution.md#http",
        why="Stable contracts + machine-readable errors for clients.",
    ),
}

# Real-but-deprecated/suboptimal choices the drafter challenges with kind=override
# (C18). Maps the deprecated token → (recommended_alternative, rationale).
_DEPRECATED_CHOICES: dict[str, tuple[str, str]] = {
    "md5": (
        "argon2id (or bcrypt/scrypt for legacy)",
        "MD5 is cryptographically broken for password hashing — fast + collision-prone.",
    ),
    "sha1": (
        "SHA-256 / SHA-3",
        "SHA-1 is deprecated; practical collisions exist (SHAttered, 2017).",
    ),
    "pickle": (
        "json / a typed schema (pydantic, protobuf)",
        "pickle deserialization is arbitrary-code-execution on untrusted input.",
    ),
    "telnet": (
        "ssh",
        "telnet transmits credentials in cleartext.",
    ),
}

# Token grammar: a token may contain internal dots (e.g. os.fastopen) but must
# not END on a dot — so trailing sentence punctuation ("DEPRECATED:md5.") is not
# captured into the token. The category is an optional "@<category>" suffix.
_TOKEN_RE = re.compile(
    r"(AMBIG|MINOR|FALSE|DEPRECATED|STDDOMAIN):([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)(?:@([a-z_]+))?"
)


class InMemorySpecDrafter(AbstractSpecDrafter):
    """Deterministic rule-based drafter (no Vertex). See module docstring."""

    def draft(
        self,
        intent: str,
        *,
        answers: Optional[dict[str, str]] = None,
        round_index: int = 0,
    ) -> DraftResult:
        answers = answers or {}

        ambig_tokens: list[tuple[str, QuestionCategory]] = []
        minor_tokens: list[str] = []
        false_tokens: list[str] = []
        deprecated_tokens: list[str] = []
        std_domains: list[str] = []

        for kind, token, category in _TOKEN_RE.findall(intent):
            if kind == "AMBIG":
                cat = category if category in _VALID_CATEGORIES else "functional"
                ambig_tokens.append((token, cat))  # type: ignore[arg-type]
            elif kind == "MINOR":
                minor_tokens.append(token)
            elif kind == "FALSE":
                false_tokens.append(token)
            elif kind == "DEPRECATED":
                deprecated_tokens.append(token)
            elif kind == "STDDOMAIN":
                std_domains.append(token)

        # --- Clarifying questions (above-threshold ambiguities), ≤5/round ---
        questions: list[ClarifyingQuestion] = []
        open_unresolved = 0
        for token, cat in ambig_tokens:
            if len(questions) >= MAX_QUESTIONS_PER_ROUND:
                break  # enforce the ≤5/round cap (PRD §6 SP-03 (b))
            if token in answers:
                continue  # resolved → no question this round (confidence rises below)
            open_unresolved += 1
            questions.append(
                ClarifyingQuestion(
                    text=(f"The goal leaves '{token}' under-specified — what should '{token}' be?"),
                    category=cat,
                    references_token=token,
                )
            )

        # --- Assumptions (below-threshold ambiguities, auto-resolved) ---
        assumptions: list[Assumption] = [
            Assumption(
                ambiguity=f"'{token}' is unspecified but low-stakes",
                chosen_interpretation=f"default '{token}' to the conventional value",
                resolved_token=token,
            )
            for token in minor_tokens
        ]

        # --- Ambiguity report: anti-sycophancy challenges (C18) ---
        ambiguities: list[AmbiguityItem] = []
        for token in false_tokens:
            ambiguities.append(
                AmbiguityItem(
                    kind="clarification",
                    claim=(
                        f"The goal references '{token}', which does not appear to exist "
                        f"(no such API/flag). Please confirm the intended target."
                    ),
                    references_token=token,
                    confidence=0.8,
                )
            )
        for token in deprecated_tokens:
            alt, rationale = _DEPRECATED_CHOICES.get(
                token, ("a current, non-deprecated equivalent", "The chosen option is deprecated.")
            )
            ambiguities.append(
                AmbiguityItem(
                    kind="override",
                    claim=f"'{token}' is a deprecated / suboptimal choice.",
                    references_token=token,
                    recommended_alternative=alt,
                    rationale=rationale,
                    confidence=0.85,
                    cite=None,
                    cite_unverified=True,
                )
            )

        # --- Applied standards (known-standard domains, overridable DEFAULTS) ---
        applied_standards: list[AppliedStandard] = []
        for domain in std_domains:
            std = _KNOWN_STANDARDS.get(domain)
            if std is not None:
                applied_standards.append(std)

        # --- Draft TaskSpec fields (NEVER encode false-premise tokens, C18) ---
        title = intent.strip().splitlines()[0][:60] if intent.strip() else "Untitled draft"
        acceptance_criteria: list[str] = []
        in_scope: list[str] = []
        out_of_scope: list[str] = []
        success_metrics: list[str] = []
        if not (ambig_tokens or minor_tokens or false_tokens or deprecated_tokens or std_domains):
            # Fully-specified goal: synthesise a minimal but real draft.
            acceptance_criteria = [intent.strip()]
            in_scope = [intent.strip()]
            out_of_scope = ["anything not named in the intent"]
            success_metrics = ["the stated success condition holds"]

        # --- Confidence ---
        # Resolving a tracked ambiguity removes its penalty (confidence rises);
        # a non-answer (key not matching any tracked token) is ignored.
        confidence = max(0.0, 1.0 - _AMBIGUITY_PENALTY * open_unresolved)

        return DraftResult(
            title=title,
            intent=intent.strip(),
            acceptance_criteria=acceptance_criteria,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            success_metrics=success_metrics,
            constraints=[],
            questions=questions,
            ambiguities=ambiguities,
            applied_standards=applied_standards,
            assumptions=assumptions,
            confidence=confidence,
        )
