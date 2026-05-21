# PR Landing + Docker Skip-Guard — Implementation Plan

> **2026-05-21 REVISION — Tasks 3 + 4 OBSOLETE.** PR #112 was squash-merged 2026-05-20T17:20:36Z (verified via `gh pr view 112 --json state` → "MERGED"). Task 3 is replaced by a single audit-log fact-recording line; Task 4 is deleted. Tasks 5 + 6 (open + merge framing-2 PR) remain authoritative. Orchestration governed by `docs/superpowers/specs/2026-05-21-execution-strategy-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the mechanical-tier work — land the 35 audit-closure commits on `feat/framing-2-bolt-on` via a new PR (PR #112 covered the *phase-0a-gcp-migration* branch and merged 2026-05-20), and harden the one hard-failing local test with a Docker skip-guard so it cleanly skips when the Compose stack is absent.

**Architecture:** Two changes in one PR. Stream B test hardening (mirror existing `_docker_available()` pattern from `tests/integration/test_hermes_plugin_loader_smoke.py:166`) lands as a commit on the current branch; then the branch opens a PR under user-explicit authorization (no autonomous `git push` or `gh pr create`).

**Tech Stack:** pytest 8.x, pytest markers, gh CLI, GitHub Actions (no new deps).

**Source spec:** `docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md` (committed `ce3ee40`), Threads #1 + #2.

---

## Task 1: Add `_docker_available()` skip-guard to `test_sandbox_isolation.py`

**Subagent:** general-purpose
**Model:** sonnet (standard implementation)
**Rationale:** Mechanical pattern-mirror; no architecture call.

**Files:**
- Modify: `tests/integration/test_sandbox_isolation.py:1-48`
- Reference (DO NOT modify): `tests/integration/test_hermes_plugin_loader_smoke.py:160-200` — proven `_docker_available()` pattern to mirror

- [ ] **Step 1: Write the failing test verifying the skip-guard fires**

Add this test ABOVE the existing two tests in `tests/integration/test_sandbox_isolation.py`:

```python
def test_docker_skip_guard_fires_when_docker_absent(monkeypatch):
    """The skip-guard MUST cause Docker tests to skip (not fail) when docker is unreachable."""
    import shutil
    # Simulate docker absent
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "docker" else "/usr/bin/" + name)
    # Reset the module-level cache so the probe re-runs
    import tests.integration.test_sandbox_isolation as mod
    mod._DOCKER_AVAILABLE_CACHE = None
    assert mod._docker_available() is False
```

- [ ] **Step 2: Run test to verify it fails (red)**

Run: `uv run --extra dev pytest tests/integration/test_sandbox_isolation.py::test_docker_skip_guard_fires_when_docker_absent -v`
Expected: FAIL with `AttributeError: module 'tests.integration.test_sandbox_isolation' has no attribute '_DOCKER_AVAILABLE_CACHE'`

- [ ] **Step 3: Implement the skip-guard (mirror `_docker_available()` from `test_hermes_plugin_loader_smoke.py:166`)**

Replace the entire content of `tests/integration/test_sandbox_isolation.py` with:

```python
"""Verify shell-sandbox isolation: no host network, no host FS escape.

Tests in this module exec into the live `deploy/docker-compose.yml` stack
via `docker compose exec`, so they hard-require a running docker daemon
AND the Compose v2 plugin. We apply `@pytest.mark.docker` + a lazy
`_docker_available()` probe so the suite cleanly SKIPS (does not FAIL)
on hosts without docker — mirroring the pattern proven in
`tests/integration/test_hermes_plugin_loader_smoke.py:166`.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

_DOCKER_AVAILABLE_CACHE: bool | None = None


def _docker_available() -> bool:
    """True iff `docker info` succeeds AND `docker compose version` succeeds.

    Lazy + process-cached: collection touches this once per process, not
    once per test, keeping `pytest --collect-only` fast on docker-less
    hosts.
    """
    global _DOCKER_AVAILABLE_CACHE
    if _DOCKER_AVAILABLE_CACHE is not None:
        return _DOCKER_AVAILABLE_CACHE
    if shutil.which("docker") is None:
        _DOCKER_AVAILABLE_CACHE = False
        return False
    try:
        info = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5, check=False
        )
        if info.returncode != 0:
            _DOCKER_AVAILABLE_CACHE = False
            return False
        ver = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        _DOCKER_AVAILABLE_CACHE = ver.returncode == 0
        return _DOCKER_AVAILABLE_CACHE
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _DOCKER_AVAILABLE_CACHE = False
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon or `docker compose` CLI not available",
    ),
]


def test_docker_skip_guard_fires_when_docker_absent(monkeypatch):
    """The skip-guard MUST cause Docker tests to skip (not fail) when docker is unreachable."""
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "docker" else "/usr/bin/" + name)
    import tests.integration.test_sandbox_isolation as mod
    mod._DOCKER_AVAILABLE_CACHE = None
    assert mod._docker_available() is False
    # Reset cache so subsequent live runs re-probe
    mod._DOCKER_AVAILABLE_CACHE = None


def test_shell_sandbox_no_network():
    """`curl example.com` from inside shell-sandbox must fail."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "exec",
            "-T",
            "shell-sandbox",
            "curl",
            "-fsS",
            "--max-time",
            "3",
            "https://example.com",
        ],
        capture_output=True,
    )
    assert out.returncode != 0, "shell-sandbox should NOT have internet access"


def test_shell_sandbox_no_root_fs_write():
    """Writing to / from inside shell-sandbox must fail (read-only)."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "exec",
            "-T",
            "shell-sandbox",
            "bash",
            "-c",
            "echo test > /etc/should-not-write 2>&1; echo $?",
        ],
        capture_output=True,
        text=True,
    )
    assert "1" in out.stdout or "Permission denied" in out.stdout or "Read-only" in out.stdout
```

- [ ] **Step 4: Run the new test (green) and verify the two original tests now SKIP cleanly on docker-less hosts**

Run: `uv run --extra dev pytest tests/integration/test_sandbox_isolation.py -v`
Expected (on a host without docker):
- `test_docker_skip_guard_fires_when_docker_absent PASSED`
- `test_shell_sandbox_no_network SKIPPED (docker daemon or `docker compose` CLI not available)`
- `test_shell_sandbox_no_root_fs_write SKIPPED (...)`

If docker IS available locally, all 3 PASS (the original two still execute their assertions).

- [ ] **Step 5: Confirm the marker is already registered in `pyproject.toml`**

Run: `grep -n '"docker:' pyproject.toml`
Expected: `pyproject.toml:39:  "docker: tests that require a running docker daemon ...` (already present, no edit needed)

- [ ] **Step 6: Commit the test-hardening change**

```bash
git add tests/integration/test_sandbox_isolation.py
git commit -m "$(cat <<'EOF'
test(sandbox): docker skip-guard on test_sandbox_isolation (Thread #2)

Mirrors the proven `_docker_available()` pattern from
test_hermes_plugin_loader_smoke.py:166. The previously hard-failing
`test_shell_sandbox_no_root_fs_write` now cleanly SKIPS when the docker
daemon or `docker compose` CLI is unreachable, instead of returning
non-zero exit code.

Root cause CORRECTED vs prior audit synthesis: the test needs the
`deploy/docker-compose.yml` stack to be running, NOT a missing
`secrets/litellm-db.env` (the env file is a separate Phase 1.6 LiteLLM
dependency, unrelated to sandbox isolation).

Marker `docker` already registered at pyproject.toml:39.
EOF
)"
```

If the pre-commit hook fails: per repo protocol, do NOT `--amend`. Fix the issue, re-stage, create a NEW commit.

---

## Task 2: Add correction addendum to verification synthesis row 1

**Subagent:** general-purpose
**Model:** haiku (mechanical doc edit)
**Rationale:** Pure markdown append; no judgment call.

**Files:**
- Modify: `audit/2026-05-21-verification-synthesis.md` (append addendum section)

- [ ] **Step 1: Append the correction addendum**

Use Edit tool to add this block immediately after the per-claim verdict table (after line 27):

```markdown

## Addendum 2026-05-21: row 1 root-cause correction

The "1 pre-existing local-env failure" in row 1 was originally attributed to a missing `secrets/litellm-db.env`. Re-verification this session confirmed the actual root cause: `test_shell_sandbox_no_root_fs_write` at `tests/integration/test_sandbox_isolation.py:30-48` execs into the live `deploy/docker-compose.yml` stack via `docker compose exec`, so it hard-fails when the Compose stack is not running. The `litellm-db.env` dependency is in a different test (`test_p1_2_judge_panel.py`) and degrades gracefully via "sk-test" fallback.

Fix landed: skip-guard applied to `test_sandbox_isolation.py` mirroring the `_docker_available()` pattern from `tests/integration/test_hermes_plugin_loader_smoke.py:166`. Marker `docker` already registered at `pyproject.toml:39`. Affected tests now cleanly SKIP on docker-less hosts. See `docs/superpowers/plans/2026-05-21-pr-merge-and-docker-skip-guard.md` Task 1.
```

- [ ] **Step 2: Commit the addendum**

```bash
git add audit/2026-05-21-verification-synthesis.md
git commit -m "$(cat <<'EOF'
docs(audit): synthesis row-1 root-cause addendum (Thread #2)

Correct the row-1 attribution from `secrets/litellm-db.env` missing to
the actual root cause: `test_sandbox_isolation.py` requires the
`deploy/docker-compose.yml` stack. Fix applied in prior commit.
EOF
)"
```

---

## Task 3: Record PR #112 status (REVISED — fact-only)

**Subagent:** general-purpose
**Model:** haiku (1-line audit append)
**Rationale:** PR #112 was already merged 2026-05-20T17:20:36Z. Original verify-and-conditionally-merge logic obsolete. This task simply records the fact in the audit trail.

**Files:**
- Modify: `audit/2026-05-21-verification-synthesis.md` (append) OR create `audit/2026-05-21-pr-112-merge-record.md`

- [ ] **Step 1: Append fact-recording line**

Append (or write new memo) with text:

```
## PR #112 disposition (recorded 2026-05-21)

PR #112 ("feat(phase-0a): gcp always-online migration — vm live, 10/10 containers, chaos test passed") was squash-merged to `main` on 2026-05-20T17:20:36Z. Verified via `gh pr view 112 --json state` → `"state":"MERGED"`. No further action required for this PR. Plan A Task 4 (conditional squash-merge) is deleted as obsolete.
```

- [ ] **Step 2: Commit**

```bash
git add audit/2026-05-21-verification-synthesis.md
git commit -m "$(cat <<'EOF'
docs(audit): record PR #112 already merged 2026-05-20 (Plan A Task 3 revision)

Verified via `gh pr view 112 --json state` → MERGED.
Plan A Task 4 (conditional squash-merge) deleted as obsolete.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: DELETED

Task 4 (conditional squash-merge of PR #112) is deleted. PR #112 was already merged 2026-05-20T17:20:36Z. See Task 3 audit record.

---

## Task 5: Open PR for `feat/framing-2-bolt-on`

**Subagent:** general-purpose
**Model:** sonnet
**Rationale:** `gh pr create` is a public-visibility action — requires explicit user authorization per CLAUDE.md.

**Files:** None — `git push` + `gh pr create` against remote.

- [ ] **Step 1: User authorization gate**

DO NOT proceed without verbatim user authorization (one of):
- "Open PR for feat/framing-2-bolt-on"
- "GO to push + open PR"
- "Push and PR"

If the user has not authorized, HALT.

- [ ] **Step 2: Push the branch to origin**

Run: `git push -u origin feat/framing-2-bolt-on`
Expected: `Branch 'feat/framing-2-bolt-on' set up to track 'origin/feat/framing-2-bolt-on'` and push summary.

If push is rejected (non-fast-forward): HALT, do NOT force-push. Surface to user; likely indicates a parallel session pushed conflicting commits.

- [ ] **Step 3: Open the PR**

Run:
```bash
gh pr create --base main --head feat/framing-2-bolt-on \
  --title "feat: framing-2 audit-closure sprint (36 commits, Persistence Trap + Phase 2 Postgres + Model Armor + A2A spike specs)" \
  --body "$(cat <<'EOF'
## Summary

Squash-merge target: 36 commits closing the architecture-research gap analysis (7 work streams shipped in one sprint).

**Verified evidence** (see `audit/2026-05-21-verification-synthesis.md` for the 15-row per-claim table):
- 8/8 Persistence Trap contract tests pass (`38856f2`)
- J9 observability gauge at `lib/durability/runtime_detectors.py:51` (`56336f5`)
- F34/F36 production handlers (`a20dd58`)
- Phase 2 Postgres sub-module `terraform validate` clean (`8cf3270`, plan-only — apply gated)
- Model Armor sub-module `terraform validate` clean (`0911028`, plan-only — apply gated)
- ADR-0010 Firecracker tier (`d94ec3e`)
- Phase 3 Governor design (`33dd934`)
- Verification synthesis (`7678746`) + row-1 root-cause addendum
- Docker skip-guard on `test_sandbox_isolation.py` (this PR's Stream B contribution)
- Outstanding-threads roadmap spec (`ce3ee40`) + 3 implementation plans

**Out of scope** (gated to follow-up PRs):
- Stream A apply (Postgres + Model Armor + GCS bucket) — requires user-explicit auth + Gemini-CLI delegation per `docs/superpowers/plans/2026-05-21-j1-unblock-sequence.md`
- A2A spike Day 0-10 implementation — separate plan at `docs/superpowers/plans/2026-05-21-a2a-spike-day-0-10.md`

## Test plan

- [ ] All required CI checks green on the head SHA
- [ ] Lint + ruff-format authoritative hooks clean (pinned versions)
- [ ] Postgres + Model Armor + root `terraform validate` clean
- [ ] `tests/integration/test_sandbox_isolation.py` SKIPS on docker-less CI hosts; runs on hosts where the Compose stack is up

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected output: a PR URL like `https://github.com/<owner>/<repo>/pull/<N>`.

- [ ] **Step 4: Wait for CI green (≤ 10 min)**

Run: `gh pr checks $(gh pr view --json number --jq .number) --watch`
Expected: all required checks reach SUCCESS within 10 minutes. If any fail, HALT and surface logs.

---

## Task 6: Squash-merge the framing-2 PR (CONDITIONAL on Task 5 green)

**Subagent:** general-purpose
**Model:** sonnet
**Rationale:** Squash-merge to main — hard-to-reverse, requires explicit user authorization.

**Files:** None — `gh` operation against remote.

- [ ] **Step 1: User authorization gate**

DO NOT proceed without verbatim user authorization (one of):
- "Squash-merge the framing-2 PR"
- "Merge it"
- "Land it"

- [ ] **Step 2: Squash-merge per repo convention**

Run: `gh pr merge $(gh pr view --json number --jq .number) --squash --delete-branch`
Expected: `✓ Squashed and merged pull request #<N>` + `✓ Deleted branch feat/framing-2-bolt-on`

- [ ] **Step 3: Verify the merge landed on main**

Run: `gh api repos/:owner/:repo/commits/main --jq '.sha,.commit.message' | head -5`
Expected: most-recent commit's message starts with the squashed PR title.

- [ ] **Step 4: Update MEMORY index**

Append to `~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/MEMORY.md`:

```markdown
- [Framing-2 sprint merged 2026-05-21](project_state_2026-05-21_framing2_merged.md) — feat/framing-2-bolt-on squash-merged at <new-main-sha>; 36 commits landed; J1 still pending (#5 + #4)
```

And create the referenced memory file with the relevant details.

---

## Sequencing summary

```
Task 1 (test hardening)           → Task 2 (synthesis addendum)
                                    ↓
Task 3 (gh pr view 112)             → Task 4 (CONDITIONAL: merge #112) ─┐
                                                                         │
Task 5 (open framing-2 PR — AUTH)  → Task 6 (CONDITIONAL: squash — AUTH)┘
```

Tasks 1 + 2 land locally without auth. Tasks 3 is read-only. Tasks 4, 5, 6 each have an explicit user-authorization gate before any remote-affecting action.

## Verification commands (end-of-plan)

After all tasks complete:
- `git log --oneline main..feat/framing-2-bolt-on | wc -l` → 0 (branch merged)
- `gh pr view 112 --json state --jq .state` → `MERGED` (if Task 4 ran)
- `gh pr list --base main --head feat/framing-2-bolt-on --json state --jq '.[].state'` → `MERGED`
- `git checkout main && git pull` → updated to new HEAD
- New `main` HEAD passes the full test suite: `uv run --extra dev pytest -q` → all green, sandbox isolation tests appropriately PASS or SKIP based on docker availability
