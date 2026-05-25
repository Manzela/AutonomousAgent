# Antigravity P-2 Execution Audit — Findings
**Date:** 2026-05-25
**Scope:** PRs #150 (feat/postgres-store-p2) and #151 (fix/a2a-tasks-get-cancel)
**Method:** 4-parallel subagent deep review + direct codebase verification
**Standard:** Tier 1 SDLC — no false positives, no false negatives

---

## Verification corrections (Subagent D read spec, not code)

Subagent D evaluated `migrate_cloud_sql.py`, `build-hnsw-index.sh`, and tests against the design *spec* rather than the committed branch code. Direct verification corrects:

| Claim | Subagent D said | Actual (verified) |
|---|---|---|
| `build-hnsw-index.sh` exists | ❌ Missing | ✅ EXISTS on branch — correctly uses `CREATE INDEX CONCURRENTLY`, targets `memory_records`, m=16, ef_construction=64 |
| HNSW in migrate script | Blocking CREATE INDEX | ✅ CONFIRMED — blocking `CREATE INDEX IF NOT EXISTS` without `CONCURRENTLY` at `scripts/migrate_cloud_sql.py:69-73` |
| `(project_id, tier)` composite index | Missing | ✅ CONFIRMED MISSING — not in `DDL_BLOCKS` |

---

## PR #150 — feat/postgres-store-p2

### F1 ❌ BLOCKER — docker-compose: cloud-sql-proxy binds `--address=0.0.0.0`

**File:** `deploy/docker-compose.yml:486`
**Evidence:**
```yaml
      - --address=0.0.0.0
```
Any service on the `internal` Docker network (litellm-proxy, escalation-watcher, budget-watchdog, shell-sandbox) can connect directly to Postgres on port 5432 without going through the application layer. The correct pattern is `--address=127.0.0.1` which restricts connections to within the proxy container itself — only the `hermes` service connecting via localhost sees the DB port.
**Fix:** Change `--address=0.0.0.0` to `--address=127.0.0.1` in `deploy/docker-compose.yml:486`.

---

### F2 ❌ BLOCKER — memorystore/main.tf: no firewall rule for port 6380

**File:** `terraform/phase-0a-gcp/memorystore/main.tf`
**Evidence:** No `google_compute_firewall` resource in the entire module.
The Redis TLS port (6380) is not explicitly opened from the Cloud Run egress CIDR to the Memorystore private IP. The instance relies on the root VPC's default allow-internal rule which may be too broad (allows all internal traffic, including from untrusted services) or too narrow (may not include the Memorystore allocated range).
**Fix:** Add a `google_compute_firewall` resource:
```hcl
resource "google_compute_firewall" "allow_cloudrun_to_redis" {
  name    = "autonomousagent-allow-cloudrun-to-redis"
  project = var.project_id
  network = data.google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["6380"]
  }

  source_ranges      = [var.cloudrun_vpc_egress_cidr]
  destination_ranges = ["${google_redis_instance.jti_replay_cache.host}/32"]

  description = "Allow Cloud Run Direct VPC Egress to reach Memorystore Redis TLS port"
}
```
Add `cloudrun_vpc_egress_cidr` to `variables.tf`.

---

### F3 ⚠️ IMPORTANT — migrate_cloud_sql.py: HNSW CREATE INDEX is blocking (not CONCURRENTLY)

**File:** `scripts/migrate_cloud_sql.py:67-74`
**Evidence:**
```python
"index_embedding_hnsw",
"""
CREATE INDEX IF NOT EXISTS memory_records_embedding_hnsw
    ON memory_records
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
""",
```
`build-hnsw-index.sh` correctly uses `CREATE INDEX CONCURRENTLY`. However, the migrate script *also* creates the HNSW index using blocking `CREATE INDEX` (no `CONCURRENTLY`). On a live table with existing data this acquires an `AccessShareLock` that blocks all writes for the duration of the HNSW build (potentially hours on 10M+ vectors). The correct pattern: migrate script creates all indexes EXCEPT the HNSW index; `build-hnsw-index.sh` creates HNSW `CONCURRENTLY` as a post-migration operator step.
**Fix:** Remove the `index_embedding_hnsw` tuple from `DDL_BLOCKS` in `migrate_cloud_sql.py`. The HNSW index lives exclusively in `build-hnsw-index.sh`.

---

### F4 ⚠️ IMPORTANT — migrate_cloud_sql.py: Missing (project_id, tier) composite index for scope isolation

**File:** `scripts/migrate_cloud_sql.py` — `DDL_BLOCKS`
**Evidence:** grep for `scope|project.*tier|tier.*project|idx_scope|idx_proj` returns no matches in DDL_BLOCKS.
The `search()` SQL uses `WHERE tier = $2 AND (project_id = ANY($3) OR ...)`. Without a composite index on `(project_id, tier)` or `(tier, project_id)`, the planner may use a sequential scan for the scope pre-filter at 1M+ records. The HNSW index handles the vector ordering but not the WHERE clause pre-filter.
**Fix:** Add to `DDL_BLOCKS`:
```python
(
    "index_scope",
    """
    CREATE INDEX IF NOT EXISTS memory_records_scope_idx
        ON memory_records (project_id, tier)
    """,
),
```

---

### F5 ⚠️ IMPORTANT — test file import fragility: asyncpg import unguarded

**File:** `app/tests/test_cloud_sql_pgvector_store.py:36-37`
**Evidence:**
```python
import app.adapters.gcp.memory as gcp_memory  # noqa: E402
from app.adapters.gcp.memory import CloudSqlPgvectorStore  # noqa: E402
```
These lines run at collection time, before the `pytestmark = [pytest.mark.skipif(not _HAS_TESTCONTAINERS, ...)]` can fire. If `asyncpg`/`pgvector` are not installed (running `uv sync` without `--extra gcp`), the entire test file fails to collect with `ModuleNotFoundError`, blocking the full test suite.
**Fix:** Gate the `app.adapters.gcp.memory` imports behind the same `_HAS_TESTCONTAINERS` flag:
```python
if _HAS_TESTCONTAINERS:
    import app.adapters.gcp.memory as gcp_memory
    from app.adapters.gcp.memory import CloudSqlPgvectorStore
```

---

### F6 (Nit) — cloud-sql-proxy image not digest-pinned

**File:** `deploy/docker-compose.yml:481` — `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.15.0`
Version-tagged but not digest-pinned. Low risk; inconsistent with the project's existing pinning convention.

### F7 (Nit) — fakeredis missing upper cap

**File:** `pyproject.toml` — `fakeredis[asyncio]>=2.20` has no `<3` upper cap.

### F8 (Nit) — memorystore redis_version no validation block

**File:** `terraform/phase-0a-gcp/memorystore/variables.tf` — `redis_version` has no `validation {}` block; GCP rejects invalid values at apply time but plan-time check is cleaner.

---

## PR #151 — fix/a2a-tasks-get-cancel

### F9 ❌ BLOCKER — HAND-OFF.md: tasks/get + tasks/cancel still listed as stubs

**File:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`
**Line 30:** `| tasks/get, tasks/cancel → -32004 | ... | Implement via lib.anchors queries |` — still in "What is stubbed" table
**Line 50:** `- [ ] Implement tasks/get and tasks/cancel via lib.anchors API` — still unchecked
Both items are now implemented (PR #151). Any operator reading this hand-off will conclude these are unimplemented and file unnecessary follow-up tickets.
**Fix:** Remove line 30 from the stubbed table; mark line 50 as `[x]` with note "implemented via in-process `_TASK_REGISTRY`; production Redis-backed registry pending".

---

### F10 ❌ BLOCKER — HAND-OFF.md: _TASK_REGISTRY in-process limitation undocumented

**File:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md` — "What is broken on purpose" section
`_TASK_REGISTRY: dict[str, Any]` is module-level, in-process, unbounded, and cleared on restart. A Cloud Run deployment with 2+ replicas will lose tasks submitted to one replica when `tasks/get` hits a different replica. This is spike-grade behavior but is a critical operational gap that **must be disclosed** before the hand-off reaches ops.
**Fix:** Add to "What is broken on purpose":
```markdown
- **In-process task registry (`_TASK_REGISTRY`)**: Tasks submitted to one Cloud Run replica are invisible to others (`lib/a2a/server.py:70`). Round-robin load balancing will cause `tasks/get` and `tasks/cancel` to return -32001 (task not found) for cross-replica calls. Production fix: shared Redis registry keyed on task ID with TTL=600s. Also: the registry is unbounded and cleared on restart — unlimited task IDs submitted without GC will grow the dict until OOM.
```

---

### F11 ⚠️ IMPORTANT — No ownership check in tasks/get + tasks/cancel; undocumented

**File:** `lib/a2a/server.py:224-248`
Any authenticated peer who knows a task UUID can read or cancel any other peer's task. The dispatcher passes `identity` only to `message/send` (`server.py:491`), so neither `handle_tasks_get` nor `handle_tasks_cancel` can verify ownership.
**Fix:** Add inline comment:
```python
# SECURITY(spike): no ownership check — any authenticated peer with a task UUID
# can read or cancel this task. Production requires threading identity through
# the dispatcher and verifying task.owner == identity.sub.
```

---

### F12 ⚠️ IMPORTANT — _TASK_REGISTRY unbounded; no HAND-OFF entry

**File:** `lib/a2a/server.py:70`
`_TASK_REGISTRY: dict[str, Any] = {}` has no eviction, TTL, or size cap. See F10 for the production note.
**Fix:** Add a `# TODO(registry-ttl): add eviction or size cap before production` comment at line 70, and include in HAND-OFF (F10 above covers this).

---

### F13 (Nit) — Dead code in handle_tasks_cancel

**File:** `lib/a2a/server.py:247-249` — `except TypeError` branch for `model_copy` fallback is unreachable. TaskSpec is a dataclass; `dataclasses.replace` always works.

### F14 (Nit) — Cancel response id not asserted in test

**File:** `lib/a2a/tests/test_tasks_get_cancel.py` — `test_tasks_cancel_marks_superseded` asserts `status == "CANCELED"` but not `id == task_id`.

### F15 (Nit) — CloudSqlPgvectorStore: no warmup query in pool init

**File:** `app/adapters/gcp/memory.py` — cold-start first query may timeout before HNSW pages fault into RAM. No warmup in `_register_vector_codec`. Add `await conn.execute("SELECT 1 FROM memory_records WHERE FALSE")` to `_register_vector_codec`.

---

## Findings confirmed correct (pass)

| Finding | Verdict |
|---|---|
| asyncpg double-checked-lock (no B1 race) | ✅ Pool lock created eagerly at module level |
| SET LOCAL hnsw.ef_search inside transaction | ✅ Wrapped in `conn.transaction()` |
| register_vector via pool init= | ✅ Correct asyncpg pattern |
| NULL scope in search() (CONSENSUS) | ✅ Split-param approach; no NULL=ANY trap |
| Upsert excludes record_id from UPDATE SET | ✅ Correct |
| delete() RETURNING + bool | ✅ Correct |
| gc_expired() count from status string | ✅ Correct |
| tier + project_id pre-filter before ORDER BY | ✅ Correct |
| EmptyScope enforced | ✅ Matches ABC contract |
| numpy float32 dtype enforcement | ✅ ascontiguousarray on put |
| SpecStore.get_by_id handles invalid UUID | ✅ try/except ValueError |
| SpecStore.cancel_by_id uses model_copy + atomic save | ✅ Correct |
| _A2ATaskNotFound mapped to -32001 | ✅ Correct |
| build-hnsw-index.sh uses CONCURRENTLY | ✅ Correct |
| build-hnsw-index.sh targets memory_records | ✅ Correct |
| pyproject.toml gcp/dev/a2a extra isolation | ✅ Correct |
| Terraform: STANDARD_HA, TLS, persistence DISABLED | ✅ Correct |
| postgres/main.tf buffer unit fix (8KB blocks) | ✅ Correct math verified |
| tasks/get+cancel mapped from _DISPATCH | ✅ Correct |
