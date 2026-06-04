"""Automated Trajectory Diffing (Phase 4.3).

Takes two trajectories (e.g., a successful run vs a failed run) and diffs
them to isolate the exact turn where the agent diverged into a failure mode.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def diff_trajectories(baseline_path: str, failed_path: str) -> dict[str, Any]:
    """Compares two JSONL MALT trajectories and finds the divergence point."""
    try:
        with open(baseline_path, "r") as b_file, open(failed_path, "r") as f_file:
            baseline = [json.loads(line) for line in b_file]
            failed = [json.loads(line) for line in f_file]

        for i, (b_turn, f_turn) in enumerate(zip(baseline, failed)):
            # Compare LLM outputs
            if b_turn.get("response") != f_turn.get("response"):
                return {
                    "divergence_turn": i,
                    "baseline_response": b_turn.get("response"),
                    "failed_response": f_turn.get("response"),
                    "diff_type": "response_mismatch",
                }

            # Compare tool calls
            if b_turn.get("tool_calls") != f_turn.get("tool_calls"):
                return {
                    "divergence_turn": i,
                    "baseline_tools": b_turn.get("tool_calls"),
                    "failed_tools": f_turn.get("tool_calls"),
                    "diff_type": "tool_mismatch",
                }

        # If we reach here, the failed trajectory might be shorter or longer
        if len(failed) != len(baseline):
            return {
                "divergence_turn": min(len(baseline), len(failed)),
                "diff_type": "length_mismatch",
                "baseline_length": len(baseline),
                "failed_length": len(failed),
            }

        return {"diff_type": "identical"}

    except FileNotFoundError:
        logger.error("Trajectory files not found.")
        return {}


if __name__ == "__main__":
    # Example usage
    diff = diff_trajectories("data/golden_run.jsonl", "data/failed_run.jsonl")
    print(f"Divergence Report: {json.dumps(diff, indent=2)}")
