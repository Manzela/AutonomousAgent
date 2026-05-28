import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ModelSpec:
    model: str
    max_tokens: int
    daily_cost_cap_usd: float
    endpoint: Optional[str] = None
    thinking_budget: Optional[str] = None
    api_base: Optional[str] = None
    provider: Optional[str] = None


def load_tiers() -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "hermes" / "model-tiers.yaml"
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return (data or {}).get("tiers", {})
    except (FileNotFoundError, OSError, yaml.YAMLError) as exc:
        logger.warning("intent_router: failed to load model-tiers.yaml (%s); tiers empty", exc)
        return {}


TIERS = load_tiers()


def resolve_model(task_intent: str) -> ModelSpec:
    """Resolve a task intent to a ModelSpec per W0.7 definition."""
    tier_data = TIERS.get(task_intent)

    if not tier_data:
        # Require explicit intent; missing/invalid intent fails CLOSED to orchestrator.
        # Guard against missing 'orchestrator' key (e.g. empty or malformed config).
        tier_data = TIERS.get("orchestrator")
        if not tier_data:
            raise KeyError(
                f"task_intent={task_intent!r} has no tier mapping and 'orchestrator' fallback "
                "is not present in config/hermes/model-tiers.yaml"
            )

    status = tier_data.get("status")
    if status == "stub-until-w1j":
        raise NotImplementedError(
            "W1.J pending \u2014 see audit/2026-05-27-ground-truth/decisions.md D-2.c"
        )

    return ModelSpec(
        model=tier_data["model"],
        max_tokens=tier_data.get("max_tokens", 4096),
        daily_cost_cap_usd=tier_data.get("daily_cost_cap_usd", 100.0),
        endpoint=tier_data.get("endpoint"),
        thinking_budget=tier_data.get("thinking_budget"),
        api_base=tier_data.get("api_base"),
        provider=tier_data.get("provider"),
    )
