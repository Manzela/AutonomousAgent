# Agent Hand-Off — 2026-06-04

**From:** Claude Sonnet 4.6 (this session)
**To:** Antigravity IDE Opus 4.6/4.8 **or** Gemini 3.1 Pro (via `gemini-gcp` skill)
**Repo HEAD:** `9f38a3b7` (main)

---

## What was completed this session

| Item | Commit | What it is |
|---|---|---|
| SP-J1 | `d4cba1cc` | Cohen's κ judge-calibration drift monitor + quarantine registry |
| SP-25 | `15338e99` | Spec-Kit prompts + EARS criteria as vendored `docs/spec-kit/` assets |
| SP-24 | `75a31be4` | `scripts/predeploy_gate.sh` — 6 checks + `docs/compliance/predeploy-controls.md` |
| SP-21 | `a4d69f57` | `app/adapters/plane/` — PlaneBoard + webhook normaliser (C15 no-ping-pong) |

All are hermetically tested (31 / 17 / 18 / 13 oracles respectively), C9-reviewed (Opus 4.8 reviewer), and sha256-pinned in `audit/acceptance/SHA256SUMS`.

---

## Honest state of the product (2026-06-03 reconciled audit)

> Source: `audit/2026-06-03-prd-code-truth/findings-reconciled.md`

| Layer | Coverage |
|---|---|
| EPIC-0 gate/CI scaffold | **83 %** (strongest layer) |
| EPIC-1 spine (SP-01..SP-06) | **30 %** (skeleton, stubs not product) |
| EPIC-2 trust loop | **22 %** |
| EPIC-3 operator surfaces | **17 %** |
| Milestones passed (Gate-0 / P0 / P1 / P2) | **0 / 4** |

The enforcement scaffold is real. The **product middle is hollow**: `SP-03 clarify`, `SP-02 decompose`, `SP-05 sandbox`, and `SP-06 eval gate` are all ABSENT/THEATRE. The spine runs but does nothing meaningful end-to-end.

---

## What to build next (priority order)

### Opus — best for complex implementation (no live infra needed)

These are hermetically buildable right now. They unlock the entire P0 milestone.

**P0 critical path (dependency order):**

```
SP-03 → SP-02 → SP-04 → SP-05 → SP-06 → SP-R7
```

| # | ID | What "done" means | Why first |
|---|---|---|---|
| 1 | **SP-03** | Replace `lib/anchors/__init__.py:220-224` stub with a Vertex structured-output drafter; real `clarify` node in `graph.py` emitting ≤5 category-tagged questions + ambiguity report + `applied_standards[]` (SP-25 `constitution.md`). | `seal_spec` currently signs a hardcoded stub — nothing downstream is conformance-checkable |
| 2 | **SP-02** | Make `TaskGraph`/`TaskNode` (`graph_state.py:72,81`) the live output of a real `decompose` node; acceptance-derived `allowed_paths`; decomposition-fidelity test | Eval gate and fan-out have nothing to act on |
| 3 | **SP-04** | One-liner: `_map_a2a_status` in `orchestrator.py:364` maps `INPUT_REQUIRED→FAILED`; fix to route to the interrupt. Update `test_peer_dispatch.py:316`. | The interrupt half already works; only the A2A mapping is wrong |
| 4 | **SP-05** | Wire `execute` (`graph.py:175`) through a real `AbstractSandbox` subclass (the inmemory adapter exists in `app/adapters/inmemory/sandbox.py` — just wire it in); kill in-process exec `orchestrator.py:294` | #1 trust requirement; in-process exec is the lethal trifecta |
| 5 | **SP-06** | A `DeepEval` `DAGMetric` with hard non-LLM roots reading the locked `TaskSpec`, in ONE Actions job that blocks merge; scope-root `git diff` vs `allowed_paths`; cross-vendor Gemini judge leaf | Turns "done" from agent-asserted to gate-derived |
| 6 | **SP-R7** | Snapshot/rehydrate wired to spin-down/resume; route checkpointer through `lib/scrubber.py` | P0 exit requires lossless mid-execute kill |

**P0 exit oracle:** `tests/integration/test_e2e_ship_acceptance.py` — file is ABSENT; must be created. It's the single §12 exit oracle: spine runs goal→clarify→sign-off→decompose→sandboxed-build→eval-gate→ship on a toy goal; eval gate blocks a bad PR; mid-execute kill rehydrates losslessly.

### Gemini 3.1 Pro — best for live GCP infrastructure

These require `terraform apply` on `autonomous-agent-2026` and are **operator-blocked until the project is provisioned.**

| ID | What it needs |
|---|---|
| **SP-23** | `terraform apply terraform/phase-0a-gcp/` on `autonomous-agent-2026`; Cloud SQL + Pub/Sub + WIF + VPC-SC + CMEK. Then wire the checkpointer + memory to Cloud SQL. |
| **SP-R5** | Live Cloud SQL PITR drill (depends SP-23). Runbook: `docs/runbooks/` (create if absent). |
| **SP-O5** | `docker-compose.yml` Langfuse service is broken (missing ClickHouse/Redis/MinIO). Fix compose or replace with Langfuse Cloud. |

---

## Critical files for any new agent

| File | Why read it |
|---|---|
| `CLAUDE.md` | Project constraints, GCP project id, C9 reviewer rule, commit-signing |
| `audit/2026-06-03-prd-code-truth/findings-reconciled.md` | Line-by-line DONE/PARTIAL/THEATRE/ABSENT table for all 49 tasks |
| `audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md` | The full PRD — all acceptance criteria |
| `app/core/graph.py` | The LangGraph spine; stub nodes are at lines ~154 (seal_spec), ~175 (execute) |
| `lib/anchors/__init__.py:214-224` | The SP-03 TODO stub block to replace |
| `app/adapters/inmemory/sandbox.py` | The hermetic sandbox adapter to wire into SP-05 |
| `config/dead_code_entrypoints.txt` | C4 gate; new public symbols from new files need entries here |
| `audit/acceptance/SHA256SUMS` | C8/C10 — every new acceptance YAML must be sha256-pinned here in a **separate commit** |

---

## Process rules to preserve (non-negotiable)

1. **C9 reviewer model class inequality**: every P0/P1 fix reviewed by a different model. Sonnet reviewing → Opus or Gemini reviews. Opus reviewing → Sonnet or Gemini. Same model = blocked.
2. **4-eyes commits**: `config/dead_code_entrypoints.txt` changes in a separate commit from the code they exempt. `SHA256SUMS` changes in a separate commit from the YAML they pin.
3. **Conventional commit titles**: subject after `type(scope):` must start lowercase. CI enforces.
4. **Never skip hooks**: `--no-verify` is forbidden. If a hook fails, fix the underlying issue.
5. **Never push plaintext secrets**. Never force-push main.
6. **GCP project**: ALL new resources in `autonomous-agent-2026` — never `i-for-ai`.

---

## Switching model notes

**Antigravity IDE (Opus 4.6/4.8)**
- Open the repo at `/Users/danielmanzela/RX-Research Project/AutonomousAgent`
- Read this file + `CLAUDE.md` + the PRD at `audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md`
- Start with SP-03 (the smallest unlock for the whole critical path)
- Use Gemini or Sonnet as C9 reviewer

**Gemini 3.1 Pro (via `gemini-gcp` skill)**
- `GEMINI.md` is in the repo root; skills auto-discovered
- Use `gemini-gcp` skill for GCP MCP calls (cloudrun, bigquery, terraform)
- GCP infrastructure tasks: `terraform plan` first, share output, get operator approval before `apply`
- Use Opus or Sonnet as C9 reviewer

---

## What NOT to do

- Do not port seed MoE/PPO/reward from `docs/research/` — SP-22 tombstoned this; LangGraph spine is the design
- Do not collapse `AbstractSandbox`/`AbstractBoard`/`AbstractEmbedder` ABCs — keep the adapter pattern
- Do not create new GCP resources in `i-for-ai`
- Do not re-run the 2026-06-03 PRD audit — it's done and reconciled; trust the findings table
