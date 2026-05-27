"""A2A canary peer — minimal FastAPI echo agent for integration testing.

Public surface:
  - main.app   — FastAPI application instance

Import ``app.a2a_canary.main`` to access the ASGI app or run via uvicorn.
"""

__all__ = [
    "main",
]
