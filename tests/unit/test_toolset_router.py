"""Tests for the toolset router."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.toolset_router import Tier, ToolsetRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLSETS = REPO_ROOT / "config" / "toolsets.yaml"


@pytest.fixture(scope="module")
def router() -> ToolsetRouter:
    return ToolsetRouter.from_config(TOOLSETS)


@pytest.mark.parametrize(
    "tool,expected_tier",
    [
        ("read_file", Tier.IN_PROCESS),
        ("ls", Tier.IN_PROCESS),
        ("grep", Tier.IN_PROCESS),
        ("rg", Tier.IN_PROCESS),
        ("shell", Tier.SHELL_SANDBOX),
        ("git", Tier.SHELL_SANDBOX),
        ("browser_navigate", Tier.BROWSER_SANDBOX),
        ("browser_click", Tier.BROWSER_SANDBOX),
        ("playwright_screenshot", Tier.BROWSER_SANDBOX),
        ("github_create_pull_request", Tier.EXTERNAL_HTTPS),
        ("context7_query", Tier.EXTERNAL_HTTPS),
        ("run_python", Tier.CLOUD_SANDBOX),
        ("exec_code", Tier.CLOUD_SANDBOX),
    ],
)
def test_known_tools_routed_correctly(router, tool, expected_tier):
    assert router.resolve(tool) == expected_tier


def test_unknown_tool_falls_to_default(router):
    assert router.resolve("never_seen_before_tool") == Tier.SHELL_SANDBOX


def test_glob_matching_for_browser_prefix(router):
    assert router.resolve("browser_anything_at_all") == Tier.BROWSER_SANDBOX
