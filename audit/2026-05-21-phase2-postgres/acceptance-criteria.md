# Acceptance Criteria — Phase 2 Postgres Provisioning

**Scope**: Cloud SQL Postgres 16 + pgvector deployment validation
**Pass Criteria**: ALL criteria must pass before promoting to production

## 1. Instance Provisioning

### AC-1.1: Instance is Running

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(state)"
```

**Expected Output**: `RUNNABLE`

**Failure Mode**: `STOPPED`, `MAINTENANCE`, or error → instance not provisioned

---

### AC-1.2: Instance Tier Matches Spec

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.tier)"
```

**Expected Output**: `db-custom-16-64000`

**Failure Mode**: Wrong tier → performance degradation or cost overrun

---

### AC-1.3: Regional HA Enabled

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.availabilityType)"
```

**Expected Output**: `REGIONAL`

**Failure Mode**: `ZONAL` → no cross-zone failover

---

### AC-1.4: Private IP Allocated, No Public IP

**Test**:
```bash
# Check private IP exists
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(ipAddresses[0].ipAddress)"

# Check public IP is absent
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(ipAddresses[?type=='PRIMARY'].ipAddress)" | wc -l
```

**Expected Output**:
- Private IP: `10.x.x.x` (VPC CIDR)
- Public IP count: `0`

**Failure Mode**: Public IP exists → security violation (internet-accessible DB)

---

## 2. Storage & Backups

### AC-2.1: Storage Provisioned at 1TB SSD

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.dataDiskSizeGb,settings.dataDiskType)"
```

**Expected Output**: `1000 PD_SSD`

**Failure Mode**: Wrong size or disk type → IOPS bottleneck or cost overrun

---

### AC-2.2: Daily Backups Enabled

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.backupConfiguration.enabled)"
```

**Expected Output**: `True`

**Failure Mode**: `False` → no backups, data loss risk

---

### AC-2.3: PITR Enabled (7-Day Retention)

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.backupConfiguration.pointInTimeRecoveryEnabled,settings.backupConfiguration.transactionLogRetentionDays)"
```

**Expected Output**: `True 7`

**Failure Mode**: PITR disabled → cannot restore to specific timestamp

---

### AC-2.4: At Least One Backup Exists

**Test**:
```bash
gcloud sql backups list \
  --instance=autonomousagent-postgres-vector \
  --project=i-for-ai \
  --limit=1 \
  --format="value(id)"
```

**Expected Output**: Non-empty backup ID (e.g., `1234567890`)

**Failure Mode**: No backups → first automated backup not yet run (retry after 24h)

---

## 3. Networking & Security

### AC-3.1: IAM Database Authentication Enabled

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.databaseFlags[?name=='cloudsql.iam_authentication'].value)"
```

**Expected Output**: `on`

**Failure Mode**: `off` → password-based auth required (insecure)

---

### AC-3.2: IAM User Exists

**Test**:
```bash
gcloud sql users list \
  --instance=autonomousagent-postgres-vector \
  --project=i-for-ai \
  --filter="name:autonomousagent-vm-runtime" \
  --format="value(name,type)"
```

**Expected Output**: `autonomousagent-vm-runtime@i-for-ai.iam CLOUD_IAM_SERVICE_ACCOUNT`

**Failure Mode**: User missing or wrong type → IAM auth will fail

---

### AC-3.3: Cloud SQL Client Role Granted to VM SA

**Test**:
```bash
gcloud projects get-iam-policy i-for-ai \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/cloudsql.client AND bindings.members:serviceAccount:autonomousagent-vm-runtime@i-for-ai.iam.gserviceaccount.com" \
  --format="value(bindings.role)"
```

**Expected Output**: `roles/cloudsql.client`

**Failure Mode**: Role missing → VM cannot authenticate to Cloud SQL

---

### AC-3.4: Connection via Cloud SQL Proxy Succeeds

**Test** (run on VM):
```bash
# Start Cloud SQL Proxy in background
cloud-sql-proxy i-for-ai:us-central1:autonomousagent-postgres-vector &
sleep 5

# Test connection
psql "host=localhost port=5432 dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT version();"
```

**Expected Output**: PostgreSQL version string (e.g., `PostgreSQL 16.3 on x86_64-pc-linux-gnu`)

**Failure Mode**: Connection refused or authentication error

---

## 4. pgvector Extension

### AC-4.1: pgvector Extension Available

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT * FROM pg_available_extensions WHERE name = 'vector';"
```

**Expected Output**: 1 row with extension details

**Failure Mode**: No rows → pgvector not installed on Cloud SQL Postgres 16

---

### AC-4.2: pgvector Extension Enabled

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

**Expected Output**: `vector`

**Failure Mode**: Error creating extension or no rows returned

---

### AC-4.3: Vector Operations Work

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT '[1,2,3]'::vector <=> '[3,2,1]'::vector AS distance;"
```

**Expected Output**: Numeric distance (e.g., `0.42857142857142855`)

**Failure Mode**: Syntax error or type not found → pgvector not functional

---

## 5. Schema & Migrations

### AC-5.1: Baseline Migration Applied

**Test**:
```bash
alembic current
```

**Expected Output**: `001_baseline (head)`

**Failure Mode**: No migrations applied or wrong revision

---

### AC-5.2: All Tables Exist

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "\dt" | grep -E "episodic_events|semantic_embeddings|procedural_skills|migrations"
```

**Expected Output**: 4 tables listed

**Failure Mode**: Missing table(s) → baseline migration incomplete

---

### AC-5.3: Episodic Events Partition Exists

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "\d+ episodic_events_2026_05"
```

**Expected Output**: Partition definition for May 2026

**Failure Mode**: Partition missing → inserts for current month will fail

---

### AC-5.4: Can Insert into Each Table Tier

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" <<EOF
-- Episodic insert
INSERT INTO episodic_events (session_id, agent_id, event_type, payload)
VALUES (gen_random_uuid(), 'test-agent', 'test', '{"test": true}');

-- Semantic insert
INSERT INTO semantic_embeddings (source_id, source_type, embedding, text, metadata)
VALUES (gen_random_uuid(), 'test', '[0.1, 0.2, 0.3]'::vector(768), 'test text', '{"test": true}');

-- Procedural insert
INSERT INTO procedural_skills (name, description, code)
VALUES ('test-skill', 'Test skill', 'print("hello")');

-- Verify
SELECT COUNT(*) FROM episodic_events WHERE agent_id = 'test-agent';
SELECT COUNT(*) FROM semantic_embeddings WHERE source_type = 'test';
SELECT COUNT(*) FROM procedural_skills WHERE name = 'test-skill';
EOF
```

**Expected Output**: 3 counts of `1`

**Failure Mode**: Insert errors or zero counts

---

### AC-5.5: Can Query Each Table Tier

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" <<EOF
-- Episodic query
SELECT id, event_type FROM episodic_events LIMIT 1;

-- Semantic query (vector search)
SELECT id, text, embedding <=> '[0.1, 0.2, 0.3]'::vector(768) AS distance
FROM semantic_embeddings
ORDER BY embedding <=> '[0.1, 0.2, 0.3]'::vector(768)
LIMIT 1;

-- Procedural query
SELECT id, name FROM procedural_skills LIMIT 1;
EOF
```

**Expected Output**: 3 result rows (1 from each table)

**Failure Mode**: No rows or query errors

---

## 6. HNSW Index (Post-Migration)

### AC-6.1: HNSW Index Exists

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT indexname FROM pg_indexes WHERE tablename = 'semantic_embeddings' AND indexname = 'idx_embedding_hnsw';"
```

**Expected Output**: `idx_embedding_hnsw`

**Failure Mode**: No index → vector queries will be slow (seq scan)

---

### AC-6.2: HNSW Index Parameters Correct

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_embedding_hnsw';"
```

**Expected Output**: `CREATE INDEX ... USING hnsw ... WITH (m = 16, ef_construction = 64)`

**Failure Mode**: Wrong parameters → suboptimal index quality

---

## 7. Secret Manager Integration

### AC-7.1: DB Connection Secret Exists

**Test**:
```bash
gcloud secrets describe autonomousagent-db-connection \
  --project=i-for-ai \
  --format="value(name)"
```

**Expected Output**: `projects/123456789/secrets/autonomousagent-db-connection`

**Failure Mode**: Secret not found → application cannot fetch connection metadata

---

### AC-7.2: VM SA Can Read DB Secret

**Test** (run on VM):
```bash
gcloud secrets versions access latest \
  --secret=autonomousagent-db-connection \
  --project=i-for-ai
```

**Expected Output**: JSON blob with `host`, `database`, `user`, `connection_name` keys

**Failure Mode**: Permission denied → VM cannot access secret

---

### AC-7.3: Secret Contains Valid Connection Metadata

**Test**:
```bash
SECRET_JSON=$(gcloud secrets versions access latest --secret=autonomousagent-db-connection --project=i-for-ai)
echo $SECRET_JSON | jq -e '.connection_name,.database,.user' > /dev/null
```

**Expected Output**: No error (exit code 0)

**Failure Mode**: Missing keys or invalid JSON

---

## 8. Performance & Observability

### AC-8.1: Query Latency <50ms (p95)

**Test** (after loading 1M test embeddings):
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" <<EOF
EXPLAIN ANALYZE
SELECT id, text, embedding <=> '[0.1, 0.2, ...]'::vector(768) AS distance
FROM semantic_embeddings
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector(768)
LIMIT 10;
EOF
```

**Expected Output**: `Execution Time: <50ms` (HNSW index scan, not seq scan)

**Failure Mode**: >50ms → insufficient RAM or HNSW index not used

---

### AC-8.2: Connection Pool Handles 50 Concurrent Connections

**Test**:
```bash
# Simulate 50 concurrent psql connections
seq 1 50 | parallel -j 50 \
  'psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" -c "SELECT 1;" > /dev/null'
```

**Expected Output**: All connections succeed (no connection refused errors)

**Failure Mode**: Connection limit exceeded → `max_connections` too low

---

### AC-8.3: Backup Restore Works (PITR Test)

**Test**:
```bash
# Insert test row
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "INSERT INTO procedural_skills (name, description, code) VALUES ('pitr-test', 'PITR test skill', 'print(\"pitr\")');"

# Record timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
sleep 60

# Delete test row
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "DELETE FROM procedural_skills WHERE name = 'pitr-test';"

# Restore to timestamp (creates new instance)
gcloud sql backups create \
  --instance=autonomousagent-postgres-vector \
  --project=i-for-ai

gcloud sql instances clone autonomousagent-postgres-vector \
  autonomousagent-postgres-vector-pitr-test \
  --point-in-time=$TIMESTAMP \
  --project=i-for-ai

# Verify test row exists on restored instance
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector-pitr-test dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" \
  -c "SELECT COUNT(*) FROM procedural_skills WHERE name = 'pitr-test';"

# Cleanup
gcloud sql instances delete autonomousagent-postgres-vector-pitr-test --project=i-for-ai --quiet
```

**Expected Output**: Count = `1` (test row restored)

**Failure Mode**: Count = `0` → PITR restore failed

---

## 9. Maintenance Window

### AC-9.1: Maintenance Window Configured

**Test**:
```bash
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format="value(settings.maintenanceWindow.day,settings.maintenanceWindow.hour)"
```

**Expected Output**: `7 2` (Sunday at 02:00 UTC)

**Failure Mode**: Wrong day/hour → maintenance during peak hours

---

## 10. Cleanup (Post-Test)

### AC-10.1: Test Data Removed

**Test**:
```bash
psql "host=/cloudsql/i-for-ai:us-central1:autonomousagent-postgres-vector dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" <<EOF
DELETE FROM episodic_events WHERE agent_id = 'test-agent';
DELETE FROM semantic_embeddings WHERE source_type = 'test';
DELETE FROM procedural_skills WHERE name = 'test-skill';
VACUUM ANALYZE;
EOF
```

**Expected Output**: No errors

**Failure Mode**: Test data persists in production DB

---

## Summary

**Total Criteria**: 28
**Pass Threshold**: 28/28 (100%)

**Critical Criteria** (Blockers for production):
- AC-1.1 (Instance running)
- AC-1.4 (Private IP only)
- AC-2.3 (PITR enabled)
- AC-3.1 (IAM auth enabled)
- AC-4.2 (pgvector enabled)
- AC-5.2 (All tables exist)
- AC-6.1 (HNSW index exists)

**Non-Critical Criteria** (Can defer to post-launch):
- AC-8.1 (Query latency — optimize later)
- AC-8.2 (Connection pooling — load test in staging first)
- AC-8.3 (PITR restore — DR drill, not launch blocker)

**Sign-Off**: Obtain approval from user after ALL criteria pass.
