import pytest
from unittest.mock import MagicMock, patch
from lib.trajectory.shipper import TrajectoryShipper, ModelArmorSanitizeUnavailable


def test_ship_trajectory_dispatches_f37_on_sanitize_unavailable():
    shipper = TrajectoryShipper.__new__(TrajectoryShipper)
    shipper._sanitize_client = MagicMock()
    shipper._sanitize_client.sanitize.side_effect = ModelArmorSanitizeUnavailable("forced")
    shipper.template = "x"
    shipper.bucket = "test-bucket"
    shipper._gcs_client = MagicMock()

    with patch("lib.durability.handlers.dispatch") as mock_dispatch:
        with pytest.raises(ModelArmorSanitizeUnavailable):
            shipper.ship_trajectory("sess-abc", [{"role": "user", "content": "hi"}])
        assert mock_dispatch.call_count == 1
        args, kwargs = mock_dispatch.call_args
        assert args[0] == "F37"
        assert kwargs["payload"]["shipper"] == "trajectory"
        assert kwargs["payload"]["session_id"] == "sess-abc"


def test_ship_trajectory_uploaded_blob_is_sanitized_not_raw():
    """Contract test: blob content MUST be the sanitized payload."""
    shipper = TrajectoryShipper.__new__(TrajectoryShipper)
    sanitized = '{"sanitized": true}'
    fake_response = MagicMock()
    fake_response.sanitized_content = sanitized
    shipper._sanitize_client = MagicMock()
    shipper._sanitize_client.sanitize.return_value = fake_response
    shipper.template = "x"
    shipper.bucket = "test-bucket"
    shipper._gcs_client = MagicMock()
    fake_blob = MagicMock()
    shipper._gcs_client.bucket.return_value.blob.return_value = fake_blob

    shipper.ship_trajectory("sess-xyz", [{"role": "user", "content": "PII?"}])
    fake_blob.upload_from_string.assert_called_once()
    uploaded = fake_blob.upload_from_string.call_args[0][0]
    assert uploaded == sanitized, "uploaded blob must be the sanitized payload, not raw"
