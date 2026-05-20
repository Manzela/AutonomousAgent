"""Tests the register() contract for the bundled ``disk-cleanup`` plugin.

This is the substantive P2-7 acceptance the audit plan calls for
(``audit/2026-05-19-resume-orchestration/audit-plan.md`` §4.4): confirm
the plugin actually loads and wires the two declared hooks
(``post_tool_call`` and ``on_session_end``) so a /disk-cleanup-enabled
container will exercise session-hygiene at runtime.

The companion test in ``tests/integration/test_plugin_loading.py`` proves
the slug is on the YAML allowlist; this test proves the code on the other
end of that allowlist is wirable. Together they cover the spec mandate
without requiring a live Docker container.

Pattern mirrors ``tests/unit/test_anchors_plugin.py`` / ``test_durability_plugin.py``:
build a ``MagicMock`` ``ctx``, call ``register(ctx)``, assert the expected
hook names appear in ``ctx.register_hook.call_args_list``.

Submodule gating
----------------
The plugin source lives in the ``hermes-agent`` submodule under
``hermes-agent/plugins/disk-cleanup/`` (bundled, not in this repo's
``lib/`` tree). The unit-test CI job runs with ``submodules: false`` per
``.github/workflows/ci.yml:186``, so the plugin source is unavailable
there — these tests skip cleanly in that scenario. They run (and assert)
locally when the submodule is initialized, and on any future integration
job that does check out submodules.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Repo root, two levels above tests/unit/.
REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "hermes-agent" / "plugins" / "disk-cleanup"
PLUGIN_INIT = PLUGIN_DIR / "__init__.py"

pytestmark = pytest.mark.skipif(
    not PLUGIN_INIT.is_file(),
    reason=(
        "hermes-agent submodule not initialized — disk-cleanup plugin source "
        "unavailable. Unit-test CI runs with submodules: false per ci.yml; "
        "this test exercises the register() contract when the source IS "
        "checked out (locally or in any submodule-enabled job)."
    ),
)


_PLUGIN_PKG_NAME = "disk_cleanup_plugin"


def _load_plugin_module():
    """Load ``hermes-agent/plugins/disk-cleanup/__init__.py`` as an importable
    package so its ``from . import disk_cleanup as dg`` relative import
    resolves against the sibling ``disk_cleanup.py``.

    ``submodule_search_locations`` is what makes the loaded module a
    package (vs. a plain module). The package must also be registered in
    ``sys.modules`` *before* ``exec_module`` runs, so the relative-import
    resolver can find its parent — without that, exec fails with
    ``ModuleNotFoundError: No module named 'disk_cleanup_plugin'``.
    """
    # Idempotent re-load: if a previous test in this run already loaded the
    # package, return the cached module rather than re-execing register()
    # against a freshly-imported copy (that would also re-run any module-
    # level side effects, which the plugin author may not have made
    # idempotent).
    cached = sys.modules.get(_PLUGIN_PKG_NAME)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        _PLUGIN_PKG_NAME,
        PLUGIN_INIT,
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert (
        spec is not None and spec.loader is not None
    ), f"Could not build module spec for {PLUGIN_INIT}"
    module = importlib.util.module_from_spec(spec)
    # MUST register before exec so `from . import disk_cleanup` resolves.
    sys.modules[_PLUGIN_PKG_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Clean up on failure so a subsequent test can retry cleanly.
        sys.modules.pop(_PLUGIN_PKG_NAME, None)
        raise
    return module


def _registered_hook_names(ctx_mock: MagicMock) -> list[str]:
    """Mirror the helper in ``tests/unit/test_observability_plugin.py``:
    each register_hook call passes the hook name as the first positional
    arg (see hermes-agent/plugins/disk-cleanup/__init__.py:309-311)."""
    return [call.args[0] for call in ctx_mock.register_hook.call_args_list]


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------


def test_register_wires_post_tool_call_hook():
    """plugin.yaml declares ``post_tool_call`` — ``register()`` must wire it."""
    module = _load_plugin_module()
    ctx = MagicMock()
    module.register(ctx)
    hook_names = _registered_hook_names(ctx)
    assert "post_tool_call" in hook_names, (
        f"disk-cleanup register() did not wire the post_tool_call hook "
        f"declared in plugin.yaml. Registered hooks: {hook_names}"
    )


def test_register_wires_on_session_end_hook():
    """plugin.yaml declares ``on_session_end`` — ``register()`` must wire it."""
    module = _load_plugin_module()
    ctx = MagicMock()
    module.register(ctx)
    hook_names = _registered_hook_names(ctx)
    assert "on_session_end" in hook_names, (
        f"disk-cleanup register() did not wire the on_session_end hook "
        f"declared in plugin.yaml. Registered hooks: {hook_names}"
    )


def test_register_wires_only_the_two_declared_hooks():
    """No spurious hook registrations — keep the runtime surface tight.

    plugin.yaml is the source of truth for which hooks fire; any drift
    between yaml and __init__.py should be a deliberate, visible change.
    """
    module = _load_plugin_module()
    ctx = MagicMock()
    module.register(ctx)
    hook_names = _registered_hook_names(ctx)
    assert sorted(hook_names) == sorted(["post_tool_call", "on_session_end"]), (
        f"disk-cleanup register() wired unexpected hooks. "
        f"Expected exactly {sorted(['post_tool_call', 'on_session_end'])}, "
        f"got {sorted(hook_names)}. Update plugin.yaml + this test together."
    )


def test_register_also_wires_the_slash_command():
    """The /disk-cleanup slash command is the manual escape hatch documented
    in the plugin README; missing it would silently strip operator UX."""
    module = _load_plugin_module()
    ctx = MagicMock()
    module.register(ctx)
    cmd_names = [
        call.kwargs.get("name") or (call.args[0] if call.args else None)
        for call in ctx.register_command.call_args_list
    ]
    assert "disk-cleanup" in cmd_names, (
        f"disk-cleanup register() did not wire the /disk-cleanup slash command. "
        f"Registered commands: {cmd_names}"
    )
