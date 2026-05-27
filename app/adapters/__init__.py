"""Adapter implementations for app/core abstractions.

Subpackages:
  - gcp        — production adapters backed by Google Cloud services
  - inmemory   — in-process adapters for unit/CI tests
  - local_model — adapters that load local ML models (non-GCP, non-in-memory)
"""

__all__ = [
    "gcp",
    "inmemory",
    "local_model",
]
