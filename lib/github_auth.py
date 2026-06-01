"""GitHub App installation-token helper — SP-00f.2.

Mints a SHORT-LIVED GitHub App installation token (valid ≤1 h; ≪2 h) from:

  - ``GITHUB_APP_ID``         — the App's numeric identifier (string int)
  - ``GITHUB_APP_INSTALLATION_ID`` — the installation id for the target org/user
  - ``GITHUB_APP_PRIVATE_KEY_PATH`` — filesystem path to the PEM-encoded RSA
                                       private key; read at call time, not import time

The returned :class:`InstallationToken` exposes:
  - ``token``   — the opaque bearer value for ``Authorization: token <token>``
  - ``expires_at`` — UTC :class:`datetime.datetime` (tz-aware)

Usage in practice (token-refresh sidecar or restart wrapper — see
``NOTE: hot-reload constraint`` below)::

    from lib.github_auth import mint_installation_token
    itok = mint_installation_token()
    # feed itok.token to the github-mcp-server restart; see deploy notes.

**NOTE: hot-reload constraint** — ``ghcr.io/github/github-mcp-server`` reads
``GITHUB_PERSONAL_ACCESS_TOKEN`` (or ``_FILE``) once at startup and holds it
for its lifetime.  It has no built-in App auth, no JWKS endpoint, and no
signal handler to reload a refreshed token.  Because App installation tokens
expire after ≤1 h (GitHub maximum), you CANNOT simply inject the token once
and leave the container running indefinitely; you MUST either:

  1. **Token-refresh sidecar** — a small container that re-mints the token
     ~5 min before expiry, writes it to the shared ``/run/secrets/github_token``
     tmpfs, then sends ``SIGHUP`` / ``docker compose restart github-mcp`` to
     force a re-read.  (The sidecar itself is NOT built here — it is a gated
     follow-on step for SP-00f.3.)
  2. **Restart strategy** — run github-mcp with ``restart: always`` or a
     cron-driven ``docker compose up -d --force-recreate github-mcp`` fired
     every 55 min so the token written at container-start is always fresh.

The docker-compose EDIT in this PR picks option 2 as an interim measure
(``restart: always`` + a ``GITHUB_TOKEN_FILE`` pointing at the tmpfs) and
documents option 1 as the target.

**Dependency note** — JWT assembly uses ``cryptography`` (RSA-SHA256 / RS256)
which is already in ``[project.optional-dependencies.a2a]``.  For production
use of this helper, ``uv sync --extra a2a`` (or a targeted
``pip install cryptography>=43.0``) must be run.  *No new dependency* is
introduced; ``pyjwt[crypto]>=2.9`` is also in the ``a2a`` extra and is
used here as the JWT library rather than hand-rolling the base64url encoding.
If the ``a2a`` extra is not installed, both packages raise ``ImportError``
at call time with a clear message pointing to the install step.

**CLI / entrypoint** — this module also provides a ``main(argv)`` entry point
that mints and prints a token, suitable for use in an entrypoint wrapper script
or the token-refresh sidecar (SP-00f.3).  Run directly::

    python -m lib.github_auth          # uses GITHUB_APP_* env vars
    python lib/github_auth.py --help   # show usage
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────

# GitHub issues a maximum 1-hour expiry for installation tokens.
# We use 55 min to provide a 5-min safety window for clock skew + restart lag.
_INSTALLATION_TOKEN_LIFETIME_SECS = 55 * 60  # 3 300 s

# JWT "issued-at / expiry" window.  GitHub rejects JWTs with exp > 10 min.
_JWT_LIFETIME_SECS = 9 * 60  # 540 s — under the 10-min ceiling

# GitHub App JWT endpoint template.
_INSTALLATION_TOKEN_URL = "https://api.github.com/app/installations/{installation_id}/access_tokens"


# ── data types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallationToken:
    """A short-lived GitHub App installation token.

    Attributes:
        token:      Opaque bearer value (``Authorization: token <token>``).
        expires_at: Expiry time returned by GitHub (UTC, tz-aware).
    """

    token: str
    expires_at: datetime.datetime

    @property
    def lifetime_seconds(self) -> float:
        """Seconds until expiry from the moment of reading this property."""
        delta = self.expires_at - datetime.datetime.now(tz=datetime.timezone.utc)
        return delta.total_seconds()

    def is_expired(self) -> bool:
        return self.lifetime_seconds <= 0


# ── JWT assembly ─────────────────────────────────────────────────────────────


def _build_jwt(app_id: str, private_key_pem: str, now: Optional[int] = None) -> str:
    """Assemble and sign a GitHub App JWT (RS256).

    Args:
        app_id:          GitHub App numeric ID (as a string).
        private_key_pem: PEM-encoded RSA private key.
        now:             Override for current UNIX timestamp (test hook).

    Returns:
        Compact JWS string ``header.payload.sig``.

    Raises:
        ImportError: if ``pyjwt[crypto]`` / ``cryptography`` is not installed.
        ValueError:  if ``app_id`` is not a positive integer string.
    """
    try:
        import jwt  # pyjwt[crypto]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "pyjwt[crypto] is required for GitHub App JWT assembly. "
            "Run: uv sync --extra a2a   (or pip install 'pyjwt[crypto]>=2.9')"
        ) from exc

    if not app_id.strip().lstrip("-").isdigit() or int(app_id) <= 0:
        raise ValueError(f"GITHUB_APP_ID must be a positive integer string, got: {app_id!r}")

    iat = now if now is not None else int(time.time())
    exp = iat + _JWT_LIFETIME_SECS

    payload = {
        "iss": app_id,  # issuer = App ID
        "iat": iat - 60,  # GitHub recommends subtracting 60s for clock skew
        "exp": exp,
    }
    token: str = jwt.encode(payload, private_key_pem, algorithm="RS256")
    return token


# ── token request ─────────────────────────────────────────────────────────────


def _request_installation_token(
    installation_id: str,
    jwt_token: str,
    *,
    _http_post=None,  # test seam: inject a callable(url, headers) → dict
) -> InstallationToken:
    """POST to the GitHub installations endpoint and return an InstallationToken.

    Args:
        installation_id: GitHub App installation ID string.
        jwt_token:       Signed App JWT from :func:`_build_jwt`.
        _http_post:      Optional test hook replacing the real HTTP call.
                         Called as ``_http_post(url, headers)`` and must return
                         a ``dict`` with at least ``token`` and ``expires_at``.

    Returns:
        :class:`InstallationToken`

    Raises:
        RuntimeError: on any HTTP error or missing response fields.
    """
    url = _INSTALLATION_TOKEN_URL.format(installation_id=installation_id)
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if _http_post is not None:
        data = _http_post(url, headers)
    else:  # pragma: no cover — covered by integration tests, not unit tests
        data = _real_http_post(url, headers)

    try:
        raw_token: str = data["token"]
        raw_expiry: str = data["expires_at"]
    except KeyError as exc:
        raise RuntimeError(
            f"GitHub App token response missing field {exc}; got keys: {list(data)}"
        ) from exc

    # GitHub returns ISO-8601 with trailing "Z" (e.g. "2026-06-01T23:59:00Z").
    expires_at = datetime.datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))

    return InstallationToken(token=raw_token, expires_at=expires_at)


def _real_http_post(url: str, headers: dict) -> dict:
    """Perform a real HTTPS POST using only stdlib ``urllib``."""
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code} for {url}: {body}") from exc


# ── public API ────────────────────────────────────────────────────────────────


def mint_installation_token(
    *,
    app_id: Optional[str] = None,
    installation_id: Optional[str] = None,
    private_key_path: Optional[str] = None,
    _http_post=None,  # test seam forwarded to _request_installation_token
    _now: Optional[int] = None,  # test seam forwarded to _build_jwt
) -> InstallationToken:
    """Mint a short-lived GitHub App installation token.

    All three parameters default to the corresponding environment variables:

    - ``GITHUB_APP_ID``
    - ``GITHUB_APP_INSTALLATION_ID``
    - ``GITHUB_APP_PRIVATE_KEY_PATH`` — filesystem path; read at call time

    Args:
        app_id:           Override for ``GITHUB_APP_ID``.
        installation_id:  Override for ``GITHUB_APP_INSTALLATION_ID``.
        private_key_path: Override for ``GITHUB_APP_PRIVATE_KEY_PATH``.
        _http_post:       Test seam — see :func:`_request_installation_token`.
        _now:             Test seam — override current Unix timestamp for JWT.

    Returns:
        :class:`InstallationToken` with ``token`` and ``expires_at``.

    Raises:
        EnvironmentError: if a required env var is missing.
        FileNotFoundError: if the key file does not exist at the resolved path.
        ValueError: if ``app_id`` is invalid.
        RuntimeError: on HTTP errors from the GitHub API.
    """
    resolved_app_id = app_id or os.environ.get("GITHUB_APP_ID", "")
    resolved_installation_id = installation_id or os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
    resolved_key_path = private_key_path or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")

    missing = [
        name
        for name, val in [
            ("GITHUB_APP_ID", resolved_app_id),
            ("GITHUB_APP_INSTALLATION_ID", resolved_installation_id),
            ("GITHUB_APP_PRIVATE_KEY_PATH", resolved_key_path),
        ]
        if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and "
            "GITHUB_APP_PRIVATE_KEY_PATH before calling mint_installation_token()."
        )

    with open(resolved_key_path, "r") as fh:
        private_key_pem = fh.read()

    jwt_token = _build_jwt(resolved_app_id, private_key_pem, now=_now)
    itok = _request_installation_token(resolved_installation_id, jwt_token, _http_post=_http_post)

    lifetime = itok.lifetime_seconds
    if lifetime > 2 * 3600:
        raise RuntimeError(
            f"GitHub returned a token that expires in {lifetime:.0f}s (> 2 h). "
            "This violates the SP-00f.2 invariant — investigate the API response."
        )

    logger.info(
        "GitHub App installation token minted; expires_at=%s lifetime_secs=%.0f",
        itok.expires_at.isoformat(),
        lifetime,
    )
    return itok


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    """Mint and print a GitHub App installation token.

    This is the legitimate runtime entrypoint used by the token-refresh
    sidecar (SP-00f.3) and the interim entrypoint-wrapper strategy.  It reads
    credentials from the standard environment variables (or --app-id /
    --installation-id / --key-path overrides) and prints the token to stdout.

    Exit codes: 0 on success, 1 on any error.

    Usage::

        python -m lib.github_auth
        python lib/github_auth.py --app-id 123 --installation-id 456 --key-path /run/secrets/key
    """
    import argparse

    ap = argparse.ArgumentParser(description="Mint a GitHub App installation token (SP-00f.2)")
    ap.add_argument("--app-id", default=None, help="GitHub App ID (overrides GITHUB_APP_ID)")
    ap.add_argument(
        "--installation-id",
        default=None,
        help="Installation ID (overrides GITHUB_APP_INSTALLATION_ID)",
    )
    ap.add_argument(
        "--key-path",
        default=None,
        help="Path to PEM private key (overrides GITHUB_APP_PRIVATE_KEY_PATH)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Output {token, expires_at} as JSON instead of bare token",
    )
    args = ap.parse_args(argv)

    try:
        itok = mint_installation_token(
            app_id=args.app_id,
            installation_id=args.installation_id,
            private_key_path=args.key_path,
        )
    except (EnvironmentError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        import json as _json

        print(_json.dumps({"token": itok.token, "expires_at": itok.expires_at.isoformat()}))
    else:
        print(itok.token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
