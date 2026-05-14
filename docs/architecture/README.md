# Architecture

The full architectural design lives in [`../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`](../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md). This index points to the design document and the related Architecture Decision Records.

## Reading order

1. **[Architecture spec](../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)** — single source of truth for the complete design (architecture, components, data flow, security, observability, error handling, RL pipeline, testing)
2. **[ADRs](../decisions/)** — point-in-time decisions and their rationale
3. **[Phase plans](../superpowers/plans/)** — implementation plans per phase

## Key concepts

| Concept | Where to read |
|---|---|
| 12-service docker-compose stack | spec §2 |
| Tiered sandboxing | spec §5.3, ADR 0003 |
| Self-RL loop | spec §6, ADR 0005 |
| Phase gating | spec §10, ADR 0006 |
| Worktree-per-phase | ADR 0007, conventions/branching.md |
