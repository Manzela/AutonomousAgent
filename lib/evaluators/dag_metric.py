"""DeepEval DAGMetric concretion for SP-06."""

from __future__ import annotations

import typing

from lib.evaluators.deepeval_judges import make_ci_judge

if typing.TYPE_CHECKING:
    from deepeval.metrics import BaseMetric, GEval
    from deepeval.test_case import LLMTestCase

    _HAVE_DEEPEVAL = True
else:
    try:
        from deepeval.metrics import BaseMetric
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase

        _HAVE_DEEPEVAL = True
    except ModuleNotFoundError:
        _HAVE_DEEPEVAL = False

        class BaseMetric:
            pass

        class LLMTestCase:
            pass


class DAGMetric(BaseMetric):
    """DeepEval DAGMetric for PRD-conformance.

    Combines deterministic hard roots (scope, symlinks, test additions, mutation testing)
    with a semantic LLM-judge leaf.
    """

    def __init__(
        self,
        *,
        changed_paths: list[tuple[str, str]],
        allowed_paths: list[str],
        base: str = "main",
        head: str = "HEAD",
        spec_sha: str = "",
        symlink_paths: list[str] = [],
        threshold: float = 0.8,
        model=None,
    ) -> None:
        self.changed_paths = changed_paths
        self.allowed_paths = allowed_paths
        self.base = base
        self.head = head
        self.spec_sha = spec_sha
        self.symlink_paths = symlink_paths
        self.threshold = threshold
        self.model = model or make_ci_judge()
        self.score = 0.0
        self.success = False
        self.reason = ""
        self.evaluation_model = (
            self.model.get_model_name() if hasattr(self.model, "get_model_name") else "unknown"
        )

    def measure(self, test_case: LLMTestCase) -> float:
        if not _HAVE_DEEPEVAL:
            raise RuntimeError("deepeval is not installed. Install with: uv sync --extra dev")

        from app.core.eval_gate import scope_root_verdict

        # 1. Run hard roots: Scope root verification (and symlinks check)
        verdict = scope_root_verdict(
            changed=self.changed_paths,
            allowed_globs=self.allowed_paths,
            base=self.base,
            head=self.head,
            spec_sha=self.spec_sha,
            symlink_paths=self.symlink_paths,
        )

        if not verdict.passed:
            self.score = 0.0
            self.success = False
            self.reason = f"Hard root check failed: {verdict.violations}"
            return 0.0

        # Check if tests were added/modified (SP-06 2.7)
        if not verdict.tests_added:
            # If code was modified but no tests were added, fail the hard root
            from app.core.eval_gate import _is_test_path

            code_changed = any(
                p.endswith(".py") and not _is_test_path(p) for _s, p in self.changed_paths
            )
            if code_changed:
                self.score = 0.0
                self.success = False
                self.reason = "Hard root check failed: No tests added/modified for python changes."
                return 0.0

        # 2. Run LLM judge leaf
        from deepeval.test_case import LLMTestCaseParams

        semantic_metric = GEval(
            name="Semantic Criteria Fulfillment",
            evaluation_steps=[
                "Verify if the actual output matches the expected behavior and satisfies the intent.",
                "Ensure there are no out-of-scope or unexplained logic additions in the actual output.",
            ],
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            threshold=self.threshold,
            model=self.model,
            async_mode=False,
        )

        semantic_metric.measure(test_case)
        self.score = semantic_metric.score if semantic_metric.score is not None else 0.0
        self.success = semantic_metric.success if semantic_metric.success is not None else False
        self.reason = semantic_metric.reason
        return self.score

    def is_successful(self) -> bool:
        return self.success is True
