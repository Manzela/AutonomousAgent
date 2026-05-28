from __future__ import annotations
import hashlib

import numpy as np

from app.core.embedder import AbstractEmbedder


class HashingEmbedder(AbstractEmbedder):
    """Token-hashing embedder: deterministic, dependency-free, fast.

    Pipeline:
      1. lowercase + word-split the text (very simple tokeniser; fine for
         capability descriptions and short summaries)
      2. for each token, hash it with SHA-256 and use the digest bytes to
         derive a (bucket_index, sign) pair
      3. accumulate ±1 contributions in the bucket vector
      4. L2-normalise

    Collisions exist but cancel on average; for the 256-dim default this
    gives stable cosine similarities for vocab up to a few thousand tokens,
    which is sufficient for capability vectors and task summaries.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0 or (dim & (dim - 1)) != 0:
            # Power-of-two helps the bucket modulo distribute uniformly.
            raise ValueError(f"dim must be a positive power of two, got {dim}")
        self._dim = dim
        self._mask = dim - 1

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self._dim, dtype=np.float32)
        if not text:
            return vec
        tokens = text.lower().split()
        for tok in tokens:
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "little") & self._mask
            sign = 1.0 if (h[4] & 0x01) else -1.0
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec


# SentenceTransformerEmbedder was moved to app.adapters.local_model.embedder (P3-3).
# Re-exported here for backward compatibility with existing imports.
from app.adapters.local_model.embedder import SentenceTransformerEmbedder  # noqa: E402, F401
