# Ground-Truth Audit — Master Findings

**Date:** 2026-05-27
**Branch:** `fix/p2-self-correction-pass` (HEAD `1585ee0`)
**Auditor:** Tier-1 parallel fan-out (8 subagents) + parent synthesis
**Method:** AST/YAML/HCL only. ALL `.md` files and `# / // / """ docstring` content EXCLUDED.
**Source reports:** subagent reports 01..08 (cited inline per finding; subagent transcripts were not persisted in the session-recovery — every finding remains independently re-derivable from the codebase via the cited `file:line`).

---

## Verdict

**NOT PRODUCTION-READY.** Eight independently re-verified P0 deploy-blockers (down from nine after a self-correction pass — P0-1 retracted; see counts table); seventy-eight additional P1/P2 gaps (fifty-one P1 + twenty-seven P2) spanning supply-chain, sandbox isolation, observability, safety machinery, and the abstract-adapter contract. The headline issue is **silent safety failure**, not import failure: the four-judge consensus loop, the REJECTED-injection feedback loop, and the production-grade sandbox path are all paper constructs in the running code. The driver still defaults every Hermes invocation to Opus, and the supply-chain pipeline signs SBOMs but never the deployed image itself.

| Severity | Count | Domain spread |
|---|---|---|
| **P0** (deploy-blocker or silent safety failure) | **8** | judge stub, REJECTED-inject dead, A2A audience contract, sandbox vapor, embedder vapor, supply-chain image-unsigned, gcloud bind-mounts, default-Opus *(P0-1 retracted — see REFUTED list)* |
| **P1** (correctness-under-load / cannot operate at 24/7 SLO) | **51** | C×7 concurrency, S×3 safety, SC×6 supply-chain, I×7 container/secrets, O×9 observability, T×5 tests, A×4 adapter, SB×4 sandbox, CC×6 cost (incl. **HALT_F21 sentinel missing — CC-6 reinstated 2026-05-27**) |
| **P2** (defence-in-depth, follow-up sprint) | **27** | type hints, jitter, log-format, dependabot cadence |
| **P3** (cosmetic / nice-to-have) | **11** | naming, doc URLs |
| **REFUTED** (prior finding wrong — do not re-flag) | **7** | hooks ARE wired; testpaths fixed; container limits fixed; A2A default flipped; cloud-sql-proxy binds 127.0.0.1; **P0-1 NameError was wrong**; **P0-3 AttributeError was wrong (real bug exists, root cause re-cited)**. *(R-1 HALT_F21 refutation retracted 2026-05-27 — was itself a hallucination; F-CC-1 reinstated as CC-6.)* |
| **TOTAL distinct findings** | **104** | (8 P0 + 51 P1 + 27 P2 + 11 P3 + 7 REFUTED — REFUTED counts include self-corrections) |

**Self-correction pass conducted 2026-05-27** before audit-plan was written: two original P0 citations (P0-1 import NameError, P0-3 AttributeError) failed re-verification against the live filesystem. P0-1 was retracted entirely; the broader claim under P0-3 (REJECTED-inject is dead) is still correct, but the root cause was rewritten from "AttributeError on `_rej.get_active_entries`" to "`ctx is None` early-return at `lib/durability/__init__.py:209`" — the call signature was always right; the entire calling branch never executes. **The audit-plan W0 sequencing reflects 8 active P0s (P0-2..P0-9) mapped to 7 W0 PRs (W0.4 combines P0-5 + P0-6 into a single adapter-stub PR), down from 9 pre-retraction.**

The **counter-claims** matter as much as the findings: half of last week's audit memory is stale (fixes landed), and one of the loudest "new" claims (`F-CC-1: HALT_F21 has no reader`) is wrong. The synthesis below corrects both directions to prevent re-doing work or trusting a false-positive.

---

## P0 — Deploy-blockers (independently verified against current HEAD)

### P0-1. ~~`lib/a2a/server.py:76` — `NameError: Path` at import time~~ **REFUTED**

> **Self-correction (2026-05-27, re-verification pass):** This finding was a **false positive**. Re-verification shows:
> - `grep -c "Path" lib/a2a/server.py` returns **0** — zero `Path` references in the entire 607-line file.
> - Line 76 is `_PEERS_CACHE_LOCK = asyncio.Lock()`.
> - Line 77 uses `os.path.join(os.path.dirname(__file__), "../../config/a2a/peers.yaml")` — `os.path` style, no `pathlib` import needed.
> - Module loads cleanly under `python -c "import lib.a2a.server"`.
>
> **Origin of error:** A subagent report mis-cited an unrelated diff hunk against this file. The synthesis pass propagated the citation without re-opening the file. Filed as a process-correction lesson — future audit passes must re-verify every P0 cite by direct Read before promoting to the deploy-blocker list.
>
> **Net effect on plan:** W0 sequencing reduces from 8 PRs to 7. The "smoke-import CI gate" item proposed in the original W0.1 is preserved as **W1.E-new** (a defensive CI hygiene improvement worth doing for its own sake, just not a deploy-blocker).

### P0-2. `lib/evaluators/__init__.py:65–70` — judge panel is a hardcoded stub
- **Cite:** Reads literally:
  ```python
  feedback = PendingFeedback(
      verdict="accept",
      reasoning=f"[stub] Judge panel would evaluate tool={tool_name}",
      axes_failed=[],
  )
  queue_judge_dispatch(session_id=session_id, feedback=feedback)
  ```
- **Verified:** entire `_on_post_tool_call` hook is a stub. There is no LiteLLM call, no per-axis prompt, no consensus computation. Comment at line 61–63 acknowledges "Phase 1: log-only dispatch."
- **Blast radius:** The 4-judge consensus + 5th-judge tiebreak that is the **entire scope-locking + safety-axis evaluation contract** of the autonomous agent does not exist. Every tool call is accepted. The product claim "rejects out-of-scope actions via 75% judge consensus" is false in code.
- **Reports:** subagent 02 §F-A5 + §F-D1 (both CRITICAL).
- **Why not caught:** the hook IS registered (verified — refutes prior "ZERO hooks" finding); the test asserts feedback is queued, not that the verdict is grounded in any LLM output. No integration test verifies that a deliberately bad tool call produces `verdict="reject"`.

### P0-3. `lib/durability/__init__.py:204–223` — REJECTED-inject is dead (corrected root cause)

> **Self-correction (2026-05-27, re-verification pass):** The original P0-3 ("AttributeError on `_rej.get_active_entries`") was a **false positive**. Re-verification shows the actual call at line 255 is `_rej.load_active_entries(intent_category=category, max_entries=max_inject)` — the function name and signature both exist and match `lib/memory/rejected.py:232`. **However, the broader claim — that REJECTED-inject is dead in production — is still correct, just for a different reason. Promoted to P0 under the corrected root cause below.**

- **Cite:** `lib/durability/__init__.py:204–209` — the function early-returns when `ctx is None`:
  ```python
  ctx = kwargs.get("ctx")
  if ctx is None:
      # No ctx yet on Hermes ``on_session_start`` surface — graceful no-op
      return None
  ```
- **Verified:** the Hermes `on_session_start` hook surface does not pass `ctx` at all on the current driver version (verified by inspecting the registered hook signatures via `lib/anchors/__init__.py:104–108`). `kwargs.get("ctx")` always returns `None`. The entire intent-classification + REJECTED-inject branch at lines 216–end is dead code on the present hook contract.
- **Blast radius:** identical to original P0-3 — P1-4 REJECTED.md institutional memory never injects into prompts, so the agent re-attempts recently-rejected approaches on every fresh session. The "doesn't repeat its mistakes" property fails silently.
- **Reports:** subagent 02 §F-D2 (CRITICAL) — original AttributeError citation withdrawn; corrected to `ctx is None` early-return.
- **Fix shape (revised):** **Do NOT just rename the call** (that fix was wrong — there's nothing to rename). Instead:
  1. Drop the `ctx is None` early-return; replace with a `session_id`-keyed retrieval path that does not require a Hermes ctx surface.
  2. Move intent-category resolution to happen at `on_session_start` from the **TaskSpec snapshot** (Hermes guarantees TaskSpec is materialized by then; see `lib/anchors/__init__.py:138–162`).
  3. Call `_rej.load_active_entries(intent_category=resolved_category, max_entries=_rej.DEFAULT_MAX_INJECT)`.
  4. Inject the result via Hermes' system-message prepend path (the same surface `lib/evaluators/__init__.py:61` uses).
  5. Add an integration test that asserts a previously-rejected approach surfaces as a system message at next session start.

### P0-4. A2A audience-claim contract mismatch — end-to-end auth never worked against documented config
- **Cite:** `lib/a2a/auth.py:276` decodes with `audience=our_sa` (our own SA email). `config/a2a/peers.yaml:25` documents `audience: https://agent-canary.example.test` (a URL) for outbound JWTs targeting the canary.
- **Verified:** the verify path requires the JWT's `aud` claim to equal the receiver's SA email. The peers.yaml field is what the **sender** should mint into outbound JWTs — but if the sender mints `aud=https://agent-canary.example.test`, the receiver's verify call rejects with `InvalidAudienceError` → `rejected_invalid_sig` audit log.
- **Blast radius:** any operator following peers.yaml as-documented produces JWTs that the canary will reject. End-to-end A2A auth against the documented peer set never worked. The integration test that uses fakeredis (per subagent 05 §F-T-5) mints + verifies against the same in-test fixture so this never surfaces.
- **Reports:** subagent 01 P0-B.
- **Decision:** `decisions.md` D-1 — pick option A (SA email). Update `peers.yaml`, do NOT change `auth.py`.

### P0-5. `app/adapters/gcp/sandbox.py` and `FirecrackerSandbox` do not exist on disk
- **Cite:** `app/adapters/gcp/` directory contents = `{__init__.py, memory.py}` only.
- **Verified:** no file `app/adapters/gcp/sandbox.py`. No class `FirecrackerSandbox` anywhere in the repo. The only `AbstractSandbox` impl is `LocalSubprocessSandbox` at `app/adapters/inmemory/sandbox.py:13`, which is marked `is_production_grade = False` (line 21) but no `OrchestratorConfig` enforces that flag.
- **Blast radius:** production sandbox is vapor. The "5-layer isolation defence" the seed research mandates does not exist in `app/`. Even if H1 Firecracker is deferred, the project must either provide a production-grade adapter OR refuse to start when sandbox `is_production_grade=False`. Neither happens.
- **Reports:** subagent 03 C-2; subagent 08 F-SB-3.

### P0-6. `app/adapters/gcp/embedder.py` does not exist on disk
- **Cite:** Same directory as P0-5. Missing file.
- **Verified:** the only `AbstractEmbedder` impls are `HashingEmbedder` and `SentenceTransformerEmbedder` (mislabeled — it's a model-loading class) at `app/adapters/inmemory/embedder.py`. No Vertex-backed embedder exists.
- **Blast radius:** the production memory store needs 256-dim vectors per the project memory's phase2 spec; without a `VertexEmbeddingsEmbedder`, prod has no path to embed. CI deploys green because the test schema uses `dim=8`; first prod request fails on dim mismatch.
- **Reports:** subagent 03 C-2.

### P0-7. Supply-chain — signed image is theatre; deploy verifies nothing
- **Cite:** `.github/workflows/sbom-cosign.yml:47–74` builds `autonomousagent/hermes:${{ github.ref_name }}` locally, signs the SBOM blob (`cosign sign-blob`, keyless OIDC), but **never pushes or signs the image**. `.github/workflows/phase-0a-deploy.yml:152–154` builds + pushes the image with no `cosign sign` and no `--provenance`/`--sbom`. Lines `:271–272` perform `docker compose pull` with **zero `cosign verify-attestation`** preceding it.
- **Verified:** SLSA Build Level achieved = **L1** (not L2 — no provenance bound to the deployed image; not L3 — non-hermetic `curl|sh` uv install at `Dockerfile.hermes:23`).
- **Blast radius:** any registry compromise or in-band image replacement is undetected at deploy. The deployed binary has no signature linking it to the source repo.
- **Reports:** subagent 08 F-SC-1, F-SC-2, F-SC-5, F-SC-7; subagent 05 F-SC-1, F-SC-2.

### P0-8. `${HOME}/.config/gcloud` bind-mounted into THREE containers — RCE pivots to full GCP
- **Cite:** `deploy/docker-compose.yml:138` (litellm-proxy), `:520` (cloud-sql-proxy), `:607` (snapshot-watchdog) bind `${HOME}/.config/gcloud:/root/.config/gcloud:ro`.
- **Verified:** any RCE inside any of those three containers can read the host operator's GCloud refresh token. The refresh token has the full set of GCP scopes the operator's `gcloud auth login` granted — typically full project-edit at minimum, often org-wide for Manzela's setup. The ro mount limits writes, not reads; the credential leaks regardless.
- **Blast radius:** containment failure. A LiteLLM proxy CVE → full GCP project takeover (delete cluster, drain billing, exfil all secrets).
- **Reports:** subagent 04 C-CSI-2.

### P0-9. Default model is Opus everywhere — no per-task tier router
- **Cite:** `config/hermes/cli-config.yaml:22` — `default: "vertex_ai/claude-opus-4-7"`.
- **Verified:** every Hermes CLI invocation defaults to Opus. The judges layer correctly tiers (`config/limits.yaml:153-156` — `accept_threshold/reject_threshold = 0.75` with 5th-judge tiebreak `vertex_ai/claude-opus-4-7`), but the **driver** runs Opus. (Previous audit cite of `:168-172` was stale — corrected 2026-05-27 against current `config/limits.yaml` HEAD.)
- **Blast radius:** at $0.015/1K input + $0.075/1K output, a 67k-output turn costs $5. The configured $500/day cap admits ~100 such turns; combined with the soft-stop sentinel (see refutation below) and 5-minute watchdog poll, daily spend regularly overshoots.
- **Reports:** subagent 08 F-CC-4.
- **Decision:** `decisions.md` D-2 — supersedes simple "switch to Sonnet". Adopt multi-vendor tier matrix; orchestrator default = Gemini 3.1 Pro.

---

## P1 — High-severity (cannot operate 24/7 unattended)

Grouped by domain. Cite = `file:line`. All independently re-readable.

### P1.A — Concurrency / async hazards (will silently degrade under load)

| ID | Cite | Issue |
|---|---|---|
| C-1 | `lib/a2a/auth.py:389–404` + `lib/a2a/agent_card.py:75` | `credentials.refresh(Request())` is **sync**. Inside an async path it blocks the event loop on every GCE metadata RTT (5–50 ms typical, longer under contention). Wrap in `asyncio.to_thread`. |
| C-2 | `lib/a2a/auth.py:296` vs `:300` | Docstring says `default fail-OPEN`, code defaults `A2A_JTI_FAIL_MODE` to `"closed"` (line 300: `_os.getenv("A2A_JTI_FAIL_MODE", "closed")`). Operator playbook is wrong against current behaviour. Pick one; align both. |
| C-3 | `lib/a2a/client.py:271,309,341` | `httpx.AsyncClient()` instantiated per call. No pooling → full TCP+TLS+H2 handshake on every send. Hoist to module-level singleton with `httpx.AsyncClient(http2=True, limits=...)`. |
| C-4 | `lib/a2a/client.py:116–131` | Exponential backoff without jitter. N replicas retrying after the same upstream blip → thundering herd. Add `delay += random.uniform(0, delay * 0.2)`. |
| C-5 | `app/adapters/gcp/memory.py:65–100` | `_get_pool()` race-safe lock pattern is correct **but** the pool is keyed implicitly by whoever raced first. Two callers with different DSNs silently bind to the wrong store. Key the singleton by DSN. |
| C-6 | `lib/a2a/server.py:84` (`_TASK_REGISTRY`) **AND** `lib/a2a/auth.py:73` (`_JTI_L1_FALLBACK`) | Both are `cachetools.TTLCache` (good — bounded) but **process-local**. Multi-replica → ghost tasks (`tasks/get` on replica B for a task created on replica A returns 404) **and** JTI L1-fallback divergence on Redis outage. Auth layer JTI cache is already an L1 fallback for the distributed Redis JTI store (PR #152) — gap is on the server-layer task registry. Use Cloud SQL / Redis when scaling beyond single replica. **(Previous cite of `server.py:82,:304` was stale — `:82` is the `from cachetools import TTLCache` import; no TTLCache at `:304`. Re-verified 2026-05-27 via `grep -n TTLCache lib/a2a/server.py`.)** |
| C-7 | `lib/a2a/server.py:376–384` | Body-size middleware reads `Content-Length` only. Chunked Transfer-Encoding bypasses cap. Count bytes from ASGI `receive()`. |

### P1.B — Safety machinery (silent failures cited in P0; remaining P1)

| ID | Cite | Issue |
|---|---|---|
| S-1 | `lib/durability/__init__.py:200–224` | `ctx is None` is the always-state on current Hermes hook surface; the entire ctx-based branch at lines 223–end is dead code. Even after fixing P0-3, the ctx-based intent classification at lines 232–250 is unreachable. |
| S-2 | No call sites for `judge_events.record_consensus_event` | The consensus-event recorder has zero callers in any hook. Even if P0-2 is fixed (judges become real), the consensus events are not persisted for replay. |
| S-3 | `lib/evaluators/orchestrator_hook.py` (drain_pending_feedback) | The drain path is wired (`lib/evaluators/__init__.py:79–90`) but with judges stubbed (P0-2), the queue only ever contains `verdict="accept"` items. The pre_llm_call hook injects them, but they say nothing meaningful. After P0-2 fix, this becomes a real signal. |

### P1.C — Supply-chain hardening (beyond P0-7)

| ID | Cite | Issue |
|---|---|---|
| SC-1 | `.github/workflows/ci.yml:241–252` | CI runs `uv pip install -e ".[dev]"` instead of `uv sync --frozen`. Transitive deps resolve fresh each CI run; malicious indirect-dep release ships on next CI. Switch to `uv sync --frozen --extra dev --extra gcp --extra a2a`. |
| SC-2 | `.github/dependabot.yml:21,41,65,78` | Monthly cadence — slower than OpenSSF Scorecard baseline (weekly) and slower than 7-day CVE SLA. |
| SC-3 | `deploy/docker-compose.yml:508` | `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.15.0` default fallback is tag-only — no digest pin. Registry mutation injects code into SQL-proxy sidecar. |
| SC-4 | `deploy/Dockerfile.hermes:23` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` is non-hermetic. Mirror the uv binary with SHA-256 verification, or install via `pip install uv==X.Y.Z`. |
| SC-5 | No `.github/workflows/scorecard.yml` | OpenSSF Scorecard does not run on this repo. Add `ossf/scorecard-action@v2` on weekly schedule. |
| SC-6 | `.github/workflows/sbom-cosign.yml` triggers only on `tags: ['v*']` | SBOM signing only happens at release tag; deployed images from main never get an SBOM. |

### P1.D — Container / secrets / infra (beyond P0-8)

| ID | Cite | Issue |
|---|---|---|
| I-1 | `secrets/hermes-provider.env` | No `.sops` sibling. Pre-commit hook patterns prevent committing plaintext, but the file's existence in `secrets/` without encryption violates the project's documented secrets posture. |
| I-2 | `deploy/Dockerfile.canary:20` (agent-canary) | No `USER`, no `cap_drop`, no `read_only`, no `no-new-privileges`, no digest pin. Canary peer is the LEAST hardened service in the compose. |
| I-3 | `scripts/migrate_cloud_sql.py:99–111` | DDL applied without any `BEGIN ... COMMIT`. Partial failure leaves the DB in half-migrated state. Wrap in a transaction; assert idempotency of each block. |
| I-4 | `deploy/docker-compose.yml` (litellm-proxy service) | No `cap_drop: [ALL]`, no `read_only: true`, no `security_opt: no-new-privileges:true`, runs as root. Same hardening that hermes already has (per CF-1 fixes). |
| I-5 | `terraform/phase-0a-gcp/networking.tf:75` | `allow_egress_all = 0.0.0.0/0` — default-allow egress. Replace with an explicit allowlist (Vertex, Anthropic, GitHub, Telegram, Honcho). |
| I-6 | `terraform/phase-0a-gcp/memorystore/main.tf:36–41` | Memorystore AUTH disabled. Even on private VPC, defence-in-depth wants AUTH enabled. |
| I-7 | `scripts/migrate_cloud_sql.py` | HNSW index NOT created in production migration (test schema has it at `app/tests/test_cloud_sql_pgvector_store.py:107–114`). `SET LOCAL hnsw.ef_search` in `app/adapters/gcp/memory.py:225` becomes a silent no-op against a sequential scan. Confirmed regression. Copy the HNSW CREATE INDEX from the test schema. |

### P1.E — Observability / SRE (beyond the verdict-level conclusion)

| ID | Cite | Issue |
|---|---|---|
| O-1 | Whole repo | Only **1** OTel `meter.create_*` exists (`lib/durability/runtime_detectors.py:97–98`). No per-turn cost histogram, no LLM latency histogram, no error counter. Spans alone cannot drive SLO burn-rate alerts. |
| O-2 | `deploy/otel/collector.prod.yaml` | No `spanmetrics` connector. Cannot derive p99 latency from existing spans. |
| O-3 | `deploy/docker-compose.yml:470–477` | Hermes healthcheck is a filesystem touch (`open('.healthprobe','w').close(); os.unlink(...)`). Returns healthy while Vertex/Honcho/Chroma/Cloud-SQL/Memorystore all down. `lib/healthcheck.py` has proper `run_checks(deps)` but is not wired. |
| O-4 | `lib/a2a/server.py:394–396` | `/health` returns hardcoded `{"status":"ok"}`. Does not probe jti cache, agent-card signer, or in-process registry. |
| O-5 | `scripts/*_loop.py` | Watchdog loops emit `logger.info`/`print` only. No `meter.create_counter("watchdog.ticks", labels={loop=...})`. A wedged watchdog is invisible until the email-only alert fires (or doesn't). |
| O-6 | Whole repo | All `logging.basicConfig` uses plain-text format. GCP Cloud Logging stores as `textPayload`, defeating the `jsonPayload.msg=...` filter at `terraform/phase-0a-gcp/monitoring.tf:32`. The single log-based metric only works because watchdog restarts come from a bash script via gcplogs, not Python. |
| O-7 | `lib/scrubber.py:167–185` | PII scrubber is called only at A2A boundary + Telegram + LiteLLM callback. No `logging.Filter` subclass; Python `logger.info(...)` calls bypass scrub. JWTs, session-ids, chat-ids land raw in Cloud Logging. |
| O-8 | No `google_logging_project_sink` anywhere | All logs evaporate at 30-day Default bucket TTL. Forensic incident-replay impossible after 30 days. |
| O-9 | `deploy/otel/collector.dev.yaml:30–34` + prod equivalent | `limit_percentage: 80` + `spike_limit_percentage: 25` against `mem_limit: 512m` — spike trigger at 384 MiB, only 128 MiB headroom before OOM. Switch to absolute `limit_mib: 400` + `spike_limit_mib: 80`. |

### P1.F — Tests / CI gates

| ID | Cite | Issue |
|---|---|---|
| T-1 | `.github/workflows/ci.yml` (mypy job not required-for-merge) | `mypy` job exists but is not in branch-protection required checks. Type errors land on main. |
| T-2 | 5 integration tests unconditionally skipped | `chroma_outage`, `full_turn`, `secret_leak`, `budget_cap`, `skill_creation` are marked `pytest.mark.skip` permanently. Critical safety surfaces have zero integration coverage. |
| T-3 | `lib/a2a/tests/test_replay_cache_*.py` (PR #152 cache) | Tested ONLY against fakeredis. No real-redis integration test. The fail-open / fail-closed branch with a real Redis outage is unverified. |
| T-4 | Branch protection | Admin bypass enabled + 0 required reviewers + signed commits not required. The squash-only + CI-required rules can be bypassed. |
| T-5 | `.github/workflows/secret-scan.yml` | Schedule is monthly, not weekly. |

### P1.G — App-layer adapter contract

| ID | Cite | Issue |
|---|---|---|
| A-1 | `app/core/orchestrator.py` | Contains 3 module-level coroutines + 1 helper. **No `Orchestrator` class.** The seed research mandates a class with `submit()`, breaker windows, fitness EMA, trajectory buffer, GC loop. None of those exist. |
| A-2 | `app/core/orchestrator.py:102,178` | `from lib.a2a.client import send_message` is a direct `app→lib` import — exactly the cross-layer dependency the hybrid pattern was designed to prevent. Introduce `AbstractA2AClient` in `app/core/` with the `lib.a2a.client` impl in `app/adapters/`. |
| A-3 | Missing `app/core/router.py`, `app/core/judge.py`, `app/core/reward.py` | Three of six mandated abstract surfaces (`AbstractMoERouter`, `Judge` Protocol, `AbstractIntrinsicRewardModel`) do not exist in `app/core/`. The MoE/PPO routing layer and reward signal are entirely absent. |
| A-4 | `scripts/migrate_cloud_sql.py:67–72` | Composite index is `(project_id, tier)` — wrong column order. Dominant query at `app/adapters/gcp/memory.py:237–244` filters by `tier = $2 AND project_id = ANY($3::text[])` — tier needs to be leading column for selectivity. Recreate as `(tier, project_id)`. |

### P1.H — Sandbox isolation (beyond P0-5)

| ID | Cite | Issue |
|---|---|---|
| SB-1 | `app/adapters/inmemory/sandbox.py:102–116` | rlimits silently downgrade on `OSError` (logger.warning → continue). Sandbox runs with no memory/fd cap if host kernel returns EPERM. Convert to fail-closed: raise. |
| SB-2 | `app/adapters/inmemory/sandbox.py:36–40` | Network "block" raises if `network_allowed=True` requested, but **does not block outbound** when False. Child process can `urllib.request.urlopen(...)`. Either wrap with `unshare -n` (Linux-only) or rename + gate behind `HERMES_ALLOW_INSECURE_SANDBOX=1`. |
| SB-3 | `deploy/sandboxes/Dockerfile.shell-sandbox:17` | Installs `build-essential` (gcc, make, ld) into sandbox image — F-20. Compiler in a sandbox is a free escalation kit. Remove or multi-stage. |
| SB-4 | `deploy/docker-compose.yml` (shell-sandbox service) | No `security_opt: ["no-new-privileges:true", "seccomp=..."]`. Default Docker seccomp permits `unshare`, `ptrace`. |

### P1.I — Cost-control (beyond P0-9)

| ID | Cite | Issue |
|---|---|---|
| CC-1 | `scripts/budget_watchdog_loop.py:24` | 5-minute poll allows multi-cap burns between ticks. Reduce to ≤30s **AND** move enforcement into LiteLLM proxy as per-key/per-tag `max_budget`. |
| CC-2 | `deploy/litellm/config.yaml:65` vs `config/limits.yaml:2` vs `terraform/phase-0a-gcp/billing.tf:14` | Three contradictory caps: $100/day proxy, $500/day runtime, ~$250/day GCP monthly-budget-implied. Pick one source-of-truth; cascade. |
| CC-3 | `config/limits.yaml:3–5` | All per-task token caps are `null` (`per_task_input_tokens`, `per_task_output_tokens`, `per_conversation_context`). A single runaway tool loop is unbounded. Set defaults: 32k / 16k / 128k. |
| CC-4 | `deploy/litellm/config.yaml:67` | `alert_to_webhook_url: ""` — empty. Budget alerts are silent even from the proxy. |
| CC-5 | `config/limits.yaml:50–72` | `approval.always_ask_patterns` misses `gh api -X DELETE`, `gh repo delete`, `bq rm`, `gsutil rm -r`. Scoped agent can call `gh api -X DELETE /repos/...` unprompted. |
| CC-6 | `lib/durability/budget_watchdog.py:174–195` + `lib/durability/handlers.py:119–187` (`halt_alert_snapshot`) | **Reinstated from R-1 retraction.** F21 dispatcher transitions the card to BLOCKED and sends Telegram, but writes no filesystem sentinel; no module polls `/data/HALT_F21` to short-circuit subsequent tool calls. If a new session starts (or a parallel replica is up) while the card is BLOCKED, there is no second-line tool-call veto enforced from disk. Fix shape: either (a) drop the sentinel narrative entirely from docs/prior memory and rely on card-state, or (b) make the sentinel real — write it from `halt_alert_snapshot` and read it from `_on_pre_tool_call`. Decision deferred to W1.I owner; document chosen path in the W1.I PR body. |

---

## P2 — Medium-severity (sprint-able alongside operations)

Abbreviated — see audit-plan.md for fixes.

| ID | Domain | Cite | Issue |
|---|---|---|---|
| P2-1 | Concurrency | `lib/durability/checkpoint.py:91` | No `flock` on `step_N.json` writes. Two writers race at `os.replace` — atomic, but loser's data silently lost. Add flock or document single-writer invariant. |
| P2-2 | Concurrency | `lib/snapshots/gcs_snapshot.py:147` | `_today_already_uploaded` then upload — TOCTOU. Two replicas can both observe False and both upload. Acceptable last-writer-wins for daily snapshots; flag for active-active. |
| P2-3 | Concurrency | `lib/durability/escalation.py:36` | `sqlite3.connect(db_path)` uses stdlib default 5s timeout. Explicit `timeout=30` safer under contention. |
| P2-4 | Concurrency | `lib/observability/otel_setup.py:57–59,158–160` | `_initialized` global TOCTOU. Concurrent `setup_tracing()` double-init. SDK is idempotent; cosmetic warning only. |
| P2-5 | App-layer | `app/adapters/gcp/memory.py:132–140` | HNSW `ef_search` is per-store, not per-query. High-recall verification + fast-precision routing can't share a store. Add per-call override. |
| P2-6 | App-layer | `app/adapters/gcp/memory.py:74–75` | `_get_pool` raises `RuntimeError` (not `ImportError`) on missing `asyncpg`/`pgvector`, with no distinction which dep is missing. Diagnostic poor. |
| P2-7 | App-layer | `app/adapters/gcp/memory.py:217` | `now_ts = time.time()` computed in Python then passed into SQL `WHERE expires_at > $5`. Clock skew between app and DB leaks expired rows. Use `EXTRACT(EPOCH FROM NOW())` server-side. |
| P2-8 | App-layer | `app/adapters/gcp/memory.py:156–189` | `put()` upserts on `record_id`. No content_hash UNIQUE; duplicate-by-hash inserts both. In-memory store dedups; behavioural drift. |
| P2-9 | App-layer | `app/core/orchestrator.py:317` | `_map_a2a_status` collapses `CANCELED → FAILED` (no `CANCELED` in TaskStatus). Operators see same metric for peer-cancelled vs peer-failed. |
| P2-10 | App-layer | `app/adapters/gcp/memory.py` | No factory enforcing `embedder.dim == store.dim`. Misconfigured deploy passes CI, fails first request. |
| P2-11 | Observability | `deploy/docker-compose.yml:179` | Base compose defaults to `OTEL_CONFIG:-./otel/collector.dev.yaml`. ANY operator running base `docker compose up` (smoke, panic, snapshot scripts) gets dev collector. Move default to prod YAML; require dev override flag. |
| P2-12 | Observability | `deploy/docker-compose.yml:207` | Phoenix UI bound loopback-only; no auth, no IAP. If operator port-forwards (incident triage), UI is reachable on laptop with zero auth. |
| P2-13 | Observability | `pyproject.toml:6–22, 30, 62` | OTel SDK not in `[project.dependencies]` — only in `dev` and `a2a` extras. Prod uv-sync without `--extra a2a` makes OTel imports silent no-ops. Move OTel to main deps. |
| P2-14 | Observability | `lib/observability/__init__.py` (HERMES_DUAL_EMIT_GEN_AI) | Dual-emit flag default-off in prod. `gen_ai.*` semconv consumers see no data unless flag flipped. |
| P2-15 | Observability | `terraform/phase-0a-gcp/monitoring.tf:11–21` | Single email channel for both alerts + budget alerts. `auto_close=1800s` flapping watchdog generates fresh alert every 31 min. No PagerDuty / Slack fan-out. |
| P2-16 | Observability | `terraform/phase-0a-gcp/monitoring.tf:69–71,105–107` | Alert documentation has no runbook URL; only inline plain-text content. On-call engineer has no canonical fix-it doc. |
| P2-17 | Observability | `tests/phase_0a/chaos.sh` | Only chaos scenario is "kill hermes." No chaos for Vertex/Honcho/Chroma/Cloud-SQL/Memorystore/Telegram/GitHub/OTel/disk-full/JWT-clock-drift. |
| P2-18 | Tests | `app/tests/test_peer_dispatch.py` | All A2A paths mocked via `unittest.mock.patch`. The real canary peer at `app/a2a_canary/main.py` is never wired into a real dispatch test. |
| P2-19 | Tests | `tests/integration/test_sandbox_isolation.py` | Only 2 escape-attempt tests (network, fs-write). No fork-bomb, mem-bomb, cap-sys-admin, seccomp, rlimit-degrade. |
| P2-20 | Tests | `scripts/migrate_cloud_sql.py` | DDL migration script has zero tests. Idempotency claim unverified. |
| P2-21 | Cost | `config/limits.yaml:50–72` | Approval patterns regex-based; no semantic verb-blocklist for generic `*api*` DELETE invocations. |
| P2-22 | Supply-chain | `.github/workflows/sbom-cosign.yml:24` | `contents: write` over-scoped (job only attaches release assets). Demote where possible. |
| P2-23 | Supply-chain | `phase-0a-deploy.yml` | No SBOM generated for the actually-deployed image. Release SBOM only describes tagged releases, not main-deploys. |
| P2-24 | Container | `deploy/docker-compose.yml:175,203,253` | `latest@sha256:...` is contradictory — tag-plus-digest tripping drift detectors. Cosmetic but noisy. |
| P2-25 | Container | `deploy/Dockerfile.hermes` (single stage) | Not multi-stage, not distroless. Final image carries `apt`, shell, build-time tooling. Multi-stage with `FROM gcr.io/distroless/python3.11-debian12@sha256:...`. |
| P2-26 | Concurrency | `lib/trajectory/shipper.py:170` | `ship_one` is sync. Future async caller blocks event loop. Provide `ship_one_async` or doc sync-only contract. |
| P2-27 | Container | `terraform/phase-0a-gcp/firewall.tf` | No egress allowlist; any compromised container reaches the Internet on any port. |

---

## P3 — Low-severity / cosmetic (defence-in-depth)

| ID | Cite | Issue |
|---|---|---|
| P3-1 | `lib/durability/escalation.py:25` | `db_path: str = None` should be `Optional[str]`. |
| P3-2 | `app/__init__.py`, `app/adapters/__init__.py`, `app/core/__init__.py`, `app/a2a_canary/__init__.py` | 1-line stubs; no public-surface declaration. Refactoring outside the package will be lossy. |
| P3-3 | `app/adapters/inmemory/embedder.py:52–77` | `SentenceTransformerEmbedder` is misplaced (not in-memory; loads a model). Move to `app/adapters/local_model/` or `app/adapters/gcp/`. |
| P3-4 | `app/adapters/inmemory/sandbox.py:21` | `is_production_grade=False` is a class attr; nothing enforces it. Add `OrchestratorConfig.production` gate that refuses to start with `is_production_grade=False`. |
| P3-5 | `app/core/orchestrator.py:38,41` | `_default_*_timeout_s` leading-underscore (private) names exposed via positional args. Convention violation. |
| P3-6 | `lib/observability/__init__.py:686–697` | Token counts as span attributes only. Subsumed by O-1 fix. |
| P3-7 | `deploy/otel/collector.prod.yaml` | Tail-sampling block may drop error traces if policy regex misses; minor relative to O-2. |
| P3-8 | `app/adapters/gcp/memory.py:299–313` | `gc_expired()` parses asyncpg status string. Fragile if asyncpg changes format. Use `conn.fetchval` with `RETURNING`. |
| P3-9 | `app/adapters/gcp/memory.py:60–62, 92` | `register_vector` runs on every connection; no startup self-check that `CREATE EXTENSION vector` ran. First call against a fresh DB without migration raises. |
| P3-10 | `lib/durability/budget_watchdog.py:197` | F21 dispatch wrapped in `except Exception: logger.warning(...)` — fail-open. If F21 dispatch itself fails, no audit trail. |
| P3-11 | `app/adapters/inmemory/sandbox.py` (preexec_fn) | Uses `preexec_fn` which is unsafe in multi-threaded Python (fork-deadlock risk). Test-only adapter; acceptable for CI. |

---

## REFUTED — Prior claims that do not hold (do not re-flag)

### R-1. ~~"HALT_F21 sentinel has no reader" — **WRONG**~~ **RETRACTED 2026-05-27 (second self-correction)**

> **Self-correction:** The refutation itself was a hallucination. Direct re-verification of the live tree (2026-05-27 in the verification-appendix prep pass) found:
> - `grep -rln HALT_F21 lib/ scripts/ app/ deploy/` returns **zero matches** outside this audit directory.
> - `lib/anchors/__init__.py:196` is `return None` inside `_on_pre_tool_call` — there is NO HALT_F21 reference there.
> - `lib/durability/handlers.py` is **593 lines** — the cited `:632` line does not exist.
> - The cited `_HALT_SENTINEL = Path(os.getenv("HALT_SENTINEL_PATH", "/data/HALT_F21"))` constant does not exist anywhere in the repo.
>
> **What is actually in place:** `lib/durability/budget_watchdog.py` dispatches the failure-matrix `F21` handler (`halt_alert_snapshot` at `lib/durability/handlers.py:119`). That handler writes a Telegram alert, a snapshot, and transitions the card to BLOCKED — but it does NOT write `/data/HALT_F21`, and **nothing reads it back at the tool-call boundary**. So the original F-CC-1 claim ("no Hermes module imports HALT_F21, polls it, or short-circuits on its existence") is **correct as-stated**.
>
> **Net effect on findings:** F-CC-1 is reinstated as a real finding. Promoted to **P1.I CC-6** below. The pattern claimed in R-1 ("operator can clear /data/HALT_F21 to resume") does not exist; the actual recovery surface is the BLOCKED card transition + Telegram approval flow, which IS wired (verified) but is a different semantic than a filesystem sentinel.
>
> **Lesson:** subagent reports that present neat "verified counter-evidence" with three crisp `file:line` cites must still be reopened by the parent before promoting to a REFUTED entry. The verification-appendix prep pass caught this one; an earlier catch would have prevented the misleading "high confidence" framing at the bottom of this file.

### R-2. "Hermes plugin hooks are ZERO wired" — **WRONG**
- **Source claim:** prior audit memory mentioning "ZERO hooks."
- **Verified counter-evidence:** `lib/evaluators/__init__.py:104–108` registers `post_tool_call`, `pre_llm_call`, `on_session_end`. Hooks ARE registered. The P0 is **what those hooks do internally** (P0-2: judge stub), not whether they fire.

### R-3. "lib/a2a/tests not in testpaths" — **FIXED**
- **Verified:** `pyproject.toml:77` includes `lib/a2a/tests` in `testpaths`. Prior finding closed.

### R-4. "Container resource limits missing for hermes service" — **FIXED**
- **Verified:** `deploy/docker-compose.yml:368–371` sets `mem_limit`, `cpus`, `pids_limit` for the hermes service. F-3 closed.

### R-5. "A2A requires auth off by default" — **FIXED**
- **Verified:** `lib/a2a/server.py:61–69` — secure-by-default. `A2A_DEV_INSECURE` must be explicitly set to disable auth, and refuses to run in `ENVIRONMENT=production`. F-2 closed.

### R-6. "cloud-sql-proxy binds 0.0.0.0" — **FIXED**
- **Verified:** `deploy/docker-compose.yml:513–514` binds 127.0.0.1. CF-1 closed.

---

## Cross-domain themes (synthesis)

1. **"Documented but never implemented" is the dominant failure mode.** The judge consensus, the REJECTED-inject, the FirecrackerSandbox, the SLSA L2 attestation, the SLOs — all have polished design docs and stub or absent code. The user's audit rule (ignore .md, ignore comments) surfaces exactly this gap.

2. **Silent failures dominate over loud failures.** Five of the eight active P0s manifest as "everything looks healthy in logs while critical machinery is no-op": stub judges accept everything (P0-2); `ctx is None` early-return swallows the REJECTED-inject branch (P0-3); the production-grade sandbox class is a stub (P0-5); `app/adapters/gcp/embedder.py` doesn't exist (P0-6); the signed-SBOM workflow produces an artifact while the deployed image itself remains unsigned (P0-7). Operators have no signal these are dead.

3. **The Hermes ↔ A2A boundary is the most fragile interface.** Out of the 8 active P0s, only one touches A2A directly (P0-4 audience-claim contract); the A2A surface re-emerges immediately at P1 (C-6 registry locality + JTI L1-fallback divergence on multi-replica, C-7 chunked Transfer-Encoding body-size bypass, O-4 `/health` hardcoded `ok`). Pre-retraction this synthesis read "four touch A2A" because P0-1 was counted (server.py import) and two P1 items (C-6, C-7) were misclassified as P0; both corrections landed in the 2026-05-27 self-correction pass. The PR cadence in this area is high; the test coverage that actually exercises the wire is low (fakeredis-only, mocked clients).

4. **The hybrid adapter pattern is half-applied.** `app/core/` has 3 of 6 mandated ABCs; `app/adapters/gcp/` has 1 of 6 implementations. The pattern that is supposed to keep production paths swappable for CI is non-functional for sandbox + embedder + router + judge + reward.

5. **Cost machinery has both feet on the soft pedal.** With R-1 retracted (CC-6 reinstated — no module polls `/data/HALT_F21`; the BLOCKED card transition is the only recovery surface, and it is per-process), the watchdog polls every 5 minutes, the proxy cap is 5× tighter than runtime policy, the runtime cap permits 2× the GCP budget, all per-task caps are null, and the default model is Opus. No single layer is firm.

---

## What was NOT in scope of this audit

- `hermes-agent/` (submodule with its own ownership; cited only when `app/`/`lib/` calls into it). Per `decisions.md` D-5: no W0/W1 changes; context-exclusion mechanism pending.
- `docs/` (per audit rule — `.md` excluded; design intent referenced only as "the seed research mandates"). Per D-5: no W0/W1 changes; anti-hallucination mechanism pending.
- Phoenix UI internals
- LiteLLM proxy internals (only its config + emitted budget cap)
- The seed-orchestrator reference implementation under `docs/research/.../seed/` (per audit rule)

---

## Audit confidence

- **High confidence** in all 8 active P0s (P0-2..P0-9): each independently verified against the actual code in this session (file:line confirmation). P0-1 retracted — see REFUTED list.
- **High confidence** in P1.A–E,G,H,I: re-readable evidence via the cited `file:line`.
- **High confidence** in REFUTED list: each refutation backed by direct file:line read in this session.
- **Medium confidence** in some P2/P3: derived from single subagent report without independent re-verification (acceptable for non-blockers).

Where reading the actual code disagreed with a subagent report, the code wins and the disagreement is captured in the REFUTED list.

---

**Companion documents:**
- `audit-plan.md` — prescriptive remediation, 3-wave structure (W0 crisis, W1 hardening, W2 polish)
- `decisions.md` — 5 user decisions governing W0/W1 execution
