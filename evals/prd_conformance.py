"""PRD Conformance Evaluation using DeepEval DAGMetric (SP-06)."""

from __future__ import annotations

from pathlib import Path
import pytest
import yaml
from deepeval.test_case import LLMTestCase

from lib.evaluators.dag_metric import DAGMetric
from lib.evaluators.deepeval_judges import make_ci_judge

CORPUS_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden" / "corpus.yaml"


def load_corpus():
    with open(CORPUS_PATH) as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("item", load_corpus())
def test_golden_item_conformance(item):
    """Verify that each golden item passes DAGMetric when changes are in-scope."""
    goal = item["goal"]
    expected_dag = item["expected_dag"]

    # Gather all allowed paths from nodes
    allowed_paths = []
    for node in expected_dag:
        allowed_paths.extend(node["allowed_paths"])

    # Simulate in-scope changes: modify the allowed paths and add a new test file
    changed_paths = [("M", path) for path in allowed_paths]
    # Add a net-new test file to satisfy the "tests_added" hard root check
    changed_paths.append(("A", "tests/unit/test_new_feature.py"))

    # Initialize DAGMetric with the in-scope changes
    metric = DAGMetric(
        changed_paths=changed_paths,
        allowed_paths=allowed_paths,
        spec_sha="0" * 64,
        model=make_ci_judge(),
    )

    test_case = LLMTestCase(
        input=goal,
        actual_output="Structured task graph successfully generated.",
    )

    metric.measure(test_case)
    assert metric.is_successful(), f"Golden item failed conformance: {metric.reason}"


@pytest.mark.parametrize("item", load_corpus())
def test_golden_item_violation_detection(item):
    """Verify that DAGMetric successfully blocks out-of-scope changes."""
    goal = item["goal"]
    expected_dag = item["expected_dag"]

    allowed_paths = []
    for node in expected_dag:
        allowed_paths.extend(node["allowed_paths"])

    # Simulate out-of-scope change: modify a file that is not in the allowed list
    changed_paths = [("M", path) for path in allowed_paths]
    changed_paths.append(("M", "app/malicious_patch.py"))
    changed_paths.append(("A", "tests/unit/test_new_feature.py"))

    metric = DAGMetric(
        changed_paths=changed_paths,
        allowed_paths=allowed_paths,
        spec_sha="0" * 64,
        model=make_ci_judge(),
    )

    test_case = LLMTestCase(
        input=goal,
        actual_output="Structured task graph successfully generated.",
    )

    metric.measure(test_case)
    assert not metric.is_successful(), "DAGMetric failed to block out-of-scope changes"
