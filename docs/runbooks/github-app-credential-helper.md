# GitHub App Credential Helper — Runbook

**SP-00f.2** | App 3920713 | Installation 136939619

---

## What it does

`scripts/git-credential-github-app.py` is a [git-credential helper](https://git-scm.com/docs/gitcredentials) that vends short-lived GitHub App installation tokens instead of a broad PAT.

- On `git credential get` for `host=github.com`: mints (or serves from cache) an App installation token and emits `username=x-access-token` + `password=<token>`.
- Caches the token to a `0600` JSON file; reuses it if >5 min remain, otherwise mints fresh.
- On any error (missing env, key file, GitHub API failure): exits 0 with **no stdout** so git falls back to the next credential helper (e.g. system keychain / broad PAT fallback). A one-line diagnostic goes to stderr only.
- `store` and `erase` subcommands are no-ops.

---

## Enabling the helper

> **Interpreter requirement.** The helper imports `lib.github_auth`, which needs
> `pyjwt[crypto]` (the `a2a`/`dev` extras). It must run under a Python that has
> those deps — i.e. the **project venv**. The `#!/usr/bin/env python3` shebang
> works only if `python3` on `PATH` *is* that venv (true inside the deployed
> agent's venv). If it is not (e.g. a developer shell with system `python3`),
> the import fails and — by the fail-safe — the helper exits 0 with no output
> and **silently falls back to the broad PAT**. To avoid that, register it with
> an explicit interpreter using git's `!`-prefix (run-as-shell-command) form:
>
> ```bash
> git config credential.'https://github.com/Manzela/AutonomousAgent'.helper \
>     '!/abs/path/.venv/bin/python /abs/path/scripts/git-credential-github-app.py'
> ```

### Scoped to this repo (recommended for the agent harness)

```bash
# Plain form — ONLY if `python3` on PATH is the project venv (deployed agent):
git config credential.'https://github.com/Manzela/AutonomousAgent'.helper \
    '/abs/path/scripts/git-credential-github-app.py'
```

### Global (all github.com operations on this machine)

```bash
git config --global credential.'https://github.com/'.helper \
    '/abs/path/scripts/git-credential-github-app.py'
```

### In `.git/config` or committed `config/.gitconfig` for the harness

```ini
[credential "https://github.com/Manzela/AutonomousAgent"]
    helper = /abs/path/scripts/git-credential-github-app.py
```

---

## Required environment variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_APP_PRIVATE_KEY_PATH` | **Yes** | Path to a **decrypted** PEM-encoded RSA private key |
| `GITHUB_APP_ID` | No (has fallback) | GitHub App numeric ID. Falls back to `config/github-app.json`. |
| `GITHUB_APP_INSTALLATION_ID` | No (has fallback) | App installation ID. Falls back to `config/github-app.json`. |
| `GITHUB_APP_TOKEN_CACHE` | No | Override the cache file path (default: `$XDG_CACHE_HOME/github-app-token.json` or `~/.cache/github-app-token.json`). |

**The private key is the only secret.** `GITHUB_APP_ID` / `GITHUB_APP_INSTALLATION_ID` are committed as non-secrets in `config/github-app.json` (values: `3920713` / `136939619`).

---

## Obtaining the decrypted private key

### Locally (developer machine)

```bash
# Decrypt the SOPS-encrypted key to a 077-masked temp file.
TMPKEY=$(mktemp)
chmod 0600 "$TMPKEY"
sops -d secrets/github-app-private-key.pem.sops > "$TMPKEY"
export GITHUB_APP_PRIVATE_KEY_PATH="$TMPKEY"

# When done:
rm -f "$TMPKEY"
```

### In deploy / CI

The harness (Workload Identity Federation or Secret Manager injection) writes the decrypted PEM to a tmpfs path (e.g. `/run/secrets/github-app-key.pem`) and sets:

```bash
export GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/github-app-key.pem
```

The helper reads the file at call time (not import time) — the path must point to the decrypted PEM, not the SOPS-encrypted file. **The helper does NOT decrypt SOPS itself.**

---

## Token lifecycle and cache

- GitHub App installation tokens expire in **≤1 hour**.
- The helper caches the token at `$GITHUB_APP_TOKEN_CACHE` (JSON: `{token, expires_at}`).
- Cache file is created with **mode 0600** (owner-readable only). If the file pre-exists with looser permissions, `chmod 0600` is applied on every write.
- A cached token is **reused** if it has >5 minutes remaining.
- If <5 min remain (or cache is absent/corrupt), a fresh token is minted.
- No cleanup is needed: expired cache entries are silently overwritten.

---

## Least-privilege scope

The GitHub App is granted the minimum scopes required for autonomous-agent operations:

| Scope | Level |
|---|---|
| `contents` | Read + Write |
| `issues` | Read + Write |
| `pull_requests` | Read + Write |
| Metadata | Read (implicit) |

**Gated (403):** admin, secrets, org-level settings, package registry, code scanning uploads. Any action requiring these will receive a 403 from GitHub, which is the correct least-privilege behaviour. The broad `gho_` PAT remains available **only as an explicit fallback** in the credential helper chain during the cutover period; it is NOT the default once this helper is registered.

---

## Cutover plan

1. Set `GITHUB_APP_PRIVATE_KEY_PATH` in the agent harness environment.
2. Register this helper as the **first** credential helper for `https://github.com/`.
3. The broad PAT helper (osxkeychain / env var) remains as the second/fallback.
4. Once the agent has operated without PAT fallback for ≥7 days, revoke the PAT and remove the fallback.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `git push` asks for username/password | Helper not registered, or registered with wrong path | Verify `git config --list \| grep credential` |
| `git-credential-github-app: failed to mint token: Missing … GITHUB_APP_PRIVATE_KEY_PATH` | Env var not set | Export `GITHUB_APP_PRIVATE_KEY_PATH` pointing to a decrypted PEM |
| `git-credential-github-app: failed to mint token: … HTTP 401` | Private key expired or App suspended | Rotate the App private key; check App status in GitHub settings |
| Cache file visible to other users | Unexpected umask | The helper explicitly `chmod 0600`s the cache; inspect with `ls -la ~/.cache/github-app-token.json` |
| `warning: could not write token cache` | Cache directory not writable | Set `GITHUB_APP_TOKEN_CACHE` to a writable path |

---

## References

- `lib/github_auth.py` — JWT mint + GitHub API call (the library this helper wraps)
- `config/github-app.json` — non-secret App identifiers
- `secrets/github-app-private-key.pem.sops` — SOPS-encrypted private key
- `tests/unit/test_git_credential_github_app.py` — unit tests
- [git-credentials protocol spec](https://git-scm.com/docs/git-credential)
