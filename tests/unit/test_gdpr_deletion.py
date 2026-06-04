from unittest import mock
import os
from lib.clients.chroma import ChromaClient
from lib.clients.honcho import HonchoClient
from lib.clients.litellm_db import LiteLLMDb
from lib.trajectory.shipper import TrajectoryShipper


def test_chroma_delete_user_not_configured():
    # If no host/port/key configured, it still processes but fails gracefully or deletes locally
    client = ChromaClient()
    with mock.patch("httpx.Client.get") as mock_get:
        mock_get.return_value = mock.MagicMock(status_code=404)
        summary = client.delete_user("test-user")
        assert summary["status"] == "failed"


def test_chroma_delete_user_success():
    client = ChromaClient()
    # Mocking standard collections list and delete POST request
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock_client_cls.return_value.__enter__.return_value

        # 1. Mock collections list response
        mock_instance.get.return_value = mock.MagicMock(
            status_code=200, json=lambda: [{"id": "col-123", "name": "hermes_memory"}]
        )

        # 2. Mock delete document response
        mock_instance.post.return_value = mock.MagicMock(
            status_code=200, json=lambda: ["doc1", "doc2"]
        )

        summary = client.delete_user("test-user")
        assert summary["status"] == "completed"
        assert summary["deleted_count"] == 2
        assert len(summary["collections_processed"]) == 1
        assert summary["collections_processed"][0]["name"] == "hermes_memory"


def test_honcho_delete_user_not_configured():
    client = HonchoClient()
    with mock.patch.dict(os.environ, {}, clear=True):
        summary = client.delete_user("test-user")
        assert summary["status"] == "skipped"


def test_honcho_delete_user_success():
    client = HonchoClient()
    with mock.patch.dict(
        os.environ,
        {"HONCHO_API_KEY": "hch-test-key"},  # pragma: allowlist secret
    ):
        with mock.patch("httpx.Client.delete") as mock_delete:
            mock_delete.return_value = mock.MagicMock(status_code=204)
            summary = client.delete_user("test-user")
            assert summary["status"] == "completed"
            assert "v2/peers/test-user" in summary["deleted_resources"]


def test_litellm_delete_spend_logs_no_db():
    db = LiteLLMDb()
    with mock.patch.dict(os.environ, {}, clear=True):
        summary = db.delete_spend_logs("test-user")
        assert summary["status"] == "skipped"
        assert "No database connection string" in summary["reason"]


def test_litellm_delete_spend_logs_success():
    import sys

    db = LiteLLMDb()
    mock_conn = mock.MagicMock(name="mock_conn")
    mock_conn.__enter__.return_value = mock_conn

    mock_cursor = mock.MagicMock(name="mock_cursor")
    mock_cursor.__enter__.return_value = mock_cursor

    mock_conn.cursor.return_value = mock_cursor

    # Mock EXISTS for table check
    mock_cursor.fetchone.side_effect = [(True,), ("user", "end_user", "api_key", "user_id")]
    # Mock column names query
    mock_cursor.fetchall.return_value = [("user",), ("api_key",)]
    mock_cursor.rowcount = 5

    mock_psycopg = mock.MagicMock(name="mock_psycopg")
    mock_psycopg.connect.return_value = mock_conn

    with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
        with mock.patch.dict(os.environ, {"LITELLM_DB_URL": "postgresql://test"}):
            summary = db.delete_spend_logs("test-user")
            assert summary["status"] == "completed"
            assert summary["deleted_count"] == 5


def test_trajectory_delete_user_blobs():
    gcs_client = mock.MagicMock()

    # 2 blobs: one matching name, one matching contents, one not matching
    blob_name = mock.MagicMock()
    blob_name.name = "trajectory/2026-06-04/test-user-tool-1.json"

    blob_content = mock.MagicMock()
    blob_content.name = "trajectory/2026-06-04/tool-2.json"
    blob_content.download_as_bytes.return_value = b'{"user_id": "test-user"}'

    blob_no_match = mock.MagicMock()
    blob_no_match.name = "trajectory/2026-06-04/tool-3.json"
    blob_no_match.download_as_bytes.return_value = b'{"user_id": "other-user"}'

    gcs_client.list_blobs.side_effect = [[blob_name, blob_content, blob_no_match], []]

    with mock.patch.dict(os.environ, {"TRAJECTORY_BUCKET": "test-bucket"}):
        with mock.patch("lib.trajectory.shipper._default_gcs_client", return_value=gcs_client):
            summary = TrajectoryShipper.delete_user_blobs("test-user")
            assert summary["status"] == "completed"
            assert "trajectory/2026-06-04/test-user-tool-1.json" in summary["deleted_blobs"]
            assert "trajectory/2026-06-04/tool-2.json" in summary["deleted_blobs"]
            assert "trajectory/2026-06-04/tool-3.json" not in summary["deleted_blobs"]
