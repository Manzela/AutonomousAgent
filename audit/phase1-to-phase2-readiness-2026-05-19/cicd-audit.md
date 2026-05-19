# CI/CD Pipeline Maturity Audit — AutonomousAgent

- **Target:** `Manzela/AutonomousAgent` @ `85512a3` (branch `main`)
- **Date:** 2026-05-19
- **Scope:** Phase 1 → Phase 2 readiness vs enterprise SDLC baseline
- **Method:** Read every `.github/workflows/*.yml`, every supporting config, queried live GitHub state via `gh api` for branch protection / Actions permissions / security feature toggles / run history. No assumptions where the API answer was available.

---

## 0. TL;DR — Maturity Grade

**Overall: C–** (functional, well-documented, but missing nearly every enterprise security/supply-chain primitive)

The pipeline is *clean, fast, and deliberately minimal* — a strong foundation. But the cost-conscious "remove anything non-blocking" stance has stripped out almost everything an enterprise SDLC reviewer would look for: there is no SAST, no SCA, no container scan, no SBOM, no signing, no coverage gate, no test parallelism, no admin enforcement of branch protection, no required reviewer count, no SHA-pinned actions, and GitHub Advanced Security is fully disabled (Code Scanning, Secret Scanning service, Vulnerability Alerts, Dependabot security fixes all OFF). What is present (gitleaks + detect-secrets + lint + unit tests + compose-render + Conventional-Commit/branch-name validation) is well-engineered. The gap is breadth, not depth.

Component sub-grades:

| Area | Grade | Headline |
|---|---|---|
| CI surface inventory | B | 4 workflows, 11 required checks, clean concurrency, near-zero failure rate (5/50) |
| Pre-commit / CI parity | B+ | Pre-commit is a strict superset of CI — no drift |
| Industry-standard checks present | D | SAST/SCA/container/SBOM/signing all missing |
| Test maturity | D | No coverage, no parallelism, no retries, no benchmarks |
| Branch protection | C– | Strict checks, but **0 required reviewers**, no admin enforcement, no signed commits, no linear history |
| Release process | B– | SemVer + Keep-a-Changelog discipline, but releases are unsigned, action not SHA-pinned, no SBOM artifact |
| Deployment automation | F | None. No GitOps, no auto-deploy, no rollback test |
| Secrets in CI | A– | Only `GITHUB_TOKEN`. No custom secrets exposed. Read-default workflow perms. |
| Workflow security | C+ | Read-default perms, no `pull_request_target`, concurrency on 2/4 workflows, **no action SHA pinning anywhere** |
| CI observability | D | No SLOs, no run-time dashboards, no failed-CI alerts, no minute-budget tracking |

---

## 1. Current CI surface inventory

### 1.1 Workflows present

Source: `.github/workflows/` directory listing + `gh workflow list`.

| Workflow | File | Triggers | Jobs | Concurrency group |
|---|---|---|---|---|
| **CI** | `ci.yml` | `pull_request: [main]`, `workflow_dispatch` | `lint-python`, `lint-shell`, `lint-yaml`, `lint-dockerfile`, `unit-tests`, `validate-config`, `validate-compose` | `ci-${{ github.ref }}` ([ci.yml:31-33](../../.github/workflows/ci.yml)) |
| **PR Validation** | `pr-validation.yml` | `pull_request: [main]` (`opened`, `synchronize`, `edited`, `reopened`) | `validate-title`, `validate-branch-name` | **none — race possible** |
| **Secret Scan** | `secret-scan.yml` | `pull_request`, `schedule: "23 6 1 * *"`, `workflow_dispatch` | `gitleaks`, `detect-secrets` | `secret-scan-${{ github.ref }}` ([secret-scan.yml:23-25](../../.github/workflows/secret-scan.yml)) |
| **Release** | `release.yml` | `push: tags [v*, phase*-accepted]` | `release` | **none — duplicate-tag race possible** |
| Dependabot Updates | *(GitHub-managed)* | per `dependabot.yml` schedule | grouped PRs | n/a |
| Dependency Graph | *(GitHub-managed)* | always | n/a | n/a |

`push: main` triggers are **intentionally absent** from every project workflow ([ci.yml:25-28](../../.github/workflows/ci.yml), [secret-scan.yml:15-17](../../.github/workflows/secret-scan.yml)). Rationale: PR-only path to main + branch protection. Justified for cost; trade-off is that *no workflow runs against `main`'s actual merge commit*, so a bad squash-merge is detected by the *next* PR, not at-merge.

### 1.2 Required status checks → emitting workflow

Source: `gh api repos/Manzela/AutonomousAgent/branches/main/protection`. Strict mode: `true`. 11 required contexts:

| Required check name | Emitted by |
|---|---|
| `Lint Python` | `ci.yml` § `lint-python` |
| `Lint Shell` | `ci.yml` § `lint-shell` |
| `Lint YAML` | `ci.yml` § `lint-yaml` |
| `Lint Dockerfiles` | `ci.yml` § `lint-dockerfile` *(advisory: `continue-on-error: true` on both hadolint steps, [ci.yml:165,173](../../.github/workflows/ci.yml))* |
| `Unit Tests` | `ci.yml` § `unit-tests` |
| `Validate config/limits.yaml` | `ci.yml` § `validate-config` |
| `Validate docker-compose` | `ci.yml` § `validate-compose` |
| `Conventional Commit title` | `pr-validation.yml` § `validate-title` |
| `Branch name follows convention` | `pr-validation.yml` § `validate-branch-name` |
| `gitleaks` | `secret-scan.yml` § `gitleaks` |
| `detect-secrets` | `secret-scan.yml` § `detect-secrets` |

**Coverage gap:** *Every* CI workflow job is a required check. There is **no non-blocking check** in CI itself — anything advisory was removed. This is binary (good for signal-noise; bad because it precludes "try-in-CI-first" hardening passes for new tools).

**Subtle false-positive risk:** `Lint Dockerfiles` is in the required-checks list but its two hadolint steps both have `continue-on-error: true` ([ci.yml:165,173](../../.github/workflows/ci.yml)). The required check therefore *never blocks merge on Dockerfile issues* — it only blocks if the GH Action step itself errors out. The check is theatre.

### 1.3 Workflows that don't run on main

All four project workflows are PR/tag/schedule-only by design. None runs on `push: main`. Documented in [docs/ci-cd.md:18](../../docs/ci-cd.md). Means: post-merge drift in `main` (e.g. squash-merge artefact, manual fix-up commit if main were ever unlocked) would not be re-validated.

### 1.4 Recent failure rate

Source: `gh run list --limit 50 --json conclusion,workflowName`. Window: 2026-05-17 → 2026-05-19.

| Conclusion | Count |
|---|---|
| `success` | 44 |
| `failure` | 5 |
| `cancelled` | 1 |
| **Failure rate** | **10%** |

Failures, by workflow:

| Workflow | Failures (50-run window) | Common cause |
|---|---|---|
| `PR Validation` | 4 | PR title not Conventional — fixed by edit-and-resync |
| `CI` | 1 | Phase 1 mega-PR (#46) baseline issues (ruff format, shellcheck SC2155/SC2034, missing env-file stubs) — captured in CHANGELOG [Unreleased]/Fixed |
| `Secret Scan` | 1 (cancelled, superseded) | PR force-push superseded the run; concurrency group worked as intended |

**Median CI runtime:** ~18-22s (10-run sample). One 116s outlier — first-time uv cache cold. Cache size: 16.6 MB across 16 entries (`gh api …/actions/cache/usage`).

---

## 2. Pre-commit hooks vs CI parity

### 2.1 What pre-commit runs

[.pre-commit-config.yaml](../../.pre-commit-config.yaml):

| Hook | Source | Pinned rev |
|---|---|---|
| `trailing-whitespace` | pre-commit-hooks | v4.6.0 |
| `end-of-file-fixer` | pre-commit-hooks | v4.6.0 |
| `check-yaml` | pre-commit-hooks | v4.6.0 |
| `check-added-large-files` (--maxkb=1024) | pre-commit-hooks | v4.6.0 |
| `detect-private-key` | pre-commit-hooks | v4.6.0 |
| `detect-aws-credentials` | pre-commit-hooks | v4.6.0 |
| `detect-secrets` (baseline-aware) | Yelp/detect-secrets | v1.5.0 |
| `ruff` (with --fix) | astral-sh/ruff-pre-commit | **v0.6.9** |
| `ruff-format` | astral-sh/ruff-pre-commit | **v0.6.9** |

### 2.2 Parity matrix

| Check | Pre-commit | CI | Parity? |
|---|---|---|---|
| ruff lint | yes | yes ([ci.yml:73](../../.github/workflows/ci.yml)) — pinned to `0.6.9` in lockstep | YES |
| ruff-format | yes | yes ([ci.yml:77](../../.github/workflows/ci.yml)) | YES |
| detect-secrets | yes | yes ([secret-scan.yml:99-121](../../.github/workflows/secret-scan.yml)) | YES |
| trailing-whitespace / EOL / large-files / private-key / AWS-creds | yes | **NO** | drift acceptable (cosmetic + redundant with gitleaks) |
| yamllint | NO | yes ([ci.yml:120-136](../../.github/workflows/ci.yml)) | one-way: CI catches what pre-commit misses |
| shellcheck | NO | yes ([ci.yml:103-107](../../.github/workflows/ci.yml)) | one-way |
| hadolint | NO | yes (advisory) | one-way |
| pytest | NO | yes | one-way (expected — too slow for pre-commit) |
| limits.yaml schema validation | NO | yes | one-way |
| docker compose render | NO | yes | one-way |
| markdownlint | NO (despite mention in docs) | NO | gap — `.markdownlint.jsonc` exists but no hook references it |
| gitleaks | NO | yes | drift — local devs only have detect-secrets; gitleaks gate hits at PR time |

**Notable:** No `pre-commit-ci.yml` integration — pre-commit isn't run *by* CI, so a contributor with `pre-commit` uninstalled can push code that fails the eventual CI checks at PR time. Adding `pre-commit run --all-files` as a CI job is cheap and would close this gap (and would simultaneously add yamllint-on-everything + shellcheck enforcement at local dev time, since pre-commit could then own them).

**Recommended additions to pre-commit (low effort, high ROI):**
1. `pre-commit/mirrors-yamllint`
2. `koalaman/shellcheck-precommit`
3. `hadolint/hadolint` mirror
4. `markdownlint-cli2` (since `.markdownlint.jsonc` already configured)
5. `pre-commit-ci/lite-action@v1` workflow to auto-fix in PRs

---

## 3. Industry-standard checks MISSING for enterprise scale

| Category | Tool options (good first pick **bold**) | Present? | Effort to add | Priority for Phase 2 |
|---|---|---|---|---|
| **SAST — generic** | **CodeQL** (free for public repos; private requires GHAS), Semgrep OSS | **NO** — CodeQL deliberately removed at [ci.yml:16-18](../../.github/workflows/ci.yml) "requires GitHub Advanced Security on private repos"; not reintroduced post-Phase-1-merge despite [docs/ci-cd.md:181](../../docs/ci-cd.md) saying it should be | S (Semgrep OSS works without GHAS) | **HIGH** — Phase 2 will add tool-dispatch and sandbox-escape surface |
| **SAST — Python security** | **Bandit**, `ruff` rules `S` (flake8-bandit subset) | **NO** — `pyproject.toml` `[tool.ruff]` has only `line-length` + `target-version`; no `select` / no `S`-rules | XS (one-line `select = ["E","F","I","S","B"]` in pyproject) | HIGH |
| **SAST — IaC / containers** | **Checkov**, KICS | NO | M | MEDIUM (no TF today; Phase 2 may introduce) |
| **SCA — Python deps** | **pip-audit**, `safety`, `osv-scanner` | **NO** — Dependabot opens version-bump PRs but does **not** scan for known CVEs in current deps | S (pip-audit is single-job) | **HIGH** — required for any "enterprise" claim |
| **SCA — GitHub Action review** | `actions/dependency-review-action` | **NO** — removed at [ci.yml:16-18](../../.github/workflows/ci.yml) ("requires GHAS on private repos to be useful") | XS | MEDIUM |
| **Dependabot security alerts** | GitHub native | **DISABLED** — `gh api /repos/.../vulnerability-alerts` returns 404 "Vulnerability alerts are disabled" | XS (single API call to enable) | **HIGH** — free, no minutes cost |
| **Dependabot automated security fixes** | GitHub native | **DISABLED** — `gh api /repos/.../automated-security-fixes` returns `{"enabled":false}` | XS | **HIGH** |
| **GitHub Secret Scanning service** | GitHub native | **DISABLED** — `gh api /repos/.../secret-scanning/alerts` returns "Secret scanning is disabled" | XS for public repos / GHAS-licensed | depends on GHAS licensing |
| **Code Scanning service** | GitHub native | **DISABLED** — `gh api /repos/.../code-scanning/alerts` returns "Code scanning is not enabled" | XS once GHAS or CodeQL workflow added | HIGH |
| **Container image scanning** | **trivy**, grype, snyk-container | **NO** — only hadolint (Dockerfile static analysis), and it's `continue-on-error: true`. No CVE scan of built layers. | M (need to actually build images in CI first — currently only compose-render check) | **HIGH** — `Dockerfile.hermes` and `Dockerfile.shell-sandbox` are unscanned attack surface |
| **License scanning** | pip-licenses, **licensee**, FOSSA | NO | XS (pip-licenses + allowlist) | LOW (single deployer, MIT-licensed) |
| **DAST** | OWASP ZAP, Burp Enterprise | NO | M | **N/A** — no web surface (Telegram bot is private-channel only) |
| **SBOM generation** | **syft** (CycloneDX/SPDX), `cyclonedx-bom` | NO | XS (syft as post-build step, upload as release artifact) | MEDIUM — required for many enterprise/government procurements |
| **Supply-chain provenance** | **SLSA generator** (slsa-framework), in-toto | NO | M | MEDIUM — Phase 2 enterprise narrative |
| **Image signing** | **cosign/Sigstore**, notary v2 | NO | M (key management + verify step in deploy) | MEDIUM — paired with SBOM |
| **Reproducible builds** | repro-build verification | NO | L | LOW |
| **OpenSSF Scorecard** | ossf/scorecard-action | NO | XS (single workflow, weekly cron) | LOW (useful as scorecard for external auditors) |
| **OpenSSF Allstar / Police** | allstar app | NO | XS | LOW |

**Bottom line on this section:** the cost-conscious philosophy ("only required-blocking checks; advisory tools waste minutes") has eliminated *every* defence-in-depth scanner that an enterprise SOC would consider table-stakes. A Phase 2 deployer reading this repo and seeing "Dependabot enabled" cannot tell that **Dependabot security alerts are turned off at the repo level** and that **no dep CVE scanner runs in CI** — only version-bump PRs land.

---

## 4. Test execution maturity

| Concern | State | Evidence |
|---|---|---|
| Test framework | pytest 8 + pytest-asyncio + pytest-mock | [pyproject.toml:15-18](../../pyproject.toml) |
| Test count | 25 unit test files | `tests/unit/` ls |
| **Parallelism** | **NO** — no `pytest-xdist`, no `-n auto`, single runner | [ci.yml:217](../../.github/workflows/ci.yml): `pytest tests/unit/ -v --tb=short --junitxml=junit.xml` |
| **Coverage measurement** | **NO** — pytest-cov not in dev deps, no `--cov` flag, no coveragerc | grep of pyproject + workflows |
| **Coverage gate** | **NO** | n/a |
| **Flaky-test detection** | **NO** — no `pytest-rerunfailures`, no retry strategy | grep of workflows |
| **Performance regression tests** | **NO** benchmarks — no `pytest-benchmark`, no `asv` | grep of pyproject |
| Test result artifacts | YES — `junit.xml` uploaded with 14-day retention | [ci.yml:219-225](../../.github/workflows/ci.yml) |
| Integration tests | **NOT RUN IN CI** — `tests/integration/` exists but `ci.yml` only invokes `tests/unit/`; integration tests marked `@pytest.mark.skip` per CHANGELOG | [ci.yml:217](../../.github/workflows/ci.yml) + [docs/runbooks/phase1-acceptance.md](../../docs/runbooks/phase1-acceptance.md) |
| Matrix testing | **NO** — single Python (3.11), single runner (ubuntu-latest), no Python 3.12/3.13 | [ci.yml:205](../../.github/workflows/ci.yml) |
| Junit consumed by anything | NO — uploaded as artifact but no test-reporter (e.g. EnricoMi/publish-unit-test-result-action) parses it into the PR UI | n/a |

**Gaps for Phase 2:**
- Integration tests are documented as "deferred to Phase 2" but there is no CI scaffold ready for them. Phase 2 will need a separate workflow (likely with docker-compose-based service spin-up + secrets).
- No coverage means there is no way to detect that, e.g., the new `lib/durability/checkpoint.py` resume path is untested before merge. The audit of test files shows `test_checkpoint.py` (5473 bytes) and `test_resume.py` (4060 bytes) exist but no enforcement that new code is covered.
- No xdist means each pytest run is serial — fine at 25 tests, will matter at 100+.

---

## 5. Branch protection effectiveness

Source: `gh api repos/Manzela/AutonomousAgent/branches/main/protection`.

| Control | Setting | Effect | Enterprise-grade? |
|---|---|---|---|
| `strict` required checks | **TRUE** — branch must be up-to-date with main | Prevents stale-merge races | YES |
| Required checks count | **11** | All present-day workflows | YES |
| `required_approving_review_count` | **0** | **No human review required to merge** | **NO — major gap** |
| `require_code_owner_reviews` | TRUE | But meaningless when required count is 0 | NO |
| `dismiss_stale_reviews` | TRUE | New commits drop existing approvals | YES |
| `require_last_push_approval` | FALSE | Same person can approve their own most-recent push | NO |
| `enforce_admins` | **FALSE** | **Repo admin (sole maintainer) can bypass everything** | **NO** |
| `required_signatures` | **FALSE** | No GPG / Sigstore / SSH-key commit signing required | **NO** |
| `required_linear_history` | **FALSE** | Merge commits allowed in theory (squash-only enforced at repo level, see below) | partial |
| `allow_force_pushes` | FALSE | Good | YES |
| `allow_deletions` | FALSE | Good | YES |
| `required_conversation_resolution` | TRUE | Unresolved review threads block merge | YES |
| `lock_branch` | FALSE | Good (would block all writes) | YES |

Repo-level merge settings (`gh api repos/Manzela/AutonomousAgent`):

| Setting | Value | Notes |
|---|---|---|
| `allow_squash_merge` | TRUE | Used in practice |
| `allow_merge_commit` | **FALSE** | Squash-only — enforces linear-ish history without `required_linear_history` |
| `allow_rebase_merge` | TRUE | Permitted but Conventional-Commit conventions favour squash |
| `delete_branch_on_merge` | TRUE | Good |
| `allow_auto_merge` | FALSE | Means Dependabot PRs need manual merge (gate against silent dep drift) |
| GitHub rulesets | empty (`[]`) | Branch protection is sole control surface |

**CODEOWNERS:** present at [.github/CODEOWNERS](../../.github/CODEOWNERS) — single owner `@Manzela` for everything, with explicit re-statement for `secrets/`, `.sops.yaml`, `.gitleaks.toml`, `.secrets.baseline`, `/.github/workflows/`, `SECURITY.md`, `docs/decisions/`. CODEOWNERS is *enforced* by `require_code_owner_reviews: true` — but again neutralised by `required_approving_review_count: 0`.

**Single-developer reality vs enterprise gap:** The "0 required reviewers" is intentional for a single-developer project, but is the #1 reason no enterprise SDLC checklist would accept this repo as-is. Phase 2 onboarding of a second contributor must trigger:
- `required_approving_review_count >= 1`
- `enforce_admins: true`
- `required_signatures: true` (paired with sigstore/cosign for releases)
- `required_linear_history: true`

---

## 6. Release process maturity

Source: [docs/release-process.md](../../docs/release-process.md), [release.yml](../../.github/workflows/release.yml), [CHANGELOG.md](../../CHANGELOG.md).

| Aspect | State | Grade |
|---|---|---|
| Release docs present | YES, comprehensive | A |
| Tag → release notes flow | Auto via Conventional-Commit grouping ([release.yml:36-66](../../.github/workflows/release.yml)) | A |
| Versioning scheme | SemVer 2.0.0 + Keep-a-Changelog 1.1.0 — documented in [docs/release-process.md:12-22](../../docs/release-process.md). Pre-1.0 breaking changes allowed in MINOR per SemVer §4 | A |
| Two parallel tag tracks | `vX.Y.Z` (semantic) + `phaseN-accepted` (milestone) — both fire same workflow | B+ (clever but adds tag-namespace complexity) |
| `version` pinned in pyproject | YES — `0.1.0` ([pyproject.toml:3](../../pyproject.toml)). **Not** auto-bumped by release workflow — manual edit + commit step | C (drift risk; PRs/release workflow don't enforce pyproject version matches tag) |
| CHANGELOG present + maintained | YES — [CHANGELOG.md](../../CHANGELOG.md) is current; `[Unreleased]` section tracks in-flight PRs explicitly | A |
| Pre-release detection | YES — `-rc`, `-alpha`, `-beta` in tag → `prerelease: true` ([release.yml:75](../../.github/workflows/release.yml)) | A |
| Release notes generation | Custom shell-driven Conventional-Commit grouping (Features / Fixes / Security / Documentation / Other), explicit opt-out of `generate_release_notes: false` | B+ (deliberate, custom; brittle to grep-regex edge-cases) |
| Compare link in release notes | YES — last line of release-notes.md ([release.yml:65](../../.github/workflows/release.yml)) | A |
| **Release artefacts** | **None** — workflow publishes only the release-notes Markdown. No SBOM, no built wheel, no Docker image digest, no provenance attestation | **D** |
| **Release signing** | **None** — `softprops/action-gh-release@v3` defaults; no cosign-sign-blob, no GPG signature on tag | **D** |
| **Image publishing** | Not in release workflow at all. Compose file pulls upstream images; no `ghcr.io/manzela/autonomousagent` namespace built | n/a (Phase 2 territory) |
| Recovery procedure | Documented in [docs/release-process.md:103-117](../../docs/release-process.md) — "mark as pre-release; cut Z+1; never delete tags" | A |
| **Recovery procedure tested** | **No evidence** of a rollback drill | D |
| Release reviewer | None — anyone with tag-push perms triggers a release | C |

**Release maturity grade: B–** (excellent docs + CHANGELOG discipline; the gaps are all in *what gets shipped with the release* — no SBOM, no signature, no provenance, no image artefact).

---

## 7. Deployment automation

| Concern | State |
|---|---|
| GitOps / ArgoCD / Flux | **NONE** — no `flux/`, `argocd/`, or any cluster manifest |
| Kubernetes manifests | **NONE** — deployment model is `docker compose` on a single host |
| Auto-deploy on tag | **NONE** — release workflow only publishes a GitHub Release page |
| Manual deployment script | `scripts/bootstrap.sh` invoked by an operator on the target host |
| Rollback procedure documented | YES — [docs/runbooks/recovery.md](../../docs/runbooks/recovery.md) |
| Rollback procedure tested | **No evidence** of a drill |
| Canary / blue-green | **N/A** for current single-host model |
| Health gate after deploy | `scripts/smoke.sh` (8 checks, run manually after deploy per [docs/runbooks/phase1-acceptance.md](../../docs/runbooks/phase1-acceptance.md)) — not invoked by CI |

**Grade: F** for "deployment automation as enterprise reviewers mean the term." The pipeline ends at "GitHub Release published"; everything beyond is manual host-side operator work. This is appropriate for Phase 1's single-deployer model and is **the largest single Phase 2 expansion area**.

---

## 8. Secrets in CI

| Concern | Evidence | Status |
|---|---|---|
| Hardcoded secrets in workflows | `grep -rn 'secrets\.'` returns **only** `${{ secrets.GITHUB_TOKEN }}` ([pr-validation.yml:24](../../.github/workflows/pr-validation.yml)) | **CLEAN** |
| Custom-repo-secrets in use | None — no `secrets.HONCHO_TOKEN`, `secrets.LITELLM_*`, etc. referenced | CLEAN |
| Default workflow permissions | `default_workflow_permissions: read` at repo level (`gh api …/actions/permissions/workflow`) | GOOD |
| Per-workflow `permissions:` block | YES — all 4 workflows declare scoped perms | GOOD |
| Most-privileged workflow | `release.yml` declares `contents: write` ([release.yml:12-13](../../.github/workflows/release.yml)) — required for creating releases. No `id-token` (good — release isn't OIDC-authenticating to a cloud). | APPROPRIATE |
| Other workflow perms | `ci.yml`: `contents: read` ([:35-36](../../.github/workflows/ci.yml)); `pr-validation.yml`: `pull-requests: read`, `contents: read` ([:11-13](../../.github/workflows/pr-validation.yml)); `secret-scan.yml`: `contents: read`, `pull-requests: read` ([:19-21](../../.github/workflows/secret-scan.yml)) | GOOD (read-default everywhere except release) |
| **Third-party action SHA-pinning** | **NONE** — every external action uses floating tag refs: `actions/checkout@v6`, `astral-sh/setup-uv@v7`, `amannn/action-semantic-pull-request@v6`, `softprops/action-gh-release@v3`, `hadolint/hadolint-action@v3.3.0`, `github/codeql-action/upload-sarif@v3`, `actions/setup-python@v6`, `actions/upload-artifact@v7` | **WEAK** — tag-spoofing/repo-takeover risk on every PR |
| Repo Actions SHA-pinning enforcement | `gh api …/actions/permissions` → `sha_pinning_required: false` | **OFF** |
| Allowed actions scope | `allowed_actions: all` | OPEN (no allow-list) |

**The action-pinning gap is the single most actionable supply-chain finding.** Recommendation:
1. Pin every `uses:` to a commit SHA (e.g. `actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1`).
2. Use Dependabot's existing `github-actions` ecosystem to keep them updated (it understands SHA-pinned actions and will bump them with a comment in the PR body showing the version mapping).
3. Set `sha_pinning_required: true` at the repo level: `gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions --field sha_pinning_required=true` (note: requires `allowed_actions: selected`).

---

## 9. Workflow security

| Concern | Finding | Status |
|---|---|---|
| `pull_request_target` usage | **None** — grep returns zero hits | CLEAN |
| Workflows editable by external contributors | Repo is **private** (`visibility: private` from `gh api`) — externals can't fork-and-PR. Internal contributors guarded by CODEOWNERS on `.github/workflows/` ([CODEOWNERS:16](../../.github/CODEOWNERS)) | OK |
| Workflow files require code-owner review | Yes (CODEOWNERS) — but neutralised by `required_approving_review_count: 0` | weak |
| `concurrency:` groups | Present on `ci.yml` and `secret-scan.yml`; **absent on `pr-validation.yml` and `release.yml`** | partial |
| Race condition risk — release.yml | Pushing `v1.0.0` and then immediately pushing `v1.0.0` again (force-pushed tag) would queue two concurrent release jobs racing on `gh release create`. Low-likelihood, but a concurrency group costs nothing | minor |
| Race condition risk — pr-validation.yml | Multiple rapid PR title edits queue stacked runs (cheap, ~2s each, but wasteful) | minor |
| `continue-on-error` on required checks | `Lint Dockerfiles` (advisory hadolint, [ci.yml:165,173](../../.github/workflows/ci.yml)) and SARIF upload steps in secret-scan ([:76,84](../../.github/workflows/secret-scan.yml)) — the latter is fine (SARIF upload needs Code Scanning which is off), the former means a *required check* never enforces Dockerfile correctness | **misleading** — see §1.2 |
| Timeout discipline | Every job has `timeout-minutes` (2–10 min range) | GOOD |
| Workflow uses self-hosted runners | NO — all `ubuntu-latest` | GOOD (no runner-isolation concerns) |
| Action allowed actions list | None — `allowed_actions: all` | OPEN |
| `id-token: write` anywhere | NO — no OIDC federated workloads to GCP/AWS/etc. yet | n/a |
| Workflow scripts injected via untrusted input | `pr-validation.yml` consumes `${{ github.head_ref }}` via env var `BRANCH` ([:56-58](../../.github/workflows/pr-validation.yml)) and uses it inside `[[ "$BRANCH" =~ … ]]`. Env-var indirection prevents the classic GitHub-Actions script-injection bug | GOOD |

---

## 10. Observability of CI itself

| Concern | State |
|---|---|
| Run-time trending dashboard | NONE — no Datadog/Grafana/PR-comment summariser |
| Slow-run alerts | NONE |
| Failure-rate SLO | NONE |
| Cost (Actions minutes) tracking | NONE — no monthly report, no allotment guardrail; the only signal that "minutes are precious" is in workflow comments ([ci.yml:5-7](../../.github/workflows/ci.yml)) |
| Failed-CI alerts | NONE — failures live only in the GitHub UI; no Slack/email/Telegram bot ping despite the project shipping a Telegram bot |
| junit.xml consumed | uploaded as artifact only; no PR summary, no dashboard ingestion |
| Workflow run-time SLO docs | NONE |
| Actions cache hygiene | 16.6 MB / 16 entries — well under 10 GB limit; no automation around eviction |

**Grade: D.** The CI is *fast and quiet* but *blind to its own degradation*. A 3x runtime regression or a Dependabot-bump that doubles cache misses would go unnoticed until someone manually checked the Actions tab.

---

## 11. Phase 2 readiness recommendations (top 12, ordered by ROI)

| # | Recommendation | Effort | Why |
|---|---|---|---|
| 1 | **Enable Dependabot vulnerability alerts + automated security fixes** (`PUT /repos/.../vulnerability-alerts`, `PUT /repos/.../automated-security-fixes`) | 2 API calls, 0 minutes | Free, immediate; you already trust Dependabot for bumps |
| 2 | **Add `pip-audit` job to CI** (new job in `ci.yml`, `pip install pip-audit && pip-audit --strict`) | XS | CVE coverage of installed deps; no GHAS dep |
| 3 | **Enable ruff security rules** — add `select = ["E","F","I","S","B"]` to `[tool.ruff]` in `pyproject.toml` | XS | Bandit-equivalent + lint baseline; same ruff binary, no new tool |
| 4 | **SHA-pin every `uses:`** in all 4 workflow files (Dependabot maintains SHA pinning natively) | S | Closes the action-substitution attack vector |
| 5 | **Add `concurrency:` to `release.yml` and `pr-validation.yml`** | XS | Symmetry + race-prevention |
| 6 | **Fix hadolint-as-required-check theatre** — either drop `continue-on-error` or remove `Lint Dockerfiles` from the required-checks list | XS | Stop emitting "green-required-check" signal for a check that never blocks |
| 7 | **Add `pre-commit run --all-files` as a CI job** | XS | Closes the contributor-skipped-hooks gap; gives one canonical "did the pre-commit pass?" signal |
| 8 | **Add coverage with a non-blocking gate**: `pytest-cov` + `--cov-fail-under=70` (set initial threshold to current baseline, raise over time) | S | Prevents new untested code; baseline is cheap |
| 9 | **Add `trivy` container scan** — once Phase 2 starts publishing images, scan `deploy/Dockerfile.hermes` + sandbox base | M | Closes container-CVE blind spot |
| 10 | **Add SBOM generation** (`anchore/sbom-action` produces CycloneDX) and **attach to GitHub Release** | S | Required for many enterprise procurement reviews; near-zero cost |
| 11 | **Tighten branch protection when the second contributor lands**: `required_approving_review_count: 1`, `enforce_admins: true`, `required_signatures: true`, `required_linear_history: true` | XS (API calls) | Single biggest leap toward enterprise-grade |
| 12 | **Add OpenSSF Scorecard workflow** (weekly cron, upload to dependency-graph) | XS | Gives external auditors a single number to grade against |

Reintroducing CodeQL (per [docs/ci-cd.md:181](../../docs/ci-cd.md)'s own future-self note) depends on whether GHAS is licensed for this private repo; Semgrep OSS is the credible non-GHAS substitute.

---

## 12. Cross-references

- [.github/workflows/ci.yml](../../.github/workflows/ci.yml)
- [.github/workflows/pr-validation.yml](../../.github/workflows/pr-validation.yml)
- [.github/workflows/release.yml](../../.github/workflows/release.yml)
- [.github/workflows/secret-scan.yml](../../.github/workflows/secret-scan.yml)
- [.github/dependabot.yml](../../.github/dependabot.yml)
- [.github/CODEOWNERS](../../.github/CODEOWNERS)
- [.pre-commit-config.yaml](../../.pre-commit-config.yaml)
- [.gitleaks.toml](../../.gitleaks.toml)
- [pyproject.toml](../../pyproject.toml)
- [SECURITY.md](../../SECURITY.md)
- [CHANGELOG.md](../../CHANGELOG.md)
- [docs/ci-cd.md](../../docs/ci-cd.md)
- [docs/release-process.md](../../docs/release-process.md)
- [docs/runbooks/phase1-acceptance.md](../../docs/runbooks/phase1-acceptance.md)
- [docs/runbooks/recovery.md](../../docs/runbooks/recovery.md)
