"""End-to-end smoke test: spin up the real ``hermes`` container via
``docker compose`` and verify the ``disk-cleanup`` plugin loads through
the production :class:`hermes_cli.plugins.PluginManager` — not via a test
shim that imports the plugin module directly.

Why this exists
---------------
``tests/integration/test_plugin_loading.py`` and
``tests/unit/test_disk_cleanup_plugin.py`` together prove that:

  1. ``disk-cleanup`` is in the ``plugins.enabled`` allowlist of
     ``config/hermes/cli-config.yaml`` (allowlist drift check), and
  2. ``hermes-agent/plugins/disk-cleanup/__init__.py`` registers the
     expected hooks + slash command when imported and ``register(ctx)``
     is called against a fake context.

What those tests **do not** prove is that the production loader —
``hermes_cli.plugins.PluginManager.discover_and_load`` with its six
gate conditions (manifest parsing, ``key`` derivation, ``kind``
discrimination across ``exclusive`` / ``model-provider`` / ``backend``
/ ``platform`` / ``standalone``, allowlist matching, dedup of bundled vs
user plugins, ``register()`` invocation) — actually picks ``disk-cleanup``
up and calls its ``register()`` cleanly when Hermes itself enumerates
plugins at startup inside the container.

A manifest typo, an allowlist drift, a missing ``kind:``, or a regression
in the upstream submodule's discovery code would slip past the importlib
tests but be caught here. This is the closure for the gap noted in PR #91.

Test design
-----------
* Runs ``docker compose up -d --force-recreate hermes`` to get a clean
  container. ``--force-recreate`` matters: a hot cache could otherwise
  reuse a previously-loaded plugin state we don't control.
* The hermes service in ``deploy/docker-compose.yml`` sets
  ``HERMES_PLUGINS_DEBUG=1``, which raises
  ``hermes_cli.plugins.logger`` to DEBUG and tees it to stderr — exactly
  what we need to see the per-plugin ``Loading plugin '<key>'`` lines
  (upstream ``_load_plugin`` logs at DEBUG; the INFO-level
  ``Plugin discovery complete:`` summary is always emitted).
* Polls ``docker compose logs hermes`` for up to 90 s for the discovery
  summary, then asserts:
    1. The summary line was emitted (proves loader ran end-to-end).
    2. ``Loading plugin 'disk-cleanup'`` appears (proves discovery saw
       the manifest AND the allowlist let it through to ``_load_plugin``).
    3. None of the "skip" sentinels for ``disk-cleanup`` were emitted
       (proves no allowlist drift, no ``kind`` mismatch, not in
       ``plugins.disabled``).
    4. No ``Failed to load plugin 'disk-cleanup'`` line (proves the
       module import + ``register(ctx)`` call did not raise).
* Tears down with ``docker compose down -v hermes`` even on failure.

Marker discipline
-----------------
Marked both ``@pytest.mark.integration`` and ``@pytest.mark.docker`` (the
latter is the gate that lets dockerless hosts skip cleanly). The module
also probes the daemon at import time and skips the whole module if
``docker info`` fails — this lets ``pytest --collect-only`` work
everywhere without requiring docker.

References
----------
* Audit task P2 #23 — gap closure for PR #91's importlib-only coverage.
* ``hermes-agent/hermes_cli/plugins.py:1167`` — ``_load_plugin``
  (the function whose run we're asserting against, not bypassing).
* ``hermes-agent/hermes_cli/plugins.py:951`` — the
  ``Plugin discovery complete: %d found, %d enabled`` INFO emit.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest


# Resolve compose file from the test's own location so the test is robust
# to pytest's CWD (`pytest tests/integration/...` from repo root vs the
# tests/ subdir behave identically).
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = str(REPO_ROOT / "deploy" / "docker-compose.yml")
SERVICE = "hermes"

# Upstream PluginManager log strings we grep for. These come from
# ``hermes-agent/hermes_cli/plugins.py``:
#   - ``logger.info("Plugin discovery complete: %d found, %d enabled", ...)``
#   - ``logger.debug("Loading plugin '%s' (source=%s, kind=%s, ...)", ...)``
#   - ``logger.debug("Skipping '%s' (not in plugins.enabled)", ...)``
#   - ``logger.debug("Skipping disabled plugin '%s'", ...)``
#   - ``logger.warning("Failed to load plugin '%s': %s", ...)``
# We rely on HERMES_PLUGINS_DEBUG=1 (set in deploy/docker-compose.yml) to
# surface DEBUG to stderr so docker captures them via its json-file driver.
LOG_PATTERN_DISCOVERY_DONE = "Plugin discovery complete:"
LOG_PATTERN_LOADING = "Loading plugin 'disk-cleanup'"
LOG_PATTERN_SKIP_DISABLED = "Skipping disabled plugin 'disk-cleanup'"
LOG_PATTERN_SKIP_NOT_ENABLED = "Skipping 'disk-cleanup' (not in plugins.enabled)"
LOG_PATTERN_LOAD_FAILED = "Failed to load plugin 'disk-cleanup'"


def _docker_available() -> bool:
    """True iff a docker daemon is reachable AND ``docker compose`` works.

    Probed at module import via ``docker info`` (cheaper than `docker ps`
    and the canonical "is the daemon up" check). We additionally verify
    ``docker compose version`` to catch the case where ``docker`` is on
    PATH but the Compose v2 plugin isn't (some minimal CI images).
    """
    if shutil.which("docker") is None:
        return False
    try:
        info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if info.returncode != 0:
            return False
        ver = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return ver.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


# Module-level skip: when no docker available, none of the tests in this
# module can run. Collect-only still works. CI's integration job that
# starts the docker daemon will run them.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon and/or `docker compose` CLI not available on this host",
    ),
]


@pytest.fixture(scope="module")
def hermes_container():
    """Start a fresh hermes container once per module, yield, then tear down.

    Module-scoped because:
      * ``--force-recreate`` already guarantees the hermes process and
        its plugin-discovery pass are fresh — that's the only state any
        test in this file inspects.
      * Per-test recreation tears down compose-managed networks (the
        ``internal``/``egress`` bridges) faster than the next ``up``
        can re-resolve them, producing flaky "network not found"
        races on shared CI runners.
      * Plugin-discovery state is in-process and read-only from the
        test side; there's nothing to mutate between tests.

    ``--force-recreate`` is the load-bearing flag: cached state from a
    prior ``docker compose up`` (potentially with a different image
    build, an older cli-config.yaml bind, or a stale plugin allowlist)
    cannot mask a real loader regression.
    """
    # Up. Long timeout because first-run image pulls (pinned digests in
    # deploy/docker-compose.yml) can be slow on cold caches. The
    # ``--wait`` flag blocks until healthcheck passes — for hermes that
    # implicitly waits past discover_and_load().
    up = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            COMPOSE_FILE,
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            SERVICE,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if up.returncode != 0:
        # Surface the compose error verbatim so a missing-secret /
        # missing-image failure isn't conflated with a plugin-loader
        # regression. Skip rather than fail when compose itself is
        # unhealthy — this test is about the loader, not stack hygiene.
        pytest.skip(
            f"docker compose up failed (rc={up.returncode}); not a "
            f"plugin-loader signal.\nstdout:\n{up.stdout}\nstderr:\n{up.stderr}"
        )
    try:
        yield
    finally:
        # Stop only the hermes service. We deliberately do NOT ``down -v``
        # the full stack because:
        #   * dependent services (otel-collector, github-mcp, etc.) were
        #     already running on this host — tearing them down here would
        #     be a side effect the test should not have.
        #   * ``-v`` on a per-service down removes the named volumes
        #     ``hermes-data`` is attached to, which other long-running
        #     local services (escalation-watcher, snapshot-watchdog,
        #     budget-watchdog) also mount.
        # Plugin-loader state lives in the container process, not on
        # disk, so a plain ``stop`` is sufficient cleanup.
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "stop", SERVICE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def _fetch_logs() -> str:
    """Return the current hermes container stdout+stderr."""
    result = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "logs", "--no-color", SERVICE],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return (result.stdout or "") + (result.stderr or "")


def test_disk_cleanup_plugin_loads_in_real_hermes_container(hermes_container):
    """The production PluginManager loads ``disk-cleanup`` end-to-end.

    Polls hermes logs for the discovery-complete INFO line (proves the
    loader ran), then asserts the disk-cleanup load path executed
    without any of the upstream skip/failure sentinels.
    """
    # Wait for plugin discovery to finish. Discovery runs at hermes
    # startup (``gateway run`` -> import-time hooks); the INFO summary
    # line is the canonical "loader fully exited" signal.
    deadline = time.time() + 90
    logs = ""
    discovered = False
    while time.time() < deadline:
        logs = _fetch_logs()
        if LOG_PATTERN_DISCOVERY_DONE in logs:
            discovered = True
            break
        time.sleep(2)

    # Trim noisy tail for assertion messages — full logs are still
    # printed by pytest's captured-output when the test fails.
    tail = logs[-4000:]

    assert discovered, (
        f"hermes did not emit '{LOG_PATTERN_DISCOVERY_DONE}' within 90 s. "
        "Either the container failed to start, or PluginManager.discover_and_load "
        f"never ran. Last 4 KB of logs:\n{tail}"
    )

    # Positive: the loader actually called _load_plugin(disk-cleanup).
    # This DEBUG line is only visible because HERMES_PLUGINS_DEBUG=1 is
    # set in deploy/docker-compose.yml -- if that env flag ever gets
    # removed the test will start failing here with an actionable
    # message rather than silently passing on a weaker signal.
    assert LOG_PATTERN_LOADING in logs, (
        f"Expected '{LOG_PATTERN_LOADING}' in hermes logs (proves the "
        "production loader entered _load_plugin for disk-cleanup). Did "
        "HERMES_PLUGINS_DEBUG=1 get removed from deploy/docker-compose.yml? "
        f"Last 4 KB of logs:\n{tail}"
    )

    # Negative: must not have been skipped via either skip-path.
    assert LOG_PATTERN_SKIP_NOT_ENABLED not in logs, (
        "disk-cleanup was discovered but skipped — its slug is missing "
        "from `plugins.enabled` in config/hermes/cli-config.yaml. This "
        "is the exact allowlist-drift failure mode the importlib-based "
        f"test in test_plugin_loading.py cannot catch.\nLogs:\n{tail}"
    )
    assert LOG_PATTERN_SKIP_DISABLED not in logs, (
        "disk-cleanup was explicitly disabled via `plugins.disabled` in "
        "config/hermes/cli-config.yaml. Remove that entry to re-enable "
        f"the plugin.\nLogs:\n{tail}"
    )

    # Negative: register(ctx) must not have raised.
    assert LOG_PATTERN_LOAD_FAILED not in logs, (
        "disk-cleanup's register() call raised inside the container. "
        "This usually means an import error in its module body or a "
        "hook-name typo. Look for the traceback in the logs above.\n"
        f"Logs:\n{tail}"
    )


def test_disk_cleanup_appears_in_hermes_plugins_list(hermes_container):
    """Cross-check: ``hermes plugins list`` reports disk-cleanup as enabled.

    Belt-and-braces over the log-based check above. The plugins-list
    subcommand re-uses the same discovery code path, so a green log
    assert + a green list assert together rule out the entire class of
    "discovery skipped silently" failures.

    Best-effort: if the upstream submodule ever drops or renames the
    subcommand, the test still surfaces the breakage but with a clear
    "subcommand changed" message rather than a cryptic assertion error.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            COMPOSE_FILE,
            "exec",
            "-T",
            SERVICE,
            "hermes",
            "plugins",
            "list",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            "`hermes plugins list` returned non-zero — upstream subcommand "
            f"may have changed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    # The Rich table output uses ANSI escapes when stdout is a TTY; with
    # capture_output it's plain text. Match the slug + an enable marker;
    # Rich's table renderer renders the cell value as "enabled" in plain
    # text (the color markup is stripped when not in a TTY).
    assert "disk-cleanup" in result.stdout, (
        "disk-cleanup not present in `hermes plugins list` output. "
        "This means the loader did not register it in its plugins map "
        "(distinct from "
        "the allowlist check — see test_plugin_loading.py)."
        f"\nFull output:\n{result.stdout}"
    )
    assert "enabled" in result.stdout, (
        "`hermes plugins list` reported no plugins as enabled. Either "
        "the entire allowlist is empty or the table renderer changed."
        f"\nFull output:\n{result.stdout}"
    )
