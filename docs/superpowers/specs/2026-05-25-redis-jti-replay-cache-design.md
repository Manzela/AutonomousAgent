# Redis-Backed Distributed JTI Replay Cache — Design Spec

**Date:** 2026-05-25
**Author:** Senior distributed systems architect (Claude Opus 4.7)
**Status:** Design — ready for implementation
**Scope:** Replace per-process `_JTI_CACHE` in `lib/a2a/auth.py` with a
Cloud Memorystore (Redis) backed distributed cache, preserving HIPAA
audit guarantees and the existing 300s token / 600s replay window.
**Related:** `lib/a2a/auth.py`, `audit/2026-05-21-a2a-spike-plan/auth-design.md`,
`docs/architecture/gcp-migration-i-for-ai-to-autonomous-agent-2026.md`

---

## Problem statement

`lib/a2a/auth.py` currently maintains an in-process replay cache:

```python
_JTI_CACHE: cachetools.TTLCache[tuple[str, str], bool] = cachetools.TTLCache(
    maxsize=100_000,
    ttl=600,  # 2× the 300s token lifetime
)
```

Cloud Run scales the A2A server horizontally (2–5 replicas in steady
state, up to ~20 under burst). Each replica holds its own `_JTI_CACHE`.
A captured JWT with a unique `jti` claim can therefore be replayed by
routing the second request to a different replica — the replay-detection
guarantee is broken in production.

Goal: every `(issuer, jti)` pair seen by any replica must be visible to
every other replica within the 600s replay window, without sacrificing
the existing latency budget (target p95 verify ≤ 25ms) or HIPAA audit
trail.

---

## Section 1 — Core design decision: fail-open vs fail-closed

### The three options

| Option | Behaviour during Redis outage | Security risk | Availability risk |
|--------|-------------------------------|---------------|-------------------|
| A — Fail-closed | Reject every token | None (no replay window) | A2A goes completely offline |
| B — Fail-open with L1 fallback (60s TTL) | Use in-process `_JTI_CACHE` with shortened TTL | Bounded: cross-replica replays succeed for ≤ 60s | A2A stays up |
| C — Fail-open, no fallback | Skip replay check entirely | Unbounded: any captured jti is replayable for the full 300s token life | A2A stays up |

### Context for the decision

- **What an A2A JWT actually authorises.** This token authenticates a
  *service account* (`iss = our_sa`) calling another service account
  (`aud = our_sa`). It is **not** an end-user authn token and it does
  **not** by itself grant PHI access. PHI access is gated downstream by
  scoped human-session credentials in the `acting_for` claim plus per-
  method authorization in the A2A server. A replayed A2A JWT lets the
  attacker repeat *exactly* the same call the original token authorised
  — it does not escalate scope.
- **Token lifetime is short.** `exp = iat + 300`. A captured JWT is
  worthless after 5 minutes regardless of replay-cache state.
- **Replica count is small.** 2–5 typical, ~20 burst. For a replay to
  succeed under Option B, the attacker must (1) capture a valid JWT
  in-flight (TLS is already enforced end-to-end), (2) time the replay
  inside the 60s L1 window, AND (3) get routed to a different replica
  than the original request. Cloud Run's load balancer distributes
  roughly uniformly, so the per-attempt success probability is
  `(N-1)/N` where N is replica count — non-trivial, but the attacker
  still needs to have broken TLS first.
- **Cloud Memorystore SLA = 99.9%** for STANDARD_HA = ~43 min/month of
  expected unavailability. During that window Option A would take the
  entire agent control plane offline. The autonomous agent depends on
  A2A for cross-component coordination (orchestrator ↔ MoE router ↔
  free-agent registry, per `docs/research/autonomous-agent-seed-orchestrator/`);
  a full A2A outage cascades.
- **HIPAA does not require fail-closed for non-PHI auth.** HIPAA
  §164.312(a)(2)(i) requires unique user identification and access
  control. The A2A JWT provides agent identity; PHI access decisions
  are made downstream against `acting_for.consent_scope`. A 60-second
  bounded-replay window for the agent-identity layer, with full audit
  logging of the degraded mode, is defensible under §164.308(a)(7)
  contingency-plan reasoning.

### Decision: **Option B — Fail-open with L1 fallback (60s TTL)**

#### Justification

1. **Bounded blast radius.** L1 TTL of 60s caps the cross-replica
   replay window at 60s during a Redis outage, vs the 300s the token
   itself is valid. A captured JWT remains the limiting factor.
2. **No control-plane cascade.** A 43-min/month Redis outage does not
   translate into a 43-min/month autonomous-agent outage.
3. **Existing infrastructure reused.** The `_JTI_CACHE` already exists
   and is already correct for single-replica operation; we shrink its
   TTL when used as fallback rather than introducing new code paths.
4. **Auditability preserved.** Every fallback decision is logged via
   the `a2a.audit` logger with `decision="accepted_redis_unavailable"`
   so security teams can detect and respond to extended outages.

#### Explicit tradeoff

We accept the following residual risk: **during a Cloud Memorystore
outage of duration D, an attacker who has already broken TLS and
captured a valid JWT in the last 300s can replay it up to
`min(D, 60s)` times on different replicas before L1 catches it.**
This is acceptable because:
- Breaking TLS is the hard step; cache state does not gate the attack.
- 60s is shorter than the token's natural lifetime.
- The audit log records the degraded mode, enabling rapid detection.
- A second mitigation lives downstream: PHI-scoped human-session
  credentials in `acting_for` carry their own replay protection at the
  PHI layer.

#### Operator override

`A2A_JTI_FAIL_MODE=closed` env var, when set, switches Option A
(fail-closed) on a per-service basis. This is the escape hatch for
deployments that prefer availability sacrifice over the 60s bounded-
replay risk. **Default is `open`.**

---

## Section 2 — Redis operation design

### Key format

```
jti:{issuer_sa_email}:{jti_value}
```

Example: `jti:hermes-agent@autonomous-agent-2026.iam.gserviceaccount.com:9b3f...`

**Why this format:**

1. **Namespaced.** The `jti:` prefix prevents collisions with any
   future Redis use in the same instance (mint cache, JWKS cache,
   rate-limit counters). Operators can run `SCAN MATCH jti:*` to
   inventory replay entries during incident response.
2. **Issuer-scoped.** The current code uses the tuple `(issuer, jti)`
   as the cache key. Including `issuer_sa_email` in the Redis key
   preserves that scoping. Two distinct service accounts that
   coincidentally mint the same UUID-v4 jti (cryptographically
   impossible in practice, but the cache contract should not depend
   on that) are tracked separately.
3. **Human-debuggable.** An operator inspecting a key via `redis-cli`
   immediately sees which SA issued the token.
4. **No further encoding.** SA emails contain only `[a-z0-9-]+@[a-z0-9.-]+`,
   all of which are valid Redis key characters. jti is a UUID-v4 string.
   No escaping needed.

### Atomic primitive: `SET key 1 NX EX 600`

```
SET jti:<sa>:<jti> 1 NX EX 600
```

- `NX` — only set if the key does not exist.
- `EX 600` — set TTL to 600 seconds atomically in the same command.
- Value `1` — payload is irrelevant; existence of the key is the signal.
  We use `1` (single byte) to minimise memory.

**Return semantics:**

- `OK` (Python: `True`) — key was created; this is the *first* time
  we have seen this jti; accept the token.
- `nil` (Python: `None`) — key already existed; this is a replay;
  reject the token.

### Why `SET NX EX` is correct and `GET` + `SET` is wrong

The naive translation of the current code is:

```python
# WRONG — TOCTOU race
if await redis.get(key):
    raise ValueError("jti replay")
await redis.set(key, 1, ex=600)
```

Two concurrent verifications of the same jti — on the *same* replica
or different replicas — can both pass the `get` check before either
issues the `set`. Both then accept the token. Replay succeeds.

`SET NX EX` is a single Redis command, executed atomically by the
Redis server's single-threaded command loop. Exactly one caller sees
`OK`; all others see `nil`. No external locking required, and the
existing `_JTI_LOCK` (per-process `asyncio.Lock`) becomes unnecessary
for the Redis path.

### Why `SETNX` + separate `EXPIRE` is wrong

The pre-Redis-2.6.12 idiom was:

```
SETNX key 1
EXPIRE key 600
```

If the client crashes between the two commands the key becomes
immortal — every entry then leaks until manual `DEL`. With 100K+ jti
per hour this fills a 1GB instance in days. The combined `SET ... NX EX`
form (Redis 2.6.12+, always available on Memorystore REDIS_7_0)
eliminates this risk.

---

## Section 3 — Python async client

### Library choice: `redis-py>=5.0` with `redis.asyncio`

`aioredis` was merged into `redis-py` in 4.2 (Sep 2021) and is no
longer maintained as a standalone package. `redis.asyncio` is the
modern unified async client and is what `redis-py` itself documents.

### Pinned dependency

Add to `pyproject.toml` under the `lib/a2a/` extras (or top-level
runtime deps if A2A is always installed):

```toml
[project.optional-dependencies]
a2a = [
    # ... existing a2a deps ...
    "redis[asyncio]>=5.0,<6",
]
```

Pin major only; minor/patch updates are picked up automatically.
`redis[asyncio]` pulls in `hiredis` which gives a ~10× parse speedup
and is the recommended production install.

### Connection pool — module-level lazy singleton

Mirrors the existing `_JTI_LOCK` / `_get_jti_lock()` pattern in
`auth.py`. Initialising the pool at module import time would fail in
test collection (no event loop) and would crash if `REDIS_URL` is
unset; a lazy getter avoids both.

```python
import os
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

_REDIS_POOL: ConnectionPool | None = None
_REDIS_POOL_LOCK: asyncio.Lock | None = None


def _get_redis_pool_lock() -> asyncio.Lock:
    global _REDIS_POOL_LOCK
    if _REDIS_POOL_LOCK is None:
        _REDIS_POOL_LOCK = asyncio.Lock()
    return _REDIS_POOL_LOCK


async def _get_redis_pool() -> ConnectionPool | None:
    """Return the shared Redis connection pool, or None if not configured.

    Returns None (not raise) when REDIS_URL is unset — caller must
    handle the None case by falling back to L1.
    """
    global _REDIS_POOL
    if _REDIS_POOL is not None:
        return _REDIS_POOL
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    lock = _get_redis_pool_lock()
    async with lock:
        if _REDIS_POOL is not None:  # double-checked locking
            return _REDIS_POOL
        timeout = float(os.environ.get("REDIS_CONNECT_TIMEOUT_SECS", "2.0"))
        _REDIS_POOL = ConnectionPool.from_url(
            url,
            max_connections=20,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            health_check_interval=30,
        )
        logger.info("Initialised Redis pool for jti replay cache: %s", _safe_url(url))
        return _REDIS_POOL


def _safe_url(url: str) -> str:
    """Redact password from REDIS_URL for safe logging."""
    if "@" not in url:
        return url
    scheme_rest = url.split("://", 1)
    if len(scheme_rest) != 2:
        return url
    scheme, rest = scheme_rest
    if "@" not in rest:
        return url
    _creds, host = rest.rsplit("@", 1)
    return f"{scheme}://***@{host}"
```

**Pool sizing rationale.** `max_connections=20` matches the typical
Cloud Run concurrency (`--concurrency=80` default) divided by 4
(`verify_token` is one of several I/O points per request). Each
replica thus holds ≤ 20 sockets to Memorystore; with 20 replicas
worst-case that is 400 sockets, well under the 65K connection limit
of a STANDARD_HA tier.

### `REDIS_URL` format

| Scheme | Use case | Port |
|--------|----------|------|
| `redis://10.x.x.x:6379/0` | Memorystore non-TLS (dev) | 6379 |
| `rediss://10.x.x.x:6380/0` | Memorystore TLS (production) | 6380 |
| `redis://:password@10.x.x.x:6379/0` | AUTH-enabled instance | 6379 |

Production must use `rediss://` (note the double `s`). Cloud
Memorystore terminates TLS on a separate port (6380) from plaintext
(6379); the scheme alone determines which port `redis-py` connects to
when one is not explicit in the URL.

---

## Section 4 — Cloud Memorystore configuration

### Terraform resource

Add to `terraform/phase-0a-gcp/` (new file `redis_jti_cache.tf`):

```hcl
resource "google_redis_instance" "jti_replay_cache" {
  name           = "autonomousagent-jti-replay"
  project        = var.project_id          # "autonomous-agent-2026"
  region         = var.region              # "us-central1"
  tier           = "STANDARD_HA"           # production; primary + replica
  memory_size_gb = 1                       # see capacity calc below
  redis_version  = "REDIS_7_0"             # SET NX EX, RESP3, ACL
  display_name   = "A2A JTI replay cache"

  authorized_network = data.google_compute_network.vpc.id

  # TLS for PHI-adjacent traffic
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  # AUTH disabled — VPC isolation + TLS is the trust boundary;
  # AUTH adds a static secret to manage with no extra security under
  # private-VPC + IAM-gated peering. Enable later via:
  #   auth_enabled = true
  # if a defense-in-depth review requires it.

  # Eviction policy: LRU. jti entries have TTL but cap memory.
  redis_configs = {
    maxmemory-policy = "allkeys-lru"
    timeout          = "0"              # never close idle client conns
  }

  # Persistence: NONE. jti cache is ephemeral by design — if Memorystore
  # restarts, the 60s L1 fallback covers the gap. Enabling RDB/AOF would
  # bloat backups with worthless 600s-lived keys.
  persistence_config {
    persistence_mode = "DISABLED"
  }

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 6              # 06:00 UTC Sunday — low traffic
        minutes = 0
      }
    }
  }

  labels = {
    component = "a2a-auth"
    env       = var.env_label
    owner     = "platform"
  }
}

# Allow Cloud Run service identities to reach Memorystore over the VPC.
resource "google_compute_firewall" "allow_cloudrun_to_redis" {
  name    = "autonomousagent-allow-cloudrun-redis"
  project = var.project_id
  network = data.google_compute_network.vpc.name

  direction     = "INGRESS"
  source_ranges = [var.cloudrun_vpc_egress_cidr]   # /28 reserved for Direct VPC Egress

  allow {
    protocol = "tcp"
    ports    = ["6379", "6380"]
  }

  target_tags = ["memorystore-jti-replay"]
}

output "redis_jti_replay_host" {
  value       = google_redis_instance.jti_replay_cache.host
  description = "Private IP for Cloud Run REDIS_URL"
}

output "redis_jti_replay_tls_port" {
  value       = google_redis_instance.jti_replay_cache.port  # 6379 plaintext or 6380 TLS
  description = "Memorystore port (use 6380 for rediss://)"
}
```

### Capacity calculation

- Peak inbound A2A rate (estimated): 1,000 verify/sec sustained, 5,000/sec burst.
- TTL: 600s.
- Steady-state keys: 1,000 × 600 = 600,000 entries.
- Burst: 5,000 × 600 = 3,000,000 entries.
- Per-entry overhead in Redis: key string (~80 bytes) + value (1 byte)
  + Redis internal overhead (~80 bytes) ≈ 160 bytes.
- Steady-state memory: 600K × 160B = 96 MB.
- Burst memory: 3M × 160B = 480 MB.
- **1 GB is correct** with ~2× headroom over burst. Scale to
  `memory_size_gb = 5` if sustained traffic exceeds 5K/sec for >10 min.

### Connectivity from Cloud Run

**Use Direct VPC Egress, not Serverless VPC Connector.** Direct VPC
Egress is GA on Cloud Run as of 2024, has lower latency (no extra
hop), no separate billed component, and supports the same private-IP
Memorystore reachability. Configure on the Cloud Run service:

```yaml
# In cloud-run service spec (terraform or `gcloud run services replace`):
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/network-interfaces: '[{
          "network": "default",
          "subnetwork": "autonomousagent-cloudrun-egress",
          "tags": ["memorystore-jti-replay-client"]
        }]'
        run.googleapis.com/vpc-access-egress: private-ranges-only
```

A `/28` subnet (`autonomousagent-cloudrun-egress`) is reserved for the
Cloud Run egress IPs; its CIDR is the `cloudrun_vpc_egress_cidr` var
referenced in the firewall above.

---

## Section 5 — Code changes to `lib/a2a/auth.py`

### Step 5.1 — New imports and module-level state

Add near the top of the file, after the existing `import` block:

```python
import os

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

# --- Redis-backed jti replay cache -----------------------------------------

_REDIS_POOL: ConnectionPool | None = None
_REDIS_POOL_LOCK: asyncio.Lock | None = None

# When Redis is unreachable, L1 fallback uses a SHORTER ttl than the
# Redis path (60s vs 600s) to bound the cross-replica replay window.
_L1_FALLBACK_TTL_SECS = 60

# Operator override: "closed" => fail-closed when Redis is unreachable.
_FAIL_MODE = os.environ.get("A2A_JTI_FAIL_MODE", "open").lower()
```

### Step 5.2 — `_get_redis_pool` (lazy singleton)

```python
def _get_redis_pool_lock() -> asyncio.Lock:
    global _REDIS_POOL_LOCK
    if _REDIS_POOL_LOCK is None:
        _REDIS_POOL_LOCK = asyncio.Lock()
    return _REDIS_POOL_LOCK


async def _get_redis_pool() -> ConnectionPool | None:
    """Return the shared Redis connection pool, or None if REDIS_URL is unset.

    Lazy: initialised on first use to avoid event-loop-at-import errors.
    Returns None (does not raise) when REDIS_URL is missing so that the
    caller can transparently fall back to L1-only mode (with a WARNING
    logged once at startup).
    """
    global _REDIS_POOL
    if _REDIS_POOL is not None:
        return _REDIS_POOL
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    lock = _get_redis_pool_lock()
    async with lock:
        if _REDIS_POOL is not None:
            return _REDIS_POOL
        timeout = float(os.environ.get("REDIS_CONNECT_TIMEOUT_SECS", "2.0"))
        _REDIS_POOL = ConnectionPool.from_url(
            url,
            max_connections=20,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            health_check_interval=30,
        )
        logger.info(
            "a2a.auth: initialised Redis pool for jti replay cache (%s)",
            _safe_url(url),
        )
        return _REDIS_POOL


def _safe_url(url: str) -> str:
    """Redact password from REDIS_URL for safe logging."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    _creds, host = rest.rsplit("@", 1)
    return f"{scheme}://***@{host}"
```

### Step 5.3 — `_jti_set_redis` (atomic check-and-set)

This single function replaces both check and set with one atomic
`SET NX EX` call. Returns:

- `True`  — first time we have seen this jti; **accept** the token.
- `False` — this jti was already in Redis; **reject** as replay.
- raises `RedisError` — Redis unreachable; caller decides L1 fallback
  vs fail-closed per `_FAIL_MODE`.

```python
async def _jti_set_redis(replay_key: tuple[str, str]) -> bool:
    """Atomically claim (issuer, jti) in Redis.

    Returns True if the key was newly created (token is fresh),
    False if it already existed (token is a replay).
    Raises RedisError if Redis is unreachable — caller must catch.
    """
    pool = await _get_redis_pool()
    if pool is None:
        # No REDIS_URL configured — signal "Redis path unavailable" to
        # the caller by raising the same exception type as a connection
        # failure. The caller's fallback logic is identical either way.
        raise RedisError("REDIS_URL not configured")
    issuer, jti = replay_key
    key = f"jti:{issuer}:{jti}"
    client = Redis(connection_pool=pool)
    # SET key 1 NX EX 600 — atomic claim with TTL.
    # Returns True on success (key did not exist and was created),
    # None when NX condition failed (key already existed = replay).
    result = await client.set(key, "1", nx=True, ex=600)
    return result is True
```

Note: we do **not** close the `Redis` client; it borrows from the pool
and returns the connection on garbage collection. The pool itself is
the singleton.

### Step 5.4 — `_jti_set_l1` (L1 fallback, shortened TTL)

The L1 path uses a separate TTLCache instance with the shortened
`_L1_FALLBACK_TTL_SECS` so we can keep the original `_JTI_CACHE`
constant for any code paths that might still reference it directly
(tests do, per existing `test_auth.py`).

Actually: we **reuse** `_JTI_CACHE` to avoid splitting the dataset,
but override the per-entry TTL to 60s in fallback mode. `cachetools`
does not support per-entry TTL on a single `TTLCache`, so we maintain
a parallel `_JTI_L1_FALLBACK` cache with the 60s TTL and check both.

```python
_JTI_L1_FALLBACK: cachetools.TTLCache[tuple[str, str], bool] = cachetools.TTLCache(
    maxsize=100_000,
    ttl=_L1_FALLBACK_TTL_SECS,
)


async def _jti_set_l1(replay_key: tuple[str, str]) -> bool:
    """L1 fallback check-and-set. Returns True if fresh, False if replay.

    Used only when Redis is unreachable. Holds the existing _JTI_LOCK
    to make check+set atomic within a single process.
    """
    lock = _get_jti_lock()
    async with lock:
        if _JTI_L1_FALLBACK.get(replay_key):
            return False
        _JTI_L1_FALLBACK[replay_key] = True
        return True
```

### Step 5.5 — Modified `verify_token` block

Replace the existing replay-check block (lines 184–190 of the current
`auth.py`):

```python
# --- OLD ---
replay_key = (issuer, jti)
lock = _get_jti_lock()
async with lock:
    if _JTI_CACHE.get(replay_key):
        _emit_audit_log("rejected_replay", None, None, None, None, peer_sa=issuer)
        raise ValueError("jti replay")
    _JTI_CACHE[replay_key] = True
```

With:

```python
# --- NEW ---
replay_key = (issuer, jti)
try:
    fresh = await _jti_set_redis(replay_key)
    if not fresh:
        _emit_audit_log("rejected_replay", None, None, None, None, peer_sa=issuer)
        raise ValueError("jti replay")
except RedisError as exc:
    # Redis unreachable: fail-closed or fall back to L1, per operator policy.
    if _FAIL_MODE == "closed":
        _emit_audit_log(
            "rejected_redis_unavailable", None, None, None, None, peer_sa=issuer
        )
        raise ValueError(f"jti replay cache unavailable (fail-closed): {exc}") from exc
    # Fail-open path — L1 with shortened TTL.
    logger.warning(
        "a2a.auth: Redis unreachable, falling back to L1 jti cache (60s TTL): %s",
        exc,
    )
    fresh = await _jti_set_l1(replay_key)
    if not fresh:
        _emit_audit_log("rejected_replay_l1", None, None, None, None, peer_sa=issuer)
        raise ValueError("jti replay")
    _emit_audit_log(
        "accepted_redis_unavailable", None, None, None, None, peer_sa=issuer
    )
```

Note the two new audit decision strings:

- `rejected_redis_unavailable` — fail-closed path; Redis down, token rejected.
- `accepted_redis_unavailable` — fail-open L1 path; Redis down, token accepted via L1.
- `rejected_replay_l1` — L1 path caught a replay within the 60s fallback window.

These give security operators precise visibility into degraded-mode
acceptances during a Memorystore outage. Add them to the documented
`decision` enum in the audit-log schema doc.

---

## Section 6 — Test strategy

### Library: `fakeredis[asyncio]>=2.20`

`fakeredis` provides an in-memory, command-compatible Redis
implementation. Version 2.20+ ships first-class `redis.asyncio`
support and implements `SET ... NX EX` correctly.

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
test = [
    # ... existing test deps ...
    "fakeredis[asyncio]>=2.20",
]
```

### `_get_redis_pool` patch helper

Tests inject a fakeredis pool by patching the module-level singleton.
Add to `lib/a2a/tests/conftest.py`:

```python
import pytest
import fakeredis.aioredis

import lib.a2a.auth as auth_mod


@pytest.fixture
async def fake_redis_pool(monkeypatch):
    """Replace _get_redis_pool() with a fakeredis-backed pool.

    The fixture also resets _JTI_L1_FALLBACK and the original
    _JTI_CACHE between tests for isolation.
    """
    fake_pool = fakeredis.aioredis.FakeConnectionPool.from_url(
        "redis://localhost", decode_responses=True
    )

    async def _fake_get_pool():
        return fake_pool

    monkeypatch.setattr(auth_mod, "_get_redis_pool", _fake_get_pool)
    auth_mod._JTI_CACHE.clear()
    auth_mod._JTI_L1_FALLBACK.clear()
    yield fake_pool
    await fake_pool.disconnect()


@pytest.fixture
async def redis_down(monkeypatch):
    """Force _jti_set_redis to raise RedisError, simulating Memorystore outage."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def _raise(*_args, **_kwargs):
        raise RedisConnectionError("simulated: Memorystore unreachable")

    monkeypatch.setattr(auth_mod, "_jti_set_redis", _raise)
    auth_mod._JTI_CACHE.clear()
    auth_mod._JTI_L1_FALLBACK.clear()
    yield
```

### Test file location

Two new tests go in **`lib/a2a/tests/test_auth_distributed.py`** (new
file). Rationale: the existing `test_auth.py` is 250+ lines and
already covers the single-replica replay path; keeping the
distributed-cache tests in their own file makes the Redis dependency
opt-in and the fixture surface explicit.

### Test (a) — replay detection via Redis

```python
# lib/a2a/tests/test_auth_distributed.py
"""Tests for Redis-backed jti replay cache (Section 6 of the design spec)."""
from __future__ import annotations

import pytest

from lib.a2a.auth import _jti_set_redis


@pytest.mark.asyncio
async def test_jti_first_accepted_second_rejected(fake_redis_pool):
    """SET NX EX semantics: first call returns True, duplicate returns False."""
    key = ("sa-a@autonomous-agent-2026.iam.gserviceaccount.com", "uuid-xyz-001")
    assert await _jti_set_redis(key) is True       # fresh — accept
    assert await _jti_set_redis(key) is False      # replay — reject
    # Different jti from same issuer: still fresh.
    other = (key[0], "uuid-xyz-002")
    assert await _jti_set_redis(other) is True
```

A second integration-style test exercises the full `verify_token`
path with a real JWT and the fakeredis pool:

```python
@pytest.mark.asyncio
async def test_verify_token_rejects_replay_across_simulated_replicas(
    fake_redis_pool, signed_jwt_fixture, jwks_patch
):
    """Same JWT verified twice — second call raises ValueError('jti replay').

    With fakeredis as the shared backing store, this models two Cloud Run
    replicas both calling verify_token on the same captured JWT.
    """
    from lib.a2a.auth import verify_token

    identity = await verify_token(
        signed_jwt_fixture.token,
        our_sa=signed_jwt_fixture.audience,
        peers_allowlist=[signed_jwt_fixture.issuer],
    )
    assert identity.jti == signed_jwt_fixture.jti

    with pytest.raises(ValueError, match="jti replay"):
        await verify_token(
            signed_jwt_fixture.token,
            our_sa=signed_jwt_fixture.audience,
            peers_allowlist=[signed_jwt_fixture.issuer],
        )
```

`signed_jwt_fixture` and `jwks_patch` already exist in
`lib/a2a/tests/conftest.py` (used by current `test_auth.py`); no new
JWT-signing scaffolding required.

### Test (b) — L1 fallback when Redis is down

```python
@pytest.mark.asyncio
async def test_verify_token_falls_back_to_l1_when_redis_down(
    redis_down, signed_jwt_fixture, jwks_patch
):
    """Redis unreachable -> fail-open path uses L1 cache; token still accepted.

    Then second verify of same JWT is rejected by L1 (same-process replay
    detection still works for the 60s fallback window).
    """
    from lib.a2a.auth import verify_token

    identity = await verify_token(
        signed_jwt_fixture.token,
        our_sa=signed_jwt_fixture.audience,
        peers_allowlist=[signed_jwt_fixture.issuer],
    )
    assert identity.jti == signed_jwt_fixture.jti

    with pytest.raises(ValueError, match="jti replay"):
        await verify_token(
            signed_jwt_fixture.token,
            our_sa=signed_jwt_fixture.audience,
            peers_allowlist=[signed_jwt_fixture.issuer],
        )


@pytest.mark.asyncio
async def test_verify_token_fail_closed_when_configured(
    redis_down, signed_jwt_fixture, jwks_patch, monkeypatch
):
    """A2A_JTI_FAIL_MODE=closed: Redis unreachable rejects every token."""
    import lib.a2a.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_FAIL_MODE", "closed")
    from lib.a2a.auth import verify_token

    with pytest.raises(ValueError, match="fail-closed"):
        await verify_token(
            signed_jwt_fixture.token,
            our_sa=signed_jwt_fixture.audience,
            peers_allowlist=[signed_jwt_fixture.issuer],
        )
```

### CI integration

No additional CI infrastructure needed. `fakeredis` runs entirely
in-process — no `docker-compose` service required, no extra GitHub
Actions step. Tests run under the existing `pytest -m "not integration"`
selection.

---

## Section 7 — Operator runbook

### Environment variables (set on the Cloud Run service)

| Variable | Required | Default | Format | Purpose |
|----------|----------|---------|--------|---------|
| `REDIS_URL` | Yes (prod) | — | `rediss://10.x.x.x:6380/0` (TLS) or `redis://10.x.x.x:6379/0` (plaintext) | Cloud Memorystore endpoint. Unset = L1-only with WARNING. |
| `REDIS_CONNECT_TIMEOUT_SECS` | No | `2.0` | float | Socket connect + per-op timeout. Fail fast to avoid stretching verify latency. |
| `A2A_JTI_FAIL_MODE` | No | `open` | `open` or `closed` | When Redis is down: `open` = L1 fallback (60s bounded-replay window); `closed` = reject all tokens. |

### Setting them

```bash
PROJECT_ID=autonomous-agent-2026
REGION=us-central1
REDIS_HOST=$(terraform -chdir=terraform/phase-0a-gcp output -raw redis_jti_replay_host)

gcloud run services update a2a-server \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars="REDIS_URL=rediss://${REDIS_HOST}:6380/0,REDIS_CONNECT_TIMEOUT_SECS=2.0,A2A_JTI_FAIL_MODE=open"
```

### Startup behaviour

- **`REDIS_URL` set, Memorystore reachable.** Log line at INFO:
  `a2a.auth: initialised Redis pool for jti replay cache (rediss://***@10.x.x.x:6380/0)`.
  First `verify_token` succeeds via Redis path.
- **`REDIS_URL` set, Memorystore unreachable at startup.** No startup
  crash; pool initialisation is lazy. First `verify_token` raises
  `RedisError` from `_jti_set_redis`, logs WARNING
  `a2a.auth: Redis unreachable, falling back to L1 jti cache (60s TTL)`,
  and accepts via L1 (assuming `A2A_JTI_FAIL_MODE=open`).
- **`REDIS_URL` unset.** First `verify_token` call logs WARNING once,
  then operates in L1-only mode. **Production deployments MUST set
  `REDIS_URL`; absence is an operator misconfiguration alert.**

### Monitoring / alerting

Add these Cloud Logging-based metrics (drop into
`terraform/phase-0a-gcp/monitoring.tf`):

```hcl
# Count of fail-open L1 acceptances. Should be ~0 outside Memorystore maintenance.
resource "google_logging_metric" "a2a_redis_unavailable_accepts" {
  name   = "a2a/auth/redis_unavailable_accepts"
  filter = <<EOT
resource.type="cloud_run_revision"
jsonPayload.event="auth_decision"
jsonPayload.decision="accepted_redis_unavailable"
EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# Alert if > 10 in 5 min (sustained Redis outage with traffic).
resource "google_monitoring_alert_policy" "a2a_redis_down_sustained" {
  display_name = "A2A jti cache: Redis sustained outage"
  combiner     = "OR"
  conditions {
    display_name = "redis_unavailable_accepts > 10 / 5m"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/a2a/auth/redis_unavailable_accepts\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "300s"
    }
  }
  notification_channels = [var.platform_pager_channel]
}

# Replay rejections via L1 fallback path — non-zero indicates active replay attempts
# during a Redis outage. Page immediately.
resource "google_monitoring_alert_policy" "a2a_l1_replay_rejected" {
  display_name = "A2A jti cache: L1 replay rejection (active attack during Redis outage)"
  combiner     = "OR"
  conditions {
    display_name = "rejected_replay_l1 > 0 / 1m"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/a2a/auth/rejected_replay_l1\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
    }
  }
  notification_channels = [var.security_pager_channel]
}
```

### Operator checklist before enabling in production

1. Terraform apply `redis_jti_cache.tf` — Memorystore instance up.
2. Terraform apply firewall + Cloud Run Direct VPC Egress subnet.
3. `gcloud redis instances describe autonomousagent-jti-replay` — verify
   `state: READY` and `host` private IP populated.
4. From a debug Cloud Run revision: `redis-cli -h $HOST -p 6380 --tls PING` — verify `PONG`.
5. Set `REDIS_URL` env var on production Cloud Run service (see above).
6. Tail logs for `a2a.auth: initialised Redis pool` on the first verify.
7. Generate a synthetic replay (mint two requests with the same jti)
   and verify the second is rejected with `decision="rejected_replay"`
   in Cloud Logging.
8. Inspect `INFO`/`metric.type=...a2a/auth/redis_unavailable_accepts` — should
   stay at 0 in steady state.

### Failure-mode reference

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `accepted_redis_unavailable` rate spikes | Memorystore failover, network partition, or pool exhaustion | Check Memorystore status in Console; check `health_check_interval` working; consider raising `max_connections` |
| `accepted_redis_unavailable` rate ~0 but verify p95 > 50ms | Slow Redis ops (large pipeline backlog) | Check Memorystore CPU/memory; consider scaling `memory_size_gb` |
| Every verify fails with `fail-closed` error | Memorystore down + `A2A_JTI_FAIL_MODE=closed` | Switch to `open` temporarily; restore Memorystore |
| Every verify logs `REDIS_URL not configured` warning | env var missing | Set `REDIS_URL`; redeploy. **Until then, replay protection is per-replica only.** |

---

## Appendix A — Decision summary (one-page)

| Decision | Choice | Rationale (one line) |
|----------|--------|----------------------|
| Failure mode | Fail-open + L1 60s fallback (Option B) | Bounded blast radius, avoids control-plane cascade |
| Operator override | `A2A_JTI_FAIL_MODE=closed` | Per-deployment escape hatch |
| Atomic primitive | `SET key 1 NX EX 600` | One round-trip, race-free |
| Key format | `jti:{issuer_sa_email}:{jti}` | Namespaced, scoped, human-debuggable |
| Client lib | `redis[asyncio]>=5.0,<6` | Modern unified client, hiredis-accelerated |
| Pool size | `max_connections=20` per replica | Matches Cloud Run concurrency budget |
| Memorystore tier | `STANDARD_HA`, `REDIS_7_0`, 1 GB | HA primary+replica, TLS-capable |
| Persistence | Disabled | jti is ephemeral; L1 covers restart gaps |
| Connectivity | Direct VPC Egress | Lower latency than Serverless VPC Connector |
| Test backend | `fakeredis[asyncio]>=2.20` | In-process, no docker-compose, CI-friendly |
| Test location | `lib/a2a/tests/test_auth_distributed.py` (new) | Isolates Redis dep; existing test_auth.py unchanged |
| Connect timeout | 2.0s | Fast fail to L1; verify latency budget preserved |
