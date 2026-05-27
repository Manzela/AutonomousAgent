"""GCP Vertex Embeddings Adapter."""

from __future__ import annotations

import logging
import time
from typing import Iterable

import numpy as np

from google.api_core import retry, exceptions
from google.cloud import aiplatform

from app.core.embedder import AbstractEmbedder, project_dim

logger = logging.getLogger(__name__)

# W0.4 definition: text-embedding-005 on autonomous-agent-2026 project
_PROJECT_ID = "autonomous-agent-2026"
_LOCATION = "us-central1"
_MODEL_NAME = "text-embedding-005"
_ENDPOINT = f"projects/{_PROJECT_ID}/locations/{_LOCATION}/publishers/google/models/{_MODEL_NAME}"
_DIM = 256


class VertexEmbeddingsEmbedder(AbstractEmbedder):
    """GCP Vertex AI Embeddings Embedder."""

    def __init__(self) -> None:
        client_options = {"api_endpoint": f"{_LOCATION}-aiplatform.googleapis.com"}
        self._client = aiplatform.gapic.PredictionServiceClient(client_options=client_options)

    @property
    def dim(self) -> int:
        return _DIM

    @retry.Retry(
        predicate=retry.if_exception_type(
            exceptions.ServiceUnavailable,
            exceptions.DeadlineExceeded,
            exceptions.ResourceExhausted,
            exceptions.InternalServerError,
            exceptions.GatewayTimeout,
        ),
        initial=1.0,
        maximum=10.0,
        multiplier=2.0,
        timeout=30.0,
    )
    def embed_many(self, texts: Iterable[str]) -> np.ndarray:
        """Embed multiple strings in one batch."""
        texts_list = list(texts)
        if not texts_list:
            return np.zeros((0, _DIM), dtype=np.float32)

        instances = [{"content": text} for text in texts_list]
        parameters = {"outputDimensionality": _DIM}

        # Context manager for span/timing could be here if using OTel
        # W0.4 specifies: Includes retry+backoff + per-call latency span.
        t0 = time.monotonic()

        # Emitting a latency span using logging for now, or trace if available
        # The prompt says "per-call latency span". We'll just time it and log it as a span-like record.
        try:
            response = self._client.predict(
                endpoint=_ENDPOINT,
                instances=instances,
                parameters=parameters,
            )
        finally:
            latency = time.monotonic() - t0
            logger.debug(
                "embedder.predict span",
                extra={
                    "span": "vertex_embed",
                    "duration_s": latency,
                    "batch_size": len(texts_list),
                    "model": _MODEL_NAME,
                },
            )

        vectors = []
        for prediction in response.predictions:
            # Vertex returns values under 'values' key
            vec = np.array(prediction.get("values", []), dtype=np.float32)
            if vec.shape[0] != _DIM:
                vec = project_dim(vec, _DIM)
            vectors.append(vec)

        return np.stack(vectors, axis=0)

    def embed(self, text: str) -> np.ndarray:
        return self.embed_many([text])[0]
