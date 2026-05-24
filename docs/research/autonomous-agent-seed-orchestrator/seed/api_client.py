"""
Anthropic API client wrapper.

Selects between AsyncAnthropicVertex (CLAUDE_CODE_USE_VERTEX=1) and
AsyncAnthropic (direct API) at construction. Provides:
  - exponential backoff with jitter on 429 / 5xx / connection errors
  - ephemeral prompt caching on system & tools blocks
  - usage tracking → UsageRecord (tokens + cached tokens + USD)
  - hard concurrency limit via asyncio.Semaphore
"""

from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated, TypeAlias

# ── SDK import (defensive: callers may run unit tests without anthropic) ─
try:
    from anthropic import (
        APIConnectionError,
        APIStatusError,
        AsyncAnthropic,
        AsyncAnthropicVertex,
        RateLimitError,
    )

    _HAS_SDK = True
except ImportError:  # pragma: no cover - keeps schema imports clean
    _HAS_SDK = False
    AsyncAnthropic = None  # type: ignore[assignment]
    AsyncAnthropicVertex = None  # type: ignore[assignment]
    APIStatusError = Exception  # type: ignore[assignment,misc]
    APIConnectionError = Exception  # type: ignore[assignment,misc]
    RateLimitError = Exception  # type: ignore[assignment,misc]


ModelID: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^claude-(?:opus|sonnet|haiku)-[0-9]+-[0-9]+(?:-[a-z0-9-]+)?$"),
]


# ── Pricing (USD per 1M tokens; update as Anthropic publishes new rates) ─
# Cached input is billed at a discount; cache-write at a small surcharge.
_PRICE_TABLE: dict[str, dict[str, float]] = {
    # claude-opus-4-7 baseline (placeholder values — recompute when Anthropic
    # publishes finalized pricing; the table is the only place to update).
    "claude-opus-4-7": {"in": 15.00, "out": 75.00, "cache_w": 18.75, "cache_r": 1.50},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00, "cache_w": 3.75, "cache_r": 0.30},
    "claude-haiku-4-5": {"in": 0.80, "out": 4.00, "cache_w": 1.00, "cache_r": 0.08},
}


def _price_for(model: str) -> dict[str, float]:
    """Return the per-1M-token price dict for `model`, with a safe default."""
    return _PRICE_TABLE.get(model, _PRICE_TABLE["claude-sonnet-4-6"])


@dataclass(slots=True, frozen=True)
class UsageRecord:
    request_id: str
    model: str
    tokens_in: int
    tokens_out: int
    cache_creation_in: int
    cache_read_in: int
    cost_usd: float
    latency_s: float


class CompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    request_id: str
    model: str
    text: str
    stop_reason: str
    usage: UsageRecord
    raw: dict[str, Any] = Field(default_factory=dict)


class AnthropicClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ModelID = "claude-opus-4-7"
    max_tokens: Annotated[int, Field(ge=256, le=200_000)] = 8_192
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.2

    # Concurrency / safety
    max_concurrent: Annotated[int, Field(ge=1, le=256)] = 8
    request_timeout_s: Annotated[float, Field(gt=0.0, le=3_600.0)] = 180.0

    # Retries
    max_retries: Annotated[int, Field(ge=0, le=10)] = 6
    backoff_base_s: Annotated[float, Field(gt=0.0, le=60.0)] = 1.5
    backoff_cap_s: Annotated[float, Field(gt=0.0, le=600.0)] = 45.0
    backoff_jitter: Annotated[float, Field(ge=0.0, le=1.0)] = 0.25

    # Vertex (used only if CLAUDE_CODE_USE_VERTEX is truthy)
    vertex_project: str | None = None
    vertex_region: str = "us-east5"


class AnthropicClient:
    """Async, thread-safe client. Construct once; reuse for the program's life.

    All public methods are coroutine-safe. Concurrency is bounded by a
    semaphore so the rest of the system can issue calls freely without
    worrying about overloading the upstream.
    """

    def __init__(self, config: AnthropicClientConfig | None = None) -> None:
        if not _HAS_SDK:
            raise RuntimeError(
                "anthropic SDK is not installed. "
                "Install with: pip install 'anthropic[vertex]>=0.40'"
            )
        self._config = config or AnthropicClientConfig()
        self._use_vertex = os.environ.get("CLAUDE_CODE_USE_VERTEX", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._client = self._build_client()
        self._sem = asyncio.Semaphore(self._config.max_concurrent)
        self._usage_log: deque[UsageRecord] = deque(maxlen=4096)
        self._cum_cost_usd: float = 0.0
        self._cum_lock = asyncio.Lock()

    def _build_client(self) -> Any:
        if self._use_vertex:
            project = (
                self._config.vertex_project
                or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
            )
            if not project:
                raise RuntimeError(
                    "Vertex selected (CLAUDE_CODE_USE_VERTEX=1) but no project "
                    "ID found. Set ANTHROPIC_VERTEX_PROJECT_ID, GOOGLE_CLOUD_PROJECT, "
                    "or AnthropicClientConfig.vertex_project."
                )
            return AsyncAnthropicVertex(
                project_id=project,
                region=self._config.vertex_region,
            )
        # direct API; SDK picks up ANTHROPIC_API_KEY automatically
        return AsyncAnthropic()

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def using_vertex(self) -> bool:
        return self._use_vertex

    @property
    def cumulative_cost_usd(self) -> float:
        return self._cum_cost_usd

    def recent_usage(self, n: int = 64) -> list[UsageRecord]:
        return list(self._usage_log)[-n:]

    # ── public surface ────────────────────────────────────────────────────

    async def complete(
        self,
        *,
        system: str | list[dict[str, Any]],
        user: str | list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop_sequences: list[str] | None = None,
        cache_system: bool = True,
    ) -> CompletionResult:
        """Single-turn completion with retry & prompt caching.

        `system` and `user` may be plain strings or pre-built block lists.
        When `cache_system=True`, the (string) system prompt is wrapped in a
        single ephemeral cache block — the next call with an identical
        system string within the cache TTL pays the cheap cache-read rate.
        """
        mdl = model or self._config.model
        max_tok = max_tokens or self._config.max_tokens
        temp = temperature if temperature is not None else self._config.temperature

        sys_blocks: list[dict[str, Any]]
        if isinstance(system, str):
            sys_blocks = [{"type": "text", "text": system}]
            if cache_system and len(system) >= 1024:
                sys_blocks[0]["cache_control"] = {"type": "ephemeral"}
        else:
            sys_blocks = list(system)

        if isinstance(user, str):
            user_blocks: list[dict[str, Any]] = [{"type": "text", "text": user}]
        else:
            user_blocks = list(user)

        request_id = uuid.uuid4().hex
        payload: dict[str, Any] = {
            "model": mdl,
            "max_tokens": max_tok,
            "temperature": temp,
            "system": sys_blocks,
            "messages": [{"role": "user", "content": user_blocks}],
        }
        if stop_sequences:
            payload["stop_sequences"] = list(stop_sequences)

        result = await self._call_with_retries(request_id, payload)
        return result

    # ── internal: retries, parsing, accounting ────────────────────────────

    async def _call_with_retries(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> CompletionResult:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self._config.max_retries:
            attempt += 1
            t0 = time.monotonic()
            try:
                async with self._sem:
                    resp = await asyncio.wait_for(
                        self._client.messages.create(**payload),
                        timeout=self._config.request_timeout_s,
                    )
            except asyncio.TimeoutError as e:
                last_exc = e
            except RateLimitError as e:
                last_exc = e
            except APIStatusError as e:
                status = getattr(e, "status_code", None)
                if status is None or status < 500:
                    # 4xx (other than rate-limit) → don't retry; the request is bad.
                    raise
                last_exc = e
            except APIConnectionError as e:
                last_exc = e
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Unknown error class — retry conservatively.
                last_exc = e
            else:
                latency_s = time.monotonic() - t0
                return self._materialize(request_id, payload["model"], resp, latency_s)

            if attempt > self._config.max_retries:
                break
            await asyncio.sleep(self._backoff_delay(attempt))

        assert last_exc is not None
        raise last_exc

    def _backoff_delay(self, attempt: int) -> float:
        base = min(
            self._config.backoff_cap_s,
            self._config.backoff_base_s * (2 ** (attempt - 1)),
        )
        jitter = base * self._config.backoff_jitter * (2 * random.random() - 1)
        return max(0.05, base + jitter)

    def _materialize(
        self,
        request_id: str,
        model: str,
        resp: Any,
        latency_s: float,
    ) -> CompletionResult:
        # Extract text from the first text block; concatenate if multiple.
        text_parts: list[str] = []
        for block in getattr(resp, "content", []):
            t = getattr(block, "type", None)
            if t == "text":
                text_parts.append(getattr(block, "text", "") or "")
        text = "".join(text_parts)

        usage_in = int(getattr(resp.usage, "input_tokens", 0) or 0)
        usage_out = int(getattr(resp.usage, "output_tokens", 0) or 0)
        cache_w = int(getattr(resp.usage, "cache_creation_input_tokens", 0) or 0)
        cache_r = int(getattr(resp.usage, "cache_read_input_tokens", 0) or 0)

        prices = _price_for(model)
        cost = (
            (usage_in - cache_w - cache_r) * prices["in"]
            + cache_w * prices["cache_w"]
            + cache_r * prices["cache_r"]
            + usage_out * prices["out"]
        ) / 1_000_000.0
        cost = max(0.0, cost)  # cache_w/r safety: never go negative

        usage = UsageRecord(
            request_id=request_id,
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            cache_creation_in=cache_w,
            cache_read_in=cache_r,
            cost_usd=cost,
            latency_s=latency_s,
        )
        self._usage_log.append(usage)
        # cumulative cost update is not on the hot path; fire-and-forget.
        asyncio.create_task(self._accumulate_cost(cost))

        return CompletionResult(
            request_id=request_id,
            model=model,
            text=text,
            stop_reason=str(getattr(resp, "stop_reason", "")),
            usage=usage,
            raw={"id": getattr(resp, "id", None)},
        )

    async def _accumulate_cost(self, delta: float) -> None:
        async with self._cum_lock:
            self._cum_cost_usd += delta

    async def aclose(self) -> None:
        """Close the underlying SDK client (best-effort)."""
        close = getattr(self._client, "close", None)
        if close is None:
            return
        try:
            res = close()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass
