---
title: "Forensic audit of HANDOFF-2026-05-19.md"
target: docs/superpowers/HANDOFF-2026-05-19.md
audit_run: 2026-05-19 (same-day review, post-#64 merge; deep re-verification appended)
audited_against:
  - origin/main HEAD `1a2c6fc` (PR #64 merged 12:24Z)
  - live container stack (Phoenix API, Hermes container env, docker compose)
  - `gh` API for PRs/issues/branch protection
  - file presence on disk + git stash + dangling objects (full `git fsck --lost-found` sweep)
  - sibling Hermes submodule + specs/plans for spec-vs-code drift
status: passes 1 + 2 + deep re-verification complete — awaiting user approval
---

# Findings

Verified every claim in HANDOFF-2026-05-19.md against current repo + live stack reality. The doc is **substantially accurate where it matters** (functional state, hook contract, secrets, branch protection) but has **two material defects** that will mislead the next session in concrete ways.

## Scorecard

| Section | Claim density | Verified | Discrepancies |
|---|---:|---:|---:|
| §1 Authoritative state | 9 rows | 7 ✓ | **2 errors** (open issues list wrong; HEAD stale by one PR — excusable) |
| §2 Phase 1.0.1 deliverables | 14 rows | 14 ✓ | 0 |
| §3 Hotfix PR series | 8 PRs | 8 ✓ | 1 cosmetic (PR #61 paraphrase) |
| §4 Credential rotation | 4 rows | 4 ✓ structurally | Could not re-verify cleartext tokens — sops files all present |
| §5 Outstanding work | 3 issues + 3 live-stack gaps + 17 SDLC items | 6 ✓ + 17 not independently verifiable (see §6 defect) | The 17 SDLC items depend on the master synthesis file that doesn't exist |
| §6 Reference deliverables | 10 file paths | **3 ✓ / 7 ✗** | **`audit/phase1-to-phase2-readiness-2026-05-19/` directory does not exist on disk or in git history** |
| §7 Critical facts | 6 callouts | 5 ✓ | 1 misleading (compose has `/root/.hermes` mounts — actually only in comments) |
| §8 Verification checklist | 6 commands | 6 ✓ | All run clean as written |
| §9 Recommended sequencing | 3 paths | n/a (forward-looking) | Sound, no defects |
| §10 Security note | n/a | ✓ | Accurate |
| §11 TL;DR | n/a | ✓ except points to non-existent SYNTHESIS.md | See §6 |

**Bottom line:** 4/9 numerically verifiable categories have at least one defect; 1 of those (§6) is severe because it makes §5.3 + §11 unactionable for a fresh session.

---

## What the doc gets right (verified live)

### §1 confirmed
- `git log -1 main` → `1a2c6fc` (the handoff PR #64 itself; doc was written at HEAD=`188794d`, which is one PR back — fine)
- `git tag --list "phase*"` → `phase1-accepted`, `phase1.0.1-accepted` ✓
- `docker ps` → 7 services Up + `volume-init` Exited (0) = 8 services ✓ (hermes, litellm-proxy, github-mcp, escalation-watcher, phoenix, shell-sandbox, otel-collector, volume-init)
- `docker exec hermes id` → `uid=1000(hermes) gid=1000(hermes)` ✓
- `docker exec hermes touch /data/probe` → succeeds ✓
- `docker exec hermes touch /home/hermes/.hermes/probe` → succeeds ✓
- Hook errors in last 24h → **0** (grep returned empty) ✓
- `.venv/bin/pytest tests/unit/` → **227 passed, 1 skipped** in 0.51s ✓ (exact match)
- `bash scripts/smoke.sh` → **✅ All 8 smoke checks passed** ✓
- `gh pr list --state open` → `[]` (0 open PRs) ✓

### §3 PR series — all 8 hashes match `git log` exactly
| PR | Hash | Doc title (abridged) | Actual title |
|---|---|---|---|
| #56 | `c38b478` | match Hermes invoke_hook(**kwargs) — KEYSTONE | fix(durability): match Hermes invoke_hook(**kwargs) contract ✓ |
| #57 | `acb517e` | DR scripts service-name rename | fix(dr): update DR scripts + runbook for service rename + removed services ✓ |
| #58 | `1ca670d` | wire Checkpoint into post_tool_call | feat(durability): wire Checkpoint.maybe_write into post_tool_call hook ✓ |
| #59 | `62a5dbb` | implement slash commands | feat(anchors): implement slash commands (/lock /skip /cancel /confirm /new) ✓ |
| #60 | `b0c480c` | non-root + image digests + scrubber wiring | chore(security): non-root container + image digest pinning + scrubber wiring ✓ |
| #61 | `17db4ab` | fill hook bodies + Telegram send + escalation path | feat(kanban): fill hook bodies + wire real Telegram send + path rename ⚠ |
| #62 | `d4b0968` | override hermes-image ENTRYPOINT for escalation-watcher | fix(deploy): override hermes-image ENTRYPOINT for escalation-watcher sidecar ✓ |
| #63 | `188794d` | rotate 4 leaked credentials | chore(secrets): rotate 4 leaked credentials (closes #88) ⚠ |

PR #61 cosmetic — git commit log title matches doc; gh API title diverges (the squash-merge commit message used "escalation path", the PR title was rewritten to "path rename"). Pick one canonical phrasing if it matters. PR #63's title contains `(closes #88)` — directly relevant to the §1 issue-list defect below.

### §7 critical facts — all source lines exist
- `hermes-agent/hermes_cli/plugins.py:1253` → `def invoke_hook(self, hook_name: str, **kwargs: Any) -> List[Any]:` ✓
- `hermes-agent/hermes_cli/plugins.py:25` → "The agent core calls ``invoke_hook(name, **kwargs)`` at the appropriate" ✓
- `hermes-agent/hermes_cli/plugins.py:747-905` → 4-source scanner (bundled / user / project / entry-points) exactly as described ✓
- `lib/durability/__init__.py` docstring explicitly cites the kwargs contract + the PR #56 KEYSTONE backstory ✓
- `lib/durability/trichotomy.py` docstring cites the same contract + the `**_` absorber pattern ✓
- `lib/observability/__init__.py` docstring confirms it's the reference pattern ✓
- `deploy/docker-compose.yml:307` → `hermes-data:/home/hermes/.hermes` ✓ (rebased from `/root/.hermes` exactly as §7.3 states)
- `deploy/Dockerfile.hermes` exists (doc didn't cite a path — fine)

### §7.6 branch protection — verified via `gh api`
- `required_status_checks.contexts`: **11 contexts** matching the doc's enumeration exactly ✓
- `allow_force_pushes`: false ✓
- `required_conversation_resolution`: true ✓
- Doc-flagged gaps confirmed: `enforce_admins.enabled: false`, `required_signatures.enabled: false`, `required_approving_review_count: 0` (all correctly listed in §5.3 as remaining enterprise items)

### §4 secrets — all 4 sops-encrypted files present in `secrets/`
- `chroma-cloud.env.sops`, `github-pat.sops`, `healthchecks-url.sops`, `telegram.env.sops` ✓
- Cleartext counterparts `chroma-cloud.env`, `github-pat`, `healthchecks-url`, `telegram.env` exist at mode 0600 + are gitignored (verified earlier by `git status` returning clean)
- Live verification of token validity was done at rotation time (PR #63 description); not re-verified here to avoid printing tokens

### §8 checklist — every command in the doc runs clean
All 6 verification commands listed in §8 produce the expected output exactly as the doc says they will. No drift.

---

## What the doc gets wrong

### DEFECT-1 (P0): §6 reference deliverables — master audit directory does not exist
**Severity:** Blocking for any fresh session that follows §11's instruction to "Open `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md`."

**Claim:** §6 lists 7 files under `audit/phase1-to-phase2-readiness-2026-05-19/`:
- `SYNTHESIS.md` (master prioritized list — also pointed at from §5.3 + §11)
- `security-audit.md`
- `quality-audit.md`
- `cicd-audit.md`
- `observability-reliability-audit.md`
- `docs-architecture-audit.md`

**Reality:**
```
$ find audit -maxdepth 2 -type d
audit
audit/phase1-completion-sweep-2026-05-18

$ git log --all --diff-filter=A --name-only -- "audit/phase1-to-phase2-readiness-2026-05-19/*"
(empty — never been added to git)
```

The directory does not exist on disk, was never committed, and has never appeared in any branch.

Additionally, `audit/phase1-completion-final-sweep-2026-05-18/` (also cited in §6) does not exist — only `audit/phase1-completion-sweep-2026-05-18/` (without `-final`) is present, and it does contain `findings.md` + `audit-plan.md` as the doc claims.

**Downstream impact:**
- §5.3's "17 P1/P2 enterprise SDLC items" claim to be "catalogued in `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md`." That master catalog doesn't exist — the only enumeration of those 17 items is the bullet list in §5.3 itself.
- §11 directs the next session to open `SYNTHESIS.md` "for the master prioritized list." That file doesn't exist.
- §6's enumeration of 5 sub-reports is fiction; no security-audit, quality-audit, cicd-audit, observability-reliability-audit, or docs-architecture-audit document exists at the cited path or any other path under `audit/`.

**Most likely cause:** During the audit-then-implement workflow that produced Phase 1.0.1, the synthesis was done in-conversation and never written to disk — only the hotfix PRs and this handoff doc carry the synthesized knowledge forward. The doc author then wrote §6 assuming the audit artifacts had been persisted alongside the prior `phase1-completion-sweep-2026-05-18/` directory.

**Fix options (decide before any §5.3 work begins):**
1. **Reconstruct the master synthesis** by re-running the audit at HEAD `1a2c6fc` and writing `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md` + the 5 sub-reports. ~6-10h.
2. **Promote §5.3 of the handoff to the canonical catalog** — copy the 17-item list into a standalone `audit/phase1.2-enterprise-sdlc/audit-plan.md`, then patch §6 + §11 to point there. ~1h.
3. **Patch the handoff with the truth** — remove §6's fictional sub-reports + redirect §5.3 and §11 to use the actual §5.3 bullet list as the source. ~15 min, accepts the loss of finer-grained sub-report detail.

Recommended: option 2 (preserves the 17-item list as the actionable artifact without requiring an audit re-run).

### DEFECT-2 (P1): §1 open-issues list is wrong + tells next session to "manually close" a non-existent issue
**Severity:** Will waste 10 minutes of a fresh session and reduces trust in the rest of the doc.

**Claim (§1, row "Open issues"):** "4 — `#53`, `#54`, `#55` (Phase 1.1 deferred), `#88` (rotation — should be closed; PR #63 didn't auto-close because the keyword wasn't in body. Manually close it.)"

**Reality:**
```
$ gh issue list --state open
#53  Phase 1.1: emit OpenInference attributes ...
#54  Phase 1.1: wire Honcho persistent memory ...
#55  Phase 1.1: attach DB to LiteLLM proxy ...
#50  AutonomousAgent is DOWN

$ gh issue view 88
GraphQL: Could not resolve to an issue or pull request with the number of 88.
```

Three errors in one sentence:
1. **Issue #88 does not exist** — never has. (`gh issue view 88` errors; `git log --all` shows no reference.)
2. **PR #63's title DOES contain a closing keyword**: `chore(secrets): rotate 4 leaked credentials (closes #88)`. The doc's claim "PR #63 didn't auto-close because the keyword wasn't in body" is contradicted by the PR's own title.
3. **Issue #50 ("AutonomousAgent is DOWN") is open and unmentioned.** This issue was filed at 2026-05-18 20:15Z when healthchecks pinged failure (the same outage that drove the Phase 1.0.1 hotfix series). It is almost certainly resolved now — smoke 8/8 + Healthchecks URL rotated and verified live in §4 — but nothing in this session has actually closed the issue.

**Cumulative effect:** §1's open-issues count (4) happens to match reality, but every individual issue number in the list is either wrong or missing.

**Fix:**
1. Close #50 with a comment linking to PR #63 + the smoke result.
2. Patch §1 to read "Open issues — 3 — `#53`, `#54`, `#55` (Phase 1.1 deferred)" once #50 is closed.
3. Delete the entire "#88 (rotation — should be closed; PR #63 didn't auto-close ...)" sentence.

### DEFECT-3 (P2): §7.3 misstates the compose state — `/root/.hermes` paths are comments only
**Severity:** Could send a fresh session looking for live config that doesn't exist; minor confusion only.

**Claim (§7.3):** "Our 6 plugins are mounted to `/root/.hermes/plugins/<name>` per PR #48 + path-rebased to `/home/hermes/.hermes/plugins/<name>` per PR #60 (non-root). **The compose has BOTH because of how the volume mounts overlay HOME.** Read `deploy/docker-compose.yml` hermes service before assuming."

**Reality:** `grep -nE "hermes-data|/home/hermes/.hermes|/root/.hermes|/data:" deploy/docker-compose.yml` shows that every `/root/.hermes` reference in the file is **inside a comment block (lines 215-229)** explaining the PR #60 migration. Every actual mount path uses `/home/hermes/.hermes`. The compose does NOT have both as live mounts.

**Fix:** Change the bolded sentence to: "The compose has explanatory comments referencing the legacy `/root/.hermes` migration but all live mounts use `/home/hermes/.hermes`."

### DEFECT-4 (cosmetic, info): §3 PR #61 — title paraphrase diverges from current gh API title
The git squash-commit subject line matches the doc ("escalation path"), but the PR's web-displayed title was renamed to "path rename" after merge. Not a real defect — just pick one canonical version if you re-publish §3.

---

## What the doc didn't cover but should

These aren't defects in what the doc says, but a fresh session would benefit from them being there:

1. **No mention of the handoff PR itself (#64).** A fresh session running `git log -1` will see `1a2c6fc docs(handoff): ...` and may briefly wonder if there's been further drift. A one-line note in §1 ("HEAD will be `1a2c6fc` after the handoff PR #64 merges; the values in this table are pre-#64") would close that loop.
2. **No live verification of the §4 cleartext tokens.** §4 says all 4 were "verified" but doesn't say HOW the next session can re-verify without exposing the tokens. A boxed example like `scripts/decrypt-secrets.sh && telegram_token=$(grep TELEGRAM secrets/telegram.env | cut -d= -f2) && curl -s "https://api.telegram.org/bot${telegram_token}/getMe"` (without printing the token) would be the right shape.
3. **No callout that `lib/durability/escalation.py` exists** — the §7.1 list of kwargs-receiving hook modules names `__init__.py` and `trichotomy.py` but the durability plugin has 5 submodules total (`failure_matrix`, `trichotomy`, `escalation`, `checkpoint`, `resume`). If a fresh session is making a hook-signature change they need to update all of them.

---

## Verification commands (re-runnable)

Every check that produced the findings above can be re-run with the commands below; copy-paste safe.

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"

# §1 — HEAD, tags, open issues, open PRs
git log -1 --format='%H %s' main
git tag --list "phase*"
gh issue list --state open --json number,title,createdAt
gh pr list   --state open --json number,title

# §3 — verify all 8 PRs by hash
for h in c38b478 acb517e 1ca670d 62a5dbb b0c480c 17db4ab d4b0968 188794d; do
  git log -1 --format='%h %s' "$h"
done

# §6 — verify the master audit dir actually exists (this is the failing check)
ls audit/phase1-to-phase2-readiness-2026-05-19/ 2>&1 || echo "MISSING"
git log --all --diff-filter=A --name-only -- "audit/phase1-to-phase2-readiness-2026-05-19/*" || echo "never added"

# §7.1 — verify the cited line numbers
grep -n "def invoke_hook" hermes-agent/hermes_cli/plugins.py
sed -n '25p' hermes-agent/hermes_cli/plugins.py

# §7.3 — verify the BOTH-paths claim
grep -nE "/root/.hermes" deploy/docker-compose.yml   # all in comments

# §7.6 — verify branch protection
gh api repos/Manzela/AutonomousAgent/branches/main/protection \
  | jq '{contexts: .required_status_checks.contexts, enforce_admins: .enforce_admins.enabled,
         signatures: .required_signatures.enabled, reviews: .required_pull_request_reviews.required_approving_review_count}'

# §1 / §8 — live container + hook + tests + smoke
docker ps --format "{{.Names}}\t{{.Status}}" --filter "name=autonomous-agent"
docker exec autonomous-agent-hermes-1 id
docker logs autonomous-agent-hermes-1 --since 24h 2>&1 | grep -ciE "Hook.*raised|TypeError|positional argument"
.venv/bin/pytest tests/unit/ -q | tail -3
bash scripts/smoke.sh | tail -3

# Issue #88 sanity
gh issue view 88   # will error — confirming it doesn't exist
gh issue view 50   # will show OPEN — confirming the missing entry
```

---

## To enrich in pass 2 (originally)

Pass 1 was codebase-only and ran clean within the time budget. Pass 2 cross-checked:
1. The 17 enterprise SDLC items in §5.3 against `audit/phase1-completion-sweep-2026-05-18/`
2. The sibling Hermes submodule for the cited security commit `62573f44c`
3. The spec + plan documents at `docs/superpowers/specs/` and `docs/superpowers/plans/`
4. The §5.3 enterprise-SDLC claims against live GitHub API + workflow files

---

## Changes from pass 1 (pass 2 results)

Pass 2 added **one new material defect (P1-2)** and **refined two numeric drifts in §5.3 (P2-3)**. None of the pass-1 P0/P1 findings were invalidated.

### New finding: P1-2 — spec contradicts live code (evaluator threshold type)

Most consequential pass-2 discovery. The spec at `docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md` §4.2 says `accept_threshold: 3` (int — judge count). Live code uses `accept_threshold: 0.75` (float — percentage). The handoff's §6 cites the spec as authoritative, but the spec is a type-incompatible mismatch with the code that ships. A fresh session writing Phase 1.1 code from the spec will introduce a real bug. Added to `audit-plan.md` as **P1-2** with three fix options; recommended option is a `## Drift from spec` section in the handoff demoting the spec to "design intent reference only."

Two lower-stakes spec divergences found alongside (bundled into P1-2's writeup):
- Spec specified 3 parallel sessions (c/d/e); reality was single consolidated PR #49.
- Spec described 4 isolation PRs (α-0.1 to α-0.4); reality is 8 hotfix PRs (#56-#63).

### Refined finding: P2-3 — two numeric drifts in §5.3

Pass 1 took §5.3's numbers at face value. Pass 2 verified them:

| §5.3 claim | Verified value | Verdict |
|---|---|---|
| README claims 12 services; reality is 7 | README does say "twelve services" (`README.md:55`); reality is **8** | "reality is 7" wrong by 1 — should be 8, matching §1's own claim. Internal inconsistency in the handoff. |
| Hermes submodule 718 commits behind | Actual `git log --oneline ddb8d8f..62573f44c \| wc -l` = **715** | Off by 3. Immaterial to recommendation. |
| Hermes security fix at upstream `62573f44c` | Commit exists, title: "fix: guard yaml.safe_load, flock unlock, TOCTOU races, and atomic writes" | ✓ verified, accurately framed as security-relevant. |
| CodeQL + GHAS all OFF | Code Scanning HTTP 403, Secret Scanning HTTP 404, Dependabot HTTP 403 | ✓ verified — all three confirmed disabled. |
| All third-party Actions on floating tags | `grep "@[a-f0-9]{40}"` of `.github/workflows/` returns 0 | ✓ verified — 0 SHA-pinned actions. |

P2-3 captures the two drifts (README service count + submodule commit count). P0-1 fix (promote §5.3 to canonical catalog) should incorporate these corrections.

### Reinforced finding: P0-1 — recommended fix is option 2 (promote §5.3)

Pass 2 independently arrived at the same recommendation as pass 1: rather than reconstructing the master synthesis from scratch, promote §5.3 to the canonical Phase 1.2 enterprise-SDLC catalog. The earlier 2026-05-18 audit only covered ~5 of the 17 §5.3 items; the other ~10 (Trivy, SBOM, cosign, SCA, Action pinning, branch protection tightening, metrics, error tracking, 10 ADRs, Phase 2 plan) are net new — reconstructing the synthesis from the earlier audit would be regressive. §5.3 is the most complete catalog the project has; promoting it preserves all 17 items.

### No invalidations

All pass-1 findings (P0-1 missing audit dir, P1-1 wrong issues list, P2-1 misleading compose claim, P2-2 cosmetic PR #61 title diff) remain valid as written. Pass 2 confirmed the live-stack state matches §1 (8 containers, hermes uid=1000, /data + /home/hermes/.hermes writable, 0 hook errors, 227 unit + 1 skip, smoke 8/8).

---

## Changes from deep re-verification (4 parallel subagent sweep)

The user pushed back on pass 2 with "Are you sure? Think deep. Take your time. Fan out subagents. No false-positives, no false-negatives." A 4-agent parallel verification surfaced one **reversal** (P0-1 dir partially recoverable), one **refutation** of a handoff claim that I had taken on faith (§5.2 daily-reset), one **downgrade** of a previously-VERIFIED §2 row to PARTIAL, and additional spec/code drift cases.

### Reversal: P0-1 — directory is recoverable from `git stash@{0}`

My pass-1 + pass-2 statement that "`audit/phase1-to-phase2-readiness-2026-05-19/` … was never committed and has never appeared in any branch" was **technically true for the on-disk tree and all named refs** but **missed the stash**. The standard `git stash show --name-only stash@{0}` only lists tracked changes; stashes also carry a separate "untracked-files" sub-commit that is invisible to that command.

**Found via:** `git fsck --lost-found` to enumerate reachable but unnamed commits, then `git ls-tree -r f2f8a6a` on the untracked-files sub-commit of `stash@{0}`.

**Stash inventory** (commit `f2f8a6a037e3d89f8a958f539f2eeb224bb2a490`):

| Path | Blob SHA | Size |
|---|---|---|
| `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md` | `51439869` | 12,751 B |
| `audit/phase1-to-phase2-readiness-2026-05-19/cicd-audit.md` | `3ed625b7` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/coverage-combined.txt` | `18fa9c43` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/coverage-unit.txt` | `9e9fee49` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/docs-architecture-audit.md` | `a5259b7e` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/observability-reliability-audit.md` | `82a649f3` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/quality-audit.md` | `937c7316` | — |
| `audit/phase1-to-phase2-readiness-2026-05-19/security-audit.md` | `191bce8d` | — |
| `audit/phase1-completion-final-sweep-2026-05-18/audit-plan.md` | `13ef660f` | — |
| `audit/phase1-completion-final-sweep-2026-05-18/findings.md` | `233e4cf4` | — |

The "`-final-sweep-`" sibling directory (the one I noted in P0-1 as cited in §6 but not on disk) is **also** in the stash. Same fix applies.

**Important caveats:**
- These blobs live in the git object store and are reachable only via `stash@{0}`. If the user runs `git stash drop stash@{0}` they become dangling. If they then run `git gc --aggressive` or `git gc --prune=now` they are deleted permanently.
- The stash message is `untracked files on chore/security-hardening: c38b478 fix(durability): match Hermes' invoke_hook(**kwargs) ... (#56)` — i.e. these were collected during pre-PR-#56 hygiene, then never committed.
- **Recovery is one command** but should happen before any `git gc`: `git checkout f2f8a6a -- audit/phase1-to-phase2-readiness-2026-05-19/` (or extract via `git show f2f8a6a:audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md > target` per file).

**Net effect on P0-1:** still P0 (the handoff is still wrong about disk presence, fresh session still hits a wall running `ls audit/phase1-to-phase2-readiness-2026-05-19/`), but the **fix path is much cheaper** than pass-1 estimated. Instead of "option 2: promote §5.3 to canonical catalog (~1h)" the leading option is now "rescue from stash to a branch, then commit to main (~10 min)". See revised `audit-plan.md` for the rewritten P0-1.

### Refutation: §5.2 "Daily 4am session reset" — no such config exists

My pass-1 + pass-2 took §5.2's bullet at face value. The deep sweep ran the exact grep the handoff recommends (`grep -rn "daily.*reset\|session.*reset" config/ docs/`) and found **0 matches**. The only 04:00 cron in the codebase is `gcs_snapshot_cron: "0 4 * * *"` at `config/limits.yaml:46` — a **GCS object-storage snapshot job**, not a session reset.

A fresh session that follows §5.2's diagnostic instruction will:
1. Run the grep → empty output
2. Be unsure whether the claim is wrong or the config has just been renamed
3. Either chase a ghost or quietly skip the item

**The bullet should be either removed or rewritten** to reflect what the cron actually does (snapshot vault to GCS daily). If a 48h-job-hostile reset actually exists somewhere, it's not in `config/`, `docs/`, or `deploy/` — and certainly not at 04:00.

This refutation also affects §5.3's "10 missing ADRs" subline at line 116, which lists "daily session reset" as one of the post-Phase-1 decisions needing an ADR. If the thing doesn't exist, no ADR is owed.

### Downgrade: §2 row 10 — Phoenix spans VERIFIED → **PARTIAL**

Pass 1 marked row 10 (`Phoenix emits turn.start / model.call / tool.dispatch from hermes-agent`) as ✓. Deep re-verification queried the live Phoenix API directly:

```
curl -s 'http://localhost:6006/v1/projects/UHJvamVjdDox/spans?limit=500'
→ 36 spans total
→ 0 named 'turn.start', 'model.call', or 'tool.dispatch'
→ all 36 have span_kind=UNKNOWN
→ 0 have service.name set
→ all 36 are from LiteLLM (attributes: gen_ai.*, llm.*, litellm.call_id, proxy_pre_call, etc.)
```

The code wiring **is** correct — `lib/observability/__init__.py` lines 99 (`turn.start`), 152 (`tool.dispatch`), 225 (`model.call`) all start spans via `_tracer.start_span(...)`, and the Hermes container has `OTEL_SERVICE_NAME=hermes` + `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` set. But the live database **contains zero** of these spans. Two possible causes:

1. **No Hermes session has actually fired since the last container restart.** The plugin loads, the hooks register, but `on_session_start` / `pre_tool_call` etc. only fire when a user actually invokes the agent. Phoenix appears to have been populated entirely by LiteLLM's startup self-trace + a small number of completions that didn't go through Hermes.
2. **OTel forwarding from Hermes to otel-collector is broken downstream of the wiring** (less likely given the LiteLLM spans are getting through the same collector).

Either way, the handoff's "✅ (PR #52 + verified live spans)" claim is currently **not verifiable against live state**. The acceptance proof referenced in PR #52 was probably valid at the time but is no longer reproducible. Fix is to either trigger one Hermes session and re-check, or downgrade the row to "wired + code-tested; live span emission pending session activity since last restart."

### Refinement: §1 / §5.3 service-count internal inconsistency

§1 row "Live container state" says **8 services**. §5.3 says **"README claims 12 services; reality is 7"**. Both are arithmetically correct under different definitions:

- **7** = `docker compose ps` (long-running only; volume-init exits 0 immediately after chown)
- **8** = `docker compose config --services` (defined services, including volume-init)

This is **internal inconsistency in the handoff itself** — the number you use should depend on what you're communicating. Recommended: §5.3 should match §1's framing (8 defined services, 7 long-running after volume-init exits). README's "twelve services" is still wrong by either count.

### Expansion: P1-2 — 4 additional spec/code drifts beyond `accept_threshold`

Pass 2 surfaced the `accept_threshold` int-vs-float drift. The deep sweep found 4 more, all in the same spec/plan pair:

**(a) `reject_threshold` — same int-vs-float drift**
- Spec line 188: `reject_threshold: 3` (int)
- `config/limits.yaml:154`: `reject_threshold: 0.75` (float)
- `lib/evaluators/consensus.py:60,89`: float comparison

**(b) `lib/anchors/__init__.py:55` citation obsolete in spec + plan**
- Spec line 304 + 401 cite `lib/anchors/__init__.py:55` as the location of the `TODO(P1-5)` stub for `/cancel <id>`
- Plan lines 1903, 1944, 1963 same citation
- Reality: anchors `__init__.py` line 55 is now error-handling code (`return _FALLBACK_STORAGE_DIR` inside a try-block). The `/cancel` handler with the `<id>` dispatch is at line ~259 (`def _slash_cancel(raw_args: str) -> str:`) — and the `TODO(P1-5)` stub is **already implemented**, not pending. Anyone using the spec to "find the TODO" will land on unrelated code.

**(c) Kanban DB path drift in spec + plan**
- Spec line 304: mount `hermes-data:/root/.hermes/kanban` for SQLite persistence
- Plan line 1267-1269: `KANBAN_DB_PATH = os.environ.get("HERMES_KANBAN_DB", "/root/.hermes/kanban/kanban.db")`
- Reality: `lib/durability/escalation.py:21` + `lib/kanban/telegram_bridge.py:57` + `config/limits.yaml:188` + `scripts/snapshot.sh` all use `/home/hermes/.hermes/kanban.db` (new HOME post PR #60, **and no `kanban/` subdir**). Comment in `config/limits.yaml:186` explicitly calls this out: "verified live ... NOT a kanban/ subdir".

**(d) `lib/durability/escalation.py` location claim — actually correct**

Pass-2's hand-off summary listed this as a drift; deep re-verification disconfirms. Spec line 214 says `lib/durability/escalation.py — 24h Telegram silence watcher`; the file exists at that exact path with that exact purpose. No drift.

Net new P1-2 entries: 3 substantive (a/b/c), 1 false-positive correction (d).

### Confirmation: Telegram bot identity matches §4

Deep sweep ran a non-token-printing live verification:

```
. secrets/telegram.env && curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
→ ok=True, id=8911196639, username=Manzelagent_bot, first_name=AutonomousAgent
```

The id `8911196639` begins with `8911` — matching §4's "first 4" digest `8911...mrLQ`. The username + display name match the handoff's claim of the bot identity. **§4's Telegram row is independently verified live**, not just structurally.

### Confirmation: stash @{0} also rescues `audit/phase1-completion-final-sweep-2026-05-18/`

P0-1 noted §6 line 130 cites `audit/phase1-completion-final-sweep-2026-05-18/{findings,audit-plan}.md` (with `-final-sweep-`) but only `audit/phase1-completion-sweep-2026-05-18/` (without `-final-`) is on disk. The stash recovery for the readiness directory **also recovers the `-final-sweep-` directory** — same commit, same fix. Two birds.

### Pass-1 claims that survived deep re-verification

To be explicit about what was *not* invalidated:

- §1 live-stack rows (HEAD, tags, 8 services, hermes uid=1000, /data writable, /home/hermes/.hermes writable, 0 hook errors, 227 passed + 1 skipped, smoke 8/8, 0 open PRs) — all still verify clean
- §3 all 8 PR hashes (#56-#63) — all still match git log
- §4 credential rotation structural (4 sops files + 4 cleartext at 0600 + gitignored); Telegram now also live-verified per above
- §7.1 `invoke_hook(self, hook_name, **kwargs)` at `plugins.py:1253`; docstring at line 25 ✓
- §7.3 compose path `/home/hermes/.hermes` ✓ (with P2-1 pass-1 correction about the misleading "compose has BOTH" sentence)
- §7.6 branch protection (11 contexts, force-pushes false, conv resolution true, enforce_admins false, signatures false, reviews 0) — all still match `gh api`
- §8 all 6 verification commands still run clean

**Pass-1 net accuracy after deep re-verification:** core technical claims are sound; the defects are concentrated in §1 (issue list), §2 row 10 (live spans), §5.2 (ghost reset config), §5.3 (numeric drifts + service count), §6 (directory presence — recoverable), §7.3 sentence (compose comments). The handoff is **structurally trustworthy** but needs the targeted edits in `audit-plan.md`.

### What this audit still does not verify

- Whether the `f2f8a6a` stash will survive arbitrary user actions (`git gc`, `stash drop`, repo re-clone) — the rescue should be treated as time-critical. If the user runs `git stash drop stash@{0}` before rescue, the blobs become dangling and `git gc --prune=now` will erase them.
- Whether triggering one fresh Hermes session would in fact emit the §2 row 10 spans. Out of audit scope (would require sending a real prompt to the agent).
- Whether the 17 enterprise-SDLC items in §5.3 individually verify against current state. P0-1 fix (rescue + promote the SYNTHESIS) makes this verification possible without re-doing the audit.
