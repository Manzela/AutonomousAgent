"""GCP Vertex Embeddings Adapter."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

import numpy as np

import typing

if typing.TYPE_CHECKING:
    from google.api_core import retry, exceptions
    from google.cloud import aiplatform

    _HAS_AIPLATFORM = True
else:
    _MISSING_DEPS: list[str] = []
    try:
        from google.api_core import retry as _retry, exceptions as _exceptions
        from google.cloud import aiplatform as _aiplatform

        retry: typing.Any = _retry
        exceptions: typing.Any = _exceptions
        aiplatform: typing.Any = _aiplatform
    except ImportError:  # pragma: no cover
        retry = None
        exceptions = None
        aiplatform = None
        _MISSING_DEPS.append("google-cloud-aiplatform")
    _HAS_AIPLATFORM = not _MISSING_DEPS

# Build a retry decorator that is safe to use at class-body evaluation time
# (i.e. when the module is imported) regardless of whether the GCP SDK is
# installed.  When the SDK is absent, ``retry`` is None and evaluating
# ``retry.Retry(...)`` inside the class body would raise AttributeError at
# import time — before any ``_HAS_AIPLATFORM`` guard can fire.
# The no-op lambda preserves the decorated function unchanged so that
# __init__ can raise ImportError on first instantiation attempt.
_retry_decorator: Any
if _HAS_AIPLATFORM:
    _retry_decorator = retry.Retry(
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
else:  # pragma: no cover
    _retry_decorator = lambda fn: fn  # noqa: E731 — identity; SDK absent

from app.core.embedder import AbstractEmbedder, project_dim  # noqa: E402

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
        if not _HAS_AIPLATFORM:
            raise ImportError(
                "google-cloud-aiplatform is not installed. Install with: uv sync --extra gcp"
            )
        client_options = {"api_endpoint": f"{_LOCATION}-aiplatform.googleapis.com"}
        self._client = aiplatform.gapic.PredictionServiceClient(client_options=client_options)

    @property
    def dim(self) -> int:
        return _DIM

    @_retry_decorator
    def embed_many(self, texts: Iterable[str]) -> np.ndarray:
        """Embed multiple strings in one batch."""
        from lib.scrubber import scrub_string

        texts_list = [scrub_string(text, source="vertex_embeddings_input") for text in texts]
        if not texts_list:
            return np.zeros((0, _DIM), dtype=np.float32)

        # Validate each element is a non-None string before sending to the API.
        # A None or non-str value produces an opaque server-side error; fail early.
        for idx, text in enumerate(texts_list):
            if not isinstance(text, str):
                raise TypeError(
                    f"embed_many() expects str elements; got {type(text).__name__!r} at index {idx}"
                )

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
        if not isinstance(text, str):
            raise TypeError(f"embed() expects a str; got {type(text).__name__!r}")
        return self.embed_many([text])[0]
