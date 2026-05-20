"""Integration test: confirm the production cli-config.yaml allow-lists every
plugin Hermes is expected to load at startup (audit P2-7 acceptance).

Hermes' ``PluginManager`` is opt-in by default — a plugin discovered under
``~/.hermes/plugins/<name>/`` (the bundled directory shipped via the
``hermes-agent`` submodule and mounted into the container) only actually
loads when its slug appears in the ``plugins.enabled:`` list of the active
``cli-config.yaml`` (see ``hermes-agent/hermes_cli/plugins.py:826-905``).

That makes this YAML the single source of truth for which plugins are live
in production. We assert against the file directly (no Docker, no live
container required) for three reasons:

1. **Allowlist drift is a silent failure mode.** A plugin can be present in
   the bundled directory and entirely unused — the only way to catch the
   omission is to enumerate the expected slugs.
2. **CI-friendly.** The unit-test job already loads ``yaml`` and runs in
   ~30 s; we don't need to stand up Docker for an allowlist check.
3. **The matching real-runtime behaviour is covered by the bundled
   plugin's own upstream tests** plus the existing per-plugin unit tests
   in ``tests/unit/`` (``test_observability_plugin.py``, etc.) which
   exercise the registered hooks via ``_FakeHermesContext``. This file
   answers a different question: "is the plugin even allowed to load?"

The "integration" tier is justified because the assertion crosses module
boundaries (the YAML config consumed by ``hermes-agent``) rather than
unit-testing a single function.

P2-7 mandate (audit/2026-05-19-resume-orchestration/audit-plan.md):
    Add `disk-cleanup` to `config/hermes/cli-config.yaml:113` plugin
    allowlist; verify no permission issues.

The plugin ships in the currently-pinned hermes-agent submodule
(``ddb8d8fa8``) at ``hermes-agent/plugins/disk-cleanup/`` — verified by
this test's companion assertion below. No submodule bump is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# Repo root, two levels above tests/integration/.
REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_CONFIG_PATH = REPO_ROOT / "config" / "hermes" / "cli-config.yaml"

# The Phase 1/2 plugin allowlist. Each entry corresponds to a directory the
# ``hermes`` container can load — either via ``lib/<slug>/`` (mounted into
# the container) or ``hermes-agent/plugins/<slug>/`` (bundled by the
# upstream submodule). Adding/removing an entry below is a deliberate scope
# change and should be paired with a config edit.
EXPECTED_PLUGINS = {
    "anchors",  # P1-1  — TaskSpec + clarification loop (lib/anchors)
    "evaluators",  # P1-2  — multi-judge panel (lib/evaluators)
    "durability",  # P1-3/4/6 — checkpointing + REJECTED + retry (lib/durability)
    "memory",  # P1-4  — /forget + /rejections slash commands (lib/memory)
    "kanban",  # P1-5  — Kanban → Telegram bridge (lib/kanban)
    "observability",  # OTel SDK init (lib/observability)
    "disk-cleanup",  # P2-7  — session hygiene (hermes-agent/plugins/disk-cleanup)
}


def _load_cli_config() -> dict:
    """Parse the production cli-config.yaml. Fails loudly if missing or
    not valid YAML so the test diagnoses the actual problem instead of a
    cryptic KeyError downstream."""
    if not CLI_CONFIG_PATH.exists():
        pytest.fail(f"cli-config.yaml not found at {CLI_CONFIG_PATH}")
    with CLI_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_disk_cleanup_loaded():
    """P2-7 acceptance: ``disk-cleanup`` is in the ``plugins.enabled`` list.

    This is the named acceptance test referenced in the audit plan and the
    Wave-3 dispatch plan. Keeping the name stable so CI greps and PR
    comments can address it directly:
        ``pytest tests/integration/test_plugin_loading.py::test_disk_cleanup_loaded``
    """
    config = _load_cli_config()
    enabled = config.get("plugins", {}).get("enabled", [])
    assert isinstance(
        enabled, list
    ), f"plugins.enabled must be a list, got {type(enabled).__name__}"
    assert "disk-cleanup" in enabled, (
        "disk-cleanup plugin is NOT enabled in cli-config.yaml. "
        f"Current allowlist: {enabled}. "
        "Hermes' PluginManager is opt-in: the bundled plugin under "
        "hermes-agent/plugins/disk-cleanup/ will not load until its slug "
        "appears in plugins.enabled. See audit P2-7."
    )


def test_full_plugin_allowlist_matches_expected():
    """Regression guard: the full allowlist matches the documented set.

    A diff here means either:
      * Someone added a plugin without updating this test (good — update
        the test to acknowledge the new plugin), or
      * Someone removed a plugin we still expect (bad — the runtime will
        silently lose that capability).
    """
    config = _load_cli_config()
    enabled = set(config.get("plugins", {}).get("enabled", []))
    missing = EXPECTED_PLUGINS - enabled
    extra = enabled - EXPECTED_PLUGINS
    assert not missing, (
        f"Expected plugins missing from cli-config.yaml allowlist: {sorted(missing)}. "
        f"Current allowlist: {sorted(enabled)}."
    )
    assert not extra, (
        f"Unexpected plugins in cli-config.yaml allowlist: {sorted(extra)}. "
        "Either update EXPECTED_PLUGINS in this test to acknowledge the "
        "addition, or remove the entry from cli-config.yaml."
    )


def test_disk_cleanup_plugin_source_present_in_bundled_submodule():
    """The bundled plugin source must exist in the pinned hermes-agent
    submodule, otherwise enabling it in cli-config.yaml is a no-op at
    best (PluginManager discovery skips it) or an error at worst
    (depending on the version of hermes_cli loaded).

    This guard is intentionally tolerant of a non-initialized submodule
    (skips with a clear reason) — unit-test CI runs with
    ``submodules: false`` per .github/workflows/ci.yml. The check is
    most useful locally and in any future integration job that does
    initialize the submodule.
    """
    plugin_dir = REPO_ROOT / "hermes-agent" / "plugins" / "disk-cleanup"
    if not (REPO_ROOT / "hermes-agent" / "plugins").exists():
        pytest.skip(
            "hermes-agent submodule not initialized in this checkout "
            "(unit-test CI runs with submodules: false). The bundled "
            "plugin source is verified in the orchestrator's post-merge "
            "smoke test."
        )
    assert plugin_dir.is_dir(), (
        f"disk-cleanup plugin source missing at {plugin_dir}. "
        "The plugin is bundled by the upstream hermes-agent submodule; "
        "if missing, the submodule pointer needs a bump (see audit P2-6)."
    )
    init_py = plugin_dir / "__init__.py"
    assert init_py.is_file(), f"disk-cleanup plugin entry point missing at {init_py}"
