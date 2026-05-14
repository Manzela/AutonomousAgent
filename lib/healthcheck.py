"""Health check helper. Used both by hermes-agent's internal /health endpoint and the
external healthchecks-ping cron script.

Returns a structured HealthReport that classifies each checked dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

import httpx


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    latency_ms: float | None = None


@dataclass
class HealthReport:
    overall: Status
    checks: list[CheckResult] = field(default_factory=list)


async def _http_check(
    client: httpx.AsyncClient, name: str, url: str, timeout: float = 3.0
) -> CheckResult:
    try:
        import time

        start = time.perf_counter()
        r = await client.get(url, timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        if r.status_code < 500:
            return CheckResult(name, Status.OK, f"http {r.status_code}", elapsed)
        return CheckResult(name, Status.DEGRADED, f"http {r.status_code}", elapsed)
    except Exception as e:
        return CheckResult(name, Status.DOWN, repr(e))


async def run_checks(deps: dict[str, str]) -> HealthReport:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_http_check(client, n, u) for n, u in deps.items()])
    if all(r.status == Status.OK for r in results):
        overall = Status.OK
    elif any(r.status == Status.DOWN for r in results):
        overall = Status.DOWN
    else:
        overall = Status.DEGRADED
    return HealthReport(overall=overall, checks=results)
