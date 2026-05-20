# Wave-3 P2 Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the Wave-3 P2 backlog (P2-1, P2-2, P2-4, P2-6, P2-7, P2-8) by dispatching 5 worktree-isolated implementer subagents in parallel, then verifying and merging in dependency order. Closes the audit at `audit/2026-05-19-resume-orchestration/audit-plan.md §P2`.

**Architecture:** Orchestrator (this session) handles pre-flight ops, fan-out, verification, merge sequencing, and the post-merge `allowed_actions` API flip. Five `general-purpose` subagents each run in `.claude/worktrees/` on isolated branches, follow a uniform prompt contract (spec §3), and return structured JSON. Spec at `docs/superpowers/specs/2026-05-20-wave-3-p2-dispatch-design.md`.

**Tech Stack:** git worktrees, GitHub CLI (`gh`), Claude Code Agent tool with `isolation: "worktree"`, GitHub Actions API, Docker Compose (for P2-7 smoke + P2-6 regression).

---

## Task 1: P2-2 Branch hygiene (user-gated deletion)

**Files:** none (local-only ref operations; no PR).

**Audit anchor:** `audit/2026-05-19-resume-orchestration/audit-plan.md §P2-2`.

- [ ] **Step 1: Capture all local branches and their merge status**

Run:
```bash
git fetch --prune origin
git for-each-ref --format='%(refname:short)|%(committerdate:iso8601)|%(authorname)' refs/heads/ \
  | sort > /tmp/wave3-local-branches.txt
wc -l /tmp/wave3-local-branches.txt
```
Expected: count ≈ 40+ lines (audit said 41 local-only branches).

- [ ] **Step 2: Identify branches already merged into origin/main (squash or otherwise)**

Squash-merge does NOT mark the source branch as merged in git, so we cross-check via GitHub's merged-PR list.

Run:
```bash
# Branches that git considers merged (non-squash)
git branch --merged origin/main | sed 's/^\*\?\s*//' | grep -vE '^(main|HEAD)$' > /tmp/wave3-merged-git.txt

# Branch names from all merged PRs (squash counts as merged here)
gh pr list --state merged --limit 200 --json headRefName --jq '.[].headRefName' | sort -u > /tmp/wave3-merged-prs.txt

# Union: any local branch whose name appears in either list is a candidate
git for-each-ref --format='%(refname:short)' refs/heads/ \
  | grep -vE '^(main|HEAD)$' \
  | sort > /tmp/wave3-local-only.txt

comm -12 /tmp/wave3-local-only.txt <(cat /tmp/wave3-merged-git.txt /tmp/wave3-merged-prs.txt | sort -u) \
  > /tmp/wave3-candidates.txt

wc -l /tmp/wave3-candidates.txt
cat /tmp/wave3-candidates.txt
```
Expected: candidate count is close to 41 (audit number) ± a few.

- [ ] **Step 3: Build the deletion-candidate report for user review**

Render the candidates as a table with branch name + last-commit date + matching PR (if any):
```bash
{
  echo "| Branch | Last commit | Matching PR | Source list |"
  echo "|--------|-------------|-------------|-------------|"
  while read -r branch; do
    date=$(git log -1 --format='%cs' "$branch" 2>/dev/null || echo "n/a")
    pr=$(gh pr list --state merged --search "head:$branch" --json number --jq '.[0].number // "—"')
    src=""
    grep -qx "$branch" /tmp/wave3-merged-git.txt && src="${src}git "
    grep -qx "$branch" /tmp/wave3-merged-prs.txt && src="${src}pr"
    echo "| $branch | $date | $pr | $src |"
  done < /tmp/wave3-candidates.txt
} > /tmp/wave3-deletion-report.md
cat /tmp/wave3-deletion-report.md
```

- [ ] **Step 4: Ask user to approve the deletion list**

Use AskUserQuestion to present the report and ask:
- Approve full list (delete all candidates)
- Approve with exclusions (user names branches to keep)
- Abort (skip P2-2 this pass)

If the user approves with exclusions, remove those names from `/tmp/wave3-candidates.txt` before Step 5.

- [ ] **Step 5: Delete approved branches with audit log**

```bash
{
  echo "Wave-3 branch deletion log — $(date -Iseconds)"
  echo "Candidates file: /tmp/wave3-candidates.txt"
  echo
  while read -r branch; do
    sha=$(git rev-parse "$branch" 2>/dev/null || echo "MISSING")
    if [ "$sha" = "MISSING" ]; then
      echo "SKIP $branch (already gone)"
      continue
    fi
    if git branch -D "$branch" 2>&1; then
      echo "DEL $branch @ $sha"
    else
      echo "FAIL $branch @ $sha"
    fi
  done < /tmp/wave3-candidates.txt
} | tee /tmp/wave3-deletion.log
```

- [ ] **Step 6: Verify deletion completed**

```bash
git for-each-ref refs/heads/ --format='%(refname:short)' | wc -l
```
Expected: roughly `(starting count) - (approved deletions)`.

- [ ] **Step 7: Move deletion log into the audit dir for traceability**

```bash
cp /tmp/wave3-deletion.log audit/2026-05-19-resume-orchestration/wave-3-branch-deletion.log
git add audit/2026-05-19-resume-orchestration/wave-3-branch-deletion.log
git commit -m "chore(audit): capture wave-3 branch deletion log (P2-2)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Pre-dispatch sanity checks

**Files:** none (read-only verification).

- [ ] **Step 1: Confirm working tree is clean**

Run:
```bash
git status --short
```
Expected: empty output (no uncommitted changes after Task 1's commit).

- [ ] **Step 2: Confirm local main matches origin/main**

```bash
git fetch origin main
git rev-parse main origin/main
```
Expected: both SHAs equal (either `ce4c344…` or whatever Task 1 advanced it to).

- [ ] **Step 3: Confirm no open PRs in flight**

```bash
gh pr list --state open --json number,title --jq '.[] | "\(.number) \(.title)"'
```
Expected: empty (zero open PRs). If any exist, document them in `/tmp/wave3-inflight-prs.txt` and re-confirm with user that Wave-3 fan-out is still safe to proceed.

- [ ] **Step 4: Confirm `gh` is authenticated as Manzela**

```bash
gh auth status 2>&1 | grep "Logged in"
```
Expected: line contains `Manzela`.

- [ ] **Step 5: Confirm no stale Wave-3 worktrees**

```bash
ls -la .claude/worktrees/ 2>/dev/null | grep -i wave-3 || echo "no stale wave-3 worktrees"
git worktree list
```
Expected: no entries matching `wave-3-*`. If any exist (left from a previous attempt), prune with:
```bash
git worktree remove --force .claude/worktrees/wave-3-<name>
git branch -D wave-3/<branch> 2>/dev/null || true
```

- [ ] **Step 6: Confirm Docker Compose is available (needed for Task 5 + Task 7 smoke/regression)**

```bash
docker compose version
docker compose ps --format '{{.Service}} {{.State}}'
```
Expected: compose version printed; service list shows current state. Note running services for later comparison.

- [ ] **Step 7: No commit (read-only task)**

---

## Task 3: Fan out 5 implementer subagents (single tool block)

**Files:**
- Read: `docs/superpowers/specs/2026-05-20-wave-3-p2-dispatch-design.md` §3 (uniform prompt template) + §4 (per-PR mandates)
- Read: `audit/2026-05-19-resume-orchestration/audit-plan.md` §P2 (canonical scope)
- Write: `audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json` (Task output; consumed by Tasks 4–8)

**Critical constraint:** All 5 `Agent` tool calls MUST be in a single assistant message tool-block so they run in parallel. Sequential dispatch defeats the purpose.

- [ ] **Step 1: Construct each subagent's prompt by composing template (spec §3) + per-PR mandate (spec §4.x)**

Each prompt has the same outer shell (verbatim from spec §3) with two slots filled in:
- `[PR-ID]` — one of `P2-1`, `P2-4`, `P2-6`, `P2-7`, `P2-8`
- `[per-PR mandate]` — verbatim copy of the matching subsection from spec §4

The 5 PR mandates are in spec §4.1 (P2-1), §4.2 (P2-4), §4.3 (P2-6), §4.4 (P2-7), §4.5 (P2-8). Copy each as-is.

Example: P2-4 prompt (fully rendered) looks like:

````
ROLE: Implementer subagent for P2-4 in autonomous-agent repo (Wave-3 P2).

CONTEXT:
- Repo: /Users/danielmanzela/RX-Research Project/AutonomousAgent
- Main HEAD: ce4c344 (Wave-2 complete)
- Audit anchor: audit/2026-05-19-resume-orchestration/audit-plan.md §P2-4
- LOCKED scope: do NOT touch anything outside this PR's mandate.

WORKTREE: You are running in an isolated worktree. The orchestrator created
it via `git worktree add` on branch `wave-3/handoff-7-3-correction` off origin/main.

MANDATE (one PR only):
- Where: docs/superpowers/HANDOFF-2026-05-19.md line 159
  (`### 7.3 Container HOME is /home/hermes, not /root (post PR #60)`).
- Background: carry-over from prior audit's DEFECT-3. Read
  audit/handoff-doc-2026-05-19-review/findings.md for the DEFECT-3 detail
  before proposing the correction.
- Out-of-scope: any other section of the handoff doc.
- Tests: N/A.
- Acceptance: diff ≤30 lines, only touches §7.3.
- Commit: `docs(handoff): correct §7.3 per DEFECT-3 carry-over (closes P2-4)`
- Branch: `docs/handoff-7-3-correction`

REQUIRED SKILLS (invoke in order):
1. superpowers:test-driven-development — N/A (skip; docs only)
2. superpowers:verification-before-completion — MANDATORY before claiming done
3. superpowers:requesting-code-review — self-review with code-review-excellence

CONVENTIONS (from repo memory):
- Conventional commit title: scoped, ≤72 chars
- Branch name regex: ^(feat|fix|chore|docs|refactor|test|perf)/[a-z0-9-]+$
- Squash-merge only
- 11 required CI checks must pass before merge
- Memory references:
  - [[repo_workflow_constraints]] for full workflow rules
  - [[phase_1_trap_warning]] do NOT re-merge origin/phase/1

VERIFICATION GATE (must complete BEFORE saying "done"):
- [ ] `git status` clean in worktree
- [ ] PR opened via `gh pr create`; URL captured
- [ ] CI is queued/running — paste `gh pr checks <num>` output
- [ ] Verification log posted as PR comment (what changed, why, how tested)
- [ ] No files modified outside mandated paths (`git diff origin/main --name-only`)

DO NOT:
- Merge your own PR
- Modify CLAUDE.md or memory files (orchestrator's job)
- Force-push, --no-verify, --amend a published commit
- Touch files outside your mandate

OUTPUT (return ONLY this JSON):
{
  "pr_id": "P2-4",
  "pr_url": "https://github.com/Manzela/AutonomousAgent/pull/NN",
  "pr_number": NN,
  "branch": "wave-3/handoff-7-3-correction",
  "files_changed": [...],
  "ci_status": "queued|pending|passing|failing",
  "verification_log_comment_url": "...",
  "blockers": [...] or []
}
````

Replicate the same shape for P2-1, P2-6, P2-7, P2-8, swapping in their mandates from spec §4.1, §4.3, §4.4, §4.5. The mandate text MUST be copied verbatim from the spec — do not paraphrase.

**Branch slugs per PR** (must match spec §4):
- P2-1: `docs/phase2-spec-codification`
- P2-4: `docs/handoff-7-3-correction`
- P2-6: `chore/hermes-submodule-bump`
- P2-7: `feat/disk-cleanup-plugin`
- P2-8: `docs/allowed-actions-runbook`

**Worktree names per PR** (used in Agent tool's `name` field):
- P2-1: `wave-3-phase2-spec`
- P2-4: `wave-3-handoff-7-3`
- P2-6: `wave-3-hermes-bump`
- P2-7: `wave-3-disk-cleanup`
- P2-8: `wave-3-allowed-actions`

- [ ] **Step 2: Dispatch all 5 agents in a single tool-block**

In one assistant message, emit 5 `Agent` tool calls with:
- `subagent_type: "general-purpose"`
- `isolation: "worktree"`
- `description: "Wave-3 [PR-ID] implementer"` (e.g. `"Wave-3 P2-4 implementer"`)
- `prompt`: the rendered prompt from Step 1
- `run_in_background: false` (we need their JSON returned)

DO NOT add a 6th call. DO NOT chain calls sequentially. The block MUST contain exactly 5 parallel `Agent` calls.

- [ ] **Step 3: Wait for all 5 agents to return**

Each agent returns its final message (which should be the JSON output specified in its prompt). Block on all 5; do not proceed until every one has returned (success or failure).

- [ ] **Step 4: Parse the JSON from each agent's return**

For each result, locate the JSON object in the agent's final message. If not at the very end, scan for a fenced ```json``` block or a `{ ... }` block whose top-level key is `pr_id`. Validate it has all required fields: `pr_id`, `pr_url`, `pr_number`, `branch`, `files_changed`, `ci_status`, `verification_log_comment_url`, `blockers`.

- [ ] **Step 5: Re-dispatch any malformed/failed agents (single one at a time)**

For each agent whose output:
- failed to parse as JSON, OR
- has `blockers` non-empty AND no PR opened, OR
- reported `ci_status: "failing"` with no fix attempted

Dispatch a single follow-up `Agent` call (NOT in parallel; sequentially after the first batch) with a corrective prompt:

```
ROLE: Repair agent for [PR-ID].
The prior attempt failed: [paste agent's final message verbatim].
PRIOR WORKTREE: still exists at .claude/worktrees/wave-3-<name>
on branch wave-3/<branch-slug>.
TASK: Continue from the prior state. Do NOT create a new worktree.
Resolve the blockers, ensure CI is at least queued, post the verification log
comment, and return the JSON output as originally specified.
```

- [ ] **Step 6: Write the consolidated roster to disk for downstream tasks**

```bash
mkdir -p audit/2026-05-19-resume-orchestration
cat > audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json <<'EOF'
{
  "wave": "3",
  "dispatched_at": "2026-05-20T<HH:MM:SS>Z",
  "main_head_at_dispatch": "<SHA from `git rev-parse origin/main`>",
  "prs": [
    <paste each agent's JSON object, comma-separated>
  ]
}
EOF
cat audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json | jq '.prs | length'
```
Expected: `5`.

- [ ] **Step 7: Commit the roster**

```bash
git add audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json
git commit -m "chore(audit): capture wave-3 PR dispatch roster

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Verify + merge P2-4 (handoff §7.3 correction)

**Files:** none modified locally; merges PR `<P2-4 pr_number>` on GitHub.

**Why P2-4 first:** smallest blast radius (≤30 line doc diff). Pattern-establishes the verify+merge loop for Tasks 5-8.

- [ ] **Step 1: Read PR number from roster**

```bash
P24_PR=$(jq -r '.prs[] | select(.pr_id == "P2-4") | .pr_number' \
  audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json)
echo "P2-4 PR: #$P24_PR"
```
Expected: a non-empty number (e.g. `88`).

- [ ] **Step 2: Fetch PR metadata**

```bash
gh pr view "$P24_PR" --json files,statusCheckRollup,mergeable,reviewDecision,title,headRefName \
  > /tmp/wave3-p2-4-pr.json
cat /tmp/wave3-p2-4-pr.json | jq '.'
```
Expected: `mergeable` is `MERGEABLE`; `statusCheckRollup` shows all checks `COMPLETED`/`SUCCESS`.

- [ ] **Step 3: Verify files match mandate (only the handoff doc)**

```bash
jq -r '.files[].path' /tmp/wave3-p2-4-pr.json
```
Expected: exactly one path = `docs/superpowers/HANDOFF-2026-05-19.md`.

If extra paths appear, abort merge and SendMessage the agent: "PR exceeds mandate — please trim to docs/superpowers/HANDOFF-2026-05-19.md only and force-push."

- [ ] **Step 4: Verify all 11 required CI checks green**

```bash
gh pr checks "$P24_PR" --required
```
Expected: every required check status is `pass`. If any fail or are pending, wait or address before proceeding.

- [ ] **Step 5: Read PR description + verification log comment**

```bash
gh pr view "$P24_PR" --json body --jq '.body'
gh api "repos/Manzela/AutonomousAgent/issues/$P24_PR/comments" --jq '.[] | .body' | head -100
```
Expected: PR body describes the §7.3 correction; verification log comment exists and shows what changed + why.

- [ ] **Step 6: Squash-merge**

```bash
gh pr merge "$P24_PR" --squash --auto
```
Expected: PR shows as merged within ~30s. Confirm:
```bash
gh pr view "$P24_PR" --json state,mergedAt --jq '.'
```
Expected: `state: "MERGED"`.

- [ ] **Step 7: Sync local main**

```bash
git checkout main
git pull --ff-only origin main
git log -1 --oneline
```
Expected: top commit is the squash merge of P2-4.

- [ ] **Step 8: Clean up the worktree**

```bash
git worktree remove --force .claude/worktrees/wave-3-handoff-7-3 2>/dev/null || true
git branch -D wave-3/handoff-7-3-correction 2>/dev/null || true
git worktree prune
```

- [ ] **Step 9: No new commit (PR already merged; only local state changed)**

---

## Task 5: Verify + merge P2-7 (disk-cleanup plugin) + smoke test

**Files:** none modified locally; merges PR `<P2-7 pr_number>`.

- [ ] **Step 1: Read PR number from roster**

```bash
P27_PR=$(jq -r '.prs[] | select(.pr_id == "P2-7") | .pr_number' \
  audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json)
echo "P2-7 PR: #$P27_PR"
```

- [ ] **Step 2: Fetch PR metadata**

```bash
gh pr view "$P27_PR" --json files,statusCheckRollup,mergeable,reviewDecision \
  > /tmp/wave3-p2-7-pr.json
jq '.' /tmp/wave3-p2-7-pr.json
```
Expected: `mergeable: MERGEABLE`; required checks green.

- [ ] **Step 3: Verify files match mandate**

```bash
jq -r '.files[].path' /tmp/wave3-p2-7-pr.json
```
Expected: `config/hermes/cli-config.yaml` plus possibly one test file. No other files.

- [ ] **Step 4: Required CI checks**

```bash
gh pr checks "$P27_PR" --required
```
Expected: all `pass`.

- [ ] **Step 5: Squash-merge**

```bash
gh pr merge "$P27_PR" --squash --auto
gh pr view "$P27_PR" --json state --jq '.state'
```
Expected: `MERGED`.

- [ ] **Step 6: Sync local main**

```bash
git checkout main && git pull --ff-only origin main
git log -1 --oneline
```

- [ ] **Step 7: Smoke-test: restart hermes container and check for `disk-cleanup loaded`**

```bash
docker compose up -d hermes
sleep 5
docker compose logs hermes 2>&1 | grep -i "disk-cleanup" || \
  docker compose logs hermes 2>&1 | tail -50
```
Expected: log line confirming `disk-cleanup` plugin loaded (text matches whatever the agent's integration test used).

If smoke test fails: do NOT auto-revert; investigate (the plugin may need a `disk-cleanup` directory to exist in `~/.hermes/plugins/` which depends on the Hermes submodule — coordinate with Task 7's bump).

- [ ] **Step 8: Clean up the worktree**

```bash
git worktree remove --force .claude/worktrees/wave-3-disk-cleanup 2>/dev/null || true
git branch -D wave-3/disk-cleanup-plugin 2>/dev/null || true
git worktree prune
```

---

## Task 6: Verify + merge P2-1 (Phase-2 spec)

**Files:** none modified locally; merges PR `<P2-1 pr_number>`.

- [ ] **Step 1: Read PR number from roster**

```bash
P21_PR=$(jq -r '.prs[] | select(.pr_id == "P2-1") | .pr_number' \
  audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json)
echo "P2-1 PR: #$P21_PR"
```

- [ ] **Step 2: Fetch PR metadata**

```bash
gh pr view "$P21_PR" --json files,statusCheckRollup,mergeable,reviewDecision,additions,deletions \
  > /tmp/wave3-p2-1-pr.json
jq '.' /tmp/wave3-p2-1-pr.json
```
Expected: `mergeable: MERGEABLE`; required checks green; `additions` is large (multi-hundred lines) since this is a system-of-record spec.

- [ ] **Step 3: Verify files match mandate**

```bash
jq -r '.files[].path' /tmp/wave3-p2-1-pr.json
```
Expected: exactly `docs/spec/phase2.md` (and possibly a `docs/spec/` README if the dir is new).

- [ ] **Step 4: Spot-check the spec covers the auditor-defined structure**

```bash
# Pull the PR's version of the file to /tmp
gh pr diff "$P21_PR" > /tmp/wave3-p2-1.diff
grep -cE "^\+## " /tmp/wave3-p2-1.diff
grep -E "5.layer|F-?code|ADR" /tmp/wave3-p2-1.diff | head -20
```
Expected: spec has multiple sections; mentions the 5-layer model, F-codes, and ADR appendix per spec §4.1.

- [ ] **Step 5: Required CI checks**

```bash
gh pr checks "$P21_PR" --required
```
Expected: all `pass`.

- [ ] **Step 6: Squash-merge**

```bash
gh pr merge "$P21_PR" --squash --auto
gh pr view "$P21_PR" --json state --jq '.state'
```
Expected: `MERGED`.

- [ ] **Step 7: Sync local main**

```bash
git checkout main && git pull --ff-only origin main
git log -1 --oneline
test -f docs/spec/phase2.md && wc -l docs/spec/phase2.md
```
Expected: file exists, multi-hundred lines.

- [ ] **Step 8: Clean up the worktree**

```bash
git worktree remove --force .claude/worktrees/wave-3-phase2-spec 2>/dev/null || true
git branch -D wave-3/phase2-spec-codification 2>/dev/null || true
git worktree prune
```

---

## Task 7: Verify + merge P2-6 (Hermes submodule bump) + regression re-verify

**Files:** none modified locally; merges PR `<P2-6 pr_number>`. Submodule pointer in repo advances.

**Why highest-risk:** submodule bump pulls 757 commits into the build. CI gate is necessary but not sufficient — also re-run the regression locally after merge.

- [ ] **Step 1: Read PR number from roster**

```bash
P26_PR=$(jq -r '.prs[] | select(.pr_id == "P2-6") | .pr_number' \
  audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json)
echo "P2-6 PR: #$P26_PR"
```

- [ ] **Step 2: Fetch PR metadata**

```bash
gh pr view "$P26_PR" --json files,statusCheckRollup,mergeable,reviewDecision \
  > /tmp/wave3-p2-6-pr.json
jq '.' /tmp/wave3-p2-6-pr.json
```
Expected: `mergeable: MERGEABLE`; required checks green.

- [ ] **Step 3: Verify files match mandate**

```bash
jq -r '.files[].path' /tmp/wave3-p2-6-pr.json
```
Expected: exactly two paths — the submodule pointer (`hermes-agent` shows as a `mode 160000` change in the diff) and the ADR file `docs/architecture/decisions/0001-hermes-submodule-bump-2026-05-19.md`.

- [ ] **Step 4: Read the ADR**

```bash
gh pr diff "$P26_PR" -- docs/architecture/decisions/0001-hermes-submodule-bump-2026-05-19.md | head -80
```
Expected: ADR documents source SHA `ddb8d8fa8`, target SHA (new), backward-compat tool-override flag at `016c772e7`, rollback procedure.

- [ ] **Step 5: Read the agent's regression-test PR comment**

```bash
gh api "repos/Manzela/AutonomousAgent/issues/$P26_PR/comments" --jq '.[] | .body' \
  | grep -A 200 -i "regression\|make test\|pytest" | head -200
```
Expected: full output of `make test` (or equivalent) showing zero failures.

- [ ] **Step 6: Required CI checks**

```bash
gh pr checks "$P26_PR" --required
```
Expected: all `pass`.

- [ ] **Step 7: Squash-merge**

```bash
gh pr merge "$P26_PR" --squash --auto
gh pr view "$P26_PR" --json state --jq '.state'
```
Expected: `MERGED`.

- [ ] **Step 8: Sync local main + submodule**

```bash
git checkout main && git pull --ff-only origin main
git submodule update --init --recursive hermes-agent
cd hermes-agent && git log -1 --oneline && cd ..
```
Expected: submodule at the new SHA (matches ADR's target).

- [ ] **Step 9: Re-verify regression locally (orchestrator sanity check)**

```bash
docker compose up -d hermes
docker compose exec -T hermes bash -c "cd /workspace && make test" 2>&1 | tee /tmp/wave3-p2-6-regression-rerun.log
echo "Exit: $?"
```
Expected: exit 0 (all tests pass).

If exit ≠ 0:
- Capture `/tmp/wave3-p2-6-regression-rerun.log` as evidence
- Open an issue: `gh issue create --title "Regression in hermes-agent bump (P2-6)" --body "$(cat /tmp/wave3-p2-6-regression-rerun.log)"`
- Roll back by reverting the merge commit:
  ```bash
  REVERT_SHA=$(git log --oneline | grep "$P26_PR" | head -1 | awk '{print $1}')
  git revert --no-edit "$REVERT_SHA"
  git push origin main
  ```
- Mark P2-6 as DEFERRED in the close-out.

- [ ] **Step 10: Clean up the worktree**

```bash
git worktree remove --force .claude/worktrees/wave-3-hermes-bump 2>/dev/null || true
git branch -D wave-3/hermes-submodule-bump 2>/dev/null || true
git worktree prune
```

---

## Task 8: Quiesce + verify + merge P2-8 (allowed_actions runbook PR)

**Files:** none modified locally; merges PR `<P2-8 pr_number>`.

**Critical pre-condition:** zero in-flight PRs and idle CI. The runbook PR is benign on its own, but Task 9 (the API flip immediately after) can break any workflow that's mid-run.

- [ ] **Step 1: Quiesce — confirm no other PRs open**

```bash
gh pr list --state open --json number,title
```
Expected: at most this one (P2-8) listed. If others appear, wait/handle them.

- [ ] **Step 2: Quiesce — confirm CI is idle**

```bash
gh run list --limit 5 --json status,name --jq '.[] | "\(.status) \(.name)"'
```
Expected: no `in_progress` or `queued` entries (all should be `completed`).

- [ ] **Step 3: Read PR number from roster**

```bash
P28_PR=$(jq -r '.prs[] | select(.pr_id == "P2-8") | .pr_number' \
  audit/2026-05-19-resume-orchestration/wave-3-pr-roster.json)
echo "P2-8 PR: #$P28_PR"
```

- [ ] **Step 4: Fetch PR metadata**

```bash
gh pr view "$P28_PR" --json files,statusCheckRollup,mergeable,body \
  > /tmp/wave3-p2-8-pr.json
jq '.' /tmp/wave3-p2-8-pr.json
```
Expected: `mergeable: MERGEABLE`; required checks green; body contains the categorized inventory.

- [ ] **Step 5: Verify files match mandate**

```bash
jq -r '.files[].path' /tmp/wave3-p2-8-pr.json
```
Expected: `docs/runbooks/allowed-actions-restriction.md` only.

- [ ] **Step 6: Read the runbook + inventory**

```bash
gh pr diff "$P28_PR" -- docs/runbooks/allowed-actions-restriction.md > /tmp/wave3-p2-8-runbook.diff
cat /tmp/wave3-p2-8-runbook.diff
jq -r '.body' /tmp/wave3-p2-8-pr.json
```
Expected: runbook lists every workflow's `uses:` entries categorized as github-owned / verified / unverified. PR body has the inventory in a code block.

Extract the patterns list for Task 9:
```bash
# Look for a JSON or YAML block with "patterns_allowed" or similar
grep -A 50 "patterns_allowed\|verified_allowed" /tmp/wave3-p2-8-runbook.diff
```

- [ ] **Step 7: Required CI checks**

```bash
gh pr checks "$P28_PR" --required
```
Expected: all `pass`.

- [ ] **Step 8: Squash-merge**

```bash
gh pr merge "$P28_PR" --squash --auto
gh pr view "$P28_PR" --json state --jq '.state'
```
Expected: `MERGED`.

- [ ] **Step 9: Sync local main**

```bash
git checkout main && git pull --ff-only origin main
git log -1 --oneline
test -f docs/runbooks/allowed-actions-restriction.md
```

- [ ] **Step 10: Clean up the worktree**

```bash
git worktree remove --force .claude/worktrees/wave-3-allowed-actions 2>/dev/null || true
git branch -D wave-3/allowed-actions-runbook 2>/dev/null || true
git worktree prune
```

---

## Task 9: Apply P2-8 API change + smoke test

**Files:** none modified locally; runs GitHub Actions API calls.

**Pre-conditions:** Task 8 complete (runbook merged); no in-flight PRs; CI idle.

- [ ] **Step 1: Snapshot current GitHub Actions permissions for rollback**

```bash
gh api repos/Manzela/AutonomousAgent/actions/permissions > /tmp/wave3-actions-perms-before.json
cat /tmp/wave3-actions-perms-before.json
```
Expected: `{"enabled":true,"allowed_actions":"all","sha_pinning_required":false}`.

- [ ] **Step 2: Read patterns_allowed list from the merged runbook**

```bash
RUNBOOK=docs/runbooks/allowed-actions-restriction.md
# The runbook should have a clearly-labeled JSON or YAML block listing
# patterns_allowed. Extract it.
sed -n '/patterns_allowed/,/^```/p' "$RUNBOOK" | head -60
```

Build the patterns array as a JSON string for the API call. Example structure (actual values come from runbook):
```bash
PATTERNS='["pypa/gh-action-pypi-publish@*", "step-security/harden-runner@*"]'
echo "$PATTERNS" | jq '.'
```

- [ ] **Step 3: Flip top-level `allowed_actions` to `selected`**

```bash
gh api -X PUT repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=selected
```
Expected: HTTP 204 (no body, success).

- [ ] **Step 4: Configure `selected-actions` with github + verified + patterns_allowed**

```bash
gh api -X PUT repos/Manzela/AutonomousAgent/actions/permissions/selected-actions \
  --field github_owned_allowed=true \
  --field verified_allowed=true \
  --raw-field "patterns_allowed=$PATTERNS"
```
Expected: HTTP 204. Note: `--raw-field` is needed because `patterns_allowed` is a JSON array, not a string.

- [ ] **Step 5: Verify the new settings**

```bash
gh api repos/Manzela/AutonomousAgent/actions/permissions
gh api repos/Manzela/AutonomousAgent/actions/permissions/selected-actions
```
Expected:
- Top-level: `"allowed_actions": "selected"`.
- selected-actions: `github_owned_allowed: true`, `verified_allowed: true`, `patterns_allowed: [...]` matching the runbook.

- [ ] **Step 6: Smoke-test — trigger a workflow run and confirm it succeeds**

Pick the smallest workflow (likely the `secret-scan` or `lint` job). Trigger it via push of a no-op commit OR `gh workflow run`:

```bash
# Option A: list available workflows and pick one
gh workflow list

# Option B: trigger a workflow_dispatch run if available
gh workflow run "<workflow-name>" --ref main

# Wait + check
sleep 30
gh run list --limit 3 --json status,conclusion,name,createdAt --jq '.[]'
```
Expected: the smoke-test run reaches `conclusion: success`. If it fails with "action not allowed", that's a runbook gap — see Step 7.

- [ ] **Step 7: If smoke test fails: immediate rollback**

```bash
gh api -X PUT repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=all
gh api repos/Manzela/AutonomousAgent/actions/permissions
```
Expected: back to `"allowed_actions": "all"`. Then:
- Capture the failing run log: `gh run view <run-id> --log > /tmp/wave3-p2-8-failure.log`
- Open an issue: `gh issue create --title "P2-8 runbook gap: allowed_actions flip broke <action>" --body "$(cat /tmp/wave3-p2-8-failure.log)"`
- Mark P2-8 as DEFERRED in close-out.

- [ ] **Step 8: Persist the post-flip permissions snapshot for audit**

```bash
gh api repos/Manzela/AutonomousAgent/actions/permissions > audit/2026-05-19-resume-orchestration/wave-3-actions-perms-after.json
gh api repos/Manzela/AutonomousAgent/actions/permissions/selected-actions > audit/2026-05-19-resume-orchestration/wave-3-actions-selected-after.json
git add audit/2026-05-19-resume-orchestration/wave-3-actions-*.json
git commit -m "chore(audit): capture post-flip allowed_actions snapshot (P2-8)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git push origin main
```

---

## Task 10: Memory update + user-facing close-out

**Files:**
- Modify: `/Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/audit_2026-05-19_p0_wave.md`
- Create: `/Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/project_state_2026-05-20.md`
- Modify: `/Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/MEMORY.md`

- [ ] **Step 1: Refresh the audit memory with Wave-3 PR ledger**

Read the existing file:
```bash
cat /Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/audit_2026-05-19_p0_wave.md
```

Edit to append a Wave-3 section. The new content (insert before the closing references / above any existing footer) should read:

```markdown
## Wave 3 (P2) — shipped 2026-05-20

Dispatched 5 implementer subagents in parallel via Claude Code `Agent` tool
with `isolation: "worktree"`. Orchestrator verified + merged in dependency
order. P2-8 API flip applied last after quiesce.

| P2 # | PR | Branch | Notes |
|---|---|---|---|
| P2-1 | #<NUM> | docs/phase2-spec-codification | Phase-2 codify-what-exists spec |
| P2-2 | n/a | n/a | Direct ops: deleted N branches with user approval; log at audit/.../wave-3-branch-deletion.log |
| P2-4 | #<NUM> | docs/handoff-7-3-correction | DEFECT-3 carry-over |
| P2-6 | #<NUM> | chore/hermes-submodule-bump | ddb8d8fa8 → <NEW_SHA>, regression green |
| P2-7 | #<NUM> | feat/disk-cleanup-plugin | smoke-test green |
| P2-8 | #<NUM> | docs/allowed-actions-runbook | API flipped post-merge, smoke-test green |

Deferred (unchanged from Wave-2 deferrals): P2-3 (already done #76/#87), P2-5 (operator-only, signed commits coordination).
```

Fill `<NUM>` and `<NEW_SHA>` from the roster + Task 7 output.

- [ ] **Step 2: Author `project_state_2026-05-20.md` memory**

```markdown
---
name: project-state-2026-05-20
description: Wave-3 P2 complete; main HEAD post-merge; durability + supply-chain + spec all shipped
metadata:
  type: project
---

Main HEAD: <SHA after Task 9 push>
Wave 3 PRs merged: <list of 5 numbers> + audit/branch-deletion bookkeeping commits.

Phase-2 codified: docs/spec/phase2.md is now the system-of-record (5-layer
architecture, F-code failure modes, ADR appendix on layer-boundary crossings).

`allowed_actions` flipped from "all" → "selected" with
github_owned_allowed + verified_allowed + patterns_allowed from the runbook.
Post-flip snapshot at audit/2026-05-19-resume-orchestration/wave-3-actions-perms-after.json.

Hermes submodule bumped ddb8d8fa8 → <NEW_SHA>; ADR at
docs/architecture/decisions/0001-hermes-submodule-bump-2026-05-19.md.

**Why:** Closes the P2 backlog from the 2026-05-19 audit. Combined with
Wave 1 (P0) and Wave 2 (P1), every audit gap with a code/doc fix has shipped.

**How to apply:** Defer to the new spec (`docs/spec/phase2.md`) as
system-of-record. Branch protection signed-commit toggle (P2-5) still
requires contributor coordination — not gated by code.

Related: [[audit_2026-05-19_p0_wave]] for the full ledger.
```

- [ ] **Step 3: Add MEMORY.md index entry**

Read current MEMORY.md, prepend:
```markdown
- [Project state 2026-05-20](project_state_2026-05-20.md) — Wave-3 P2 complete; Phase-2 spec, Hermes bump, allowed_actions selected
```

Mark the old `project_state_2026-05-19.md` line as superseded in its description (edit its `description:` field to add "(superseded by 2026-05-20)").

- [ ] **Step 4: Run the close-out summary verification**

Capture the final state for the close-out report:
```bash
git log --oneline -10
gh pr list --state merged --limit 10 --json number,title,mergedAt --jq '.[] | "\(.number) \(.title)"'
gh api repos/Manzela/AutonomousAgent/actions/permissions
test -f docs/spec/phase2.md && echo "phase2.md: $(wc -l < docs/spec/phase2.md) lines"
test -f docs/runbooks/allowed-actions-restriction.md && echo "runbook present"
git submodule status hermes-agent
```

- [ ] **Step 5: Post final summary to user**

Write a concise message covering:
- Wave-3 PRs merged (numbers + one-liner per PR)
- Branch hygiene: N branches deleted
- `allowed_actions` flipped (with patterns_allowed shown)
- Hermes submodule bumped (old → new SHA)
- Defensible deferrals remaining (only P2-5 + operator items from Wave-2 table)
- Any new gaps surfaced during the wave
- Main HEAD now: `<SHA>`

Format matches the user's existing close-out style (per audit-plan §5 wording).

- [ ] **Step 6: No commit on memory files (memory dir lives outside the repo)**

The repo-side commits (Tasks 1, 3, 9) already pushed via `git push`. Memory updates are local to `~/.claude/projects/.../memory/` and don't need a git operation.

---

## Self-Review checklist (orchestrator runs before starting Task 1)

- [ ] Spec coverage: every item in spec §1.1 (P2-1/2/4/6/7/8) has a task above? **Yes** — Task 1 = P2-2; Task 3 dispatches P2-1/4/6/7/8; Tasks 4–9 verify + merge.
- [ ] Spec §6 merge order matches Tasks 4→5→6→7→8? **Yes** — P2-4, P2-7, P2-1, P2-6, P2-8 in that order.
- [ ] Spec §7 failure modes addressed? **Yes** — Task 3 Step 5 (re-dispatch), Task 4 Step 3 (scope-creep abort), Task 7 Step 9 (regression rollback), Task 9 Step 7 (API rollback).
- [ ] Spec §8 close-out present? **Yes** — Task 10.
- [ ] No "TBD" / "TODO" / "handle errors" / "similar to Task N" placeholders? Confirmed (all steps have concrete commands; SHAs and PR numbers are explicit `<NUM>` placeholders that get filled at execution time, which is unavoidable).
- [ ] Type/identifier consistency: `wave-3-pr-roster.json` referenced in Task 3 Step 6 + Tasks 4-8 Step 1? Confirmed.
- [ ] Branch slugs in Task 3 Step 1 match `git worktree remove` paths in Tasks 4-8 Step 8? Confirmed.
