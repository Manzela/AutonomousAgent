"""Tool → sandbox-tier router.

Reads config/toolsets.yaml and resolves tool names to sandbox tiers using
glob-style match (first match wins, fnmatch semantics).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class Tier(str, Enum):
    IN_PROCESS = "in_process"
    SHELL_SANDBOX = "shell_sandbox"
    BROWSER_SANDBOX = "browser_sandbox"
    EXTERNAL_HTTPS = "external_https"
    CLOUD_SANDBOX = "cloud_sandbox"


@dataclass(frozen=True)
class Route:
    patterns: tuple[str, ...]
    tier: Tier
    evaluate_after: bool


class ToolsetRouter:
    def __init__(self, routes: list[Route], default_tier: Tier):
        self._routes = routes
        self._default = default_tier

    @classmethod
    def from_config(cls, config_path: Path) -> ToolsetRouter:
        with config_path.open() as f:
            data = yaml.safe_load(f)
        routes = [
            Route(
                patterns=tuple(r["match"]),
                tier=Tier(r["tier"]),
                evaluate_after=bool(r.get("evaluate_after", True)),
            )
            for r in data.get("routes", [])
        ]
        default = Tier(data.get("default_tier", "shell_sandbox"))
        return cls(routes, default)

    def is_evaluation_eligible(self, tool_name: str) -> bool:
        for route in self._routes:
            for pattern in route.patterns:
                if fnmatch.fnmatchcase(tool_name, pattern):
                    return route.evaluate_after
        return True

    def resolve(self, tool_name: str) -> Tier:
        for route in self._routes:
            for pattern in route.patterns:
                if fnmatch.fnmatchcase(tool_name, pattern):
                    return route.tier
        return self._default
