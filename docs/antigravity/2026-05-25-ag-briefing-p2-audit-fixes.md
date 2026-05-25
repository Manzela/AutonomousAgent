# Antigravity Briefing — P-2 Audit Fix Wave
**Date:** 2026-05-25
**Priority:** HIGH — blocks merge of PRs #150 and #151
**Collision boundary:** You own PRs #150 and #151. Do NOT touch `lib/a2a/auth.py` (Redis wiring is Claude Code territory).

---

## Background

A parallel 4-subagent audit of your PR #150 (PostgresStore + Memorystore TF) and PR #151 (tasks/get+cancel) identified 8 issues requiring fixes before merge. Full audit report at:
- `audit/2026-05-25-antigravity-p2-review/findings.md`
- `audit/2026-05-25-antigravity-p2-review/audit-plan.md`

**What is correct and does NOT need changing:**
- `CloudSqlPgvectorStore` implementation (asyncpg patterns, SQL, scope isolation, upsert) ✅
- `build-hnsw-index.sh` (correctly uses `CONCURRENTLY`, targets `memory_records`) ✅
- `SpecStore.get_by_id` + `cancel_by_id` ✅
- `_A2ATaskNotFound` → -32001 mapping ✅
- Terraform STANDARD_HA, TLS, persistence DISABLED, buffer unit fix ✅
- pyproject.toml dependency isolation (gcp/a2a/dev extras) ✅

---

## All fixes go on existing branches

- **PR #150 fixes** → commit to `feat/postgres-store-p2`
- **PR #151 fixes** → commit to `fix/a2a-tasks-get-cancel`

---

## PR #150 Fixes (feat/postgres-store-p2)

### Fix A — deploy/docker-compose.yml: proxy bind address (BLOCKER)

**File:** `deploy/docker-compose.yml`
**Problem:** `--address=0.0.0.0` allows any Docker-internal container to reach Postgres. Must be loopback-only.
**Change:** Find the `cloud-sql-proxy` service command args and change:
```yaml
      - --address=0.0.0.0
```
to:
```yaml
      - --address=127.0.0.1
```
That is the only change in this file for Fix A. Do not touch anything else.

---

### Fix B — terraform/phase-0a-gcp/memorystore/main.tf: Add firewall rule (BLOCKER)

**Problem:** No explicit firewall rule exists for the Redis TLS port (6380) from Cloud Run to Memorystore. The instance relies on the root VPC's default allow rules, which may be too broad or too narrow.

**Step 1:** Add to `terraform/phase-0a-gcp/memorystore/variables.tf`:
```hcl
variable "cloudrun_vpc_egress_cidr" {
  description = "CIDR block reserved for Cloud Run Direct VPC Egress (the /28 subnet)."
  type        = string
  # Example: "10.10.1.0/28" — check VPC subnet assignments in networking.tf
}
```

**Step 2:** Add to the END of `terraform/phase-0a-gcp/memorystore/main.tf` (after the `google_redis_instance` resource):
```hcl
# ---------------------------------------------------------------------------
# Firewall: allow Cloud Run Direct VPC Egress → Memorystore Redis TLS (6380).
#
# Cloud Memorystore does NOT accept network tags — it is a managed service
# with no underlying VM to tag. The destination must be specified by IP range.
# The source is the /28 subnet reserved for Cloud Run Direct VPC Egress.
# ---------------------------------------------------------------------------
resource "google_compute_firewall" "allow_cloudrun_to_redis" {
  name    = "autonomousagent-allow-cloudrun-to-redis"
  project = var.project_id
  network = data.google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["6380"]
  }

  # Source: Cloud Run's Direct VPC Egress /28 subnet.
  source_ranges = [var.cloudrun_vpc_egress_cidr]

  # Destination: Memorystore Redis instance private IP (/32 host route).
  # Cannot use target_tags — Memorystore has no underlying tagged VM.
  destination_ranges = ["${google_redis_instance.jti_replay_cache.host}/32"]

  description = "Allow Cloud Run Direct VPC Egress to Memorystore Redis TLS (port 6380)"
}
```

**Step 3:** Find the actual `cloudrun_vpc_egress_cidr` value by running:
```bash
gcloud compute networks subnets list \
  --project=autonomous-agent-2026 \
  --filter="region:us-central1 AND name~cloudrun" \
  --format="value(ipCidrRange)"
```
If no Cloud Run egress subnet exists yet, use the VM subnet CIDR as a placeholder and document it.

**Step 4:** Run `terraform validate` in the memorystore directory. It should pass.

**Step 5:** Do NOT `terraform apply` — Claude Code will review the plan before applying the firewall.

---

### Fix C — scripts/migrate_cloud_sql.py: Remove HNSW index (BLOCKER-ADJACENT)

**Problem:** `DDL_BLOCKS` in `scripts/migrate_cloud_sql.py` includes `index_embedding_hnsw` as a plain `CREATE INDEX` (blocking). On a live table this locks writes for the entire HNSW build (hours). `build-hnsw-index.sh` already handles this correctly with `CONCURRENTLY`.

**Change:** In `scripts/migrate_cloud_sql.py`, find and DELETE the entire tuple:
```python
(
    "index_embedding_hnsw",
    """
    CREATE INDEX IF NOT EXISTS memory_records_embedding_hnsw
        ON memory_records
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """,
),
```
Delete only this tuple. Do not touch any other DDL block.

---

### Fix D — scripts/migrate_cloud_sql.py: Add (project_id, tier) composite index

**Problem:** The `search()` SQL pre-filters on `WHERE tier = $2 AND (project_id = ANY(...) OR ...)` but there is no index on `(project_id, tier)`. At 1M+ records the planner may fall back to sequential scan for scope pre-filtering.

**Change:** Add this tuple to `DDL_BLOCKS` in `scripts/migrate_cloud_sql.py` (after the `index_gc_idx` tuple):
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

### Fix E — app/tests/test_cloud_sql_pgvector_store.py: Guard asyncpg import

**Problem:** Lines 36-37 do unguarded imports of `app.adapters.gcp.memory`. If `asyncpg`/`pgvector` are not installed (running without `--extra gcp`), test collection fails before the `skipif` marker fires, breaking the entire test run.

**Change:** In `app/tests/test_cloud_sql_pgvector_store.py`, find the two import lines:
```python
import app.adapters.gcp.memory as gcp_memory  # noqa: E402
from app.adapters.gcp.memory import CloudSqlPgvectorStore  # noqa: E402
```

Wrap them:
```python
if _HAS_TESTCONTAINERS:
    import app.adapters.gcp.memory as gcp_memory  # noqa: E402
    from app.adapters.gcp.memory import CloudSqlPgvectorStore  # noqa: E402
else:
    gcp_memory = None  # type: ignore[assignment]
    CloudSqlPgvectorStore = None  # type: ignore[assignment]
```

---

### PR #150 Verification

After all fixes, run:
```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"

# 1. Tests still pass (skip DB tests — no testcontainers in CI)
.venv/bin/python -m pytest app/tests/ lib/a2a/tests/ \
  --ignore=app/tests/test_cloud_sql_pgvector_store.py -q --no-header 2>&1 | tail -3

# 2. docker-compose validates
docker compose -f deploy/docker-compose.yml config --quiet

# 3. Terraform validates
cd terraform/phase-0a-gcp/memorystore && terraform validate
```

All must pass. Then commit on `feat/postgres-store-p2`:
```bash
git add deploy/docker-compose.yml \
        terraform/phase-0a-gcp/memorystore/main.tf \
        terraform/phase-0a-gcp/memorystore/variables.tf \
        scripts/migrate_cloud_sql.py \
        app/tests/test_cloud_sql_pgvector_store.py
git commit -m "fix(p2): proxy loopback bind, memorystore firewall, remove blocking HNSW, scope index, test import guard"
```

---

## PR #151 Fixes (fix/a2a-tasks-get-cancel)

### Fix F — HAND-OFF.md: Mark tasks/get+cancel done (BLOCKER)

**File:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`

**Change 1:** Remove this row from the "What is stubbed" table (around line 30):
```markdown
| `tasks/get`, `tasks/cancel` → `-32004` | `lib/a2a/server.py` | Implement via `lib.anchors` queries |
```

**Change 2:** Find the unchecked production checklist item (around line 50):
```markdown
- [ ] Implement `tasks/get` and `tasks/cancel` via lib.anchors API
```
Replace with:
```markdown
- [x] Implement `tasks/get` and `tasks/cancel` — done via in-process `_TASK_REGISTRY` (PR #151); production upgrade to Redis-backed registry pending when multi-replica deployment is required
```

---

### Fix G — HAND-OFF.md: Document _TASK_REGISTRY in-process limitation (BLOCKER)

**File:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md`, "What is broken on purpose" section

Add this entry:
```markdown
- **In-process task registry (`_TASK_REGISTRY`, `lib/a2a/server.py:70`)**: Tasks submitted to one Cloud Run replica are invisible to others. Under round-robin load balancing, `tasks/get` and `tasks/cancel` will return -32001 (task not found) for cross-replica calls. The registry is also unbounded (no TTL or size cap) and is cleared on process restart. Production fix: Redis-backed registry with TTL=600s, keyed on A2A task UUID.
```

---

### Fix H — lib/a2a/server.py: Add SECURITY(spike) comments (IMPORTANT)

**File:** `lib/a2a/server.py`

Find `handle_tasks_get` and add one comment line at the top of the function body (before the `.get()` call):
```python
# SECURITY(spike): no ownership check — any authenticated peer with a task UUID
# can read this task. Production: verify identity.sub == spec.owner.
```

Find `handle_tasks_cancel` and add:
```python
# SECURITY(spike): no ownership check — any authenticated peer with a task UUID
# can cancel this task. Production: verify identity.sub == spec.owner.
```

---

### PR #151 Verification

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
.venv/bin/python -m pytest lib/a2a/tests/ app/tests/ tests/unit/ -q --no-header 2>&1 | tail -3
# Expected: 633+ passed
```

Then commit on `fix/a2a-tasks-get-cancel`:
```bash
git add audit/2026-05-21-a2a-spike-plan/HAND-OFF.md lib/a2a/server.py
git commit -m "fix(a2a): hand-off accuracy — tasks/get+cancel done, registry caveat; security spike comments"
```

---

## Non-negotiable rules

- No `git add -A` or `git add .` — stage specific files only
- No `--no-verify`
- No force-push
- No touching `lib/a2a/auth.py` — Redis wiring is Claude Code territory
- No touching `app/adapters/gcp/memory.py` — implementation is correct, no changes needed
- PR title subjects must start with **lowercase** after `type(scope):`
- Report all results for both PRs when done

---

## Acceptance criteria

| Check | Expected |
|---|---|
| `docker compose config --quiet` | No errors |
| `terraform validate` (memorystore/) | Success |
| Proxy command has `--address=127.0.0.1` | ✅ |
| `migrate_cloud_sql.py` has no `hnsw` in DDL_BLOCKS | ✅ |
| `migrate_cloud_sql.py` has `memory_records_scope_idx` | ✅ |
| Test collection works without `--extra gcp` | ✅ |
| HAND-OFF.md: tasks/get+cancel marked `[x]` | ✅ |
| HAND-OFF.md: `_TASK_REGISTRY` entry in broken-on-purpose | ✅ |
| Full test suite: 633+ passed | ✅ |
