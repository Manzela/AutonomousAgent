# Antigravity P-2 Execution — Audit Plan (P0/P1/P2)
**Generated:** 2026-05-25
**Blocks merge of:** PR #150 (feat/postgres-store-p2), PR #151 (fix/a2a-tasks-get-cancel)

---

## P0 — Must fix before merge (4 items)

### P0-A: docker-compose cloud-sql-proxy bind address
**What:** `--address=0.0.0.0` → `--address=127.0.0.1`
**Why:** Internal Docker containers (litellm-proxy, escalation-watcher, etc.) can reach Postgres directly, bypassing all application auth
**Where:** `deploy/docker-compose.yml:486`
**Effort:** 1 line, 2 min
**PR:** #150

### P0-B: Memorystore firewall rule for port 6380
**What:** Add `google_compute_firewall` resource to `terraform/phase-0a-gcp/memorystore/main.tf`
**Why:** Without an explicit rule, Redis TLS port access relies on the root VPC's default policy (unknown coverage)
**Where:** `terraform/phase-0a-gcp/memorystore/main.tf` (new resource) + `variables.tf` (new var)
**Effort:** 20 lines TF, 15 min
**PR:** #150

### P0-C: HAND-OFF.md tasks/get+cancel still listed as stubs
**What:** Remove from "What is stubbed" table (line 30); mark production checklist item (line 50) as `[x]`
**Why:** Ops reading the hand-off will conclude these are unimplemented and file follow-up tickets
**Where:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md:30,50`
**Effort:** 2 lines, 5 min
**PR:** #151

### P0-D: HAND-OFF.md _TASK_REGISTRY in-process limitation undocumented
**What:** Add entry to "What is broken on purpose" documenting that `_TASK_REGISTRY` is in-process, cleared on restart, and will fail under multi-replica load balancing
**Why:** Ops deploying multi-replica Cloud Run will see unexplained -32001 errors on `tasks/get`
**Where:** `audit/2026-05-21-a2a-spike-plan/HAND-OFF.md` "What is broken on purpose" section
**Effort:** 4 lines, 5 min
**PR:** #151

---

## P1 — Should fix in this PR (4 items)

### P1-A: Remove HNSW from migrate_cloud_sql.py (keep in build-hnsw-index.sh only)
**What:** Delete `index_embedding_hnsw` tuple from `DDL_BLOCKS` in `scripts/migrate_cloud_sql.py`
**Why:** `CREATE INDEX` (blocking) on a live table with data locks writes for the HNSW build duration (potentially hours). `build-hnsw-index.sh` already handles this correctly with `CONCURRENTLY`.
**Where:** `scripts/migrate_cloud_sql.py:67-74` — delete the tuple
**Effort:** Delete 7 lines, 5 min
**PR:** #150

### P1-B: Add (project_id, tier) composite index to migrate_cloud_sql.py
**What:** Add `CREATE INDEX IF NOT EXISTS memory_records_scope_idx ON memory_records (project_id, tier)` to `DDL_BLOCKS`
**Why:** `search()` WHERE clause pre-filters on both columns; without this index the planner may seqscan at 1M+ records
**Where:** `scripts/migrate_cloud_sql.py` `DDL_BLOCKS` list
**Effort:** 7 lines, 5 min
**PR:** #150

### P1-C: Guard asyncpg import in test file
**What:** Gate `import app.adapters.gcp.memory` lines behind `if _HAS_TESTCONTAINERS:` block
**Why:** If `asyncpg`/`pgvector` not installed, test collection fails before skipif fires — breaks CI for contributors without `--extra gcp`
**Where:** `app/tests/test_cloud_sql_pgvector_store.py:36-37`
**Effort:** 2 lines changed, 5 min
**PR:** #150

### P1-D: Add # SECURITY(spike) comment to tasks/get + tasks/cancel handlers
**What:** One-line comment before each handler documenting that no ownership check is performed
**Why:** Future reviewers will not discover this silently; the spike-scope tradeoff must be visible
**Where:** `lib/a2a/server.py:224` and `lib/a2a/server.py:237`
**Effort:** 2 lines, 2 min
**PR:** #151

---

## P2 — Nice to have (fix in follow-up)

| ID | Item | File:Line | Effort |
|---|---|---|---|
| P2-A | Digest-pin cloud-sql-proxy image | `deploy/docker-compose.yml:481` | 5 min |
| P2-B | Add `<3` upper cap to fakeredis | `pyproject.toml` | 1 min |
| P2-C | Add validation block to redis_version variable | `terraform/phase-0a-gcp/memorystore/variables.tf` | 3 min |
| P2-D | Remove dead TypeError branch in handle_tasks_cancel | `lib/a2a/server.py:247-249` | 1 min |
| P2-E | Assert `id` in cancel test | `lib/a2a/tests/test_tasks_get_cancel.py` | 2 min |
| P2-F | Add warmup query in CloudSqlPgvectorStore pool init | `app/adapters/gcp/memory.py` `_register_vector_codec` | 3 min |
| P2-G | Add DB CHECK constraint bypass test (raw SQL INSERT) | `app/tests/test_cloud_sql_pgvector_store.py` | 15 min |
| P2-H | Add expires_at boundary test in search() | `app/tests/test_cloud_sql_pgvector_store.py` | 10 min |
| P2-I | `_TASK_REGISTRY` unbounded — add size cap comment | `lib/a2a/server.py:70` | 1 min |

---

## Confirmed passing (no action needed)

All asyncpg correctness checks, SQL parameterization, scope isolation logic, numpy serialization, SpecStore atomic operations, _A2ATaskNotFound error code, build-hnsw-index.sh CONCURRENTLY usage, Terraform STANDARD_HA + TLS + persistence DISABLED, postgres/main.tf buffer math — all correct. See `findings.md` for the full pass table.

---

## To enrich in pass 2

- Run `terraform validate` on the corrected memorystore module after adding the firewall rule
- Run `.venv/bin/python -m pytest lib/a2a/tests/ app/tests/ -q --no-header` after each fix to confirm no regressions
- Verify `build-hnsw-index.sh` is documented in HAND-OFF.md as the post-migration operator step for HNSW
