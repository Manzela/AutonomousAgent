"""Tests for healthcheck.py."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from lib.healthcheck import Status, run_checks


@pytest.mark.asyncio
async def test_all_ok(mocker: MockerFixture):
    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, timeout):
            return FakeResponse()

    mocker.patch("lib.healthcheck.httpx.AsyncClient", return_value=FakeClient())
    report = await run_checks({"chroma": "http://x", "honcho": "http://y"})
    assert report.overall == Status.OK
    assert len(report.checks) == 2


@pytest.mark.asyncio
async def test_one_down(mocker: MockerFixture):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, timeout):
            if "chroma" in url:
                raise Exception("connection refused")

            class R:
                status_code = 200

            return R()

    mocker.patch("lib.healthcheck.httpx.AsyncClient", return_value=FakeClient())
    report = await run_checks({"chroma": "http://chroma", "honcho": "http://honcho"})
    assert report.overall == Status.DOWN
