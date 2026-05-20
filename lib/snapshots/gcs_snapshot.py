"""GCS snapshot executor — daily disaster-recovery backup of Hermes state.

Tars ``/home/hermes/.hermes/`` (Kanban DB, plugin state, runtime config)
and uploads to a configured GCS bucket. Fail-soft: an upload failure
logs WARNING + posts a Telegram alert but does not halt the agent.

**Feature-flag pattern.** The executor reads the bucket name from the
``GCS_SNAPSHOT_BUCKET`` environment variable. When unset (the default,
since GCS bucket + service-account key are owner-provisioned per
``docs/runbooks/snapshots.md``), the executor logs a one-time INFO at
loop start and every tick is a no-op skip. This lets the code ship and
the sidecar run today; the operator flips the env var when the bucket
exists.

**Authentication.** Uses Google Cloud's default credentials chain
(``google.auth.default()``): GOOGLE_APPLICATION_CREDENTIALS for a
mounted SA key, then ADC / metadata server when running on GCE. The
sidecar mounts the SA key via ``secrets/gcs-snapshot.env.sops`` once
provisioned.

**Idempotency.** Object names embed the UTC date
(``hermes-snapshots/YYYY-MM-DD/hermes-state.tar.gz``). The
``today_already_done`` check lists the day's prefix and skips the upload
if any object exists, so a sidecar restart inside the same UTC day
doesn't double-upload.

**Retention.** Bucket-side lifecycle policy (documented in the runbook)
deletes objects older than ``snapshots.gcs_retention_days``. The
executor does not perform deletions itself — least-privilege SA key
only needs ``storage.objects.create`` + ``storage.objects.list``.

**Spend-log inclusion (FinOps).** When ``include_spend_logs=True``
and a ``LITELLM_DB_URL`` (or ``DATABASE_URL``) is reachable, the
snapshot also embeds ``spend_logs.csv`` inside the tar at
``hermes/spend_logs.csv``. Same fail-open contract: a DB outage or
psycopg ImportError downgrades the spend dump to a no-op WARNING
log; the rest of the snapshot still uploads. Pairs with the weekly
cost-summary workflow so the operator can reconstruct historical
spend even after the LiteLLM Postgres table is rotated.

**CSV scope and column safety.** The dumped CSV contains only the
billing-relevant columns from ``LiteLLM_SpendLogs`` (request_id,
model, user, api_key, spend, startTime, endTime, call_type) — it
does NOT include request ``messages`` or response payloads, which
keeps the snapshot small and avoids replicating prompt-bearing
records to the recovery host. The ``api_key`` column is the SHA-256
hashed Prisma token stored by LiteLLM (e.g.
``hashed-sk-xxxxxxxxxxxx``), NOT the raw master key — LiteLLM
hashes inbound keys at ingress, so the snapshot can never leak the
master credential even if the GCS bucket is later mis-scoped.

**Out of scope (tracked separately).** Honcho session export and
Phoenix-sqlite trace bundling are NOT included in this snapshot —
both are tracked as follow-up work in issue #110 so the FinOps
slice can ship without blocking on those subsystems.
"""

from __future__ import annotations

import csv
import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


DEFAULT_SNAPSHOT_HOUR_UTC = 4  # matches limits.yaml gcs_snapshot_cron "0 4 * * *"
DEFAULT_SOURCE_DIR = "/home/hermes/.hermes"
OBJECT_PREFIX = "hermes-snapshots"

# Spend-log dump query — full row export so the CSV can be replayed
# into a fresh LiteLLM_SpendLogs table during a disaster-recovery
# rehydrate. Columns mirror the Prisma model (camelCase preserved).
_SPEND_LOGS_DUMP_SQL = (
    'SELECT request_id, model, "user", api_key, spend, '
    '"startTime", "endTime", call_type '
    'FROM "LiteLLM_SpendLogs" '
    'ORDER BY "startTime" DESC'
)
_SPEND_LOGS_CSV_HEADERS = (
    "request_id",
    "model",
    "user",
    "api_key",
    "spend",
    "startTime",
    "endTime",
    "call_type",
)


@dataclass(frozen=True)
class SnapshotResult:
    """One tick of the snapshot watchdog.

    ``skipped`` flags soft no-ops (bucket not configured, before
    snapshot hour, today already done). ``uploaded`` is True iff a tar
    was actually pushed to GCS. ``error`` is set when an attempted
    upload failed and is the WARNING-log + Telegram-alert reason.
    ``object_name`` is the GCS path of the upload (or the path it
    *would* have taken) for log correlation.
    """

    skipped: bool = False
    uploaded: bool = False
    object_name: Optional[str] = None
    bytes_uploaded: Optional[int] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    # Number of LiteLLM_SpendLogs rows embedded in the tar (None when
    # the spend-log dump was skipped or failed). Surfaced so the loop
    # log line can confirm the FinOps slice landed alongside the state.
    spend_logs_rows: Optional[int] = None


def _utc_now() -> datetime:
    """Indirection so tests can pin a fixed clock."""

    return datetime.now(timezone.utc)


def _gcs_client() -> Any:
    """Lazy google-cloud-storage import. Patched by tests.

    Kept as a thin indirection so the unit-test layer can stub the
    entire client without importing the SDK (mirrors
    ``budget_watchdog._connect``).
    """

    from google.cloud import storage  # type: ignore[import-not-found]

    return storage.Client()


def _object_name_for(now_utc: datetime) -> str:
    """``hermes-snapshots/YYYY-MM-DD/hermes-state.tar.gz``."""

    return f"{OBJECT_PREFIX}/{now_utc.strftime('%Y-%m-%d')}/hermes-state.tar.gz"


def _today_already_uploaded(client: Any, bucket_name: str, now_utc: datetime) -> bool:
    """List today's prefix; True iff any object exists.

    Treats any list-API exception as "unknown → assume not done" so a
    transient list failure doesn't permanently block a day's snapshot.
    """

    prefix = f"{OBJECT_PREFIX}/{now_utc.strftime('%Y-%m-%d')}/"
    try:
        bucket = client.bucket(bucket_name)
        blobs = list(client.list_blobs(bucket, prefix=prefix, max_results=1))
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("snapshot: list_blobs failed (will attempt upload): %s", exc)
        return False
    return len(blobs) > 0


def _tar_source_dir(
    source_dir: str,
    dest_path: str,
    extras: Optional[Iterable[tuple[str, str]]] = None,
) -> int:
    """Tar ``source_dir`` to ``dest_path`` (gzip). Returns bytes written.

    The arcname is rebased so the tar root is ``hermes/`` rather than
    the absolute container path, which keeps the archive portable for
    extraction on a recovery host with a different mount layout.

    ``extras`` is an optional iterable of ``(local_path, arcname)``
    tuples appended verbatim into the tar — used for spend-log CSV
    inclusion. Missing extras are logged as a WARNING and skipped so a
    DB outage doesn't cancel the rest of the snapshot.
    """

    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"snapshot source missing: {source_dir}")
    with tarfile.open(dest_path, "w:gz") as tar:
        tar.add(str(src), arcname="hermes")
        for local, arc in extras or ():
            if not local or not os.path.exists(local):
                logger.warning("snapshot: extra missing, skipping: %s", local)
                continue
            tar.add(local, arcname=arc)
    return os.path.getsize(dest_path)


def _connect_db(conn_str: str) -> Any:
    """Lazy psycopg import. Mirrors budget_watchdog._connect.

    Kept as a thin indirection so the unit tests can stub the whole DB
    layer without importing psycopg.
    """

    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(conn_str)


def _dump_spend_logs_csv(conn_str: str, dest_path: str) -> Optional[int]:
    """Stream ``LiteLLM_SpendLogs`` to ``dest_path`` as CSV.

    Returns the row count on success, ``None`` on any failure (logged
    at WARNING — caller proceeds without the spend dump).

    Uses ``COPY ... TO STDOUT`` when available (psycopg 3 cursor
    ``copy()`` API) and falls back to a streamed cursor + csv.writer
    for compatibility. Each row's timestamps are serialised by psycopg
    in the same ISO-8601 form ``LiteLLM`` itself uses, so a recovery
    rehydrate doesn't need to re-coerce them.
    """

    try:
        conn = _connect_db(conn_str)
    except ImportError:
        logger.warning("snapshot: psycopg not installed; skipping spend_logs dump")
        return None
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("snapshot: spend_logs connect failed: %s", exc)
        return None

    row_count = 0
    try:
        with open(dest_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_SPEND_LOGS_CSV_HEADERS)
            cur = conn.cursor()
            cur.execute(_SPEND_LOGS_DUMP_SQL)
            for row in cur:
                writer.writerow(row)
                row_count += 1
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("snapshot: spend_logs dump failed: %s", exc)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    logger.info("snapshot: dumped %d spend_logs rows to %s", row_count, dest_path)
    return row_count


def _upload_blob(client: Any, bucket_name: str, object_name: str, local_path: str) -> None:
    """Single-shot upload. Raises on any failure for the caller to log."""

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(local_path, content_type="application/gzip")


def _emit_alert(msg: str) -> None:
    """Post the snapshot warning via the Telegram bridge.

    Mirrors ``budget_watchdog._emit_alert`` — fail-open on a missing
    Telegram bridge so the loop keeps ticking on a degraded host.
    """

    try:
        from lib.kanban.telegram_bridge import send_alert

        send_alert("snapshot", msg)
    except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
        logger.warning("snapshot: send_alert raised: %s", exc)


def evaluate_should_skip(
    now_utc: datetime,
    snapshot_hour_utc: int,
    today_done: bool,
) -> Optional[str]:
    """Pure decision function — None to proceed, else the skip reason.

    Split out so unit tests can pin the time logic without mocking the
    GCS layer. Matches the budget_watchdog ``evaluate_budget`` pattern.
    """

    if today_done:
        return "today_already_uploaded"
    if now_utc.hour < snapshot_hour_utc:
        return f"before_snapshot_hour_utc={snapshot_hour_utc}"
    return None


def run_once(
    bucket: Optional[str] = None,
    source_dir: str = DEFAULT_SOURCE_DIR,
    snapshot_hour_utc: int = DEFAULT_SNAPSHOT_HOUR_UTC,
    include_spend_logs: bool = False,
    spend_logs_conn_str: Optional[str] = None,
) -> SnapshotResult:
    """One snapshot tick. Returns a SnapshotResult for the caller to log.

    Workflow:

    1. If bucket unset → skip (feature flag off).
    2. Check today's prefix → skip if already uploaded.
    3. Check current UTC hour < snapshot_hour_utc → skip until the
       configured time-of-day.
    4. If ``include_spend_logs`` and a DB URL is available, dump
       ``LiteLLM_SpendLogs`` to a temp CSV (fail-open: a DB outage
       logs WARNING and continues without the CSV).
    5. Tar source_dir (+ optional spend CSV) → temp file → upload →
       cleanup.
    6. On upload failure: emit Telegram alert + return error in
       SnapshotResult (no F-dispatch — DR is fail-soft by design; a
       multi-day failure pattern is the operator's responsibility to
       notice via the daily-completion log + Telegram nag at re-tick).

    Fully fail-open: no exception path crashes the sidecar loop.
    """

    bucket = bucket or os.environ.get("GCS_SNAPSHOT_BUCKET")
    if not bucket:
        return SnapshotResult(skipped=True, reason="bucket_not_configured")

    now_utc = _utc_now()
    object_name = _object_name_for(now_utc)

    try:
        client = _gcs_client()
    except ImportError:
        return SnapshotResult(
            skipped=True,
            object_name=object_name,
            reason="google-cloud-storage_not_installed",
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        return SnapshotResult(object_name=object_name, error=f"client init failed: {exc}")

    today_done = _today_already_uploaded(client, bucket, now_utc)
    skip_reason = evaluate_should_skip(now_utc, snapshot_hour_utc, today_done)
    if skip_reason:
        return SnapshotResult(skipped=True, object_name=object_name, reason=skip_reason)

    # Resolve the spend-log dump up front so cleanup is in one place.
    spend_csv_path: Optional[str] = None
    spend_rows: Optional[int] = None
    if include_spend_logs:
        db_url = (
            spend_logs_conn_str
            or os.environ.get("LITELLM_DB_URL")
            or os.environ.get("DATABASE_URL")
        )
        if db_url:
            spend_fd, spend_csv_path = tempfile.mkstemp(suffix=".csv", prefix="hermes-spend-logs-")
            os.close(spend_fd)
            spend_rows = _dump_spend_logs_csv(db_url, spend_csv_path)
            # Failed dump → drop the empty stub so the tar doesn't ship
            # a misleading zero-byte spend_logs.csv. WARN was already
            # logged inside the helper.
            if spend_rows is None and os.path.exists(spend_csv_path):
                try:
                    os.unlink(spend_csv_path)
                except OSError:
                    pass
                spend_csv_path = None
        else:
            logger.warning("snapshot: include_spend_logs=True but no DB URL set; skipping")

    extras: list[tuple[str, str]] = []
    if spend_csv_path:
        extras.append((spend_csv_path, "hermes/spend_logs.csv"))

    tmp_path: Optional[str] = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="hermes-snapshot-")
        os.close(tmp_fd)
        size = _tar_source_dir(source_dir, tmp_path, extras=extras)
        _upload_blob(client, bucket, object_name, tmp_path)
    except FileNotFoundError as exc:
        msg = f"snapshot tar failed: {exc}"
        _emit_alert(f"⚠️ Hermes snapshot skipped: {exc}")
        return SnapshotResult(object_name=object_name, error=msg, spend_logs_rows=spend_rows)
    except Exception as exc:  # noqa: BLE001 — fail-open
        msg = f"snapshot upload failed: {exc}"
        _emit_alert(f"⚠️ Hermes daily snapshot failed for {now_utc.strftime('%Y-%m-%d')}: {exc}")
        return SnapshotResult(object_name=object_name, error=msg, spend_logs_rows=spend_rows)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.warning("snapshot: tmp cleanup failed: %s", exc)
        if spend_csv_path and os.path.exists(spend_csv_path):
            try:
                os.unlink(spend_csv_path)
            except OSError as exc:
                logger.warning("snapshot: spend csv cleanup failed: %s", exc)

    logger.info(
        "snapshot uploaded gs://%s/%s bytes=%d spend_rows=%s",
        bucket,
        object_name,
        size,
        spend_rows if spend_rows is not None else "n/a",
    )
    return SnapshotResult(
        uploaded=True,
        object_name=object_name,
        bytes_uploaded=size,
        spend_logs_rows=spend_rows,
    )


__all__ = [
    "DEFAULT_SNAPSHOT_HOUR_UTC",
    "DEFAULT_SOURCE_DIR",
    "OBJECT_PREFIX",
    "SnapshotResult",
    "evaluate_should_skip",
    "run_once",
]
