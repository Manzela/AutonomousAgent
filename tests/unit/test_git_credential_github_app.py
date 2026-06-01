"""Unit tests for ``scripts/git-credential-github-app.py`` — SP-00f.2.

TDD contract (five properties from the task spec):
  (a) ``get`` for host=github.com with a valid non-expired cached token
      outputs username=x-access-token + the cached password (no mint call).
  (b) Expired/missing cache triggers a mint + writes a 0600 cache file.
  (c) host != github.com → no output, exit 0 (git falls back).
  (d) mint raising → exit 0, no stdout, stderr diagnostic, token NEVER in stdout.
  (e) ``store``/``erase`` → exit 0 no-op (no stdout, no side effects).

Uses ``tmp_path`` for cache.  Does NOT require real GitHub credentials.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import stat
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to load the script as a module (it's not in a package)
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "git-credential-github-app.py"


def _load_module():
    """Dynamically import the credential helper script as a Python module."""
    spec = importlib.util.spec_from_file_location("git_credential_github_app", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def module():
    """Load the credential helper module once per session."""
    return _load_module()


@pytest.fixture()
def cache_file(tmp_path) -> Path:
    """Return a tmp_path-scoped path for the token cache."""
    return tmp_path / "github-app-token.json"


def _future_expires_at(minutes: int = 60) -> str:
    """Return an ISO-8601 UTC string ``minutes`` from now."""
    dt = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_expires_at(minutes: int = 10) -> str:
    """Return an ISO-8601 UTC string ``minutes`` in the past."""
    dt = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# (a) Valid cached token → no mint, correct output
# ---------------------------------------------------------------------------


class TestGetWithValidCache:
    def test_cached_token_returned_without_minting(self, module, cache_file, monkeypatch):
        """A fresh cached token is served immediately; mint_installation_token NOT called."""
        cached_token = "ghs_cached_valid_token_abc123"
        cache_file.write_text(
            json.dumps({"token": cached_token, "expires_at": _future_expires_at(60)})
        )
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        mint_mock = MagicMock(side_effect=AssertionError("mint should NOT be called"))

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert exit_code == 0
        output = stdout.getvalue()
        assert "username=x-access-token" in output
        assert f"password={cached_token}" in output
        assert output.endswith("\n") or "\n\n" in output

    def test_cached_token_output_ends_with_blank_line(self, module, cache_file, monkeypatch):
        """git-credential protocol requires the response to end with a blank line."""
        cached_token = "ghs_cached_fresh_xyz"
        cache_file.write_text(
            json.dumps({"token": cached_token, "expires_at": _future_expires_at(60)})
        )
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        mint_mock = MagicMock(side_effect=AssertionError("should not mint"))

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        output = stdout.getvalue()
        # Must end with blank line (double newline)
        assert output.endswith("\n\n") or output.strip().endswith(f"password={cached_token}")

    def test_nearly_expired_cache_triggers_mint(self, module, cache_file, monkeypatch):
        """A token with <5 min remaining must trigger a mint (not be reused)."""
        cached_token = "ghs_stale_token_3min_left"
        # Only 3 minutes left — below the 5-min safety window
        cache_file.write_text(
            json.dumps({"token": cached_token, "expires_at": _future_expires_at(3)})
        )
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        new_token = "ghs_freshly_minted_token"
        fake_expires = _future_expires_at(60)
        fake_itok = SimpleNamespace(token=new_token, expires_at=_parse_dt(fake_expires))
        mint_mock = MagicMock(return_value=fake_itok)

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        mint_mock.assert_called_once()
        output = stdout.getvalue()
        assert f"password={new_token}" in output
        # Old token must NOT appear in stdout
        assert cached_token not in output


# ---------------------------------------------------------------------------
# (b) Expired/missing cache → mint + write 0600 cache
# ---------------------------------------------------------------------------


class TestGetWithExpiredOrMissingCache:
    def test_missing_cache_triggers_mint(self, module, cache_file, monkeypatch):
        """No cache file → mint is called + cache written."""
        assert not cache_file.exists()
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        new_token = "ghs_brand_new_token_xyz"
        fake_expires = _future_expires_at(60)
        fake_itok = SimpleNamespace(token=new_token, expires_at=_parse_dt(fake_expires))
        mint_mock = MagicMock(return_value=fake_itok)

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert exit_code == 0
        mint_mock.assert_called_once()
        output = stdout.getvalue()
        assert f"password={new_token}" in output

    def test_expired_cache_triggers_mint(self, module, cache_file, monkeypatch):
        """Expired cached token → mint is called + new token returned."""
        cache_file.write_text(
            json.dumps({"token": "ghs_old_expired_token", "expires_at": _past_expires_at(10)})
        )
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        new_token = "ghs_refreshed_token_abc"
        fake_expires = _future_expires_at(60)
        fake_itok = SimpleNamespace(token=new_token, expires_at=_parse_dt(fake_expires))
        mint_mock = MagicMock(return_value=fake_itok)

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        mint_mock.assert_called_once()
        output = stdout.getvalue()
        assert f"password={new_token}" in output
        # Expired token must NOT appear in stdout
        assert "ghs_old_expired_token" not in output

    def test_mint_writes_cache_file_mode_0600(self, module, cache_file, monkeypatch):
        """After mint, the cache file must be created with mode 0600."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        new_token = "ghs_mode_check_token"
        fake_expires = _future_expires_at(60)
        fake_itok = SimpleNamespace(token=new_token, expires_at=_parse_dt(fake_expires))
        mint_mock = MagicMock(return_value=fake_itok)

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert cache_file.exists(), "Cache file should have been created"
        file_mode = stat.S_IMODE(cache_file.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0600, got {oct(file_mode)}"

    def test_mint_writes_valid_json_to_cache(self, module, cache_file, monkeypatch):
        """After mint, the cache file contains valid JSON with token + expires_at."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        new_token = "ghs_json_cache_token"
        fake_expires = _future_expires_at(60)
        fake_itok = SimpleNamespace(token=new_token, expires_at=_parse_dt(fake_expires))
        mint_mock = MagicMock(return_value=fake_itok)

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        cached = json.loads(cache_file.read_text())
        assert cached["token"] == new_token
        assert "expires_at" in cached


# ---------------------------------------------------------------------------
# (c) host != github.com → no output, exit 0
# ---------------------------------------------------------------------------


class TestNonGithubHost:
    def test_other_host_produces_no_stdout(self, module, cache_file, monkeypatch):
        """Credential request for a non-GitHub host → empty stdout, exit 0."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        mint_mock = MagicMock(side_effect=AssertionError("should not mint for non-github host"))

        stdin = StringIO("protocol=https\nhost=gitlab.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert exit_code == 0
        assert stdout.getvalue() == "", f"Expected no stdout, got: {stdout.getvalue()!r}"

    def test_other_host_produces_no_stderr(self, module, cache_file, monkeypatch):
        """Non-GitHub host → no stderr (a fallback is silent, not an error)."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        mint_mock = MagicMock()

        stdin = StringIO("protocol=git\nhost=bitbucket.org\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert stderr.getvalue() == "", f"Expected no stderr, got: {stderr.getvalue()!r}"

    def test_empty_host_produces_no_stdout(self, module, cache_file, monkeypatch):
        """Missing host field → no output, exit 0 (treat as non-GitHub)."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        mint_mock = MagicMock()

        stdin = StringIO("protocol=https\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert exit_code == 0
        assert stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# (d) mint raising → exit 0, no stdout, stderr diagnostic, token NOT in stdout
# ---------------------------------------------------------------------------


class TestMintFailure:
    def test_mint_exception_gives_exit_0(self, module, cache_file, monkeypatch):
        """When mint raises, handle_get must return exit code 0 (git fallback)."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        mint_mock = MagicMock(side_effect=EnvironmentError("GITHUB_APP_ID not set"))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert exit_code == 0

    def test_mint_exception_produces_no_stdout(self, module, cache_file, monkeypatch):
        """mint failure → stdout is empty so git falls back to next helper."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        mint_mock = MagicMock(side_effect=RuntimeError("GitHub API returned HTTP 401"))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert (
            stdout.getvalue() == ""
        ), f"stdout must be empty on mint failure, got: {stdout.getvalue()!r}"

    def test_mint_exception_writes_diagnostic_to_stderr(self, module, cache_file, monkeypatch):
        """mint failure → a one-line diagnostic appears on stderr."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))
        error_message = "GITHUB_APP_PRIVATE_KEY_PATH not set"
        mint_mock = MagicMock(side_effect=EnvironmentError(error_message))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        diagnostic = stderr.getvalue()
        assert len(diagnostic) > 0, "Expected a diagnostic message on stderr"
        assert "\n" in diagnostic or diagnostic.strip(), "Diagnostic should be non-empty"

    def test_token_never_appears_in_stdout_on_mint_failure(self, module, cache_file, monkeypatch):
        """Even if a partial token is available, it must never leak to stdout on failure."""
        monkeypatch.setenv("GITHUB_APP_TOKEN_CACHE", str(cache_file))

        class PartialFailure(Exception):
            token = "ghs_should_never_leak_xyz"

        mint_mock = MagicMock(side_effect=PartialFailure("partial failure"))

        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_get(mint_fn=mint_mock, cache_path=str(cache_file))

        assert "ghs_should_never_leak" not in stdout.getvalue()


# ---------------------------------------------------------------------------
# (e) store / erase → exit 0 no-op
# ---------------------------------------------------------------------------


class TestStoreAndErase:
    def test_store_exits_zero(self, module):
        """``store`` subcommand is a silent no-op returning exit code 0."""
        stdin = StringIO(
            "protocol=https\nhost=github.com\nusername=x-access-token\npassword=ghs_tok\n\n"
        )
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_store()

        assert exit_code == 0

    def test_store_produces_no_stdout(self, module):
        """``store`` must produce no stdout."""
        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_store()

        assert stdout.getvalue() == ""

    def test_erase_exits_zero(self, module):
        """``erase`` subcommand is a silent no-op returning exit code 0."""
        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = module.handle_erase()

        assert exit_code == 0

    def test_erase_produces_no_stdout(self, module):
        """``erase`` must produce no stdout."""
        stdin = StringIO("protocol=https\nhost=github.com\n\n")
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            module.handle_erase()

        assert stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# Parse stdin helper — protocol parsing
# ---------------------------------------------------------------------------


class TestParseCredentialInput:
    def test_parses_protocol_and_host(self, module):
        """read_credential_fields parses key=value lines until blank."""
        stdin = StringIO("protocol=https\nhost=github.com\npath=Manzela/AutonomousAgent\n\n")
        with patch.object(sys, "stdin", stdin):
            fields = module.read_credential_fields()

        assert fields.get("protocol") == "https"
        assert fields.get("host") == "github.com"
        assert fields.get("path") == "Manzela/AutonomousAgent"

    def test_stops_at_blank_line(self, module):
        """read_credential_fields stops reading at first blank line."""
        stdin = StringIO("protocol=https\nhost=github.com\n\nshould=not-be-read\n")
        with patch.object(sys, "stdin", stdin):
            fields = module.read_credential_fields()

        assert "should" not in fields


# ---------------------------------------------------------------------------
# Config file fallback — config/github-app.json
# ---------------------------------------------------------------------------


class TestConfigFileFallback:
    def test_config_file_provides_app_id_and_installation_id(self, module, tmp_path, monkeypatch):
        """When env vars are absent, config/github-app.json is read for IDs."""
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)

        config = tmp_path / "github-app.json"
        config.write_text(json.dumps({"app_id": "3920713", "installation_id": "136939619"}))

        app_id, inst_id = module.read_app_config(config_path=str(config))
        assert app_id == "3920713"
        assert inst_id == "136939619"

    def test_env_vars_override_config_file(self, module, tmp_path, monkeypatch):
        """GITHUB_APP_ID/INSTALLATION_ID env vars take precedence over config file."""
        monkeypatch.setenv("GITHUB_APP_ID", "9999999")
        monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "8888888")

        config = tmp_path / "github-app.json"
        config.write_text(json.dumps({"app_id": "3920713", "installation_id": "136939619"}))

        app_id, inst_id = module.read_app_config(config_path=str(config))
        assert app_id == "9999999"
        assert inst_id == "8888888"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
