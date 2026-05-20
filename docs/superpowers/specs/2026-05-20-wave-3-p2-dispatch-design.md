# Wave-3 P2 Dispatch Design

**Date:** 2026-05-20
**Status:** Draft → user-review pending
**Audit anchor:** `audit/2026-05-19-resume-orchestration/audit-plan.md §P2`
**Wave context:** Wave 1 (P0) + Wave 2 (P1) shipped 17 PRs (#69–#87). Main HEAD: `ce4c344`. This document specifies how the Wave-3 (P2) backlog is dispatched, verified, and merged.

---

## 1. Scope

### 1.1 In scope (LOCKED)

| # | Item | Type | Effort | Parallelizable |
|---|------|------|--------|----------------|
| P2-1 | Phase-2 spec — codify hardened foundation (`docs/spec/phase2.md`) | Doc / system-of-record | 2–3 days agent-time | Single PR |
| P2-2 | Branch hygiene — delete 41 merged local branches | Ops | 30 min | Direct ops |
| P2-4 | Handoff §7.3 correction (DEFECT-3 carry-over) | Doc | 30 min | Yes |
| P2-6 | Hermes submodule bump `ddb8d8fa8` → upstream HEAD + ADR | Code + ADR | 4 hours | Yes (regression-sensitive) |
| P2-7 | Add `disk-cleanup` plugin to `config/hermes/cli-config.yaml` allowlist | Config | 30 min | Yes |
| P2-8 | `allowed_actions` restriction — audit + runbook (orchestrator flips API) | Doc + ops | 1 hour | Yes |

### 1.2 Out of scope this wave

| # | Item | Reason |
|---|------|--------|
| P2-3 | README service-count correction | **Already shipped** via #76 + #87 |
| P2-5 | Branch protection: signed commits | **Operator-only** — requires contributor coordination first; per deferrals table |

### 1.3 Scope clarifications (user-confirmed)

- **Phase-2 spec scope (P2-1)**: codify-what-exists only. No forward-looking feature set, no GCP migration plan. The deliverable is `docs/spec/phase2.md` describing the existing hardened-foundation architecture as system-of-record.
- **Branch hygiene (P2-2)**: list → user approves → orchestrator deletes. Honors audit's "manually inspected, no automation" guidance.
- **Merge authority**: orchestrator merges after CI green + agent's verification log. Matches established Wave-1/2 pattern (17 PRs).

---

## 2. Architecture

### 2.1 Roles

- **Orchestrator** = this Claude Code session. Responsibilities: pre-flight ops, plan authorship, subagent dispatch, verification, merge, memory update.
- **Implementers** = 5 `general-purpose` subagents, each running in an isolated git worktree on its own branch. Responsibilities: implement one PR-scoped mandate, open the PR, post a verification log, return structured JSON.

### 2.2 Topology

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Orchestrator (this session)                                            │
│                                                                          │
│   Phase 1: P2-2 branch hygiene (inline, user-gated)                     │
│   Phase 2: write + commit this spec; user reviews;                      │
│            invoke writing-plans → consolidated Wave-3 plan;              │
│            user reviews plan                                             │
│   Phase 3: fan out 5 subagents (single tool block, parallel)            │
│   Phase 4: collect JSON results, verify each independently               │
│   Phase 5: merge in dependency order; apply P2-8 API change last       │
│   Phase 6: update memory; report close-out                              │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │
       ┌───────────┬───┴───────┬───────────┬───────────┐
       ▼           ▼           ▼           ▼           ▼
  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
  │  P2-1   │ │  P2-4   │ │  P2-6   │ │  P2-7   │ │  P2-8   │
  │ Agent   │ │ Agent   │ │ Agent   │ │ Agent   │ │ Agent   │
  │ (gen)   │ │ (gen)   │ │ (gen)   │ │ (gen)   │ │ (gen)   │
  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
   worktree     worktree    worktree    worktree    worktree
       │           │           │           │           │
       └───────────┴───────────┴───────────┴───────────┘
                       │
                       ▼
              5 PRs (numbered #88..#92)
```

### 2.3 Dependency order for merge

1. **P2-4** — handoff §7.3 doc correction (smallest blast radius)
2. **P2-7** — `disk-cleanup` plugin (config only)
3. **P2-1** — Phase-2 spec (large doc; no code risk)
4. **P2-6** — Hermes submodule bump (largest blast radius — regression-test gated)
5. **P2-8** — `allowed_actions` runbook PR + orchestrator API flip (must be last; affects all subsequent workflows; quiesce all in-flight PRs first)

---

## 3. Subagent prompt contract

Every subagent prompt is built from this uniform template:

```
ROLE: Implementer subagent for [PR-ID] in autonomous-agent repo (Wave-3 P2).

CONTEXT:
- Repo: /Users/danielmanzela/RX-Research Project/AutonomousAgent
- Main HEAD: ce4c344 (Wave-2 complete)
- Audit anchor: audit/2026-05-19-resume-orchestration/audit-plan.md §P2-[N]
- LOCKED scope: do NOT touch anything outside this PR's mandate.

WORKTREE: You are running in an isolated worktree. The orchestrator created
it via `git worktree add` on branch `wave-3/[branch-slug]` off origin/main.

MANDATE (one PR only):
[per-PR mandate — see §4]

REQUIRED SKILLS (invoke in order):
1. superpowers:test-driven-development — IF tests apply
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
  "pr_id": "P2-N",
  "pr_url": "https://github.com/Manzela/AutonomousAgent/pull/NN",
  "pr_number": NN,
  "branch": "wave-3/...",
  "files_changed": [...],
  "ci_status": "queued|pending|passing|failing",
  "verification_log_comment_url": "...",
  "blockers": [...] or []
}
```

**Standards rationale:**

- **NIST SSDF PW.7** (code review) — each agent self-reviews via `code-review-excellence` before posting.
- **NIST SSDF PS.3** (verify software integrity) — `verification-before-completion` is non-negotiable.
- **ISO/IEC/IEEE 12207 §7.1.6** (implementation) — explicit mandate prevents scope creep.
- **Trust-but-verify** — orchestrator independently checks `git diff --name-only` against the mandate; the agent's claim can lie, the diff cannot.
- **Structured JSON output** — enables programmatic handoff to the orchestrator's merge loop.

---

## 4. Per-PR mandates

### 4.1 P2-1 — Phase-2 spec (`docs/spec/phase2.md`)

- **Source material**: `audit/2026-05-19-resume-orchestration/audit-plan.md §1` (5-layer orchestration model), `findings.md §4` (capability surface), `findings.md §5` (F-code risk register), Wave-1/2 PRs #76–#86 (what's now real).
- **Structure** (auditor-defined):
  1. System-of-record: 5-layer architecture, what each layer optimizes for.
  2. F-code failure modes — every F-code in the matrix references a real handler post-P0-5.
  3. ADR appendix: layer-boundary cross criteria.
- **Out-of-scope**: forward-looking features, GCP migration plan.
- **Standard**: ISO/IEC/IEEE 42010 (architecture description).
- **Tests**: N/A (docs).
- **Acceptance**: `markdownlint docs/spec/phase2.md` clean; spec covers all 5 layers + all live F-codes.
- **Commit**: `docs(spec): author Phase-2 codification of hardened foundation (closes P2-1)`
- **Branch**: `docs/phase2-spec-codification`

### 4.2 P2-4 — Handoff §7.3 correction

- **Where**: `docs/superpowers/HANDOFF-2026-05-19.md` line 159 (`### 7.3 Container HOME is /home/hermes, not /root (post PR #60)`).
- **Background**: carry-over from prior audit's DEFECT-3. Agent must first read `audit/handoff-doc-2026-05-19-review/findings.md` for the DEFECT-3 detail before proposing the correction.
- **Out-of-scope**: any other section of the handoff doc.
- **Tests**: N/A.
- **Acceptance**: diff ≤30 lines, only touches §7.3.
- **Commit**: `docs(handoff): correct §7.3 per DEFECT-3 carry-over (closes P2-4)`
- **Branch**: `docs/handoff-7-3-correction`

### 4.3 P2-6 — Hermes submodule bump + ADR

- **What**: `git submodule update --remote hermes-agent` (currently `ddb8d8fa8`, target upstream HEAD).
- **Audit evidence**: 757 commits / 5 days behind, hook contract STABLE in the delta, one backward-compatible feature added (tool-override flag at `016c772e7`).
- **ADR**: `docs/architecture/decisions/0001-hermes-submodule-bump-2026-05-19.md` — what changed, why bumping now, regression evidence, rollback procedure.
- **Regression test**: run `make test` (or equivalent — agent inspects `Makefile`/`pyproject.toml`/`pytest.ini`) inside the running `hermes` container; paste full output as PR comment.
- **Standard**: NIST SSDF PW.4 ("Keep only needed software" — current).
- **Tests**: existing test suite must remain green.
- **Acceptance**: ADR exists; submodule pointer updated; CI green; regression log posted.
- **Commit**: `chore(deps): bump hermes-agent submodule ddb8d8fa8→<new-sha> + ADR (closes P2-6)`
- **Branch**: `chore/hermes-submodule-bump`

### 4.4 P2-7 — `disk-cleanup` plugin

- **Where**: `config/hermes/cli-config.yaml`, append `- disk-cleanup` to `plugins.enabled:` (around line 119, after `observability`).
- **Verify upstream**: confirm `~/.hermes/plugins/disk-cleanup/` ships in the upstream Hermes (post-bump or current). If only available post-bump, document the dependency on P2-6 in the PR description but do not block the PR — coordinate with orchestrator.
- **Hooks** (per audit): `post_tool_call`, `on_session_end`.
- **Test**: integration test that starts hermes container, greps startup logs for `disk-cleanup loaded` (or equivalent confirmation). Add to existing integration-test suite.
- **Acceptance**: plugin in allowlist; integration test green.
- **Commit**: `feat(hermes): enable disk-cleanup plugin for session hygiene (closes P2-7)`
- **Branch**: `feat/disk-cleanup-plugin`

### 4.5 P2-8 — `allowed_actions` restriction (audit + runbook only; orchestrator flips API)

- **Audit phase**: scan all `.github/workflows/*.yml` for every `uses:` entry; categorize as (a) GitHub-owned `actions/*`, (b) verified-publisher, (c) unverified. Output the categorization as a PR comment.
- **PR content**: `docs/runbooks/allowed-actions-restriction.md` runbook + the categorized inventory in the PR body.
- **Do NOT**: call the GitHub API to flip the setting (shared repo state — orchestrator does it after merge).
- **PR description must include**: "After merge, orchestrator runs `gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions --field allowed_actions=selected` then PUTs `/repos/Manzela/AutonomousAgent/actions/permissions/selected-actions` with `github_owned_allowed=true, verified_allowed=true, patterns_allowed=[<list>]` per the runbook's inventory."
- **Standard**: OWASP CICD-SEC-05 ("Restrict action usage").
- **Tests**: N/A (audit + runbook).
- **Acceptance**: runbook covers every workflow; categorization complete; all unverified actions either flagged for replacement or pinned-by-SHA.
- **Commit**: `docs(security): allowed_actions inventory + restriction runbook (P2-8 prep)`
- **Branch**: `docs/allowed-actions-runbook`

---

## 5. Orchestrator verification

For each subagent's returned PR, before considering it merge-eligible, orchestrator runs:

1. `gh pr view <num> --json files,statusCheckRollup,mergeable,reviewDecision` — confirm:
   - all 11 required CI checks green
   - `mergeable: true`
   - no requested changes outstanding
2. `git diff origin/main..<branch> --name-only` — sanity-check no scope creep against the mandate's file list.
3. Read the PR description + verification log comment — confirm the agent did what it claimed.
4. If a check fails: post a follow-up via SendMessage to the agent (do NOT merge); ask for fix; re-verify on return.

---

## 6. Merge sequence

| Step | Action | Notes |
|------|--------|-------|
| 1 | Merge P2-4 (smallest doc) | Squash, conventional title |
| 2 | Merge P2-7 (config) | Squash; quick container smoke-test post-merge |
| 3 | Merge P2-1 (Phase-2 spec) | Squash |
| 4 | Merge P2-6 (submodule bump) | Squash; regression-test re-verify post-merge |
| 5 | **Quiesce** — confirm no PRs in flight, no CI running | Hard gate |
| 6 | Merge P2-8 (runbook PR) | Squash |
| 7 | **Apply P2-8 API change** | `gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions --field allowed_actions=selected` then PUT `selected-actions` per runbook inventory |
| 8 | Smoke-test post-flip | Trigger no-op workflow run or watch next push |

---

## 7. Failure modes + recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Subagent's PR has scope creep | `git diff --name-only` vs mandate | SendMessage: "trim to mandated paths only" |
| CI red on a PR | `gh pr checks <num>` | SendMessage with failure log; if structural, orchestrator fixes inline |
| Subagent returns malformed JSON | Parse failure in orchestrator | Re-dispatch with stricter output instructions |
| Submodule bump regression | Test suite fails post-bump | Roll back submodule to `ddb8d8fa8`; mark P2-6 DEFERRED; open issue with regression trace |
| `allowed_actions` flip breaks CI | Workflow runs fail post-flip | Immediate rollback: `gh api -X PUT ... --field allowed_actions=all`; document gap in close-out |

---

## 8. Close-out

After Phase 6 (merge sequence) completes, orchestrator:

1. Update `audit_2026-05-19_p0_wave.md` memory: append Wave-3 PR ledger (#88..#92), mark P2-1/2/4/6/7/8 complete, retain P2-3 (done pre-wave) + P2-5 (operator-only) flags.
2. Author `project_state_2026-05-20.md` memory: new main HEAD post-merge, Wave-3 summary, surfaced gaps, defensible deferrals.
3. Post final summary back to user: PRs merged with numbers, gaps closed, any new gaps surfaced this pass, remaining deferrals (P2-5 + operator items already in deferrals table).

---

## 9. Standards crosswalk

| Activity | Standard |
|----------|----------|
| Architecture description (P2-1 spec) | ISO/IEC/IEEE 42010 |
| Subagent code review | NIST SSDF PW.7 |
| Verification gate | NIST SSDF PS.3 |
| Scope discipline | ISO/IEC/IEEE 12207 §7.1.6 |
| Submodule currency | NIST SSDF PW.4 |
| Restricted GitHub Actions | OWASP CICD-SEC-05 |
| Plan-before-implement | This document + Wave-3 plan |

---

## 10. Open questions for user review

(None — all clarifications resolved in pre-design Q&A.)
