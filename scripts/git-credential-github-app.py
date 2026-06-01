#!/usr/bin/env python3
"""git-credential helper — GitHub App installation token vending (SP-00f.2).

Implements the git-credential protocol so that git operations authenticate as
the least-privilege GitHub App (App 3920713, installation 136939619) instead
of a broad PAT.

Protocol:
  ``get``   — mint-on-demand + cache a short-lived App installation token.
  ``store`` — no-op (token lifecycle managed by this helper, not git).
  ``erase`` — no-op.

On any error the helper exits 0 with NO stdout so git falls back to the next
configured credential helper (e.g. the system keychain).  A one-line
diagnostic is written to stderr for troubleshooting; the token value is NEVER
written to stderr.

Cache:
  The minted token is cached in JSON (``{token, expires_at}``) at
  ``$GITHUB_APP_TOKEN_CACHE``  (default: ``$XDG_CACHE_HOME/github-app-token.json``
  or ``~/.cache/github-app-token.json``).  The file is created/rewritten with
  mode 0600.  A cached token is reused if it has >5 min of lifetime remaining;
  otherwise a fresh token is minted.

Config (non-secret):
  ``GITHUB_APP_ID``              — numeric App ID (env preferred).
  ``GITHUB_APP_INSTALLATION_ID`` — installation ID  (env preferred).
  ``config/github-app.json``     — fallback committed non-secret identifiers.

Private key (secret):
  ``GITHUB_APP_PRIVATE_KEY_PATH`` — path to a **decrypted** PEM file.
  The helper does NOT decrypt SOPS itself; the operator/harness provides the
  decrypted PEM via this env var.

Usage (register globally or per-repo)::

    git config --global credential.'https://github.com/'.helper \\
        '/abs/path/scripts/git-credential-github-app.py'

    # Or scoped to a single repo:
    git config credential.'https://github.com/Manzela/AutonomousAgent'.helper \\
        '/abs/path/scripts/git-credential-github-app.py'
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Default config file location (resolved relative to this script)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent.resolve()
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_CONFIG_PATH = str(_REPO_ROOT / "config" / "github-app.json")

# git invokes this helper as a standalone executable, so sys.path[0] is the
# scripts/ dir, NOT the repo root — `from lib.github_auth import …` would fail
# with ModuleNotFoundError (and the fail-safe would then silently fall back to
# the keyring on every call, never using the App token). Put the repo root on
# sys.path so the first-party `lib` package resolves regardless of cwd.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Cached-token minimum remaining lifetime before a re-mint is triggered.
_MIN_REMAINING_SECS = 5 * 60  # 5 minutes


# ---------------------------------------------------------------------------
# Public protocol: functions imported by tests
# ---------------------------------------------------------------------------


def read_credential_fields() -> dict[str, str]:
    """Read key=value lines from stdin until a blank line (git-credential protocol).

    Returns:
        A dict of all key→value pairs found before the blank line.
    """
    fields: dict[str, str] = {}
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\n")
        if not line:
            break
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def read_app_config(config_path: Optional[str] = None) -> tuple[str, str]:
    """Return (app_id, installation_id) from env vars or config file.

    Env vars take precedence over the config file.

    Args:
        config_path: Path to a JSON file with ``app_id`` and ``installation_id``.
                     Defaults to ``config/github-app.json`` next to the repo root.

    Returns:
        A (app_id, installation_id) tuple (both as strings).

    Raises:
        ValueError: if neither env vars nor config file provides the IDs.
    """
    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    inst_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()

    if app_id and inst_id:
        return app_id, inst_id

    resolved_config = config_path or _DEFAULT_CONFIG_PATH
    try:
        with open(resolved_config) as fh:
            data = json.load(fh)
        file_app_id = str(data.get("app_id", "")).strip()
        file_inst_id = str(data.get("installation_id", "")).strip()
    except (OSError, json.JSONDecodeError, ValueError):
        file_app_id = ""
        file_inst_id = ""

    # Merge: env vars win per-field
    app_id = app_id or file_app_id
    inst_id = inst_id or file_inst_id

    return app_id, inst_id


def _default_cache_path() -> str:
    """Return the default path for the token cache file."""
    cache_env = os.environ.get("GITHUB_APP_TOKEN_CACHE", "").strip()
    if cache_env:
        return cache_env
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".cache"
    return str(base / "github-app-token.json")


def _load_cached_token(cache_path: str) -> Optional[str]:
    """Return a cached token if it has >5 min remaining, else None."""
    try:
        data = json.loads(Path(cache_path).read_text())
        token: str = data["token"]
        raw_expires: str = data["expires_at"]
        expires_at = datetime.datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
        remaining = (expires_at - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds()
        if remaining > _MIN_REMAINING_SECS:
            return token
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return None


def _write_cache(cache_path: str, token: str, expires_at: datetime.datetime) -> None:
    """Write ``{token, expires_at}`` to ``cache_path`` with mode 0600.

    Creates parent directories if needed.
    """
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    expires_str = expires_at.isoformat() if expires_at.tzinfo else expires_at.isoformat() + "+00:00"
    payload = json.dumps({"token": token, "expires_at": expires_str})

    # Write with O_CREAT | O_WRONLY | O_TRUNC and mode 0600 atomically.
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)

    # Ensure mode is 0600 even if the file pre-existed with different perms
    # (os.open with O_CREAT doesn't change perms on an existing file on some OSes).
    os.chmod(str(path), 0o600)


def _default_mint_fn(**kwargs: Any):  # type: ignore[return]
    """Import and call lib.github_auth.mint_installation_token at call time.

    Resolves app_id/installation_id from env-or-``config/github-app.json`` (via
    :func:`read_app_config`) so the helper works without those env vars set —
    only ``GITHUB_APP_PRIVATE_KEY_PATH`` (the decrypted PEM) must be supplied by
    the harness/operator. The deferred import means the script still exits 0
    (fail-safe) when lib/github_auth.py or its deps are not importable.
    """
    from lib.github_auth import mint_installation_token  # noqa: PLC0415

    if "app_id" not in kwargs or "installation_id" not in kwargs:
        cfg_app_id, cfg_inst_id = read_app_config()
        kwargs.setdefault("app_id", cfg_app_id or None)
        kwargs.setdefault("installation_id", cfg_inst_id or None)

    return mint_installation_token(**kwargs)


def handle_get(
    *,
    mint_fn: Optional[Callable] = None,
    cache_path: Optional[str] = None,
) -> int:
    """Handle a ``git credential get`` invocation.

    Reads credential fields from stdin.  If host != "github.com", exits
    immediately with no output so git falls back.  Otherwise:
    - Returns a cached token (if valid, >5 min remaining).
    - Mints a fresh token + caches it (0600) otherwise.
    - On ANY error: writes a diagnostic to stderr, returns 0 with NO stdout.

    Args:
        mint_fn:    Callable that returns an object with ``.token`` and
                    ``.expires_at``.  Defaults to
                    ``lib.github_auth.mint_installation_token``.
        cache_path: Path to the JSON token cache.  Defaults to
                    ``_default_cache_path()``.

    Returns:
        Exit code (always 0 — git must never see a non-zero from a helper).
    """
    fields = read_credential_fields()

    host = fields.get("host", "")
    if host != "github.com":
        # Not our concern — fall through to next helper silently.
        return 0

    resolved_cache = cache_path or _default_cache_path()

    # Try cache first.
    cached = _load_cached_token(resolved_cache)
    if cached is not None:
        _emit_credentials(cached)
        return 0

    # Need a fresh token. Call _default_mint_fn() DIRECTLY (not via a variable)
    # so the static call-graph traces its body — and the read_app_config() within
    # it — keeping those symbols reachable for the C4 dead-code gate.
    try:
        itok = mint_fn() if mint_fn is not None else _default_mint_fn()
        token: str = itok.token
        expires_at: datetime.datetime = itok.expires_at
    except Exception as exc:  # noqa: BLE001
        # Diagnostic to stderr — never include the token value.
        print(f"git-credential-github-app: failed to mint token: {exc}", file=sys.stderr)
        # Return 0 with NO stdout so git falls back.
        return 0

    # Write cache (best-effort; don't break git on cache write failure).
    try:
        _write_cache(resolved_cache, token, expires_at)
    except Exception as exc:  # noqa: BLE001
        print(
            f"git-credential-github-app: warning: could not write token cache: {exc}",
            file=sys.stderr,
        )

    _emit_credentials(token)
    return 0


def handle_store() -> int:
    """Handle ``git credential store`` — no-op.

    Token lifecycle is managed by this helper's mint+cache mechanism.
    """
    return 0


def handle_erase() -> int:
    """Handle ``git credential erase`` — no-op.

    Tokens expire naturally (≤1 h); cache will be refreshed automatically.
    """
    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit_credentials(token: str) -> None:
    """Write the git-credential ``get`` response to stdout."""
    sys.stdout.write("username=x-access-token\n")
    sys.stdout.write(f"password={token}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """Dispatch git-credential subcommand.

    Args:
        argv: Argument list (typically sys.argv[1:]).

    Returns:
        Exit code (always 0 per git-credential protocol).
    """
    if not argv:
        print(
            "git-credential-github-app: usage: git-credential-github-app.py <get|store|erase>",
            file=sys.stderr,
        )
        return 0  # Still 0 — don't break git

    subcommand = argv[0]

    if subcommand == "get":
        return handle_get()
    elif subcommand in ("store", "erase"):
        return handle_store() if subcommand == "store" else handle_erase()
    else:
        print(
            f"git-credential-github-app: unknown subcommand: {subcommand!r}",
            file=sys.stderr,
        )
        return 0  # Still 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
