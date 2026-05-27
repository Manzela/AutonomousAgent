import pytest
from app.core.orchestrator import OrchestratorConfig
from app.adapters.inmemory.sandbox import LocalSubprocessSandbox


def test_orchestrator_config_rejects_inmemory_in_production():
    config = OrchestratorConfig(sandbox=LocalSubprocessSandbox(), environment="production")
    with pytest.raises(
        RuntimeError, match="Cannot use non-production sandbox LocalSubprocessSandbox"
    ):
        config.validate()


def test_orchestrator_config_allows_inmemory_in_development():
    config = OrchestratorConfig(sandbox=LocalSubprocessSandbox(), environment="development")
    config.validate()
