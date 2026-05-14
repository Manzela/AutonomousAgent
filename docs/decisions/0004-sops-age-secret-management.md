# 0004. sops + age for secret management

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The deployment depends on multiple secrets (Telegram bot token, LiteLLM master key, Chroma auth token, Postgres password, Healthchecks.io URL, Modal/Daytona tokens). These must:
- Be checked into git (so the project is self-contained and reproducible)
- Be unreadable to anyone without the decryption key
- Have a clean rotation story
- Work in both Phase 1 (local Mac) and Phase 2 (GCP VM)

## Decision

We will use [sops](https://github.com/getsops/sops) with an [age](https://github.com/FiloSottile/age) recipient. Encrypted secrets live in `secrets/*.sops`, gitignored plaintext counterparts (`secrets/*.env`, `secrets/*.json`) are decrypted at bootstrap into tmpfs-mounted compose secrets. The age private key lives at `~/.config/sops/age/keys.txt` (Mac host) and is backed up to a password manager.

Phase 2 substitutes Google Secret Manager for the same encrypted-secret pattern, mounted into containers.

## Consequences

### Positive
- Encrypted secrets in git (reproducible deployment, code-reviewable secret rotation)
- No proprietary dependency (sops is OSS, age is OSS)
- Single age recipient is simpler than GPG
- Pre-commit hook `detect-secrets` catches accidental plaintext commits as second line of defense

### Negative
- Lose the age key = lose access to all secrets (mitigated by password-manager backup)
- Adds two host-level tools to install (sops, age)
- Requires explicit decryption step before `docker compose up`

### Neutral
- Phase 2 transition to Secret Manager is straightforward (encrypted-at-rest pattern matches)

## Alternatives considered

### Option A: HashiCorp Vault
- Pros: Industry standard; mature
- Cons: Heavyweight for single-developer; another service to operate
- Why rejected: Overkill for this scale

### Option B: GCP Secret Manager from Phase 1
- Pros: Uniform across phases; managed
- Cons: Cloud dependency for local-only Phase 1; small monthly cost; no offline work
- Why rejected: Adds cloud dependency to a pure-local phase

### Option C: Plain `.env` with strong gitignore
- Pros: Simplest
- Cons: Prone to accidental commits; no encryption-at-rest in git; no rotation history
- Why rejected: Security antipattern

## References

- [sops](https://github.com/getsops/sops)
- [age](https://github.com/FiloSottile/age)
- `.sops.yaml`
- `secrets/README.md`
