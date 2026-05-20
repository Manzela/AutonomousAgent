"""Nightly smoke test — confirm the evaluators plugin loads cleanly.

This is the regression check the nightly eval workflow
(``.github/workflows/nightly-eval.yml``) runs after every config / skill
change. It is intentionally **lightweight**: no LiteLLM round-trips, no
live judge dispatch. The deeper end-to-end behaviour (4-judge panel
against a known-bad worker output) lives in
``tests/integration/test_p1_2_judge_panel.py`` and requires the full
LiteLLM stack to be reachable.

What this file asserts:

1. The ``evaluators`` plugin slug is allow-listed in the production
   ``config/hermes/cli-config.yaml`` (Hermes' PluginManager is opt-in;
   without the slug, the plugin never loads).
2. ``lib.evaluators.register`` is callable against a fake context and
   wires the three lifecycle hooks the plugin contract requires
   (``post_tool_call``, ``pre_llm_call``, ``on_session_end``).
3. The judge / consensus modules import without side-effects so a
   downstream test can build judge prompts and consensus verdicts.

If any of these fail at 03:07 UTC nightly, the workflow opens an issue
labelled ``agent-regression`` assigned to ``@Manzela``. That is the
smoke-test alarm — actual debug should happen against this file's
failures plus the deeper P1-2 panel test.

Companion: IEEE 829 §4 — every release is gated by an automated
regression run; SSDF PW.7 calls out the same control.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_CONFIG_PATH = REPO_ROOT / "config" / "hermes" / "cli-config.yaml"


def _load_cli_config() -> dict:
    if not CLI_CONFIG_PATH.exists():
        pytest.fail(f"cli-config.yaml not found at {CLI_CONFIG_PATH}")
    with CLI_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_evaluators_plugin_allow_listed():
    """The ``evaluators`` slug must appear in ``plugins.enabled``.

    Without this entry, the plugin file under ``lib/evaluators/`` is
    discovered but never registered with Hermes (PluginManager is
    opt-in; see ``hermes-agent/hermes_cli/plugins.py:826-905``). The
    nightly eval would silently report green even though the judge
    panel is not actually wired.
    """
    config = _load_cli_config()
    enabled = config.get("plugins", {}).get("enabled", [])
    assert isinstance(
        enabled, list
    ), f"plugins.enabled must be a list, got {type(enabled).__name__}"
    assert "evaluators" in enabled, (
        "evaluators plugin is NOT enabled in cli-config.yaml. "
        f"Current allowlist: {enabled}. "
        "The nightly eval workflow assumes this plugin is live — "
        "either re-enable it or remove the nightly job."
    )


def test_evaluators_register_wires_three_hooks():
    """``register(ctx)`` must call ``ctx.register_hook`` exactly 3 times.

    Mirrors the assertion in ``tests/unit/test_evaluators_plugin.py``
    but at the integration tier — i.e. crossing the module-boundary
    between the plugin entry point and the hook registry. If the hook
    count drifts, the plugin will silently lose one of its lifecycle
    callbacks at runtime.
    """
    from lib.evaluators import register

    ctx = MagicMock()
    register(ctx)
    assert ctx.register_hook.call_count == 3, (
        f"Expected exactly 3 register_hook() calls, got {ctx.register_hook.call_count}. "
        "The evaluators plugin contract is post_tool_call + pre_llm_call + "
        "on_session_end. Any drift indicates a regression in lib/evaluators/__init__.py."
    )
    names = {c.args[0] for c in ctx.register_hook.call_args_list}
    expected = {"post_tool_call", "pre_llm_call", "on_session_end"}
    missing = expected - names
    extra = names - expected
    assert not missing, f"Missing lifecycle hooks: {sorted(missing)}"
    assert not extra, f"Unexpected lifecycle hooks: {sorted(extra)}"


def test_evaluators_submodules_import():
    """Importing the judge / consensus modules must not error.

    A circular-import or missing-dependency regression here would break
    the entire 4-judge panel at runtime. The check is intentionally
    minimal — import + sanity-call.
    """
    from lib.evaluators.consensus import decide_consensus
    from lib.evaluators.judge import (
        JUDGE_AXES,
        build_judge_prompt,
        parse_judge_response,
    )

    assert len(JUDGE_AXES) >= 1, "JUDGE_AXES must declare at least one axis"
    # Smoke: the consensus function must be callable. An empty list is a
    # legitimate degenerate input; the contract for the empty case is
    # documented in lib/evaluators/consensus.py.
    assert callable(decide_consensus)
    assert callable(build_judge_prompt)
    assert callable(parse_judge_response)
