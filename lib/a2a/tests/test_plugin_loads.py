"""Day 1 acceptance gate test — the plugin module loads cleanly.

Per spike-plan.md §Day 1 acceptance:
  pytest lib/a2a/tests/test_plugin_loads.py -q  green
  docker compose config                          validates
  Hermes startup logs `register: a2a` once

This file covers item 1. Items 2-3 are operator-verified (CI green on
docker-compose validation; manual hermes start for the register log).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_plugin_module_imports() -> None:
    """The module must be importable without side effects."""
    import lib.a2a  # noqa: F401


def test_register_function_exists() -> None:
    """register(ctx) is the Hermes PluginManager entry-point contract."""
    from lib.a2a import register

    assert callable(register), "register must be callable"


def test_register_wires_session_start_hook() -> None:
    """Day 1 contract: register installs exactly one on_session_start hook."""
    from lib.a2a import register

    ctx = MagicMock()
    register(ctx)

    ctx.register_hook.assert_called_once()
    hook_name, hook_fn = ctx.register_hook.call_args.args
    assert hook_name == "on_session_start"
    assert callable(hook_fn)


def test_stub_submodules_import() -> None:
    """All 5 Day 2-8 stub modules must import without raising."""
    import lib.a2a.agent_card  # noqa: F401
    import lib.a2a.auth  # noqa: F401
    import lib.a2a.client  # noqa: F401
    import lib.a2a.server  # noqa: F401
    import lib.a2a.task_bridge  # noqa: F401
