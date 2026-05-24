# Memory

## ⚠️ GCP Project Migration

> **MIGRATION IN PROGRESS**: GCP project is migrating from `i-for-ai` to `autonomous-agent-2026`.
> All new GCP resources, configs, and references must use `autonomous-agent-2026`.
> Full plan: `docs/architecture/gcp-migration-i-for-ai-to-autonomous-agent-2026.md`

## Project context
- GCP Project: `autonomous-agent-2026` (migrating from `i-for-ai`)
- Deployment: Phase 1 (local Mac, docker-compose)
- LLM: Anthropic Claude Opus 4.7 via Vertex AI (project autonomous-agent-2026) via LiteLLM proxy
- Storage: SQLite + Chroma + Honcho

(Memory grows from agent experience; this is the seed.)
