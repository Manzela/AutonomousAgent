"""Core abstractions for the autonomous-agent control plane.

Each module exports exactly one abstract base class (or protocol).
Concrete implementations live in app/adapters/{gcp,inmemory,local_model}/.

  - embedder    — AbstractEmbedder, project_dim
  - memory      — AbstractMemoryStore
  - orchestrator — OrchestratorConfig, execute, TaskStatus helpers
  - sandbox     — AbstractSandbox, SandboxResult
  - schemas     — AgentCapability, ExecutionResult, TaskRequest, TaskStatus
"""

__all__ = [
    "embedder",
    "memory",
    "orchestrator",
    "sandbox",
    "schemas",
]
