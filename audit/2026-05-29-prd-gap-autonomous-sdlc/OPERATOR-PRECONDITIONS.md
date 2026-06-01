# Operator preconditions before PRD execution

Three things only you can do: (1) rotate/revoke keys, (2) register + install the GitHub App, (3) the
working-tree base (already done for you). After these, the executor can start at Gate 0.

---

## 1. Key rotation / revocation

**Why:** every secret has been sitting decrypted as plaintext on the local disk (the normal SOPS-decrypt
dev workflow, but the meta-audit flagged it as exposure — P0-3). The genuinely sensitive, externally-valid
ones should be rotated; the classic PAT is being retired.

**SOPS placement workflow (verified from the repo):** age recipient is pinned in `.sops.yaml`
(`age1z4c2kx2...`); your private age key is at `~/.config/sops/age/keys.txt`. For each secret:

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
sops secrets/<name>.sops          # opens DECRYPTED in $EDITOR → change the value → save (re-encrypts in place)
#   — or from a fresh plaintext file —
sops -e secrets/<name> > secrets/<name>.sops && rm secrets/<name>
```

The containers read these via `env_file` / docker-secret / volume mounts (paths below), so after updating
the `.sops` you just re-run your decrypt/bootstrap step — **no code change needed**.

### Priority 1 — MUST rotate/revoke
| Secret (`.sops`) | What it is | App consumes it as | Action |
|---|---|---|---|
| `sa-keys/cloud-sql-proxy.json`, `sa-keys/litellm-proxy.json`, `sa-keys/snapshot-watchdog.json` | **3 GCP SA private keys** (plaintext on disk, mode 0644 — the P0-3 finding) | `GOOGLE_APPLICATION_CREDENTIALS=/secrets/sa-key.json` (volume-mounted per service) | **Best: migrate to WIF** (`terraform/phase-0a-gcp/wif.tf` + `wif-migration.tf` already exist) and **disable** the keys. Interim: `gcloud iam service-accounts keys create new.json --iam-account=<SA_EMAIL>` → put in `secrets/sa-keys/<name>.json` → `sops -e` → `rm` plaintext → `gcloud iam service-accounts keys delete <OLD_ID> --iam-account=<SA_EMAIL>`. Get `<SA_EMAIL>` from each JSON's `client_email`. |
| `hermes-provider.env` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` (the 64-hex provider key the meta-audit found) | `hermes` service env_file | Rotate the key on the LiteLLM/provider side; put the new value in the `.sops`. |
| `litellm-master-key` | LiteLLM proxy master key (clients auth to the proxy with it) | docker secret `/run/secrets/litellm_master_key` | Generate a new master key; update the proxy config **and** this secret together. |
| `github-pat` | classic GitHub PAT | `github-mcp` sidecar `/run/secrets/github_pat` | **Retire, don't rotate** — after the GitHub App (§2) works, **revoke** this PAT on GitHub and delete the secret (SP-00f). |

### Priority 2 — rotate (externally-valid, were on disk)
| `telegram.env` | `TELEGRAM_BOT_TOKEN` | `hermes` + watchdogs env_file | rotate via **@BotFather** (`/revoke`) if you consider it exposed. |
| `honcho.env` | `HONCHO_API_KEY` | `hermes` env_file | rotate on the Honcho dashboard. |
| `chroma-cloud.env` | `CHROMA_CLOUD_API_KEY` | `hermes` env_file | rotate on Chroma Cloud. |

### Priority 3 — optional (internal / low-risk)
`litellm-db.env` (Postgres password — internal; **do NOT rotate `LITELLM_SALT_KEY`**, it invalidates
stored virtual keys), `honcho-db-password`, `chroma-token`, `healthchecks-url`.

> After rotating, verify: `find secrets -name '*.json' -not -name '*.sops'` returns **empty**, and
> `git ls-files secrets/` shows only `*.sops` + README + .gitignore. (This is SP-00b's acceptance.)

---

## 2. Register the least-privilege GitHub App + install on the run repo (SP-00f)

This replaces the broad classic PAT with a fine-grained, short-lived-token identity scoped to one repo.

1. **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.**
2. **GitHub App name:** `autonomousagent-executor` (must be globally unique). **Homepage URL:** any
   (e.g. the repo URL).
3. **Webhook:** uncheck **Active** (not needed now).
4. **Repository permissions** — set ONLY these (the C17 pre-authorized class):
   - Contents: **Read & write**
   - Pull requests: **Read & write**
   - Issues: **Read & write**
   - Checks: **Read-only**
   - Actions: **Read-only**
   - Metadata: **Read-only** (mandatory)
   - **Leave everything else "No access"** — especially **Administration, Secrets, Workflows, Members,
     Organization** = No access. (This is what makes create-repo / force-push / secret-write return 403.)
5. **Where can this GitHub App be installed?** → **Only on this account.**
6. **Create GitHub App.** On the app page: note the **App ID** and **Client ID**; click **Generate a
   private key** → download the `.pem`.
7. **Store the private key encrypted** in the repo:
   ```bash
   mv ~/Downloads/autonomousagent-executor.*.private-key.pem secrets/github-app-private-key.pem
   sops -e secrets/github-app-private-key.pem > secrets/github-app-private-key.pem.sops
   rm secrets/github-app-private-key.pem
   ```
   Put the **App ID** in `secrets/github-app.env` (`GITHUB_APP_ID=...`) → `sops -e` → `rm` plaintext.
8. **Install it on the run repo:** App page → **Install App** → your account → **Only select
   repositories** → choose **`Manzela/AutonomousAgent`** → Install. Note the **Installation ID** (in the
   install URL `…/installations/<ID>`), add it to `secrets/github-app.env` as `GITHUB_APP_INSTALLATION_ID=...`.
9. The executor mints **short-lived (<1h) installation tokens** per call from (App ID + private key +
   installation ID) — in CI via `actions/create-github-app-token`, at runtime via a small token helper.
10. **Only after** the App is verified working: **revoke the classic PAT** (Settings → Developer settings
    → Personal access tokens) and delete `secrets/github-pat*`.

> Acceptance the executor will check (SP-00f): with the App token, `gh api -X POST /user/repos`
> (create-repo), a force-push, and an Actions-secret write each return **403**; branch-create + PR-open +
> issue-comment each return **2xx**.

> **✅ VERIFIED 2026-05-31** (App ID `3920713`, Installation ID `136939619`). Negative-permission proof
> ran green: create-repo **403**, actions-secret-write **403**, force-push-to-protected-`main` **422**;
> branch-create / contents-write / PR-open (#169, auto-closed) / issue-comment all **201**; installation
> token **TTL 3599 s (<2 h)**. Evidence + reproducer:
> [`evidence/SP-00f-evidence.md`](./evidence/SP-00f-evidence.md). **Two caveats, both surfaced there:**
> (1) the App grants `repository_projects:write` + `agent_tasks:write` beyond the documented C17 set —
> trim to "No access" for strict least-privilege (gated boundary still intact); (2) the *compose-side*
> retirement is **still OPEN** — `deploy/docker-compose.yml` still has `--toolsets all` (:308) and the
> `github_pat` wiring (:317/:319/:723-724). So **SP-00f is NOT fully done** — only the App-identity half
> is proven; the compose half remains an executor EPIC-0 task.

---

## 3. Working-tree base — DONE (for reference)

Established 2026-05-29: branch `remediation/p1-01-rewrite-tests` at **`7e6f7a43`**, tracked tree clean.
The prior 14 uncommitted changes (`.github/`, `config/toolsets.yaml`, `lib/a2a/server.py`, the integration
tests, `uv.lock`, …) are preserved in **`git stash@{0}`** — recover any time with `git stash pop` (or
`git stash show -p stash@{0}` to inspect). Your `audit/` PRD deliverables remain untracked and intact.
