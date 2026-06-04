# SP-00f — GitHub App least-privilege: negative-permission proof

**Date:** 2026-05-31 · **Run by:** operator-precondition verification (Claude Opus 4.8)
**App:** `autonomousagent-executor` · App ID `3920713` · Installation ID `136939619`
**Repo under test:** `Manzela/AutonomousAgent`
**Reproduce:** `.venv/bin/python audit/2026-05-29-prd-gap-autonomous-sdlc/evidence/sp00f_app_token_proof.py`

> Scope note: this is **operator-precondition evidence**, not a CI-graded acceptance pass. The
> formal `pr-meta-checks` negative-permission gate is still the executor's work under **SP-00e/SP-00f**.
> The proof mints a short-lived installation token in-process; the token and the App private key are
> never written to disk or echoed.

## Result — `red_all_denied: true`, `green_all_2xx: true`, `ttl_under_2h: true`

Token minted: `expires_at = 2026-05-31T10:52:02Z`, **TTL ≈ 3599 s (< 2 h)** ✓

| Class | Action | Call | Status | Expect | Verdict |
|---|---|---|---|---|---|
| 🔴 GATED | create-repo | `POST /user/repos` | **403** | denied | ✅ |
| 🔴 GATED | actions-secret write | `PUT …/actions/secrets/SP00F_PROOF` | **403** | denied | ✅ |
| 🔴 GATED | force-push protected `main` | `PATCH …/git/refs/heads/main force=true` (no-op self-sha) | **422** | denied | ✅ |
| 🟢 STANDING | contents read | `GET …/git/ref/heads/main` | **200** | ok | ✅ |
| 🟢 STANDING | branch-create | `POST …/git/refs` | **201** | 2xx | ✅ |
| 🟢 STANDING | contents-write (branch only) | `PUT …/contents/.sp00f-proof.txt` | **201** | 2xx | ✅ |
| 🟢 STANDING | PR-open (draft) | `POST …/pulls` → PR #169 | **201** | 2xx | ✅ |
| 🟢 STANDING | issue-comment | `POST …/issues/169/comments` | **201** | 2xx | ✅ |

**Cleanup verified:** PR #169 `state=closed, merged=false`; test branch `sp00f-app-proof` deleted;
`.sp00f-proof.txt` absent from `main` (it only ever existed on the deleted branch).

> First run mis-scored `pr-open` as 422 — that was *not* a permission denial (the App holds
> `pull_requests: write`) but GitHub's "no commits between head and base" rule, because the test branch
> was created at `main`'s exact tip. Fixed by committing a throwaway marker to the branch first; re-run → 201.

## Granted permissions (echoed by the token-mint response)

Matches the C17 pre-authorized set **and the gated/dangerous class is absent** (proven by the 403s):

- **r/w (intended):** `contents`, `pull_requests`, `issues`
- **read (intended):** `checks`, `actions`, `metadata`
- **absent (dangerous/gated):** `administration` (repo create/delete/visibility/branch-protection),
  `secrets` (write), `members`/`organization`, `workflows` — confirmed by create-repo 403,
  secret-write 403, force-push-to-protected 422.

### ⚠ Least-privilege finding (not a blocker — gated boundary holds)

The token grants **two write scopes beyond the documented C17 set**:

- `repository_projects: write` — not required by the design (the board projection is local/Plane, not
  GitHub Projects).
- `agent_tasks: write` — not in the design.

Plus several GitHub-bundled **read** scopes (`statuses`, `pages`, `artifact_metadata`, `code_quality`,
`repository_advisories`, `secret_scanning_alerts`, `security_events`, `vulnerability_alerts`) that are
low-risk.

**Recommendation:** in the App's *Repository permissions*, set `Projects` and any "agent tasks" scope back
to **No access** for strict least-privilege — or accept + document them in C17. Either way the **gated
boundary is intact**; this is a tightening, not a hole.

## Still OPEN under SP-00f (executor work — do NOT mark SP-00f done yet)

The App *identity* is proven, but the **compose-side retirement is not done**:

- `deploy/docker-compose.yml:308` still runs github-mcp with `--toolsets all` (must become a
  pre-authorized-only toolset). `grep -rn 'toolsets.*all' deploy/` is **non-empty**.
- `deploy/docker-compose.yml:317,319,723-724` still wire the classic `github_pat`
  (`GITHUB_PERSONAL_ACCESS_TOKEN_FILE` + the `github_pat` secret pointing at `../secrets/github-pat`).
  The encrypted `secrets/github-pat.sops` is deleted from the repo, but the compose reference and the
  PAT→App token-helper wiring remain.

So SP-00f's acceptance (`grep -rn 'toolsets.*all' deploy/` empty **and** no classic PAT in compose) is
**not yet green**. The negative-permission half is proven here; the compose half stays the executor's
EPIC-0 task.
