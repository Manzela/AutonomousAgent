"""A2A sender — outbound JSON-RPC 2.0 client (Day 3).

Per spike-plan.md §Day 3:
- `send_message(peer_url, message)` → Task dict (SUBMITTED or later state)
- `get_task(peer_url, task_id)` → Task dict
- `cancel_task(peer_url, task_id)` → Task dict (CANCELED or error)
- `A2AError` hierarchy mapping JSON-RPC error codes to Python exceptions
- httpx.AsyncClient with timeout + exponential-backoff retry on transient errors

Day 4 adds `stream_message(...)` via httpx-sse.
Day 5 wires `mint_token()` into the `agent_identity` path.
Day 6 adds OTel `traceparent` header injection.

Spec reference: §6.4 (request envelope), §7.6.1 (message/send), §5.4 (error codes).
Pinned spec: e997516542bd6e3a12ecb6b4939aa0bae3b13a21
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

try:
    from opentelemetry import propagate as _otel_propagate
    from opentelemetry import trace as _otel_trace

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_AVAILABLE = False
    _otel_propagate = None  # type: ignore[assignment]
    _otel_trace = None  # type: ignore[assignment]

# --- Error hierarchy ---------------------------------------------------------

_JSONRPC_TO_EXC: dict[int, type["A2AError"]] = {}


class A2AError(Exception):
    """Base for all A2A protocol errors. Carries the raw JSON-RPC error dict."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def __init_subclass__(cls, code: int | None = None, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if code is not None:
            _JSONRPC_TO_EXC[code] = cls


class A2ATaskNotFound(A2AError, code=-32001):
    """Task ID not found on the remote agent."""


class A2ATaskNotCancelable(A2AError, code=-32002):
    """Task exists but is in a terminal state and cannot be canceled."""


class A2APushNotificationNotSupported(A2AError, code=-32003):
    """Remote agent does not support push notifications."""


class A2AUnsupportedOperation(A2AError, code=-32004):
    """Method not yet implemented by the remote agent."""


class A2AContentTypeNotSupported(A2AError, code=-32005):
    """Content-type in the request parts not accepted by the remote agent."""


class A2AInvalidAgentResponse(A2AError, code=-32006):
    """Remote agent's response does not conform to the Task schema."""


class A2ARPCError(A2AError):
    """Catch-all for JSON-RPC standard errors (-32700 to -32600) and unknowns."""


def _raise_for_error(error: dict[str, Any]) -> None:
    """Map a JSON-RPC error object to the appropriate A2AError subclass."""
    code: int = error.get("code", 0)
    message: str = error.get("message", "unknown error")
    data: Any = error.get("data")
    exc_cls = _JSONRPC_TO_EXC.get(code, A2ARPCError)
    raise exc_cls(code, message, data)


# --- Retry policy ------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """POST with exponential-backoff retry on transient transport errors.

    Only retries on httpx.TransportError (connection refused, reset, etc.).
    4xx / JSON-RPC application errors are NOT retried — they are deterministic.
    """
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.post(url, json=payload, timeout=timeout, headers=headers or {})
        except httpx.TransportError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            logger.warning(
                "a2a: transient error (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                _MAX_RETRIES,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


# --- OTel helpers ------------------------------------------------------------


def _build_otel_headers() -> dict[str, str]:
    """Return headers dict with W3C traceparent/tracestate from the active span.

    Returns {} when: OTel not installed, no active span, NonRecordingSpan, or
    the span is not sampled (SAMPLED bit unset in TraceFlags).
    Never force-samples. Sampled bit read verbatim from TraceFlags.
    """
    if not _OTEL_AVAILABLE:
        return {}
    span = _otel_trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return {}
    if not ctx.trace_flags.sampled:
        return {}
    headers: dict[str, str] = {}
    _otel_propagate.inject(headers)
    return headers


# --- Envelope helpers --------------------------------------------------------


def _build_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }


async def _call(
    client: httpx.AsyncClient,
    peer_url: str,
    method: str,
    params: dict[str, Any],
    timeout: float,
) -> Any:
    """Build envelope, POST with OTel traceparent, decode result."""
    payload = _build_request(method, params)
    otel_headers = _build_otel_headers()
    resp = await _post_with_retry(client, peer_url, payload, timeout, headers=otel_headers)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        _raise_for_error(body["error"])
    return body["result"]


# --- Public API --------------------------------------------------------------


async def send_message(
    peer_url: str,
    message: dict[str, Any],
    *,
    agent_identity: Any = None,  # reserved for Day 5 JWT wiring
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a message/send request to a peer and return the resulting Task.

    Args:
        peer_url: Base URL of the peer's A2A endpoint (e.g. "http://host:9001/").
        message: A2A Message object with at least a ``parts`` list.
        agent_identity: Unused until Day 5; pass ``None`` for the spike.
        timeout: httpx total request timeout in seconds.

    Returns:
        Task dict with at least ``{"id": "<task-id>", "status": "SUBMITTED"}``.

    Raises:
        A2AUnsupportedOperation: peer does not support message/send.
        A2AInvalidAgentResponse: peer's response is malformed.
        A2ARPCError: other JSON-RPC error.
        httpx.HTTPStatusError: non-2xx HTTP response after retries.
        httpx.TransportError: connection failure after retries exhausted.
    """
    if agent_identity is not None:  # pragma: no cover — Day 5
        logger.debug("a2a: agent_identity provided but not yet used (Day 5)")

    async with httpx.AsyncClient() as client:
        result = await _call(
            client,
            peer_url.rstrip("/") + "/",
            "message/send",
            {"message": message},
            timeout,
        )

    if not isinstance(result, dict) or "id" not in result:
        raise A2AInvalidAgentResponse(
            -32006,
            f"peer returned malformed Task: {result!r}",
        )
    return result


async def get_task(
    peer_url: str,
    task_id: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Retrieve the current state of a Task from a peer.

    Args:
        peer_url: Base URL of the peer's A2A endpoint.
        task_id: Opaque task identifier returned by ``send_message``.
        timeout: httpx total request timeout in seconds.

    Returns:
        Task dict with current status.

    Raises:
        A2ATaskNotFound: task ID not found on the peer.
        A2AUnsupportedOperation: peer does not implement tasks/get (stub).
    """
    async with httpx.AsyncClient() as client:
        result = await _call(
            client,
            peer_url.rstrip("/") + "/",
            "tasks/get",
            {"id": task_id},
            timeout,
        )
    return result  # type: ignore[return-value]


async def cancel_task(
    peer_url: str,
    task_id: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Request cancellation of a running Task on a peer.

    Args:
        peer_url: Base URL of the peer's A2A endpoint.
        task_id: Opaque task identifier.
        timeout: httpx total request timeout in seconds.

    Returns:
        Updated Task dict (status may be CANCELED or unchanged if
        cancellation is not immediate).

    Raises:
        A2ATaskNotFound: task ID not found.
        A2ATaskNotCancelable: task is in a terminal state.
    """
    async with httpx.AsyncClient() as client:
        result = await _call(
            client,
            peer_url.rstrip("/") + "/",
            "tasks/cancel",
            {"id": task_id},
            timeout,
        )
    return result  # type: ignore[return-value]
