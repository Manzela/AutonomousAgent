"""H-06: FirecrackerSandbox stub must NOT advertise is_production_grade=True.

Audit finding: app/adapters/gcp/sandbox.py set is_production_grade = True on a
class whose __init__ and run() both raise NotImplementedError.  The sole runtime
reader — OrchestratorConfig.validate() — would have FALSELY ACCEPTED this stub
as production-ready, then crashed on the first .run() call.

These tests pin the correct behaviour:
  (a) FirecrackerSandbox.is_production_grade is False (class attribute, no instantiation)
  (b) OrchestratorConfig.validate() REJECTS FirecrackerSandbox when environment='production'
      (by patching around its NotImplementedError __init__)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.gcp.sandbox import FirecrackerSandbox
from app.core.orchestrator import OrchestratorConfig


# ─────────────────────────────────────────────────────────────────────────────
# (a) Class-level flag — verified without instantiation
# ─────────────────────────────────────────────────────────────────────────────


def test_firecracker_sandbox_is_not_production_grade_class_attribute():
    """H-06(a): The class attribute must be False — not True — on the stub.

    Reads the class attribute directly to avoid triggering __init__
    (which intentionally raises NotImplementedError until SP-05 is
    complete).
    """
    assert FirecrackerSandbox.is_production_grade is False, (
        "FirecrackerSandbox.is_production_grade must be False while __init__/run() "
        "raise NotImplementedError (SP-05 scaffolding). "
        "Setting it True would cause OrchestratorConfig.validate() to falsely "
        "accept this stub in production."
    )


# ─────────────────────────────────────────────────────────────────────────────
# (b) Orchestrator gate — validate() must REJECT the stub in production
# ─────────────────────────────────────────────────────────────────────────────


def test_orchestrator_config_rejects_firecracker_stub_in_production():
    """H-06(b): OrchestratorConfig.validate() must raise for the unimplemented stub.

    We bypass FirecrackerSandbox.__init__ (NotImplementedError) via MagicMock,
    but preserve the real class attribute so validate() sees the correct value.
    """
    # Create a mock that mimics an instance of FirecrackerSandbox.
    # We set is_production_grade from the class attribute so the gate logic
    # (getattr(sandbox, 'is_production_grade', False)) sees the real value.
    stub = MagicMock(spec=FirecrackerSandbox)
    stub.is_production_grade = FirecrackerSandbox.is_production_grade  # False

    config = OrchestratorConfig(sandbox=stub, environment="development")
    with pytest.raises(
        RuntimeError,
        match="Cannot use non-production sandbox FirecrackerSandbox",
    ):
        # Force validate() as if environment were production.
        config.environment = "production"
        config.validate()


def test_orchestrator_config_allows_no_sandbox_in_production():
    """Regression: validate() must not raise when sandbox is None in production."""
    config = OrchestratorConfig(sandbox=None, environment="development")
    config.environment = "production"
    config.validate()  # must not raise
