"""
seed — Phase-Aware MoE Orchestrator with Hierarchical Memory & Free Agents.

Public surface (the import-level API). Internal helpers live behind a
single-underscore convention; nothing under `_` is part of the contract.
"""

from __future__ import annotations

from .agent_registry import AgentRegistry, RegistryConfig
from .api_client import (
    AnthropicClient,
    AnthropicClientConfig,
    CompletionResult,
    UsageRecord,
)
from .bootstrap import (
    META_SYSTEM_PROMPT,
    META_USER_TEMPLATE,
    make_spawn_callback,
)
from .embedder import (
    AbstractEmbedder,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    project_dim,
)
from .memory_store import AbstractMemoryStore, EmptyScope, InMemoryStore
from .moe_router import (
    AbstractMoERouter,
    SoftmaxBilinearRouter,
    TrajectoryStep,
)
from .orchestrator import (
    CircuitBreakerOpen,
    Orchestrator,
    OrchestratorConfig,
    ProductionConfigError,
)
from .reward_model import (
    AbstractIntrinsicRewardModel,
    AnthropicJudge,
    HeuristicJudge,
    IntrinsicRewardConfig,
    IntrinsicRewardModel,
    Judge,
    JudgeEnsemble,
    RollingDiversity,
)
from .sandbox import (
    AbstractSandbox,
    LocalSubprocessSandbox,
    SandboxResult,
)
from .schemas import (
    AgentCapability,
    AgentID,
    BudgetVector,
    ContentHash,
    ExecutionResult,
    Lifecycle,
    MemoryRecord,
    MemoryTier,
    MetaAction,
    ProjectID,
    Reward,
    RoutingAction,
    StateVector,
    TaskID,
    TaskRequest,
    TaskStatus,
)
from .telemetry import TelemetrySink
from .virtual_context import (
    HandleClosed,
    NamespaceContamination,
    VirtualContextHandle,
    VirtualContextManager,
)


__all__ = [
    # registry
    "AgentRegistry",
    "RegistryConfig",
    # api client
    "AnthropicClient",
    "AnthropicClientConfig",
    "CompletionResult",
    "UsageRecord",
    # bootstrap
    "META_SYSTEM_PROMPT",
    "META_USER_TEMPLATE",
    "make_spawn_callback",
    # embedder
    "AbstractEmbedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "project_dim",
    # memory store
    "AbstractMemoryStore",
    "EmptyScope",
    "InMemoryStore",
    # router
    "AbstractMoERouter",
    "SoftmaxBilinearRouter",
    "TrajectoryStep",
    # orchestrator
    "CircuitBreakerOpen",
    "Orchestrator",
    "OrchestratorConfig",
    "ProductionConfigError",
    # reward
    "AbstractIntrinsicRewardModel",
    "AnthropicJudge",
    "HeuristicJudge",
    "IntrinsicRewardConfig",
    "IntrinsicRewardModel",
    "Judge",
    "JudgeEnsemble",
    "RollingDiversity",
    # sandbox
    "AbstractSandbox",
    "LocalSubprocessSandbox",
    "SandboxResult",
    # schemas
    "AgentCapability",
    "AgentID",
    "BudgetVector",
    "ContentHash",
    "ExecutionResult",
    "Lifecycle",
    "MemoryRecord",
    "MemoryTier",
    "MetaAction",
    "ProjectID",
    "Reward",
    "RoutingAction",
    "StateVector",
    "TaskID",
    "TaskRequest",
    "TaskStatus",
    # telemetry
    "TelemetrySink",
    # vcm
    "HandleClosed",
    "NamespaceContamination",
    "VirtualContextHandle",
    "VirtualContextManager",
]
