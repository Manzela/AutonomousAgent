from __future__ import annotations

import numpy as np

from app.core.embedder import AbstractEmbedder


class SentenceTransformerEmbedder(AbstractEmbedder):
    """Model-backed embedder using the sentence-transformers library.

    Loads a model from disk or the Hugging Face Hub on construction.
    Suitable for local development and on-prem deploys where a GPU or
    large CPU is available.  For serverless / Cloud Run use
    ``app.adapters.gcp.VertexEmbeddingsEmbedder`` instead.

    Moved from ``app.adapters.inmemory`` (P3-3): this class is NOT in-memory —
    it loads a real model — so it belongs in its own adapter tier.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", dim: int = 384) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Add it to your pyproject.toml when you swap embedders."
            ) from e
        self._model = SentenceTransformer(model_name)
        # Validate declared dim matches the model's actual output dimension so
        # that callers (e.g. memory stores) never see a silent dim mismatch.
        probe: np.ndarray = np.asarray(
            self._model.encode(["dim-probe"], normalize_embeddings=True)[0],
            dtype=np.float32,
        )
        actual_dim = probe.shape[0]
        if actual_dim != dim:
            raise ValueError(
                f"SentenceTransformerEmbedder(model_name={model_name!r}, dim={dim}) — "
                f"declared dim {dim} does not match model output dim {actual_dim}. "
                f"Pass dim={actual_dim} or choose a different model."
            )
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> np.ndarray:  # pragma: no cover - exercised only when SDK present
        vec = self._model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(vec, dtype=np.float32)
