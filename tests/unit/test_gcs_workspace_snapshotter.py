"""Unit tests for GCS workspace snapshotting and rehydration (SP-R7)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app.adapters.gcp.gcs_snapshotter import GcsWorkspaceSnapshotter
from app.core.workspace import WorkspaceSession


@pytest.fixture
def fake_gcs(monkeypatch):
    uploaded = {}

    class FakeBlob:
        def __init__(self, name, *args, **kwargs):
            self.name = name
            self.kms_key_name = kwargs.get("kms_key_name")

        def upload_from_filename(self, filename, content_type):
            uploaded[self.name] = Path(filename).read_bytes()

        def download_to_filename(self, filename):
            if self.name not in uploaded:
                raise RuntimeError(f"Blob {self.name} not found")
            Path(filename).write_bytes(uploaded[self.name])

    fake_bucket = mock.MagicMock()
    fake_bucket.blob.side_effect = FakeBlob

    fake_client = mock.MagicMock()
    fake_client.bucket.return_value = fake_bucket

    monkeypatch.setattr(
        "app.adapters.gcp.gcs_snapshotter.GcsWorkspaceSnapshotter._gcs_client",
        lambda self: fake_client,
    )
    return uploaded


def test_gcs_workspace_snapshotter_snapshot_and_rehydrate(tmp_path, fake_gcs):
    # Create a source workspace
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app").mkdir()
    (src_dir / "app" / "code.py").write_text("print('hello')\n")
    (src_dir / "untracked.txt").write_text("untracked file\n")
    (src_dir / ".git").write_text("git ref placeholder\n")  # should be excluded

    snapshotter = GcsWorkspaceSnapshotter(bucket_name="test-bucket")
    result = snapshotter.snapshot(src_dir, thread_id="t1", node_id="n1")

    assert result is not None
    assert result["kind"] == "gcs"
    assert result["bucket"] == "test-bucket"
    assert result["object"] == "workspaces/t1/n1.tar.gz"
    assert len(result["digest"]) == 64
    assert result["ref"] == "gs://test-bucket/workspaces/t1/n1.tar.gz"

    # Verify GCS received the upload
    assert "workspaces/t1/n1.tar.gz" in fake_gcs

    # Rehydrate into a clean target directory
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / ".git").write_text("this should be kept\n")  # should not be overwritten

    success = snapshotter.rehydrate(dest_dir, thread_id="t1", node_id="n1")
    assert success is True

    # Verify extracted contents
    assert (dest_dir / "app" / "code.py").read_text() == "print('hello')\n"
    assert (dest_dir / "untracked.txt").read_text() == "untracked file\n"
    assert (dest_dir / ".git").read_text() == "this should be kept\n"  # kept intact


def test_workspace_session_gcs_snapshot_routing(tmp_path, monkeypatch, fake_gcs):
    # Set env var to enable GCS snapshot store
    monkeypatch.setenv("SPINE_SNAPSHOT_STORE", "gcs")
    monkeypatch.setenv("SPINE_SNAPSHOT_BUCKET", "test-bucket")

    # Create workspace session
    repo_dir = Path(__file__).resolve().parents[2]
    ws = WorkspaceSession.create(repo_dir=repo_dir, base_ref="HEAD", thread_id="t2", node_id="n2")

    assert ws.ok
    assert ws.ws_dir is not None

    # Write a sentinel file
    (ws.ws_dir / "app" / "sentinel.txt").write_text("sentinel")

    # Snapshot to GCS
    wref = ws.snapshot(thread_id="t2", node_id="n2")
    ws.close()

    assert wref is not None
    assert wref["kind"] == "gcs"
    assert wref["bucket"] == "test-bucket"
    assert wref["object"] == "workspaces/t2/n2.tar.gz"
    assert wref["base_sha"] == ws.base_sha

    # Rehydrate
    ws2 = WorkspaceSession.rehydrate(repo_dir=repo_dir, ref=wref, thread_id="t2")
    try:
        assert ws2.ok
        assert ws2.ws_dir is not None
        assert (ws2.ws_dir / "app" / "sentinel.txt").read_text() == "sentinel"
    finally:
        ws2.close()
