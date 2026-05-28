# AutonomousAgent — Agent Context

## ⚠️ CRITICAL: GCP Project Migration In Progress

> **The GCP project for AutonomousAgent is migrating from `i-for-ai` to `autonomous-agent-2026`.**
>
> - **Old project**: `i-for-ai` (shared, being decommissioned for AutonomousAgent scope)
> - **New project**: `autonomous-agent-2026` (dedicated, isolated)
> - **Migration plan**: `docs/architecture/gcp-migration-i-for-ai-to-autonomous-agent-2026.md`
> - **Status**: PLANNED — awaiting execution
>
> **DO NOT**:
> - Create new GCP resources in `i-for-ai` for AutonomousAgent
> - Hardcode `i-for-ai` in any new code, configs, or scripts
> - Deploy anything new to `i-for-ai`
>
> **DO**:
> - Use `autonomous-agent-2026` as the project ID in all new work
> - Check the migration plan before making GCP-related changes
> - Flag any `i-for-ai` references you encounter in active code paths

---

## Project Overview

- **Repo**: `Manzela/AutonomousAgent` (GitHub)
- **GCP Project**: `autonomous-agent-2026` ← **USE THIS** (migrating from `i-for-ai`)
- **Region**: `us-central1`
- **Billing**: `01FABE-89B1B2-4C704D`

## LLM Backend

- **Primary**: Anthropic Claude Opus 4.7 via Vertex AI
- **Vertex AI project**: `autonomous-agent-2026` (org-wide quotas carry over)
- **Proxy**: LiteLLM (`deploy/litellm/config.yaml`)
- **Judges**: Gemini 3.1 Pro via Vertex AI

## Key Directories

- `terraform/phase-0a-gcp/` — Infrastructure as Code
- `deploy/` — Docker Compose, LiteLLM, OTel configs
- `config/` — Runtime configuration
- `secrets/` — SOPS-encrypted secrets (age key at `~/.config/sops/age/keys.txt`)
- `scripts/` — Operational scripts
- `docs/architecture/` — Architecture docs and migration plans
- `docs/research/` — Research artefacts (design specs, reference implementations)
- `audit/` — Audit findings and decision records

## Active research artefacts to consult before building

When implementing the autonomous-agent control plane (orchestrator, MoE
router, hierarchical memory, reward model, free-agent registry, sandbox,
bootstrap loop), the canonical design is at:

**`docs/research/autonomous-agent-seed-orchestrator/`** (added 2026-05-21,
PR #117).

Read order:
1. `README.md` — orientation
2. `01-phase1-mathematical-spec.md` — MDP, decomposed reward, PPO trust
   region, 5-layer isolation defence, Free Agent FSM
3. `02-self-correction-pass.md` — risk register
4. `03-phase3-bootstrapping-protocol.md` — meta-prompts + hot-plug
5. **`04-gcp-native-adapter-plan.md` — REQUIRED before lifting any seed
   module into `app/`.** Locks the hybrid pattern: abstract interfaces in
   `app/core/`, GCP-native implementations in `app/adapters/gcp/`,
   in-memory implementations in `app/adapters/inmemory/` for tests.
6. `INTEGRATION.md` — work items P-1..P-17 with acceptance criteria
7. `seed/` — reference Python implementation (15 files); read
   `seed/README.md` first for the module map and provenance

**Builder-agent rule:** when porting a `seed/` module into `app/`, do NOT
collapse the abstract base class. Keep `AbstractMemoryStore`,
`AbstractSandbox`, `AbstractEmbedder`, `AbstractMoERouter`, `Judge`
Protocol, and `AbstractIntrinsicRewardModel` exactly as written; add the
GCP-native subclass as a sibling under `app/adapters/gcp/`. CI runs
against `adapters/inmemory/`; staging + prod run against `adapters/gcp/`.

## Security Constraints

- All secrets SOPS-encrypted at rest with age recipients
- Never commit plaintext secrets — pre-commit blocks obvious patterns
- Never skip hooks (`--no-verify`) or bypass signing
- Never force push to main/master
- Never push to remote unless explicitly asked
- When staging files, prefer specific filenames over `git add -A`

## Conventions

- Squash-only merges to main
- Conventional commit PR titles (lowercase subject after `type(scope):`)
- `autonomousagent-*` prefix for all GCP resources
- GitHub operations via `gh` CLI (authenticated as `Manzela`)

## Audit reviewer model class rule

Every P0/P1 fix produced by an LLM agent MUST be reviewed by an LLM of a different model class.
Same-vendor counts as same-class (Opus reviewing Sonnet → ALLOWED; Opus reviewing Opus → FORBIDDEN; Claude → Gemini → ALLOWED).
The reviewer model is recorded in the PR description under `Reviewer model:` (line literal). PR template enforces.

## Audit rubric immutability

Audit rubrics MUST be sha256-pinned and chmod 444 for the duration of any audit session.
Files matching `update_plan.py`, `update_rubric.py`, `update_brain.py`, `update_audit_*.py`
are FORBIDDEN in this repository and are blocked by `.pre-commit-config.yaml`.

If a rubric must be revised, the change lands in a SEPARATE dated commit by a DIFFERENT
actor (4-eyes principle). No in-session rubric edits.
