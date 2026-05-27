"""AutonomousAgent application package.

Public surface:
  - app.core      — abstract base classes (ABCs) and domain schemas
  - app.adapters  — concrete implementations (gcp, inmemory, local_model)
  - app.a2a_canary — echo peer for A2A integration testing
"""

__all__ = [
    "core",
    "adapters",
    "a2a_canary",
]
