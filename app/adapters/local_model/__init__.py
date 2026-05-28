"""Local-model adapter implementations.

These adapters load ML models from the local filesystem or the Hugging Face
Hub. They are NOT in-memory stubs (they have real model dependencies) but are
also NOT GCP-backed (no Vertex AI calls).

  - embedder — SentenceTransformerEmbedder (sentence-transformers library)

Suitable for: local development with GPU, on-prem deploys, staging with
model-cache mounts. NOT suitable for serverless / Cloud Run deployments;
use ``app.adapters.gcp.VertexEmbeddingsEmbedder`` there.
"""

__all__ = [
    "embedder",
]
