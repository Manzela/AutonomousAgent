# AutonomousAgent — Gemini Context

## ⚠️ CRITICAL: GCP Project Migration In Progress

> **The GCP project for AutonomousAgent is migrating from `i-for-ai` to `autonomous-agent-2026`.**
>
> - **Old project**: `i-for-ai` (shared, being decommissioned for AutonomousAgent scope)
> - **New project**: `autonomous-agent-2026` (dedicated, isolated)
> - **Migration plan**: `docs/architecture/gcp-migration-i-for-ai-to-autonomous-agent-2026.md`
> - **Status**: PLANNED — awaiting execution
>
> **DO NOT** create new GCP resources in `i-for-ai` for AutonomousAgent.
> **DO** use `autonomous-agent-2026` as the project ID in all new work.

## Project

- **Repo**: `Manzela/AutonomousAgent`
- **GCP Project**: `autonomous-agent-2026` (migrating from `i-for-ai`)
- **Region**: `us-central1`
- **Vertex AI**: Org-wide quotas — model access carries over from `i-for-ai`

## Key Files

- `CLAUDE.md` — Claude Code agent context (same migration notice)
- `config/hermes/MEMORY.md` — Hermes agent runtime memory
- `terraform/phase-0a-gcp/` — Infrastructure as Code
- `deploy/` — Docker Compose, LiteLLM, OTel configs
