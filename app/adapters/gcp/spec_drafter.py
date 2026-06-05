"""VertexSpecDrafter — GCP/Vertex concretion of AbstractSpecDrafter.

This is the production spec-drafter: a Vertex structured-output (Pydantic-
constrained) drafter that emits TaskSpec fields + typed clarifying questions + an
ambiguity report (PRD §6 SP-03).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import litellm

from app.core.spec_drafter import (
    MAX_QUESTIONS_PER_ROUND,
    AbstractSpecDrafter,
    CONSTITUTION_PATH,
    DraftResult,
)

logger = logging.getLogger(__name__)


class VertexSpecDrafter(AbstractSpecDrafter):
    """Vertex structured-output spec-drafter."""

    is_production_grade = True

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        location: str = "us-central1",
        model: str = "vertex_ai/gemini-3.1-pro-preview",
    ) -> None:
        self.project = project or os.environ.get("GCP_PROJECT_ID", "autonomous-agent-2026")
        self.location = location
        # Ensure correct prefix for Vertex models
        if model and not (
            model.startswith("vertex_ai/")
            or model.startswith("openrouter/")
            or model.startswith("hosted_vllm/")
        ):
            self.model = f"vertex_ai/{model}"
        else:
            self.model = model or "vertex_ai/gemini-3.1-pro-preview"

    def draft(
        self,
        intent: str,
        *,
        answers: Optional[dict[str, str]] = None,
        round_index: int = 0,
    ) -> DraftResult:
        """Produce one round via Vertex structured output."""
        answers = answers or {}

        # Grounding rule (C16 / non-goal #6): load the constitution.md asset
        constitution_content = ""
        if CONSTITUTION_PATH.exists():
            try:
                constitution_content = CONSTITUTION_PATH.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("VertexSpecDrafter: failed to read constitution.md: %s", exc)

        system_prompt = (
            "You are a Senior Principal Software Architect. Given the user's intent, "
            "prior clarification answers, and the system constitution, produce a DraftResult.\n\n"
            f"=== System Constitution ===\n{constitution_content}\n\n"
            "Constraints:\n"
            f"1. You must not seek/ask more than {MAX_QUESTIONS_PER_ROUND} questions.\n"
            "2. Ground your applied_standards and ambiguities strictly in the system constitution and model knowledge. Do not reference external links.\n"
            "3. Return the output matching the requested schema format."
        )

        user_content = f"User Intent: {intent}\n\nAnswers so far: {json.dumps(answers)}"

        resp = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=DraftResult,
            temperature=0.0,
        )

        raw_content = resp.choices[0].message.content
        if not raw_content:
            raise ValueError("VertexSpecDrafter: model returned empty content")

        # Mistake-proofing: Parse the JSON dictionary first, slice questions if they exceed MAX_QUESTIONS_PER_ROUND, and instantiate DraftResult.
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"VertexSpecDrafter: failed to parse JSON response: {raw_content[:200]!r}"
            ) from exc

        if isinstance(data, dict) and "questions" in data and isinstance(data["questions"], list):
            if len(data["questions"]) > MAX_QUESTIONS_PER_ROUND:
                logger.warning(
                    "VertexSpecDrafter: model returned %d questions, slicing to %d",
                    len(data["questions"]),
                    MAX_QUESTIONS_PER_ROUND,
                )
                data["questions"] = data["questions"][:MAX_QUESTIONS_PER_ROUND]

        return DraftResult(**data)
