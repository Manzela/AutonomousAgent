from unittest.mock import MagicMock, patch

from app.adapters.gcp.embedder import VertexEmbeddingsEmbedder
from google.api_core import exceptions


def test_vertex_embedder_dimension():
    embedder = VertexEmbeddingsEmbedder()
    assert embedder.dim == 256


@patch("app.adapters.gcp.embedder.aiplatform.gapic.PredictionServiceClient")
def test_vertex_embedder_embed_many_success(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Setup mock response
    mock_response = MagicMock()
    mock_prediction = MagicMock()
    mock_prediction.get.return_value = [
        0.1
    ] * 768  # simulate 768-dim output from text-embedding-005
    mock_response.predictions = [mock_prediction, mock_prediction]
    mock_client.predict.return_value = mock_response

    embedder = VertexEmbeddingsEmbedder()
    vectors = embedder.embed_many(["hello", "world"])

    assert vectors.shape == (2, 256)  # projected to 256
    mock_client.predict.assert_called_once()

    kwargs = mock_client.predict.call_args.kwargs
    assert kwargs["instances"] == [{"content": "hello"}, {"content": "world"}]
    assert kwargs["parameters"] == {"outputDimensionality": 256}


@patch("app.adapters.gcp.embedder.aiplatform.gapic.PredictionServiceClient")
def test_vertex_embedder_retry_semantics(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_response = MagicMock()
    mock_prediction = MagicMock()
    mock_prediction.get.return_value = [0.1] * 256
    mock_response.predictions = [mock_prediction]

    # Fail first, then succeed
    mock_client.predict.side_effect = [exceptions.ServiceUnavailable("unavailable"), mock_response]

    embedder = VertexEmbeddingsEmbedder()

    with patch("time.sleep", return_value=None):
        vectors = embedder.embed("test")

    assert vectors.shape == (256,)
    assert mock_client.predict.call_count == 2


@patch("app.adapters.gcp.embedder.aiplatform.gapic.PredictionServiceClient")
def test_vertex_embedder_embed_many_scrubs_secrets(mock_client_cls, monkeypatch):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_response = MagicMock()
    mock_prediction = MagicMock()
    mock_prediction.get.return_value = [0.1] * 256
    mock_response.predictions = [mock_prediction]
    mock_client.predict.return_value = mock_response

    # Force scrubber load
    from pathlib import Path

    monkeypatch.setenv(
        "SCRUBBER_PATTERNS_PATH", str(Path.cwd() / "config" / "scrubber-patterns.yaml")
    )
    from lib.scrubber import _reset_singleton_for_tests

    _reset_singleton_for_tests()

    embedder = VertexEmbeddingsEmbedder()
    embedder.embed_many(["my secret token is AKIA1234567890123456"])  # pragma: allowlist secret

    mock_client.predict.assert_called_once()
    kwargs = mock_client.predict.call_args.kwargs
    content = kwargs["instances"][0]["content"]
    assert "AKIA1234567890123456" not in content  # pragma: allowlist secret
    assert "[REDACTED:aws_access_key_id]" in content
