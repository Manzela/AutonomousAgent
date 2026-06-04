# Agent Implementation Runbook — AutonomousAgent SDLC

**Audience:** Any LLM agent (Opus, Gemini, future) executing PRD tasks autonomously.
**Status:** Rigid. These are learned-from-failure rules, not suggestions.
**Update discipline:** Add a new rule only after it prevented a real failure. Never add rules speculatively.

---

## Part 0 — Before You Write a Single Line

### 0.1 Read these files in order. No skipping.

```
1. CLAUDE.md                                           # project constraints + GCP project id
2. audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md  # acceptance oracles
3. audit/2026-06-03-prd-code-truth/findings-reconciled.md                 # what's DONE vs ABSENT
4. HAND-OFF.md                                         # current session state
```

**DO NOT start building until you have read all four.** The reconciled findings table will tell you what is real code vs stub. Building on top of a stub is the most common way to produce work that tests green but does nothing.

### 0.2 Identify the exact stub or absent code you are replacing.

Every P0/P1 task has a named stub site. Find it before writing anything:

| Task | Stub location |
|---|---|
| SP-03 clarify | `lib/anchors/__init__.py:220-224` — `return None` block |
| SP-02 decompose | `app/core/graph_state.py:72,81` — `TaskGraph`/`TaskNode` are unused TypedDicts |
| SP-04 INPUT_REQUIRED | `app/core/orchestrator.py:364` — `INPUT_REQUIRED → FAILED` mapping |
| SP-05 sandbox | `app/core/graph.py:175` — `execute` node calls in-process exec, not `AbstractSandbox` |
| SP-06 eval gate | absent — no `DAGMetric` in `app/` or `lib/` |

**DO NOT create a new file where an existing stub already lives.** Replace the stub in place.

---

## Part 1 — The C9 Review Rule (hardest rule, most failures)

### 1.1 Rule statement

Every P0/P1 implementation produced by model X MUST be reviewed by a model of a different class. The class is the specific model identifier (operator-resolved 2026-06-01).

```
Sonnet reviewing Sonnet → BLOCKED
Opus reviewing Opus     → BLOCKED
Sonnet reviewing Opus   → ALLOWED
Opus reviewing Gemini   → ALLOWED
Gemini reviewing Sonnet → ALLOWED
```

**Vendor-sameness does NOT make same-class.** Claude Sonnet and Claude Opus are different classes.

### 1.2 How to run C9 review

1. Spawn a separate agent of a different model class with ONLY the code under review and specific adversarial questions.
2. The reviewer must try hard to find bugs. Brief it to DEFAULT to refuted/REQUEST_CHANGES, not APPROVE.
3. Record the reviewer model in the PR description under the literal line `Reviewer model: <model-id>`.
4. CI enforces this via `.github/workflows/c9-reviewer-class-gate.yml`. The check reads `config/c9-reviewer-allowlist.txt` — if your reviewer model is not listed, the gate fails.

### 1.3 What the reviewer must check (minimum)

For any code that handles caller-provided strings in URL paths or shell commands:
- **Path traversal**: does `workspace_slug = "../../admin"` reach an unintended endpoint?
- **Empty-string bypass**: does `var=""` pass a check that should fail?
- **Regex anchoring**: does `$` admit a trailing `\n`? Use `\Z` instead.

For any HTTP client code:
- **Multiple `.json()` calls on one response**: `resp.json().get("k", resp.json() if ...)` crashes on list responses — `list` has no `.get`. Parse ONCE: `data = resp.json(); return data.get(...) if isinstance(data, dict) else data`.

For any local-store + remote-API pattern:
- **Orphan state**: if you write to `self._store[card.id]` BEFORE the REST call, a failed POST leaves a zombie card. Wrap the REST call in `try/except`, roll back with `self._store.pop(card.id, None)` in the except block.

### 1.4 After round 1 REQUEST_CHANGES

Fix ALL P0 and P1 findings before requesting round 2. Do not argue with the reviewer. Do not mark a finding "by design" unless the finding explicitly says the threat model is intentional-by-design.

---

## Part 2 — Pre-commit Hook Reality

### 2.1 Ruff auto-modifies your files. This is expected. This is not an error.

When a commit attempt fails with `ruff: files were modified by this hook`, the hook FIXED the files on disk. The commit did NOT happen. Do this:

```bash
git add <the files ruff modified>   # re-stage the fixed versions
git commit -m "..."                  # retry the commit
```

**DO NOT** re-edit the files ruff just fixed. **DO NOT** retry the commit without re-staging. **DO NOT** use `--no-verify` to skip ruff.

### 2.2 detect-secrets triggers on strings that look like secret names.

`"PREDEPLOY_SECRETS_RESULT"` contains the word `SECRET`. detect-secrets flags it. Fix:

```python
"SECRETS": "PREDEPLOY_SECRETS_RESULT",  # pragma: allowlist secret
```

The pragma must be on the SAME line as the flagged string. A pragma on the next line does nothing.

### 2.3 Pre-commit order matters. When any hook modifies files, ALL modifications must be re-staged.

If hook A modifies `foo.py` and hook B separately modifies `bar.py`, you must `git add foo.py bar.py` before retrying. Missing one means the commit contains the un-fixed version.

### 2.4 `--no-verify` is FORBIDDEN.

Never use `git commit --no-verify`. If a hook fails, diagnose and fix the underlying issue. If a hook is incorrect (e.g., a false-positive in detect-secrets), add a `# pragma: allowlist secret` annotation — do not bypass the whole hook system.

---

## Part 3 — C4 Dead-Code Gate

### 3.1 Module-level entrypoints DO NOT cover nested methods.

`config/dead_code_entrypoints.txt` entry `lib.evaluators.judge_calibration` covers top-level symbols in that module (`class JudgeCalibrationMonitor`, `class JudgeQuarantineRegistry`) but NOT their methods. The CI gate will flag `JudgeCalibrationMonitor.score` as unreachable.

For every nested method that is only called from outside the module (e.g., by a deferred cron or by future SP-XX), add explicit entries:

```
lib.evaluators.judge_calibration
lib.evaluators.judge_calibration:JudgeCalibrationMonitor.score
lib.evaluators.judge_calibration:JudgeQuarantineRegistry.quarantine
lib.evaluators.judge_calibration:JudgeQuarantineRegistry.is_quarantined
```

**Rule of thumb:** if a method is public, used only externally, and your PR adds it, add an explicit entrypoint line.

### 3.2 Test the dead-code gate before every commit.

```bash
git diff origin/main...HEAD | uv run --extra dev python scripts/ci/dead_code_gate.py --diff -
```

If it reports failures, add the missing entrypoints. Do not guess — the gate output lists the exact symbol name.

### 3.3 The 4-eyes rule for entrypoints.txt.

`config/dead_code_entrypoints.txt` changes MUST be in a SEPARATE commit from the code they exempt. Two commits, never one. The CI `acceptance-frozen.yml` enforces this diff isolation.

---

## Part 4 — SHA256SUMS / C8 / C10

### 4.1 Sequence: code commit THEN sha256 commit. Never together.

```
commit 1: feat(xxx): <implementation + test + acceptance YAML>
commit 2: chore(acceptance): pin SP-XX.yaml sha256 in SHA256SUMS (C8/C10 4-eyes)
```

Combining both changes in one commit fails the `acceptance-frozen.yml` gate.

### 4.2 Compute the SHA only after the acceptance YAML is finalized.

The SHA is computed on the final YAML. If C9 review requires a change to the YAML, you must recompute. Computing the SHA before C9 review is always wrong — it will be stale after round-1 fixes.

```bash
sha256sum audit/acceptance/SP-XX.yaml
# then paste the output into SHA256SUMS as:
# <hash>  SP-XX.yaml
```

### 4.3 SHA256SUMS merge conflicts.

When two branches both append to SHA256SUMS and one is merged first, the second branch gets a conflict. Resolution:

```python
# Python one-liner: keep both entries, one per file
with open("audit/acceptance/SHA256SUMS") as f:
    content = f.read()
# Remove conflict markers, deduplicate lines, sort
```

Use `GIT_EDITOR=true git rebase --continue` (not `git rebase --continue --no-edit` — `--no-edit` is not a valid rebase flag).

---

## Part 5 — Conventional Commits

### 5.1 The subject first character after `type(scope): ` MUST be lowercase.

CI enforces `^[a-z]` as the first character of the subject. This blocks:

```
feat(eval): SP-J1 judge-calibration...   ← BLOCKED (S is uppercase)
```

Always:

```
feat(eval): sp-j1 judge-calibration...   ← OK
```

### 5.2 Type vocabulary (most common).

| Type | When |
|---|---|
| `feat` | new behavior, new file, new oracle |
| `fix` | corrects a bug in existing behavior |
| `chore` | SHA256SUMS pin, dependency bump, CI config |
| `docs` | documentation only |
| `test` | test-only change |
| `refactor` | no behavior change |

### 5.3 Scope must be specific.

`feat(board):` not `feat(app):`. The scope is the subsystem, not the repo.

---

## Part 6 — YAML Files

### 6.1 Backtick cannot start a YAML plain scalar.

PyYAML raises `ScannerError: found character '`' that cannot start any token` if a value begins with a backtick. This also applies inside the value if it follows certain tokens. Fix: remove backticks from YAML values or quote the string.

```yaml
# WRONG
rationale: `src/payments/billing.py` is outside the declared scope.

# CORRECT
rationale: src/payments/billing.py is outside the declared scope.
```

### 6.2 Validate YAML before committing.

```bash
python -c "import yaml; yaml.safe_load(open('path/to/file.yaml'))"
```

Run this on any YAML file you created or modified. A parse error caught before commit saves a full hook cycle.

---

## Part 7 — Shell Script Security

### 7.1 Empty-string bash override bypass.

In bash, `[[ "$var" -ne 0 ]]` (arithmetic context) coerces an empty string to 0. An empty override variable PASSES the check and silently bypasses verification. This is a P1 security bug.

```bash
# WRONG — empty string coerces to 0 (passes)
[[ "${OVERRIDE}" -ne 0 ]] && FAILURES+=("check-name")

# CORRECT — only exact "0" passes; empty string = fail
if [[ -n "${OVERRIDE+x}" ]]; then
    raw="${OVERRIDE}"
    if [[ "$raw" == "0" ]]; then
        result=0
    else
        result=1   # includes empty string
    fi
fi
```

### 7.2 Glob patterns for secrets checks.

`find secrets/ ! -name "*.sops*"` matches any filename CONTAINING `.sops` — including `token.sops.evil`. An attacker can bypass the check by naming a plaintext file `secret.sops.evil`.

Use `git ls-files` (only tracked files) + bash `case` suffix pattern:

```bash
while IFS= read -r f; do
    case "$f" in
        secrets/README.md|secrets/.gitignore) continue ;;
        *.sops) continue ;;   # suffix-only match: "token.sops.evil" does NOT match
        *) echo "plaintext: $f"; failed=1 ;;
    esac
done < <(git ls-files secrets/)
```

### 7.3 `$` vs `\Z` in Python regex.

`re.compile(r"^[A-Za-z0-9_-]{1,256}$")` — the `$` anchor matches before a trailing `\n`. The string `"valid-slug\n"` passes. Use `\Z` to require end-of-string with no exceptions:

```python
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,256}\Z")
```

### 7.4 URL path interpolation from caller-provided strings.

If a caller provides `workspace_slug` and you build `/api/v1/workspaces/{workspace_slug}/...`, the slug `../../admin` (after httpx normalization) reaches `/api/admin/...`. Validate at constructor time:

```python
if not _SLUG_RE.match(workspace_slug):
    raise ValueError(f"workspace_slug {workspace_slug!r} is not a valid slug")
```

---

## Part 8 — Test Discipline

### 8.1 The iron law: no completion claim without a fresh test run in the same message.

```
✅  [run pytest] [see: 31/31 pass] → "All tests pass"
❌  "This should pass now" (no run)
❌  "Tests were passing before" (stale)
```

### 8.2 OTEL noise suppression.

Always prefix pytest runs with:

```bash
OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none uv run --extra dev python -m pytest ...
```

Without this, the test teardown emits ~30 lines of OTel retry noise that obscures actual failures.

### 8.3 Full suite before any PR.

Before creating a PR, run:

```bash
OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none uv run --extra dev python -m pytest tests/unit/ -q --tb=no
```

Report the exact count: `N passed, 0 failed`. If N is not ≥ the baseline from the previous session, you introduced a regression. Fix it before opening the PR.

### 8.4 Red-green for every new oracle.

For each test you write:
1. Run the test BEFORE implementing — it MUST FAIL (red). If it passes, the test does not cover the behavior.
2. Implement.
3. Run again — it MUST PASS (green).

A test that passes before implementation is either testing the wrong thing or testing something already implemented.

### 8.5 `pytest.skip()` calls in production test paths are a CI gate violation.

The `test-integrity-gate.yml` CI job grep-bans bare `pytest.skip()` calls (without `reason=`). Use `pytest.skip(reason="...")` or `pytest.mark.skip(reason="...")` only for legitimately deferred oracles, and document WHY in the acceptance YAML's `deferred:` section.

---

## Part 9 — Acceptance YAML Discipline

### 9.1 Write the acceptance YAML AFTER C9 review is APPROVED.

C9 round 1 may require code changes. Those changes may affect what the oracles test. Writing the YAML before APPROVED wastes SHA computation.

### 9.2 Acceptance YAML must list every deferred item explicitly.

Every item the PRD requires that your implementation defers MUST appear under `deferred:` in the YAML. Silent omission is the failure mode. Example:

```yaml
deferred:
  - GitHub two-way sync (Plane-Pro-only feature; not implemented in CE)
  - Live state-name resolution (CardStatus → Plane project state UUID)
```

### 9.3 `oracle_count` must match the actual test count.

Count oracles in the test file. Write the count. If parametrize expands one oracle to 6 cases, it is still 1 oracle (one assertion type) but you note the parametrized count in `proof.artifact`.

---

## Part 10 — State Consistency in Adapters

### 10.1 Write-after-confirm, not write-before-attempt.

Whenever you have a local cache (`self._store`) AND a remote (REST, DB), write to the local cache ONLY AFTER the remote call succeeds:

```python
# WRONG — orphan state on failure
self._store[card.id] = card
resp = self._client.post(url, json=payload)  # if this fails, _store has a zombie
resp.raise_for_status()

# CORRECT — rollback on failure
self._store[card.id] = card
try:
    resp = self._client.post(url, json=payload)
    resp.raise_for_status()
except Exception:
    self._store.pop(card.id, None)  # undo the pre-write
    raise
```

### 10.2 Parse HTTP responses once.

```python
# WRONG — calls resp.json() 3 times; crashes if response is a list (list.get does not exist)
return resp.json().get("results", resp.json() if isinstance(resp.json(), list) else [])

# CORRECT — parse once, branch on type
data = resp.json()
if isinstance(data, dict):
    return data.get("results", [])
if isinstance(data, list):
    return data
return []
```

---

## Part 11 — GCP and Infrastructure

### 11.1 GCP project is `autonomous-agent-2026`. Never `i-for-ai`.

Any `gcloud`, `terraform`, or `gsutil` command that targets `i-for-ai` for AutonomousAgent work is wrong. Use:

```bash
gcloud config set project autonomous-agent-2026
```

### 11.2 `terraform plan` before `terraform apply`. Always.

Never run `terraform apply` without first running `terraform plan` and sharing the plan output with the operator for approval. `plan -detailed-exitcode` exits 2 if there are planned changes — treat this as a prompt for operator review, not an error to suppress.

### 11.3 SP-23 / SP-R5 are operator-blocked.

These require a live `autonomous-agent-2026` environment with provisioned Cloud SQL, Pub/Sub, and WIF. Do not attempt to fake this with mocks and call it done. The acceptance criteria explicitly require `chaos test + IAM audit` (SP-23) and `restore-drill log + PITR confirmation from GCP console` (SP-R5). These are non-hermetic by design.

---

## Part 12 — What NOT to Build

These are explicitly forbidden regardless of how tempting they look:

| Forbidden | Why |
|---|---|
| Port `docs/research/seed/` MoE/PPO/reward into `app/` | SP-22 tombstoned this; LangGraph is the design |
| Collapse `AbstractSandbox`/`AbstractBoard`/`AbstractEmbedder` ABCs into adapters | The hybrid pattern is mandatory; CLAUDE.md §builder-agent rule |
| Add a `requirements.txt` alongside `pyproject.toml` | `uv` manages all deps via `pyproject.toml` |
| Use `git add -A` or `git add .` | Stage specific files by name; `-A` risks committing secrets or node_modules |
| Create new files under `scripts/` named `update_plan.py`, `update_rubric.py`, `update_brain.py` | Pre-commit blocks these; they are forbidden by the audit-rubric immutability rule |
| Edit an acceptance YAML that is already sha256-pinned | C8 frozen; change lands in a SEPARATE dated commit by a DIFFERENT actor |
| Use `subprocess` to call the LLM in tests | Tests must be hermetic; LLM calls go in the implementation, not the oracle |

---

## Part 13 — The P0 Exit Oracle

The single test that defines "P0 done" does not exist yet. When SP-03/02/04/05/06/R7 are all real, create:

```
tests/integration/test_e2e_ship_acceptance.py
```

It must run the full spine on a toy goal (e.g., "write a hello-world Python function") through:

```
clarify → sign-off → decompose → sandboxed build → eval gate → ship
```

And assert:
1. Eval gate blocks a PR whose test suite intentionally fails.
2. Mid-execute kill (SIGTERM) + resume produces identical output (`lossless rehydrate`).
3. An operator REJECT at sign-off parks the graph in `FAILED`, not `INTERRUPTED`.

This test is the gate. Until it passes, P0 is not done, regardless of what individual unit tests say.

---

## Appendix A — Checklist for Every PR

```
[ ] Read the acceptance criteria from the PRD before writing
[ ] Identified the exact stub/absent code being replaced (file:line)
[ ] Red-green verified every new oracle (fail before implement, pass after)
[ ] Dead-code gate: `git diff origin/main...HEAD | python scripts/ci/dead_code_gate.py --diff -`
[ ] Entrypoints.txt change in SEPARATE commit if any new public symbols were added
[ ] Full unit suite: `pytest tests/unit/ -q --tb=no` → N passed, 0 failed
[ ] Pre-commit hooks pass on commit attempt (ruff/detect-secrets/yaml)
[ ] If ruff auto-fixed files: re-staged and recommitted
[ ] C9 review: spawned different-model-class reviewer; APPROVED received
[ ] Acceptance YAML written AFTER C9 APPROVE
[ ] SHA256 computed on final YAML
[ ] SHA256SUMS update in SEPARATE commit with message `chore(acceptance): pin SP-XX.yaml sha256 in SHA256SUMS (C8/C10 4-eyes)`
[ ] PR title subject starts with lowercase letter
[ ] PR description contains `Reviewer model: <model-id>` as a literal line
```
