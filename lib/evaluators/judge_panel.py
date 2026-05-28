"""Async 4-judge + 5th-judge tiebreak panel."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import litellm

from lib.evaluators.consensus import ConsensusResult, decide_consensus
from lib.evaluators.judge import JUDGE_AXES, build_judge_prompt, parse_judge_response, JudgeResult
from lib.evaluators.orchestrator_hook import get_per_axis_model
from lib.anchors import _get_spec_store, _most_recent_draft

logger = logging.getLogger(__name__)


async def _call_judge(axis: str, taskspec_json: str, worker_output: str, model: str) -> JudgeResult:
    prompt = build_judge_prompt(axis=axis, taskspec_json=taskspec_json, worker_output=worker_output)
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = response.choices[0].message.content
        result = parse_judge_response(raw, axis=axis)
        result.model = model
        return result
    except Exception as exc:
        logger.warning("Judge call failed for axis %s: %s", axis, exc)
        return JudgeResult(
            axis=axis, score=0, verdict="unsure", reasoning=f"Exception: {exc}", model=model
        )


async def evaluate(worker_action: dict[str, Any]) -> ConsensusResult:
    # 1. Get taskspec
    store = _get_spec_store()
    spec = _most_recent_draft(store, statuses=("locked",))
    taskspec_json = spec.model_dump_json() if spec else "{}"

    worker_output = json.dumps(worker_action, default=str)
    models = get_per_axis_model()

    # 2. Dispatch 4 judges in parallel
    tasks = []
    for axis in JUDGE_AXES:
        model = models.get(axis, "vertex_ai/gemini-3.1-pro-preview")
        tasks.append(_call_judge(axis, taskspec_json, worker_output, model))

    judges: list[JudgeResult] = list(await asyncio.gather(*tasks))

    # 3. Compute consensus
    try:
        import yaml
        from pathlib import Path

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
        if not cfg_path.exists():
            raise FileNotFoundError(cfg_path)
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        consensus_cfg = cfg.get("evaluators", {}).get("consensus", {})
        accept_threshold = float(consensus_cfg.get("accept_threshold", 0.75))
        reject_threshold = float(consensus_cfg.get("reject_threshold", 0.75))
        fifth_judge_model = consensus_cfg.get("fifth_judge_model", "vertex_ai/claude-opus-4-7")
        fifth_judge_axis = consensus_cfg.get("fifth_judge_axis", "completeness")
    except Exception:
        accept_threshold = 0.75
        reject_threshold = 0.75
        fifth_judge_model = "vertex_ai/claude-opus-4-7"
        fifth_judge_axis = "completeness"

    result = decide_consensus(
        judges=judges, accept_threshold=accept_threshold, reject_threshold=reject_threshold
    )

    if result.verdict == "needs_5th_judge":
        logger.info(
            "4-judge panel tied, escalating to 5th judge (axis=%s model=%s)",
            fifth_judge_axis,
            fifth_judge_model,
        )
        fifth_judge = await _call_judge(
            fifth_judge_axis, taskspec_json, worker_output, fifth_judge_model
        )
        result = decide_consensus(
            judges=judges,
            fifth_judge=fifth_judge,
            accept_threshold=accept_threshold,
            reject_threshold=reject_threshold,
        )

    return result
