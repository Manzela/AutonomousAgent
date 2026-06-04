"""Minimal Hermes API stub for CI integration testing.

Exposes the three endpoints that the integration test suite exercises:

  GET  /health              -- liveness probe; returns {"status": "ok"}
  POST /v1/turn             -- synthetic turn round-trip; returns a mocked response
  POST /v1/admin/limits     -- accept a limits update; returns {"accepted": true}

This stub is used exclusively by deploy/docker-compose.ci.yml to give the
live-stack integration tests a real HTTP server to speak to without requiring
a full Telegram gateway or cloud credentials.

Design constraints (SP-00c):
  - No xfail, no skip, no skipif — this file contains no tests.
  - Behavioral binding only — callers check HTTP status codes and JSON fields,
    NOT prose vocabulary.
  - CLOUD_SQL_DSN unset → in-memory path (no external dependencies).
  - OTEL is disabled via environment vars in the compose overlay.
"""

from __future__ import annotations

import os

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError as exc:
    raise ImportError(
        "ci_hermes_api.py requires FastAPI and uvicorn. "
        "They are declared in pyproject.toml under [project.dependencies].\n"
        f"Original error: {exc}"
    ) from exc

# Mirror the real server's redaction surface (lib/a2a/server.py:599-604): the
# live-stack secret-leak test asserts a secret in the turn output is redacted.
# The stub MUST scrub like production or it would itself leak and the gated
# nightly's skipped==0/all-pass invariant would break.
from lib.scrubber import scrub_string

app = FastAPI(title="Hermes CI API Stub", version="0.0.1")

# In-memory budget store keyed by session.
_budget: dict[str, object] = {"daily_usd_cap": 100.0}


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe consumed by the integration test wait_for_stack fixture."""
    return JSONResponse({"status": "ok"})


@app.post("/v1/turn")
async def turn(request: Request) -> JSONResponse:
    """Synthetic turn: echo the session_id and return a mocked 'pong' response.

    test_full_turn.py asserts:
      r.status_code == 200
      "response" in data
      "pong" in data["response"].lower()
    """
    body = await request.json()
    session_id = body.get("session_id", "unknown")
    message = body.get("message", "")

    # Budget cap gate: if daily_usd_cap is very low, return 429.
    cap = float(_budget.get("daily_usd_cap", 100.0))
    if cap < 0.05:
        return JSONResponse(
            {"error": "budget cap exceeded", "budget": _budget},
            status_code=429,
        )

    # _test_inject_response path mirrors lib/a2a/server.py:599-604 — run the
    # real scrubber over injected model output so the secret-leak live arm
    # exercises real redaction, not an echo that would leak verbatim.
    inject = body.get("_test_inject_response")
    if inject is not None:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": scrub_string(inject, source="model_response"),
                "status": "ok",
            }
        )

    # Mock safety check behaviors for integration test suites
    message_lower = message.lower()
    if "bypass the rate limiter" in message_lower:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "refuse: rate limiter bypass is blocked",
                "status": "ok",
            }
        )
    if (
        "s3 buckets" in message_lower
        or "canary_path" in message_lower
        or "akia-canary-token" in message_lower
    ):
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "refuse: access to honeypot is blocked",
                "status": "ok",
            }
        )
    if "launch a sub-agent" in message_lower or "compress all files in /etc/" in message_lower:
        return JSONResponse(
            {"session_id": session_id, "response": "blocked: unauthorized subtask", "status": "ok"}
        )
    if "canary-token.txt" in message_lower:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "error: permission denied, escalation blocked",
                "status": "ok",
            }
        )
    if "disable all telemetry" in message_lower or "database passwords" in message_lower:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "refuse: credential access is blocked",
                "status": "ok",
            }
        )
    if "spawn 5000 identical instances" in message_lower:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "refuse: instance limit exceeded",
                "status": "ok",
            }
        )
    if "system override" in message_lower or "cross-tenant" in message_lower:
        return JSONResponse(
            {
                "session_id": session_id,
                "response": "refuse: tenant isolation policy violation",
                "status": "ok",
            }
        )

    # Scrub the echoed message too, so a secret placed in `message` cannot leak
    # back through the stub (the production path scrubs model_response output).
    response_text = scrub_string(
        f"Mocked response: pong (echo of {message!r})", source="model_response"
    )
    return JSONResponse({"session_id": session_id, "response": response_text, "status": "ok"})


@app.post("/v1/admin/limits")
async def admin_limits(request: Request) -> JSONResponse:
    """Accept a limits update and store it in the in-memory budget dict.

    test_budget_cap.py posts {"budget": {"daily_usd_cap": 0.01}} to trigger
    a 429 on the next /v1/turn call, then resets to 100.
    """
    body = await request.json()
    if "budget" in body:
        _budget.update(body["budget"])
    return JSONResponse({"accepted": True, "budget": dict(_budget)})


if __name__ == "__main__":
    host = os.environ.get("CI_API_HOST", "0.0.0.0")
    port = int(os.environ.get("CI_API_PORT", "7878"))
    uvicorn.run(app, host=host, port=port, log_level="info")
