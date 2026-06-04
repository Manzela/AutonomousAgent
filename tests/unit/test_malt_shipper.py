from unittest import mock
from lib.trajectory.shipper import TrajectoryShipper


def test_malt_ship_trajectory():
    gcs_client = mock.MagicMock()
    sanitize_client = mock.MagicMock()
    sanitize_client.sanitize.return_value = (
        '{"sanitized_content": "{"session_id":"123","trajectory":[],"type":"malt_trajectory_v1"}"}'
    )

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="test-template",
        sanitize_client=sanitize_client,
        gcs_client=gcs_client,
    )

    shipper.ship_trajectory("123", [{"user": "hi"}, {"assistant": "hello"}])

    # Verify sanitize was called
    sanitize_client.sanitize.assert_called_once()

    # Verify GCS upload was called
    bucket = gcs_client.bucket.return_value
    blob = bucket.blob.return_value
    blob.upload_from_string.assert_called_once()
    assert bucket.blob.call_args[0][0] == "malt/trajectory/123.json"


def test_malt_replay_trajectory():
    gcs_client = mock.MagicMock()
    bucket = gcs_client.bucket.return_value
    blob = bucket.blob.return_value
    blob.exists.return_value = True
    blob.download_as_string.return_value = b'{"trajectory": [{"turn": 1}]}'

    shipper = TrajectoryShipper(
        bucket="test-bucket", template="test-template", gcs_client=gcs_client
    )

    traj = shipper.replay_trajectory("123")
    assert traj == [{"turn": 1}]
