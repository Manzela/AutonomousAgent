# Phase 1 → Phase 2 Readiness: Test Coverage + Code Quality + Tech Debt Audit

**Target:** AutonomousAgent at `/Users/danielmanzela/RX-Research Project/AutonomousAgent`
**Commit:** `85512a3` (tag `phase1-accepted`)
**Date:** 2026-05-19
**Branch:** `main` (up to date with `origin/main`)
**Auditor methodology:** Read every `lib/*.py`, ran the full test suite with `coverage`, grep across `lib/ scripts/ deploy/ config/ docs/` for debt markers, verified each plugin's `register()` vs `plugin.yaml` declaration, validated config files against schemas, and inspected exception handling discipline. Every claim cites `file:line`. Nothing is inferred from documentation alone — production paths are verified by reading the actual hook implementations.

---

## TL;DR (grading per category)

| Category | Grade | Headline |
|---|---|---|
| 1. Test coverage | **B-** | 75% statement coverage on `lib/`; unit suite is dense; integration suite is mostly skipped (6 of 14 tests blanket-`pytest.mark.skip`'d) |
| 2. Test quality | **B** | No vacuous asserts; good `tmp_path` use; ~21% of unit tests are register-was-called wiring smoke tests that pass even when handler bodies are TODO stubs |
| 3. TODO/FIXME debt | **D** | 10 TODOs in shipped code (`lib/anchors/__init__.py` alone returns literal "TODO(...)" strings to users for `/lock /skip /confirm` plus the bare `/cancel` form); 2 in `lib/kanban/__init__.py`; 1 in `lib/durability/escalation.py` |
| 4. Dead code | **C** | 4 top-level lib modules (`scrubber`, `toolset_router`, `healthcheck`, `limits_validator`) are tested only — not imported by any plugin/hook/runtime; `queue_judge_dispatch` is called only by tests |
| 5. Complexity | **A-** | Largest file 336 LOC (`lib/memory/rejected.py`); no god-module; max function count 9 per file |
| 6. Naming + types | **B+** | Type hints high (most modules ≥80%); `lib/durability/__init__.py` register/_p1_3/_p1_4 untyped; public/private convention consistent |
| 7. Plugin contracts | **B** | All 6 plugins satisfy `provides_hooks` ↔ `register_hook` contract; BUT slash/CLI commands are not declared in plugin.yaml; AND `lib/evaluators/__init__.py:_on_post_tool_call` returns `None` instead of dispatching judges (production-path dead) |
| 8. Config validation | **B** | `limits.yaml` validates against schema; `toolsets.yaml` schema-less but loaded by `lib/toolset_router.py`; `scrubber-patterns.yaml` is loaded by `lib/scrubber.py` — but `Scrubber.from_config` is called only by tests, never by production |
| 9. Subprocess discipline | **A+** | Zero `os.system()`, zero `subprocess.*` in `lib/`, zero `shell=True` anywhere in `lib/` or `scripts/.py` |
| 10. Error handling | **A-** | 16 broad `except Exception` in `lib/`, all annotated `# noqa: BLE001` with explicit justification + log/fall-through; zero bare `except:`; zero silent swallowing |

**Overall: B-** — Architecture is sound and test discipline is real, but **multiple Phase-1-acceptance modules are dead-on-arrival at runtime** (anchors slash commands return TODO strings; evaluators post-tool dispatch is a no-op; scrubber + healthcheck + toolset_router are tested but unwired). These need to land before Phase 2 logic depends on them.

---

## 1. Test coverage

**Suite:** 170 unit tests pass, 1 skipped (OTel SDK absent); 8 integration tests pass, 6 skipped.

**Combined coverage on `lib/` (unit + integration):**

```
Name                                  Stmts   Miss  Cover   Missing
-------------------------------------------------------------------
lib/__init__.py                           0      0   100%
lib/anchors/__init__.py                  37      8    78%   20, 35, 40, 45, 70, 75, 80-81
lib/anchors/clarification_loop.py        32      0   100%
lib/anchors/intent_classifier.py         21      0   100%
lib/anchors/spec_store.py                46     10    78%   75-84
lib/anchors/task_spec.py                 32      0   100%
lib/durability/__init__.py               57     30    47%   55-58, 60, 66-68, 89-90, 96-127
lib/durability/checkpoint.py             92     11    88%   116, 131-133, 142-144, 151-152, 163, 180, 197-199
lib/durability/escalation.py             27     17    37%   21-35, 41, 45-48, 52-53
lib/durability/failure_matrix.py          9      0   100%
lib/durability/resume.py                 82     13    84%   50, 87, 94, 103, 106, 110-111, 141, 158-159, 162, 165-166
lib/durability/trichotomy.py             33     13    61%   70, 75-87
lib/evaluators/__init__.py               23      4    83%   48, 67-69
lib/evaluators/consensus.py              82     14    83%   72, 123, 152-166, 227-228
lib/evaluators/judge.py                  37      4    89%   88-89, 107, 116
lib/evaluators/orchestrator_hook.py      31      3    90%   57, 63-64
lib/healthcheck.py                       39      2    95%   47, 60
lib/kanban/__init__.py                   16      4    75%   52-53, 70-73
lib/kanban/notification_policy.py        27      0   100%
lib/kanban/telegram_bridge.py            52     16    69%   54-59, 71-73, 94-95, 119-120, 164-166, 170-171
lib/limits_validator.py                  43     20    53%   40-59, 63
lib/memory/__init__.py                   31      9    71%   32, 35-37, 41, 48-50, 53
lib/memory/intent_classifier.py          32      6    81%   44-45, 48, 51-52, 76
lib/memory/rejected.py                  158     28    82%   109, 113-115, 122-127, 145-146, 150-157, 206-207, 256-259, 300, 327-329
lib/observability/__init__.py           121     81    33%   44-47, 98-109, 151-166, 181-203, 224-237, 251-265
lib/observability/otel_setup.py          34     23    32%   50, 54-108
lib/scrubber.py                          35      0   100%
lib/toolset_router.py                    33      0   100%
-------------------------------------------------------------------
TOTAL                                  1262    316    75%
```

(Verbatim from `audit/phase1-to-phase2-readiness-2026-05-19/coverage-combined.txt`.)

### Files with <60% coverage

| File | Coverage | Reason |
|---|---|---|
| `lib/observability/otel_setup.py` | **32%** | OTel SDK not installed in test venv; `setup_tracing` falls to import-fail no-op path. 23 stmts of import-error/exporter setup never exercised. |
| `lib/observability/__init__.py` | **33%** | OTel-dependent hook bodies (`_pre_tool_call`, `_post_tool_call`, `_pre_llm_call`, `_post_llm_call`, `_on_session_start`) all short-circuit on `_tracer is None`. The wiring is verified but the span-emit logic is not. |
| `lib/durability/escalation.py` | **37%** | `find_stale_blocked_cards` reads SQLite at module-default `/root/.hermes/kanban/kanban.db` which doesn't exist in the test venv; the `Path.exists()` guard short-circuits. Untested: lines 21-35 (SQL query), 41 (Telegram alert path that is still TODO), 45-48 (loop body), 52-53 (`__main__` entry). |
| `lib/durability/__init__.py` | **47%** | `_p1_4_inject_rejected` body (lines 55-58, 60, 66-68, 89-90, 96-127) requires a real `ctx.active_taskspec` + `ctx.inject_message` shape. No unit test stubs these; the only verification is "returns None when ctx is a `MagicMock`". |
| `lib/limits_validator.py` | **53%** | The CLI `if __name__ == "__main__"` block (lines 40-59, 63) is untested. The library function `validate(data, schema)` is exercised by `test_limits_schema.py`. |

### Per-`lib/<module>` coverage rollup

| Module | Files | Combined coverage |
|---|---|---|
| `lib/anchors/` | 5 | 89% |
| `lib/durability/` | 5 | 73% |
| `lib/evaluators/` | 4 | 86% |
| `lib/kanban/` | 3 | 75% |
| `lib/memory/` | 3 | 80% |
| `lib/observability/` | 2 | **33%** |
| Top-level (`scrubber.py`, `toolset_router.py`, `healthcheck.py`, `limits_validator.py`) | 4 | 87% (but see Dead Code §4 — none are wired) |

### Integration test coverage — which subsystems exercised end-to-end?

Inventory of `tests/integration/`:

| Test file | Tests | Status | Real E2E? |
|---|---|---|---|
| `test_budget_cap.py` | 1 | **SKIP** (`pytestmark`) | No — requires `/v1/admin/limits` (P2) |
| `test_chroma_outage.py` | 1 | **SKIP** | No — requires HTTP gateway (de-scoped P1) |
| `test_full_turn.py` | 2 | **SKIP** | No — requires HTTP gateway |
| `test_p1_2_judge_panel.py` | 1 | conditional (skipif proxy unreachable) | Yes when proxy up |
| `test_p1_6_failure_matrix.py` | 5 | conditional (skipif proxy unreachable) | **Misleading** — `pytestmark` gates on LiteLLM proxy but the 5 test bodies are pure local classifier checks duplicating `tests/unit/test_trichotomy.py` and `tests/unit/test_failure_matrix.py`. They will not actually use the proxy if it IS up. See `tests/integration/test_p1_6_failure_matrix.py:22-61`. |
| `test_sandbox_isolation.py` | 2 | always-run | Yes — `docker compose exec shell-sandbox …`. But assertions are loose: `out.returncode != 0` passes on EITHER "sandbox blocked it" OR "container not running". Strengthen with stderr/stdout content checks. |
| `test_secret_leak.py` | 1 | **SKIP** | No — requires live `lib/scrubber.py` wiring (B5 audit item) |
| `test_skill_creation.py` | 1 | **SKIP** | No — requires `/v1/nudges/skill_extractor/run` endpoint |

**End-to-end coverage of P1 acceptance criteria via integration tests: ~2 of 8 areas** (sandbox isolation; conditional judge-panel). The full-turn round-trip, budget cap, secret-leak prevention, chroma degradation, and skill creation are explicitly P2 deferrals or HTTP-gateway-dependent.

### Missing test categories

- **Concurrency.** No tests exercise concurrent `_feedback_queue` mutation despite the `_lock` in `lib/evaluators/orchestrator_hook.py:39`. No test of concurrent `_TOOL_SPANS` access in `lib/observability/__init__.py:56-58`.
- **Error paths in critical infrastructure.** `lib/durability/checkpoint.py:142-144, 197-199` (mid-write `OSError`) untested by hard-failure injection.
- **TaskSpec state machine.** The `draft → draft_locked → locked` transitions referenced by `lib/anchors/__init__.py:38-70` slash command stubs are not tested because the handlers themselves are TODO.
- **Resume across container restart.** `lib/durability/resume.py` is unit-tested for the function shape but no integration test cycles container down/up to verify autoresume.
- **Telegram bridge end-to-end.** `lib/kanban/telegram_bridge.py` lines 54-59, 71-73, 94-95, 119-120, 164-166, 170-171 are uncovered — these are the import-fallback paths and the actual `httpx`-send code.

**Grade: B-** — Unit coverage is strong by % but the integration tier under-delivers what P1 acceptance documents claim is "tested."

---

## 2. Test quality

**Total test functions:** 162 (across 34 test files).

### Vacuous assertions

- **None found.** No `assert True`, no `assert 1`, no `assert <constant>`. `grep -rnE 'assert True\b|assert 1\b|assert\s*$'` returned 0 hits.
- The `pass` statements that grep found in tests are all method bodies inside fake mock classes (`__aexit__` no-ops, e.g. `tests/unit/test_healthcheck.py:21, 39`; `tests/integration/conftest.py:31`) or exception class declarations (`tests/unit/test_trichotomy.py:7, 11`).

### Mock heaviness

- 45 occurrences of `Mock(|MagicMock(|mocker.|@patch|monkeypatch` across the suite, of which **the vast majority are constructive** — replacing `httpx.AsyncClient`, `llm.complete`, `os.environ`, monkeypatching module paths, etc. Examples of legitimately mocked external boundaries:
  - `tests/unit/test_healthcheck.py:26` mocks `httpx.AsyncClient` (network)
  - `tests/unit/test_anchors_cancel_dispatch.py:20` mocks `lib.kanban.telegram_bridge.cancel_card` (cross-module boundary)
- **Wiring-only smoke tests** (test calls `register(MagicMock())` and asserts `register_hook` was called with the right name):
  - `tests/unit/test_anchors_plugin.py` — 4 tests, all wiring-only (`test_register_wires_session_start_hook`, etc.)
  - `tests/unit/test_durability_plugin.py` — 3 of 4 tests wiring-only (`test_register_wires_pre_tool_call_hook`, etc.)
  - `tests/unit/test_evaluators_plugin.py` — 4 of 6 tests wiring-only
  - `tests/unit/test_kanban_plugin.py` — both tests wiring-only
  - `tests/unit/test_memory_plugin.py` — wiring + light dispatch (mixed)
  - `tests/unit/test_observability_plugin.py` — `test_register_wires_all_five_hooks`, etc.
  - Approximate count: **34 of 162 tests (~21%) are register-was-called assertions**. They pass even when the handler body is `return None` or a TODO string.

### Test isolation

- No `global` keywords or module-level mutable state in tests.
- Tests that touch the filesystem use `tmp_path` (pytest builtin) — see `tests/unit/test_rejected.py:27-32`, `tests/unit/test_checkpoint.py:17, 22`. Good.
- `lib/evaluators/orchestrator_hook._feedback_queue` is module-level mutable state. `tests/unit/test_evaluators_plugin.py:37-49` and `tests/unit/test_orchestrator_hook.py:24` each use unique session IDs (`"test-session-pre-llm-inject"`, `"test-session-no-feedback-xyz"`, `"sess-task18-1"`) to avoid cross-contamination. There is no `clear()` between tests, so a future test author who picks a duplicate session ID would have a cross-test failure. **Latent fragility, not current defect.**

### Test naming

- 162 test names; **zero generic names** (no `test_foo`, `test_bar`, `test_simple`, `test_basic`, `test_it_works`).
- Most follow the `test_<behavior>_<condition>` pattern, e.g.
  - `test_load_active_entries_skips_expired` (`tests/unit/test_rejected.py:86`)
  - `test_F22_secret_leak_classifies_as_fail_loud` (`tests/integration/test_p1_6_failure_matrix.py:39`)
  - `test_pre_llm_call_no_op_without_feedback` (`tests/unit/test_evaluators_plugin.py:52`)
- One misleading name: `test_stub_callbacks_return_none` in `tests/unit/test_durability_plugin.py:39` — these callbacks (`_p1_3_resume_session`, `_p1_4_inject_rejected`) are no longer stubs; they have real implementations that happen to return None when ctx is a `MagicMock`. The test passes for the wrong reason.

**Grade: B** — High discipline on the body, but the dense ratio of plugin-wiring smoke tests over-credits coverage where the handler bodies are still incomplete.

---

## 3. TODO/FIXME/XXX/HACK debt inventory

Scan: `git grep -nE 'TODO|FIXME|XXX|HACK|NotImplementedError|raise NotImplemented' -- lib/ scripts/ deploy/ config/`

**10 hits in shipped code paths** (excluding docs/ and superpowers/plans/):

| # | File:line | Code/Text | Severity | Category | Visible to end user? |
|---|---|---|---|---|---|
| 1 | `lib/anchors/__init__.py:18` | `# TODO(P1-1 task 6): wire to session metadata loader once limits.yaml` | **MEDIUM** | Tracked (P1-1 task 6) | No — comment only |
| 2 | `lib/anchors/__init__.py:34` | `# TODO(P1-1 task 6): wire heuristic + state machine integration` | **HIGH** | Tracked (P1-1 task 6) | No — but means `_on_pre_tool_call` returns `None` unconditionally; clarification loop never triggers |
| 3 | `lib/anchors/__init__.py:40` | `return "TODO(P1-1 task 6): force-lock the active draft TaskSpec."` | **CRITICAL** | Tracked | **YES** — `/lock` command literally returns the TODO string to the operator |
| 4 | `lib/anchors/__init__.py:45` | `return "TODO(P1-1 task 6): mark current question as skipped."` | **CRITICAL** | Tracked | **YES** — `/skip` returns TODO string |
| 5 | `lib/anchors/__init__.py:65` | `return "TODO(P1-1 task 6): abandon the current draft TaskSpec."` | **CRITICAL** | Tracked | **YES** — `/cancel` (no arg) returns TODO string |
| 6 | `lib/anchors/__init__.py:70` | `return "TODO(P1-1 task 6): transition draft_locked → locked."` | **CRITICAL** | Tracked | **YES** — `/confirm` returns TODO string |
| 7 | `lib/anchors/__init__.py:80` | `print(f"TODO(P1-1 task 6): create draft TaskSpec for intent: {args.intent}")` | **CRITICAL** | Tracked | **YES** — `hermes new <intent>` CLI prints TODO string |
| 8 | `lib/kanban/__init__.py:48` | `# TODO(P1-5 follow-up): read session metadata for the lock flag and the…` | **HIGH** | Tracked (P1-5 follow-up) | No — comment only; `_on_pre_tool_call` is registered but inert |
| 9 | `lib/kanban/__init__.py:71` | `# TODO(P1-5 follow-up): inspect result for status-change side effects,…` | **HIGH** | Tracked (P1-5 follow-up) | No — `_on_post_tool_call` is registered but inert |
| 10 | `lib/durability/escalation.py:40` | `# TODO(P1-5): replace with telegram_bridge.send_alert(...)` | **MEDIUM** | Tracked | No — currently `print(f"[ESCALATION F32] …")` |

### Categorization

- **Critical** (would break in production / breaks documented surface): **5 of 10** — all in `lib/anchors/__init__.py`. The `/lock`, `/skip`, `/cancel` (no arg), `/confirm` slash commands and `hermes new <intent>` CLI are all live (registered, advertised in command descriptions) but return literal "TODO(...)" strings. This is documented in `docs/runbooks/phase1-acceptance-prep-2026-05-18.md:53` as a known gap, but the commands ARE advertised through `register_command(..., description=…)`.
- **High** (silent broken behaviour but not exposed as command output): **3 of 10** — `_on_pre_tool_call` hooks in anchors + kanban are no-ops; the clarification loop and card-creation-at-lock are not triggered.
- **Tracked**: **All 10**. Every TODO carries an issue/phase reference (`P1-1 task 6`, `P1-5`, `P1-5 follow-up`). None are orphan.
- **Untracked**: **0 of 10**.

### Debt-magnet files

- `lib/anchors/__init__.py` — **7 of 10 TODOs** (70%). The plugin is registered and exposes 4 slash commands + 1 CLI subcommand whose handlers are all stubs returning TODO strings.
- `lib/kanban/__init__.py` — 2 of 10 (20%). Both hooks register but no-op.
- `lib/durability/escalation.py` — 1 of 10 (10%). The escalation sender is a print().

### No `NotImplementedError` / `raise NotImplemented` anywhere

- 0 hits in `lib/`, `scripts/`, `deploy/`, `config/`.

**Grade: D** — Strong tracking discipline (everything is tagged with a phase/task), but **the magnitude is large** for a tagged-accepted release: 5 user-visible TODO strings ship in `phase1-accepted`. The Phase 1 acceptance is honest about it (`docs/runbooks/phase1-acceptance-prep-2026-05-18.md:53`) but Phase 2 cannot build on these surfaces until P1-1 task 6 lands.

---

## 4. Dead code

### Files in `lib/` that nothing-but-tests imports

Verified via `grep -rn "from lib.<X>\|from lib import <X>" --include='*.py' . | grep -v ".venv|.pytest_cache"`:

| Module | Production callers | Test callers | Status |
|---|---|---|---|
| `lib/scrubber.py` | **0** | `tests/unit/test_scrubber.py:9` | **DEAD** — `Scrubber.from_config(...)` never called in production. `lib/durability/failure_matrix.py:128` references the *concept* "Critical secret leak detected by scrubber" in a failure-code description but does not call the Scrubber class. |
| `lib/toolset_router.py` | **0** | `tests/unit/test_toolset_router.py:9` | **DEAD** — `ToolsetRouter.from_config(...)` never instantiated in production. The docstring in `lib/evaluators/__init__.py:43-47` says wiring to `toolset_router.is_evaluation_eligible()` is "intentionally deferred to Task 21". |
| `lib/healthcheck.py` | **0** | `tests/unit/test_healthcheck.py:8` | **DEAD** — `run_checks(...)` never called by production. Docker compose healthchecks are inline Python (`deploy/docker-compose.yml:88-93, 279-281`), not invocations of this module. |
| `lib/limits_validator.py` | **0** (CLI entry only) | `tests/unit/test_limits_schema.py:9` | **CLI-only** — `if __name__ == "__main__"` block at line 62 means it's executable via `python lib/limits_validator.py config/limits.yaml`. Docs say smoke check 6 uses it. Not imported by any other lib module. |

The 6 plugin packages (anchors, durability, evaluators, kanban, memory, observability) all have external callers and are wired in `deploy/docker-compose.yml:266-278` via `../lib/<plugin>:/root/.hermes/plugins/<plugin>:ro` mounts.

### Unused functions/classes

- `lib.evaluators.orchestrator_hook.queue_judge_dispatch` (`lib/evaluators/orchestrator_hook.py:42`) — called only by `tests/unit/test_orchestrator_hook.py:26` and `tests/unit/test_evaluators_plugin.py:42`. **No production code path queues feedback**, because `lib/evaluators/__init__.py:_on_post_tool_call` (line 28-48) returns `None` instead of dispatching to the judge panel. Self-described in line 43-47 docstring: "Wiring to `toolset_router.is_evaluation_eligible()` is intentionally deferred to Task 21". This makes the entire evaluator feedback path **dead-on-arrival in P1**.

### Stale scripts

All 10 scripts in `scripts/` are referenced from docs and/or compose:

| Script | External refs | Status |
|---|---|---|
| `bootstrap.sh` | 26 | live |
| `decrypt-secrets.sh` | 28 | live |
| `escalation_loop.py` | 6 | live (sidecar) |
| `healthcheck-ping.sh` | 37 | live (cron) |
| `panic.sh` | 11 | live |
| `smoke.sh` | 68 | live |
| `snapshot.sh` | 20 | live |
| `teardown.sh` | 13 | live |
| `test.sh` | 27 | live |
| `verify-prereqs.sh` | 18 | live |

No stale scripts.

**Grade: C** — 4 of 28 lib/ files are tested-only, and the `queue_judge_dispatch` orchestrator is dead-on-arrival. Plugin contracts are honored, but the production wiring trails the test surface.

---

## 5. Complexity hotspots

### Top 10 files by LOC

```
 86  lib/memory/intent_classifier.py
 87  lib/durability/trichotomy.py
 87  lib/kanban/notification_policy.py
 90  lib/kanban/__init__.py
108  lib/anchors/__init__.py
108  lib/observability/otel_setup.py
118  lib/evaluators/judge.py
127  lib/durability/__init__.py
169  lib/durability/resume.py
179  lib/kanban/telegram_bridge.py
191  lib/durability/failure_matrix.py
199  lib/durability/checkpoint.py
237  lib/evaluators/consensus.py
265  lib/observability/__init__.py
336  lib/memory/rejected.py     ← largest
```

**No file exceeds 500 LOC.** Largest is `lib/memory/rejected.py` at 336 LOC — appropriate for a markdown-with-frontmatter parser + dedup + TTL filter + writer + classifier-coordinator.

### Function count per file (top 10)

```
3  lib/scrubber.py
3  lib/toolset_router.py
3  lib/memory/intent_classifier.py
4  lib/evaluators/__init__.py
4  lib/limits_validator.py
5  lib/durability/trichotomy.py
5  lib/evaluators/consensus.py
5  lib/kanban/notification_policy.py
5  lib/kanban/telegram_bridge.py
6  lib/anchors/spec_store.py
7  lib/observability/__init__.py
8  lib/durability/checkpoint.py
8  lib/durability/resume.py
9  lib/anchors/__init__.py
9  lib/memory/rejected.py
```

(Includes class methods. Counts `def` at column 0 or column 4 — i.e. module-level functions + first-level class methods.)

No god-module. The 9-function files (`anchors/__init__.py`, `memory/rejected.py`) are reasonable for a plugin entrypoint and a stateful storage module respectively.

**Grade: A-**

---

## 6. Naming + consistency

### Public vs private convention

- `_underscore_prefix` for internal handlers (e.g. `_on_session_start`, `_pre_tool_call`, `_p1_3_resume_session`) — consistent across all 6 plugins.
- Public surface in `__all__` declarations where present (`lib/durability/__init__.py:7`, `lib/kanban/__init__.py:86-90`, `lib/memory/__init__.py:75`).
- Test files reach into `_` private functions in 2 places: `tests/unit/test_evaluators_plugin.py:39, 54` imports `_on_pre_llm_call`. Documented as the price of testing the inject path; acceptable but a tighter API would expose this as a public helper.

### Inter-module prefix consistency

- `lib/anchors/intent_classifier.py` and `lib/memory/intent_classifier.py` — two modules with the same filename, different responsibilities. `lib/anchors/intent_classifier.py` is for TaskSpec intent string classification at lock-time; `lib/memory/intent_classifier.py` is for REJECTED.md intent_category. Different scopes — not a duplicate. Naming is defensible but a future reader will need to be careful.
- Slash command handlers consistently named `_slash_<command>` (anchors, memory).
- Hook handlers consistently named `_on_<event>` or `_<event>` (mixed: anchors uses `_on_session_start`/`_on_pre_tool_call`; observability uses `_on_session_start`/`_pre_tool_call`/`_post_tool_call`).
  - **Minor inconsistency**: anchors prefixes both with `_on_`, observability mixes `_on_session_start` with `_pre_tool_call`. Not breaking, but reduces grep-ability for hook handlers.

### Type hints

Refined check (counting `\)\s*->\s*[A-Za-z]` return annotations vs def counts):

- Best: `lib/observability/__init__.py` (7/7), `lib/durability/checkpoint.py` (7/8), `lib/durability/resume.py` (8/8), `lib/anchors/__init__.py` (9/9), `lib/memory/rejected.py` (11/9).
- Worst: **`lib/durability/__init__.py` — 0 of 3** functions have return annotations (`register`, `_p1_3_resume_session`, `_p1_4_inject_rejected` all lack `-> None`). Other modules' register functions all have `-> None`. Inconsistent.
- `lib/durability/trichotomy.py` — 3/5 typed.
- 23 of 28 lib files declare `from __future__ import annotations` (good — future-compatible string-deferred evaluation).

**Grade: B+**

---

## 7. Plugin contract integrity

### `register(ctx)` presence

All 6 plugins define `register(ctx) -> None`:

| Plugin | Defined at | Provides hooks (yaml) | Registers hooks (code) | Match? |
|---|---|---|---|---|
| anchors | `lib/anchors/__init__.py:84` | `[on_session_start, pre_tool_call]` | `on_session_start, pre_tool_call` | ✅ |
| durability | `lib/durability/__init__.py:10` | `[pre_tool_call, post_tool_call, on_session_start]` | `pre_tool_call, post_tool_call, on_session_start (×2: P1-3 + P1-4)` | ✅ |
| evaluators | `lib/evaluators/__init__.py:76` | `[post_tool_call, pre_llm_call, on_session_end]` | `post_tool_call, pre_llm_call, on_session_end` | ✅ |
| kanban | `lib/kanban/__init__.py:76` | `[pre_tool_call, post_tool_call]` | `pre_tool_call, post_tool_call` | ✅ |
| memory | `lib/memory/__init__.py:56` | `[]` (none) | (none — only commands) | ✅ |
| observability | `lib/observability/__init__.py:61` | `[on_session_start, pre_tool_call, post_tool_call, pre_llm_call, post_llm_call]` | all 5 | ✅ |

**Provides_hooks declarations match registered hooks in all 6 plugins.**

### Gaps

1. **plugin.yaml does not declare slash commands or CLI commands.** `anchors` registers `/lock`, `/skip`, `/cancel`, `/confirm` (`lib/anchors/__init__.py:88-101`) plus `hermes new` CLI (line 102-107). `memory` registers `/forget`, `/rejections` (`lib/memory/__init__.py:63-72`). None of these are declared in the corresponding `plugin.yaml` files. The schema/contract is silent on commands today — but if Phase 2 introduces command-collision detection, the yaml will need a `provides_commands` field.

2. **Dead-on-arrival post_tool_call in evaluators.** `lib/evaluators/__init__.py:_on_post_tool_call` (line 28-48) returns `None`. The docstring acknowledges: "Wiring to `toolset_router.is_evaluation_eligible()` is intentionally deferred to Task 21 (live integration) — the orchestrator-side dispatch plumbing is fully in place via `orchestrator_hook.queue_judge_dispatch`, callable from a background thread once the eligibility lookup lands." So the plugin satisfies the **contract** (hook is registered) but does **nothing useful** at runtime. The matching `pre_llm_call` hook correctly drains the queue (`_on_pre_llm_call`) — but the queue is never populated by the production path, only by tests. The plumbing is one-end-disconnected.

3. **Dead-on-arrival kanban hooks.** Same pattern: `lib/kanban/__init__.py:_on_pre_tool_call` and `_on_post_tool_call` (lines 34-73) both register but log-only-then-return. The docstring on each says the production card-creation/notification path is deferred to "P1-5 follow-up". Plugin contract satisfied; behaviour missing.

4. **Anchors stub bodies.** As detailed in §3, 7 of `anchors/__init__.py`'s registered surface items return TODO strings.

**Grade: B** — Contract metadata matches reality. But "contract satisfied" ≠ "production-ready," and 3 of 6 plugins have hooks that register-and-no-op.

---

## 8. Configuration validation

### `config/limits.yaml` vs `config/limits-schema.json`

Validated programmatically:
```
$ python -c "import json, yaml; from jsonschema import validate; \
    validate(yaml.safe_load(open('config/limits.yaml')), \
             json.load(open('config/limits-schema.json')))"
limits.yaml VALIDATES against schema
```

Schema covers: `budget, retries, sandboxes, agent, nudges, health, snapshots, approval, rl_rewards, rl_training, alerts, notify_channels, log_retention, local_logs_dev, anchors, evaluators, durability, memory, kanban` (19 top-level required keys). YAML supplies all 19. ✅

### `config/scrubber-patterns.yaml`

- **Loaded by**: `lib/scrubber.py:42` (`Scrubber.from_config(config_path)`).
- **Called from**: `tests/unit/test_scrubber.py:17` only.
- **No production code path calls `Scrubber.from_config(...)`**. `tests/integration/test_secret_leak.py:9` is marked `pytest.mark.skip` with reason "requires live `lib/scrubber.py` wiring".
- **Schema**: None (no `scrubber-patterns-schema.json` exists).

### `config/toolsets.yaml`

- **Loaded by**: `lib/toolset_router.py:39` (`ToolsetRouter.from_config(config_path)`).
- **Called from**: `tests/unit/test_toolset_router.py:12` only.
- **No production code path calls `ToolsetRouter.from_config(...)`**.
- **Schema**: None. Per `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md:255`, a `replay_safe` field is planned for Phase 2 but not present today.
- The yaml uses an `evaluate_after: bool` field (per `docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md:3025`) which `toolset_router.py` does NOT currently parse — the `Route` dataclass at `lib/toolset_router.py:22-26` has only `patterns` and `tier`, no `evaluate_after`. Field is set in yaml but unread.

### `config/hermes/AGENTS.md` vs reality

- Line 17 mentions `~/.hermes/new-repo-template.md`; line 27 also references `docs/conventions/new-repo-template.md`. The latter exists (`docs/conventions/new-repo-template.md`, 17,519 bytes). The former is a runtime path inside the container; not verifiable from the host without a running shell. Plausible.
- Line 12 claims "GitHub MCP via `github-mcp` sidecar (HTTP, port 8003)". `deploy/docker-compose.yml:162` defines the `github-mcp` service. Port number not directly verifiable in head-of-file, but service exists.
- Line 13 claims "LLM access: routed through LiteLLM proxy → Vertex AI". Compose has `litellm-proxy` (`deploy/docker-compose.yml:60`). ✅

**Grade: B** — `limits.yaml` validates cleanly. `scrubber-patterns.yaml` and `toolsets.yaml` load via library code but production never instantiates the consumers. The `evaluate_after` field in `toolsets.yaml` is set but not parsed.

---

## 9. Subprocess / shell discipline

### `os.system()` calls in lib/scripts

`grep -rn "os.system" lib/ scripts/ --include='*.py'` returns **0 hits**. ✅

### `subprocess.*` in lib/

`grep -rn "subprocess\." lib/ --include='*.py'` returns **0 hits**. ✅

`lib/` is shell-free; all process-spawning is left to scripts or compose orchestration.

### `subprocess.*` in scripts/

`scripts/escalation_loop.py` does not use subprocess (verified via grep). Shell scripts (`scripts/*.sh`) are POSIX shell, not Python subprocess.

### `shell=True` anywhere

`grep -rn "shell=True" lib/ scripts/ --include='*.py'` returns **0 hits**. ✅

The only `subprocess` usage in the repo's Python is in **integration tests** (`tests/integration/test_chroma_outage.py:17, 32` and `tests/integration/test_sandbox_isolation.py:10, 32`), all invoking `docker compose` via argv lists (no `shell=True`).

### Shell injection vectors

None found in `lib/`. Telegram bridge (`lib/kanban/telegram_bridge.py`) sends HTTP via `httpx` — no shell. The escalation loop uses SQLite via `sqlite3` parameterized queries (`lib/durability/escalation.py:28-32`):
```python
conn.execute(
    "SELECT id, title, last_heartbeat_at FROM tasks "
    "WHERE status = 'blocked' AND (? - last_heartbeat_at) > ?",
    (now, threshold_s),
)
```
Parameterized; not vulnerable to injection.

**Grade: A+** — Best-in-class. Zero process-spawning surface in `lib/`; all subprocess usage is in tests and is argv-list with no shell expansion.

---

## 10. Error handling discipline

### Counts in `lib/`

- `except Exception:` (broad): **16 occurrences** across `lib/healthcheck.py`, `lib/memory/`, `lib/durability/`, `lib/observability/`, `lib/evaluators/`, `lib/kanban/`, `lib/anchors/`.
- `except:` (bare): **0 occurrences**. ✅
- `except: pass` (silent): **0 occurrences**. ✅
- `try/except` with re-raise as `from None` (silently dropped traceback): **0 occurrences**.

### Audit of each broad `except Exception`

All 16 broad catches have one of two qualities:

1. **Annotated `# noqa: BLE001` with justification** (12 of 16):
   - `lib/durability/__init__.py:57, 89, 123` — "never block session start"
   - `lib/memory/__init__.py:35, 48` — "bridge mustn't crash on bad input"
   - `lib/memory/intent_classifier.py:51` — "never let config-read break classification"
   - `lib/observability/__init__.py:107, 164, 195, 201, 235, 260, 263` — defensive span-emit; logs at DEBUG and falls through
   - `lib/evaluators/consensus.py:165, 227` — "config faults must not break consensus", "memory write must not abort consensus"
   - `lib/kanban/telegram_bridge.py:164` — "bridge must not crash on bad input"
   - `lib/anchors/intent_classifier.py:55` — logs warning, falls to `'unknown'` category

2. **`# pragma: no cover` for import fallbacks** (2 of 16):
   - `lib/observability/otel_setup.py:95`, `lib/observability/__init__.py:48` — fallback when OTel SDK is not installed

3. **Logged + degraded return** (2 of 16):
   - `lib/healthcheck.py:48` — `except Exception as e: return CheckResult(name, Status.DOWN, repr(e))` (degrades to DOWN; embeds error in payload)
   - `lib/durability/resume.py:165` — `except Exception: return True` (defaults autoresume_enabled if yaml is malformed; comments explain)

### Silent swallow check

None found. Every `except Exception` either logs (`logger.warning`/`logger.debug`), returns a degraded/default value with the rationale in a comment, or both.

### Narrow exceptions (counted for completeness)

- `except (TypeError, ValueError)` — 3 occurrences in `lib/memory/rejected.py` (parsing untrusted markdown frontmatter values).
- `except ImportError` — 4 occurrences (graceful fallbacks for optional deps: `yaml`, `opentelemetry`, `kanban_db`).
- `except OSError` — 5 occurrences in checkpoint/resume (atomic write recovery, EAGAIN).
- `except json.JSONDecodeError` — 2 occurrences in checkpoint/judge parsing.

**Grade: A-** — Discipline is excellent. The single criticism: `lib/healthcheck.py:48` catches `Exception` rather than `httpx.HTTPError` + `asyncio.TimeoutError`, which would be tighter. Not a defect.

---

## CRITICAL / HIGH / MEDIUM / LOW findings (consolidated)

### CRITICAL

| # | File:line | Finding | Impact |
|---|---|---|---|
| C1 | `lib/anchors/__init__.py:38-80` | 5 user-facing handlers (`/lock`, `/skip`, `/cancel` no-arg, `/confirm`, `hermes new`) return literal "TODO(P1-1 task 6): ..." strings | Operators using these documented commands receive TODO text as the reply. Documented as a known gap in the acceptance runbook but the commands are still advertised. |
| C2 | `lib/evaluators/__init__.py:28-48` | `_on_post_tool_call` returns `None` instead of dispatching judge panel; `queue_judge_dispatch` (the orchestrator's intended feeder) is called only by tests | Multi-judge evaluator panel is **dead-on-arrival in P1**. Plugin advertises the hook but the production path never queues feedback. |
| C3 | `lib/kanban/__init__.py:34-73` | Both `_on_pre_tool_call` (card creation at lock) and `_on_post_tool_call` (status notification) log-then-return | Kanban → Telegram bridge writes no cards and emits no notifications from the production hook path. The standalone helper `telegram_bridge.cancel_card` IS wired to `/cancel <id>` but only that one surface works. |

### HIGH

| # | File:line | Finding | Impact |
|---|---|---|---|
| H1 | `lib/scrubber.py` | `Scrubber.from_config(...)` never called by production; only by `tests/unit/test_scrubber.py` | Secret-scrubbing is implemented and unit-tested but not wired into any persist or outbound path. `tests/integration/test_secret_leak.py` is `pytest.mark.skip` for this exact reason. |
| H2 | `lib/toolset_router.py` | `ToolsetRouter.from_config(...)` never called by production; the `evaluate_after` field in `toolsets.yaml` is set but the `Route` dataclass at `lib/toolset_router.py:22-26` does not parse it | Tool routing per toolsets.yaml is not enforced at runtime. The dependent eligibility lookup for evaluators is the same gap as C2. |
| H3 | `tests/integration/test_p1_6_failure_matrix.py:1-61` | The 5 test bodies are pure local classifier checks duplicating unit tests, despite a `pytestmark` that gates on LiteLLM proxy availability | Misleading skipif: claims to be live-stack but does no proxy I/O. Hides the fact that the actual failure-mode E2E paths are untested. |
| H4 | `tests/integration/test_sandbox_isolation.py:27, 48` | Assertions are loose (`out.returncode != 0`) and pass on either "sandbox blocked it" OR "container not running" | Test can give a green light on a broken/missing sandbox. Tighten to assert specific stderr (`Could not resolve host`, `Read-only file system`). |
| H5 | `lib/observability/__init__.py:56-58` | `_TOOL_SPANS` and `_LLM_SPANS` module-level dicts are guarded by `_LOCK` but no concurrency test exercises racing pre/post pairs from multiple sessions | Latent risk under multi-session load. |
| H6 | `tests/integration/`: 6 of 14 tests blanket-`pytest.mark.skip`'d | `test_full_turn`, `test_chroma_outage`, `test_budget_cap`, `test_skill_creation`, `test_secret_leak`, plus parts of others | Phase 1 acceptance docs claim end-to-end testing of these flows; the suite does not deliver it. |

### MEDIUM

| # | File:line | Finding | Impact |
|---|---|---|---|
| M1 | `lib/healthcheck.py` | Module unwired; called only by `tests/unit/test_healthcheck.py`. Compose healthchecks are inline Python (`deploy/docker-compose.yml:88, 279`) rather than invocations of this module | 95%-covered code with zero production users. Either wire as a `/healthz` endpoint or delete. |
| M2 | `lib/durability/__init__.py:10, 21, 35` | `register`, `_p1_3_resume_session`, `_p1_4_inject_rejected` lack `-> None` return annotations (other plugins do have them) | Inconsistent. Doesn't break anything; reduces type-check rigor. |
| M3 | `lib/anchors/intent_classifier.py` and `lib/memory/intent_classifier.py` | Two modules with the same filename, different responsibilities | Reader confusion. Rename one (e.g. `lib/anchors/taskspec_intent.py` vs `lib/memory/category_intent.py`). |
| M4 | `tests/unit/test_durability_plugin.py:39` | `test_stub_callbacks_return_none` — name claims "stubs" but the callbacks have real bodies | Passes for the wrong reason: `_p1_4_inject_rejected` has 73 lines of logic that all gracefully return None on a `MagicMock` ctx. |
| M5 | `lib/observability/__init__.py:48`, `lib/observability/otel_setup.py:95` | 33% coverage on `__init__.py`, 32% on `otel_setup.py` because OTel SDK isn't installed in the test venv | The hook bodies (~75 lines) never execute under tests. Add a CI matrix run with `pip install opentelemetry-api opentelemetry-sdk`. |
| M6 | `lib/durability/escalation.py` | 37% coverage; escalation loop is the on-call lifeline | Add a fixture-backed SQLite test that exercises the stale-card detector. |

### LOW

| # | File:line | Finding | Impact |
|---|---|---|---|
| L1 | `plugin.yaml` (all 6) | `provides_hooks` is declared; `provides_commands` (slash + CLI) is not | Future surface for collision detection if Phase 2 introduces a command registry. |
| L2 | `lib/evaluators/__init__.py:28` and `lib/anchors/__init__.py:23, 12` | Hook handlers named `_on_<event>` AND `_<event>` mixed across modules | Cosmetic; reduces greppability. |
| L3 | `lib/anchors/__init__.py:48-65` | Slash command `_slash_cancel` mixes two responsibilities (P1-1 draft cancel + P1-5 card cancel) | Acceptable per design (argument-shape dispatch) but should grow a comment block explaining the two-mode behaviour at the top of the function (it has one, but it's brief). |
| L4 | `tests/integration/conftest.py:30-31` | `try: ... except httpx.HTTPError: pass` is a deliberate poll loop, but a comment would help future readers | Clarity nit. |
| L5 | `lib/limits_validator.py:40-59` | `__main__` CLI block untested (53% coverage) | Add a `subprocess` smoke test invoking `python lib/limits_validator.py config/limits.yaml`. |

---

## Phase 2 readiness gates (recommended)

Before merging Phase 2 work that depends on these P1 surfaces:

1. **Land P1-1 task 6** — implement the anchors slash-command handlers (`/lock`, `/skip`, `/cancel`, `/confirm`, `hermes new`) so they don't return TODO strings. The state-machine module (`lib/anchors/clarification_loop.py`, `lib/anchors/task_spec.py`, `lib/anchors/spec_store.py`) is already implemented and tested at 78-100%; only the plugin wiring is missing. (C1)
2. **Wire `_on_post_tool_call` → `queue_judge_dispatch`** — currently the evaluator plugin satisfies the hook contract but produces no judge dispatches. (C2)
3. **Wire `_on_pre_tool_call` / `_on_post_tool_call` for kanban** — card creation at lock + status notifications. (C3)
4. **Wire `Scrubber.from_config` into the persist + outbound path** — `lib/scrubber.py` is dead until then; remove or use. (H1)
5. **Wire `ToolsetRouter` + add `evaluate_after` parsing** — required for #2 above. (H2)
6. **Strengthen `test_sandbox_isolation.py` assertions** — current passes can mask a missing container. (H4)
7. **Replace `test_p1_6_failure_matrix.py` test bodies with real proxy-driven failure injection** — currently they are misleading. (H3)
8. **Unskip the 6 integration tests OR remove them** — the P2 deferral reason is honest, but the dead test files should either ship working or move out of `tests/integration/`. (H6)
9. **Add concurrency tests for the two module-global dicts in observability** — `_TOOL_SPANS`, `_LLM_SPANS`. (H5)
10. **Decide on `lib/healthcheck.py`** — wire it as `/healthz` or delete. (M1)

---

## Verification notes

- **Branch state**: `main @ 85512a3` (tag `phase1-accepted`).
- **Test infra**: `pytest 9.0.3`, `coverage 7.14.0` (just installed via `uv pip install`), Python 3.12.11 in `.venv/`.
- **Pytest unknown markers** warned: `integration` and `slow` are not registered. Add to `pyproject.toml` `[tool.pytest.ini_options].markers`.
- **Docker status at audit time**: `hermes`, `litellm-proxy`, `github-mcp`, `otel-collector` are running and healthy (12-16h uptime). This means `test_sandbox_isolation.py` exercised a live sandbox during this audit's run.
- **Raw coverage reports**: `audit/phase1-to-phase2-readiness-2026-05-19/coverage-unit.txt` and `coverage-combined.txt` saved verbatim.
