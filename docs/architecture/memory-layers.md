# Memory Layers — Five Files, Three Lifecycles

The repo has five files that all look like "memory" but are actually three
different things. This note disambiguates them, so a future reader doesn't
treat them interchangeably and so the link to the research doc's
"consensus core" is explicit.

## At a glance

| File | Lifecycle | Writer | Reader | Purpose |
|---|---|---|---|---|
| `config/hermes/SOUL.md` | Static | Operator (hand-edited) | Hermes upstream, prepended to system prompt | Persona / behavioral defaults |
| `config/hermes/USER.md` | Mostly static | Operator (Honcho dialectic-learning planned) | Hermes upstream | User identity + communication preferences |
| `config/hermes/MEMORY.md` | Static | Operator | Hermes upstream | Project-level facts (deployment, LLM, storage) |
| `config/hermes/AGENTS.md` | Static | Operator | Hermes upstream + nested-agent runtime | Agent-specific tool inventory + sandbox conventions + new-repo SDLC playbook |
| `/data/MEMORY/REJECTED.md` | **Dynamic** | `lib/memory/rejected.py:append_entry()` (judge-panel consensus path) | `lib/durability/__init__.py:255-277` (on_session_start inject, intent-filtered) | Institutional memory of approach-level rejections |

Two important integrity properties to note:

- **The four `config/hermes/*.md` files have a soul-integrity pin** for SOUL.md specifically (`config/limits.yaml:217-220`, SHA-256 enforced by `tests/unit/test_soul_md_integrity.py` and CI gate). The other three are not pinned — they evolve more freely.
- **`REJECTED.md` is the only dynamic layer**, and `lib/memory/rejected.py` is its only writer in normal operation. Operator can still `vi /data/MEMORY/REJECTED.md` directly (per `lib/memory/rejected.py:5`); the file format is YAML frontmatter precisely to keep it human-editable.

## Layer 1 — Persona (`SOUL.md`)

5-line system-prompt prefix. Establishes defaults: verify before claiming success, prefer small reversible changes, acknowledge uncertainty, surface degradation. Static; **pinned by SHA-256**; changes require updating `config/limits.yaml:integrity.soul_md_sha256` in the same commit (see `CONTRIBUTING.md → "Updating pinned hashes (SOUL.md)"`).

## Layer 2 — User & project context (`USER.md`, `MEMORY.md`, `AGENTS.md`)

Hand-authored markdown loaded by upstream Hermes' standard context-file mechanism. They cover, in order:

- `USER.md` — who the user is, how they like to communicate. Honcho will eventually contribute dialectic-learned updates here (per file footer: "More to be learned from interaction via Honcho dialectic modeling").
- `MEMORY.md` — what the project is and how it's deployed. Today: deployment phase, LLM provider, storage backends. Intentionally tiny — the file footer notes it's a "seed."
- `AGENTS.md` — what the agent has access to (tools, sandbox tier conventions, conventional-commit rules, new-repo SDLC playbook). The longest of the four; this is the file an upstream nested-agent invocation will inherit.

None of these participate in the consensus-feedback loop. They are inputs to the agent's reasoning, not outputs of it.

## Layer 3 — Institutional memory (`/data/MEMORY/REJECTED.md`)

**This is the consensus layer.** It is the only one of the five files that captures what the agent has learned through outcome.

Lifecycle:

1. **Write path** — when the 4-judge consensus panel rejects an approach, `lib/memory/rejected.py:append_entry()` writes a YAML-frontmatter entry containing the `approach_fingerprint` (sha256 over tool-call sequence, per design-alignment spec L337-339), `approach_summary`, `intent_category`, `why_failed`, `alternatives`, and a TTL-derived `expires_at` (default 30 days, configurable via `config/limits.yaml:memory.rejected_default_ttl_days`). Duplicates by fingerprint bump `occurrence_count` and refresh the expiry instead of writing a new row.

2. **Read path** — `lib/durability/__init__.py:255-277` runs on every session start. It (a) reads the current TaskSpec's `intent_category` (or classifies on the fly via `lib.memory.intent_classifier`), (b) loads up to `rejected_max_inject_per_session` matching entries (default 10), (c) calls `ctx.inject_message(role="system", ...)` with a "DO NOT repeat" prelude.

3. **Operator path** — `/forget <pattern>` and `/rejections` slash commands (`lib/memory/__init__.py:24-53`) let the operator curate without editing the file directly. Order of inject ownership: `lib/durability/__init__.py` runs the inject hook, NOT the memory plugin — this is intentional so the resume hook (P1-3) is guaranteed to run before the inject hook (P1-4) per design-alignment spec L330-332.

## Mapping to the research doc's "consensus core"

The architecture-research doc describes a "consensus/episodic memory layer" where the agent's accumulated outcomes shape its future planning. Of the five files above, **`REJECTED.md` is the only one that matches**:

- It records consensus outputs (judge-panel rejections), not author-supplied context.
- It's indexed by approach fingerprint, not by content — enabling exact-match suppression of repeat attempts.
- It decays via TTL so old guidance doesn't dominate.
- It's intent-classified at read time so memory injected is relevant to the current TaskSpec.

The four static files (`SOUL.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`) play the role of *priors* — they shape behavior, but they are not consensus-derived. Future work toward the research doc's full "hierarchical memory manager" (Component 3) would build on REJECTED.md's pattern (typed entries, fingerprint dedup, TTL, intent filter) rather than replacing it.

## Failure modes

- `F11` (`lib/durability/failure_matrix.py:120`) — REJECTED inject would exceed context budget; the inject is skipped, not silently truncated.
- `F18` (`lib/durability/failure_matrix.py:151`) — 3-strike approach rejection (same fingerprint hit three times) triggers a harder escalation than a normal inject.
- `ctx.inject_message` absent (older Hermes build) — inject is logged and skipped (`lib/durability/__init__.py:278-284`); session still starts.

## References

- `config/hermes/{SOUL,USER,MEMORY,AGENTS}.md` — the four static layers
- `lib/memory/rejected.py:53-79, 175-229, 232-269` — REJECTED.md write/read API
- `lib/memory/__init__.py:24-72` — slash command surface (`/forget`, `/rejections`)
- `lib/memory/intent_classifier.py` — TaskSpec-shaped wrapper for category classification
- `lib/durability/__init__.py:255-290` — on_session_start inject flow
- `config/limits.yaml:191-196, 217-220` — memory config block + SOUL.md SHA-256 pin
- `tests/unit/test_soul_md_integrity.py` — local fast-fail integrity check
- `docs/decisions/0005-self-rl-pipeline-architecture.md` — pending update (J10) to name REJECTED.md as the RLAIF substrate
