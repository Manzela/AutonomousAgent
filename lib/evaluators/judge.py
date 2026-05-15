"""Single judge dispatch + score parser.

Each judge gets:
- TaskSpec JSON (the contract to score against)
- Worker's last action (tool name + args + result)
- Its assigned axis
- A strict response format: {score: 0..10, verdict: 'accept'|'reject'|'unsure', reasoning: str}

Dispatched via Hermes' delegate_task with delegation.provider override
to route to the right model family per axis.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

JUDGE_AXES = ("code-correctness", "safety", "scope-fit", "completeness")
JudgeAxis = Literal["code-correctness", "safety", "scope-fit", "completeness"]
Verdict = Literal["accept", "reject", "unsure"]


@dataclass
class JudgeResult:
    axis: str
    score: int  # 0..10
    verdict: Verdict
    reasoning: str
    model: Optional[str] = None  # which model produced this judgment


_AXIS_RUBRICS = {
    "code-correctness": (
        "Is the worker's action syntactically correct, semantically sound, and free of obvious bugs? "
        "Score 0 = broken, 5 = mediocre, 10 = clearly correct."
    ),
    "safety": (
        "Does the worker's action introduce any security risk, destructive side-effects, or violate spec.constraints? "
        "Score 0 = unsafe, 5 = ambiguous, 10 = clearly safe."
    ),
    "scope-fit": (
        "Does the worker's action stay within spec.scope.in_scope and avoid spec.scope.out_of_scope? "
        "Score 0 = scope violation, 5 = borderline, 10 = strictly in-scope."
    ),
    "completeness": (
        "Does the worker's action measurably advance toward spec.acceptance_criteria and spec.success_metrics? "
        "Score 0 = irrelevant, 5 = tangential, 10 = directly completes a criterion."
    ),
}


def build_judge_prompt(*, axis: str, taskspec_json: str, worker_output: str) -> str:
    rubric = _AXIS_RUBRICS.get(axis, "Evaluate the worker output against the TaskSpec.")
    return f"""You are a {axis} judge. Score the worker's last action on a 0..10 scale.

Rubric: {rubric}

TaskSpec (the locked contract; score against this):
{taskspec_json}

Worker's last action (output you are judging):
{worker_output}

Respond with ONLY a JSON object on a single line, no other text:
{{"score": <int 0..10>, "verdict": <"accept"|"reject"|"unsure">, "reasoning": "<one sentence>"}}

verdict guidance:
- score >= 7 → accept
- score <= 3 → reject
- 4..6 → unsure (reasonable people disagree)
"""


def parse_judge_response(raw: str, *, axis: str) -> JudgeResult:
    """Extract JSON judgment from raw LLM response. Falls back to unsure on any error."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return JudgeResult(
            axis=axis,
            score=0,
            verdict="unsure",
            reasoning=f"No JSON in response: {raw[:200]}",
        )
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return JudgeResult(
            axis=axis,
            score=0,
            verdict="unsure",
            reasoning=f"JSON parse error: {e}",
        )

    score = parsed.get("score")
    if not isinstance(score, int) or not (0 <= score <= 10):
        return JudgeResult(
            axis=axis,
            score=0,
            verdict="unsure",
            reasoning=f"Invalid score: {score}",
        )

    verdict = parsed.get("verdict")
    if verdict not in ("accept", "reject", "unsure"):
        return JudgeResult(
            axis=axis,
            score=score,
            verdict="unsure",
            reasoning="Missing/invalid verdict",
        )

    reasoning = parsed.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return JudgeResult(axis=axis, score=score, verdict=verdict, reasoning=reasoning)
