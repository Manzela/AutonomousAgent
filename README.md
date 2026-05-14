# AutonomousAgent

Production deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) with self-improving capabilities, tiered sandboxing, OpenTelemetry observability, and an Atropos RL training pipeline.

**Status:** Phase 1 (local Mac deployment).

**Spec:** [docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)

**Plans:**
- Phase 1: [docs/superpowers/plans/2026-05-14-phase1-local-deployment.md](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md)

## Quick start

After completing all Phase 1 tasks:

```bash
./scripts/bootstrap.sh    # one-shot setup
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
./scripts/smoke.sh        # verify everything works
```

## Layout

See file structure in the Phase 1 plan.
