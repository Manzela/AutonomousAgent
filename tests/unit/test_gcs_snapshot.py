"""Unit tests for lib.snapshots.gcs_snapshot."""

from __future__ import annotations

import os
import tarfile
from datetime import datetime, timezone
from unittest import mock

import pytest

from lib.snapshots import gcs_snapshot as gs


# ---------------------------------------------------------------------------
# evaluate_should_skip — pure decision function
# ---------------------------------------------------------------------------


def test_evaluate_skips_when_today_done():
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    assert gs.evaluate_should_skip(now, snapshot_hour_utc=4, today_done=True) == (
        "today_already_uploaded"
    )


def test_evaluate_skips_before_snapshot_hour():
    now = datetime(2026, 5, 20, 3, 59, tzinfo=timezone.utc)
    assert gs.evaluate_should_skip(now, snapshot_hour_utc=4, today_done=False) == (
        "before_snapshot_hour_utc=4"
    )


def test_evaluate_proceeds_at_or_after_snapshot_hour():
    for hour in (4, 5, 12, 23):
        now = datetime(2026, 5, 20, hour, 0, tzinfo=timezone.utc)
        assert gs.evaluate_should_skip(now, snapshot_hour_utc=4, today_done=False) is None


def test_evaluate_today_done_dominates_hour_check():
    """today_done short-circuits even when we'd otherwise proceed."""

    now = datetime(2026, 5, 20, 23, 0, tzinfo=timezone.utc)
    assert gs.evaluate_should_skip(now, snapshot_hour_utc=4, today_done=True) == (
        "today_already_uploaded"
    )


# ---------------------------------------------------------------------------
# run_once — feature-flag / skip paths
# ---------------------------------------------------------------------------


def test_run_once_no_bucket_skips_feature_flag_off(monkeypatch):
    monkeypatch.delenv("GCS_SNAPSHOT_BUCKET", raising=False)
    result = gs.run_once(bucket=None)
    assert result.skipped is True
    assert result.uploaded is False
    assert result.reason == "bucket_not_configured"


def test_run_once_bucket_from_env(monkeypatch):
    monkeypatch.setenv("GCS_SNAPSHOT_BUCKET", "env-bucket")
    # Force an ImportError path so we don't actually try the SDK.
    with mock.patch.object(gs, "_gcs_client", side_effect=ImportError("no sdk")):
        result = gs.run_once()
    assert result.skipped is True
    assert result.reason == "google-cloud-storage_not_installed"


def test_run_once_skips_when_today_already_uploaded(monkeypatch):
    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    fake_client = mock.MagicMock()
    fake_client.list_blobs.return_value = [mock.MagicMock()]  # truthy → today done
    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket")
    assert result.skipped is True
    assert result.reason == "today_already_uploaded"
    assert result.object_name == "hermes-snapshots/2026-05-20/hermes-state.tar.gz"


def test_run_once_skips_before_snapshot_hour(monkeypatch):
    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 3, 0, tzinfo=timezone.utc))
    fake_client = mock.MagicMock()
    fake_client.list_blobs.return_value = []  # not done
    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", snapshot_hour_utc=4)
    assert result.skipped is True
    assert "before_snapshot_hour" in (result.reason or "")


# ---------------------------------------------------------------------------
# run_once — happy-path upload
# ---------------------------------------------------------------------------


def test_run_once_uploads_when_due(monkeypatch, tmp_path):
    # Synthetic source dir with a couple of small files.
    src = tmp_path / "hermes"
    src.mkdir()
    (src / "kanban.db").write_text("fake-sqlite")
    (src / "config.yaml").write_text("foo: bar")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))

    uploaded_paths = []

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            uploaded_paths.append((path, content_type))

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []  # not done

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", source_dir=str(src))

    assert result.uploaded is True
    assert result.skipped is False
    assert result.error is None
    assert result.object_name == "hermes-snapshots/2026-05-20/hermes-state.tar.gz"
    assert result.bytes_uploaded is not None and result.bytes_uploaded > 0

    # Verify the upload received a valid tar.gz path.
    assert len(uploaded_paths) == 1
    path, content_type = uploaded_paths[0]
    assert content_type == "application/gzip"
    # Tmp file is cleaned up after upload, so we can't reopen it. Verify the
    # tar would have contained "hermes/kanban.db" via the tar helper directly.


def test_tar_source_dir_rebases_to_hermes_arcname(tmp_path):
    """The tar arcname should be ``hermes/...`` for portable restore."""

    src = tmp_path / "state"
    src.mkdir()
    (src / "marker").write_text("x")

    out = tmp_path / "out.tar.gz"
    size = gs._tar_source_dir(str(src), str(out))
    assert size > 0

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "hermes" in names
    assert "hermes/marker" in names


def test_tar_source_dir_raises_when_source_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        gs._tar_source_dir(str(tmp_path / "nope"), str(tmp_path / "out.tar.gz"))


# ---------------------------------------------------------------------------
# run_once — error paths (fail-open + Telegram alert)
# ---------------------------------------------------------------------------


def test_run_once_upload_failure_emits_alert_and_returns_error(monkeypatch, tmp_path):
    src = tmp_path / "hermes"
    src.mkdir()
    (src / "f").write_text("x")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))

    class _BrokenBlob:
        def upload_from_filename(self, path, content_type):
            raise RuntimeError("403 Forbidden")

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _BrokenBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    alerts = []
    monkeypatch.setattr(gs, "_emit_alert", lambda msg: alerts.append(msg))

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", source_dir=str(src))

    assert result.uploaded is False
    assert result.error is not None
    assert "403 Forbidden" in result.error
    assert len(alerts) == 1
    assert "2026-05-20" in alerts[0]
    assert "403 Forbidden" in alerts[0]


def test_run_once_missing_source_dir_emits_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    fake_client = mock.MagicMock()
    fake_client.list_blobs.return_value = []
    fake_client.bucket.return_value = mock.MagicMock()

    alerts = []
    monkeypatch.setattr(gs, "_emit_alert", lambda msg: alerts.append(msg))

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", source_dir=str(tmp_path / "absent"))

    assert result.uploaded is False
    assert result.error is not None
    assert "snapshot source missing" in result.error
    assert len(alerts) == 1


def test_run_once_client_init_failure_returns_error(monkeypatch):
    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    with mock.patch.object(gs, "_gcs_client", side_effect=RuntimeError("ADC missing")):
        result = gs.run_once(bucket="my-bucket")
    assert result.uploaded is False
    assert result.error is not None
    assert "client init failed" in result.error


def test_run_once_list_blobs_failure_proceeds_to_upload(monkeypatch, tmp_path):
    """A transient list_blobs failure should NOT permanently block snapshots."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "f").write_text("x")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            pass

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.side_effect = RuntimeError("transient API failure")

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", source_dir=str(src))

    # list_blobs failed → assumed not done → upload proceeded.
    assert result.uploaded is True
    assert result.error is None


def test_run_once_cleans_up_temp_file_on_success(monkeypatch, tmp_path):
    """The tmp tar must be removed even after a successful upload."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "f").write_text("x")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))

    captured = []

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            captured.append(path)

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        gs.run_once(bucket="my-bucket", source_dir=str(src))

    assert len(captured) == 1
    assert not os.path.exists(captured[0]), "tmp tar must be unlinked after upload"


# ---------------------------------------------------------------------------
# object_name format — pins the YYYY-MM-DD layout
# ---------------------------------------------------------------------------


def test_object_name_format():
    now = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    assert gs._object_name_for(now) == "hermes-snapshots/2026-12-31/hermes-state.tar.gz"


def test_object_name_uses_utc_not_local():
    """Even a 'morning' local time on the next day should bucket per UTC."""

    now = datetime(2026, 5, 20, 0, 1, tzinfo=timezone.utc)
    assert "2026-05-20" in gs._object_name_for(now)


# ---------------------------------------------------------------------------
# Spend-log inclusion — FinOps slice (paired with weekly-cost-summary.yml)
# ---------------------------------------------------------------------------


def test_tar_source_dir_includes_extras(tmp_path):
    """Extras must land in the tar at their declared arcname."""

    src = tmp_path / "state"
    src.mkdir()
    (src / "marker").write_text("x")

    extra = tmp_path / "spend.csv"
    extra.write_text("request_id,spend\nreq1,1.50\n")

    out = tmp_path / "out.tar.gz"
    size = gs._tar_source_dir(str(src), str(out), extras=[(str(extra), "hermes/spend_logs.csv")])
    assert size > 0

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "hermes/marker" in names
    assert "hermes/spend_logs.csv" in names


def test_tar_source_dir_skips_missing_extras(tmp_path):
    """A missing extra path is logged + skipped, not an exception."""

    src = tmp_path / "state"
    src.mkdir()
    (src / "marker").write_text("x")

    out = tmp_path / "out.tar.gz"
    # Pass a non-existent extra; tar still succeeds.
    size = gs._tar_source_dir(
        str(src),
        str(out),
        extras=[(str(tmp_path / "ghost.csv"), "hermes/ghost.csv")],
    )
    assert size > 0
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "hermes/marker" in names
    assert "hermes/ghost.csv" not in names


def test_dump_spend_logs_csv_writes_rows(tmp_path, monkeypatch):
    """The dump helper streams cursor rows into a CSV with the header."""

    captured_rows = [
        (
            "req-1",
            "opus",
            "alice",
            "sk-1",
            0.5,
            "2026-05-20T00:00:00Z",
            "2026-05-20T00:00:01Z",
            "completion",
        ),
        (
            "req-2",
            "haiku",
            "bob",
            "sk-2",
            0.01,
            "2026-05-20T00:01:00Z",
            "2026-05-20T00:01:00Z",
            "completion",
        ),
    ]

    class _FakeCursor:
        def execute(self, sql):
            self._sql = sql

        def __iter__(self):
            return iter(captured_rows)

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(gs, "_connect_db", lambda conn_str: _FakeConn())
    dest = tmp_path / "spend.csv"
    rows = gs._dump_spend_logs_csv("postgresql://stub", str(dest))
    assert rows == 2

    contents = dest.read_text()
    assert contents.splitlines()[0].startswith("request_id,")
    assert "req-1,opus,alice,sk-1,0.5" in contents
    assert "req-2,haiku,bob,sk-2,0.01" in contents


def test_dump_spend_logs_csv_returns_none_on_psycopg_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "_connect_db", mock.MagicMock(side_effect=ImportError("no psycopg")))
    result = gs._dump_spend_logs_csv("postgresql://stub", str(tmp_path / "spend.csv"))
    assert result is None


def test_dump_spend_logs_csv_returns_none_on_db_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gs,
        "_connect_db",
        mock.MagicMock(side_effect=RuntimeError("connection refused")),
    )
    result = gs._dump_spend_logs_csv("postgresql://stub", str(tmp_path / "spend.csv"))
    assert result is None


def test_run_once_embeds_spend_logs_when_flag_set(monkeypatch, tmp_path):
    """Happy-path: bucket configured + include_spend_logs=True + DB reachable."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "marker").write_text("ok")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://stub")

    # Stub the spend dump to write a small CSV at the temp path.
    def _fake_dump(conn_str, dest):
        with open(dest, "w") as f:
            f.write("request_id,model,spend\nreq-1,opus,1.25\n")
        return 1

    monkeypatch.setattr(gs, "_dump_spend_logs_csv", _fake_dump)

    uploaded_tar_paths: list[str] = []

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            uploaded_tar_paths.append(path)

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    # Snapshot the tar before run_once unlinks it — pluck the path out
    # of _upload_blob and copy the tar's member list.
    members_at_upload: list[list[str]] = []

    real_upload = gs._upload_blob

    def _spy_upload(client, bucket, object_name, local_path):
        with tarfile.open(local_path, "r:gz") as tar:
            members_at_upload.append(tar.getnames())
        real_upload(client, bucket, object_name, local_path)

    monkeypatch.setattr(gs, "_upload_blob", _spy_upload)

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(
            bucket="my-bucket",
            source_dir=str(src),
            include_spend_logs=True,
        )

    assert result.uploaded is True
    assert result.spend_logs_rows == 1
    assert len(members_at_upload) == 1
    assert "hermes/spend_logs.csv" in members_at_upload[0]
    assert "hermes/marker" in members_at_upload[0]


def test_run_once_proceeds_when_spend_dump_fails(monkeypatch, tmp_path):
    """A failed spend dump must not abort the snapshot — fail-open."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "marker").write_text("ok")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://stub")
    monkeypatch.setattr(gs, "_dump_spend_logs_csv", lambda *_a, **_kw: None)

    uploaded = []

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            uploaded.append(path)

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(
            bucket="my-bucket",
            source_dir=str(src),
            include_spend_logs=True,
        )

    assert result.uploaded is True
    assert result.spend_logs_rows is None
    assert result.error is None
    assert len(uploaded) == 1


def test_run_once_skips_spend_dump_when_no_db_url(monkeypatch, tmp_path):
    """include_spend_logs=True + no DB URL → snapshot proceeds without CSV."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "marker").write_text("ok")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    called = mock.MagicMock()
    monkeypatch.setattr(gs, "_dump_spend_logs_csv", called)

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            pass

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(
            bucket="my-bucket",
            source_dir=str(src),
            include_spend_logs=True,
        )

    called.assert_not_called()
    assert result.uploaded is True
    assert result.spend_logs_rows is None


def test_run_once_default_does_not_dump_spend_logs(monkeypatch, tmp_path):
    """include_spend_logs defaults False — backwards compatible."""

    src = tmp_path / "hermes"
    src.mkdir()
    (src / "marker").write_text("ok")

    monkeypatch.setattr(gs, "_utc_now", lambda: datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://stub")

    called = mock.MagicMock()
    monkeypatch.setattr(gs, "_dump_spend_logs_csv", called)

    class _FakeBlob:
        def upload_from_filename(self, path, content_type):
            pass

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.return_value = _FakeBlob()
    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket
    fake_client.list_blobs.return_value = []

    with mock.patch.object(gs, "_gcs_client", return_value=fake_client):
        result = gs.run_once(bucket="my-bucket", source_dir=str(src))

    called.assert_not_called()
    assert result.uploaded is True
    assert result.spend_logs_rows is None
