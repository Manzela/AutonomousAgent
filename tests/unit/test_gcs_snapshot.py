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
