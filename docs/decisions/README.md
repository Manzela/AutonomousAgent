# Architecture Decision Records

This directory holds [MADR](https://adr.github.io/madr/) — Markdown Architecture Decision Records — that document point-in-time decisions on this project.

## When to write an ADR

Write one whenever you make a decision that:
- Locks in a tradeoff (chose X over Y)
- Affects code that's hard to unwind
- Future-you would want to know the reasoning

Don't write one for purely cosmetic choices or short-lived implementation details.

## How to write one

1. Copy `template.md` to `<NNNN>-<short-kebab-name>.md` where NNNN is the next zero-padded number
2. Fill in: Status, Context, Decision, Consequences, Alternatives
3. Commit it as `docs(adr): NNNN <title>`
4. Update this index

## Index

| # | Title | Status |
|---|---|---|
| 0001 | [Use Hermes Agent as base](0001-use-hermes-agent-as-base.md) | Accepted |
| 0002 | [Vertex AI via LiteLLM proxy](0002-vertex-ai-via-litellm-proxy.md) | Accepted |
| 0003 | [Tiered sandboxing strategy](0003-tiered-sandboxing-strategy.md) | Accepted |
| 0004 | [sops + age for secret management](0004-sops-age-secret-management.md) | Accepted |
| 0005 | [Self-RL pipeline: soft loop now, hard loop Phase 4](0005-self-rl-pipeline-architecture.md) | Accepted |
| 0006 | [Iterative phase build with acceptance gates](0006-iterative-phase-build-with-gates.md) | Accepted |
| 0007 | [Worktree-per-phase branching](0007-worktree-per-phase-branching.md) | Accepted |
