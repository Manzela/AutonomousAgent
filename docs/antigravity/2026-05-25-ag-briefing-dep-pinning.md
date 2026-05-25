# Antigravity Briefing — AG-3: Dependency Version Pinning (M8, M9, M10)
**Date:** 2026-05-25
**Model:** Gemini 3.1 Pro Preview
**Priority:** MEDIUM — prevents silent breakage on next dep update
**TIMING: Wait for PR #136 and `fix/a2a-security-hardening` to merge before starting.**
Both PRs modify `pyproject.toml`/`uv.lock`. Starting before they merge causes merge conflicts.

**Collision boundary:** Only `pyproject.toml` and `uv.lock`. No Python source files.

---

## 1. Context

Audit identified three dependency specification issues in `pyproject.toml`:

- **M8:** `google-cloud-modelarmor>=0.1.0` — pre-1.0 library with no upper bound; 0.2.x could have breaking API changes
- **M9:** `numpy>=2.4.6` — no upper bound; NumPy 3.x is expected to have breaking changes (similar to NumPy 1.x → 2.x migration)
- **M10:** `httpx>=0.27` listed in BOTH `[project.dependencies]` AND `[project.optional-dependencies] a2a` — redundant, creates confusion about the actual required version

---

## 2. Prerequisites Check

Before starting:
```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main && git pull

# Verify these PRs are merged (check the merge commits)
git log --oneline -5 | grep -E "PR #136|wire-scrubber|security-hardening"
```

Only proceed once PR #136 and `fix/a2a-security-hardening` are on main.

---

## 3. Fixes

### M8: Pin google-cloud-modelarmor

In `pyproject.toml`, find:
```toml
"google-cloud-modelarmor>=0.1.0",
```
Change to:
```toml
"google-cloud-modelarmor>=0.1.0,<0.2",
```

### M9: Pin numpy

In `pyproject.toml`, find:
```toml
"numpy>=2.4.6",
```
Change to:
```toml
"numpy>=2.4.6,<3",
```

### M10: Remove httpx duplicate from [a2a] extra

In `pyproject.toml`, find the `a2a` extra section. It has `"httpx>=0.27"`. This is already in `[project.dependencies]` as `"httpx>=0.27"`. Remove the duplicate from `[a2a]`:

Remove this line from the `a2a = [...]` list:
```toml
  "httpx>=0.27",
```

---

## 4. Execution

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main && git pull  # ensure latest (post-PR-#136 and post-security-hardening)
git checkout -b fix/dep-version-pinning

# Edit pyproject.toml with all 3 fixes

# Regenerate lockfile
uv lock

# Sync and verify tests still pass
uv sync --extra a2a --extra dev
uv run pytest lib/a2a/tests/ -v 2>&1 | tail -5
uv run pytest app/tests/ -v 2>&1 | tail -5

git add pyproject.toml uv.lock
git commit -m "fix(deps): pin google-cloud-modelarmor<0.2, numpy<3, remove httpx duplicate

M8: google-cloud-modelarmor>=0.1.0,<0.2 — pre-1.0 API can break in minor releases
M9: numpy>=2.4.6,<3 — NumPy 3.x expected breaking API changes (similar to 1.x→2.x)
M10: remove duplicate httpx entry from [a2a] extra (already in [project.dependencies])"

git push -u origin fix/dep-version-pinning

gh pr create \
  --title "fix(deps): pin google-cloud-modelarmor<0.2 + numpy<3 + remove httpx dup" \
  --base main \
  --body "Dependency pinning from 2026-05-25 security audit (M8, M9, M10). No code changes."
```

---

## 5. Acceptance Criteria

```bash
grep "modelarmor" pyproject.toml  # should show <0.2
grep "numpy" pyproject.toml       # should show <3
grep -c "httpx" pyproject.toml    # should show 1 (not 2)
uv run pytest lib/a2a/tests/ app/tests/ 2>&1 | tail -3  # all pass
```
