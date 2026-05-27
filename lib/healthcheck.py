"""Health check helper. Used both by hermes-agent's internal /health endpoint and the
external healthchecks-ping cron script.

Returns a structured HealthReport that classifies each checked dependency.

O-3 fix: run_checks() now accepts an optional deps dict (defaults to
_DEFAULT_DEPS so it can be called from the Docker HEALTHCHECK CMD without
arguments).  The default set covers the HTTP-probeable services that Hermes
depends on.  TCP-only services (Cloud SQL, Memorystore/Redis) are probed via
socket connect rather than HTTP.
"""

from __future__ import annotations

import asyncio
import socket
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

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict (used by W1.E verification command)."""
        return {
            "status": self.overall.value,
            **{c.name: {"status": c.status.value, "detail": c.detail} for c in self.checks},
        }


# O-3: Default dependency set — probe targets inside the compose network.
# HTTP services use /health or / (get < 500 = alive); TCP services use a
# lightweight socket connect.  Names match the audit expectation:
# "vertex, honcho, chroma, cloud_sql, memorystore".
# Values: "http:<url>" for HTTP probes; "tcp:<host>:<port>" for TCP probes.
_DEFAULT_DEPS: dict[str, str] = {
    "litellm_proxy": "http://litellm-proxy:4000/health",
    "otel_collector": "http://otel-collector:13133/",  # OTel contrib default health port
    "vertex": "http://litellm-proxy:4000/health/liveliness",  # Vertex reachable via proxy
    "honcho": "http://honcho:8001/health",
    "chroma": "http://chroma:8000/api/v1/heartbeat",
    "cloud_sql": "tcp:127.0.0.1:5432",  # Cloud SQL Auth Proxy
    "memorystore": "tcp:redis:6379",  # Redis / Memorystore
}


async def _http_check(
    client: httpx.AsyncClient, name: str, url: str, timeout: float = 3.0
) -> CheckResult:
    import time

    try:
        start = time.perf_counter()
        r = await client.get(url, timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        if r.status_code < 500:
            return CheckResult(name, Status.OK, f"http {r.status_code}", elapsed)
        return CheckResult(name, Status.DEGRADED, f"http {r.status_code}", elapsed)
    except Exception as e:
        return CheckResult(name, Status.DOWN, repr(e))


async def _tcp_check(name: str, host: str, port: int, timeout: float = 2.0) -> CheckResult:
    """Non-blocking TCP connectivity probe for services that don't speak HTTP."""
    import time

    try:
        start = time.perf_counter()
        loop = asyncio.get_event_loop()
        conn = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: socket.create_connection((host, port), timeout=timeout),
            ),
            timeout=timeout + 0.5,
        )
        elapsed = (time.perf_counter() - start) * 1000
        conn.close()
        return CheckResult(name, Status.OK, f"tcp connect {host}:{port}", elapsed)
    except Exception as e:
        return CheckResult(name, Status.DOWN, repr(e))


async def run_checks(deps: dict[str, str] | None = None) -> HealthReport:
    """Probe each dependency and return a HealthReport.

    Args:
        deps: Mapping of dep-name → probe-spec.  If None, uses _DEFAULT_DEPS.
              HTTP probe spec: ``"http:<url>"`` or bare URL starting with http.
              TCP probe spec:  ``"tcp:<host>:<port>"``.

    Returns:
        HealthReport with overall status and per-dep CheckResult list.
    """
    if deps is None:
        deps = _DEFAULT_DEPS

    tasks = []
    async with httpx.AsyncClient() as client:
        for name, spec in deps.items():
            if spec.startswith("tcp:"):
                _, host, port_s = spec.split(":", 2)
                tasks.append(_tcp_check(name, host, int(port_s)))
            else:
                url = spec[len("http:") :] if spec.startswith("http:http") else spec
                tasks.append(_http_check(client, name, url))
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[CheckResult] = []
    dep_names = list(deps.keys())
    for i, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            results.append(CheckResult(dep_names[i], Status.DOWN, repr(r)))
        else:
            results.append(r)
    if all(r.status == Status.OK for r in results):
        overall = Status.OK
    elif any(r.status == Status.DOWN for r in results):
        overall = Status.DOWN
    else:
        overall = Status.DEGRADED
    return HealthReport(overall=overall, checks=results)


def run_checks_sync(deps: dict[str, str] | None = None) -> HealthReport:
    """Synchronous wrapper around run_checks — usable from Docker HEALTHCHECK CMD."""
    return asyncio.run(run_checks(deps))


if __name__ == "__main__":
    import json
    import sys

    report = run_checks_sync()
    print(json.dumps(report.as_dict(), indent=2))
    sys.exit(0 if report.overall != Status.DOWN else 1)
