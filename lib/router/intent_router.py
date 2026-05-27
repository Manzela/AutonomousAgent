import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


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
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return data.get("tiers", {})


TIERS = load_tiers()


def resolve_model(task_intent: str) -> ModelSpec:
    """Resolve a task intent to a ModelSpec per W0.7 definition."""
    tier_data = TIERS.get(task_intent)

    if not tier_data:
        # Require explicit intent; missing/invalid intent fails CLOSED to orchestrator
        tier_data = TIERS["orchestrator"]

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
