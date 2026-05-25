"""Embedder ABC + dimension-projection helper.

For production-scale fleets (>500 active agents) swap to
``VertexEmbeddingsEmbedder`` wrapping ``text-embedding-005``
(see ``04-gcp-native-adapter-plan.md`` P-9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import numpy as np


class AbstractEmbedder(ABC):
    """Map text (or batches of text) to fixed-dim L2-normalised float vectors."""

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    def embed(self, text: str) -> np.ndarray: ...

    def embed_many(self, texts: Iterable[str]) -> np.ndarray:
        return np.stack([self.embed(t) for t in texts], axis=0)


def project_dim(vec: np.ndarray, target_dim: int) -> np.ndarray:
    """Trivial projection: truncate or zero-pad to target_dim, then renormalise.

    Used by the state-vector builder when an embedder's native dim doesn't
    match the router's expected `capability_dim`. A learned projection
    would be better; this keeps the seed dependency-free.
    """
    src = vec.astype(np.float32)
    if src.shape[0] == target_dim:
        out = src
    elif src.shape[0] > target_dim:
        out = src[:target_dim]
    else:
        out = np.zeros(target_dim, dtype=np.float32)
        out[: src.shape[0]] = src
    norm = float(np.linalg.norm(out))
    if norm > 0.0:
        out = out / norm
    return out
