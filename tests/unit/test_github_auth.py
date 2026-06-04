"""Unit tests for ``lib.github_auth`` — SP-00f.2.

TDD contract (four mandatory properties from the task spec):
  (a) A correct JWT is built — iss == app_id, exp <= now + 10 min.
  (b) The installation-token endpoint is called with the JWT as Bearer.
  (c) The returned InstallationToken exposes token + expires_at.
  (d) expires_at is < 2 h from mint time.

All HTTP calls are mocked; no network required.  RSA key pair is generated
once per session via the ``rsa_key_pair`` fixture so tests are hermetic.
The conftest network-block fixture is compatible: all HTTP is intercepted
via the ``_http_post`` test seam before any real socket call is attempted.
"""

from __future__ import annotations

import datetime
import time
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Optional-dep guard — skip gracefully if pyjwt/cryptography not installed
# ---------------------------------------------------------------------------
jwt = pytest.importorskip("jwt", reason="pyjwt[crypto] not installed; skipping SP-00f.2 tests")
try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    pytest.skip("cryptography not installed", allow_module_level=True)

from lib import github_auth  # noqa: E402 — after optional-dep guard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rsa_key_pair():
    """Generate a 2048-bit RSA key pair once for the session."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem


@pytest.fixture()
def key_file(rsa_key_pair, tmp_path) -> Generator[str, None, None]:
    """Write the PEM private key to a temp file and yield its path."""
    p = tmp_path / "private_key.pem"
    p.write_text(rsa_key_pair)
    yield str(p)


@pytest.fixture()
def fake_now() -> int:
    """A fixed 'now' timestamp for deterministic JWT assertions."""
    return int(time.time())


@pytest.fixture()
def good_token_response() -> dict:
    """A minimal GitHub API response for a successful installation token."""
    future = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(hours=1)
    return {
        "token": "ghs_test_installation_token_xyz",
        "expires_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# (a) JWT construction: iss == app_id, exp <= now + 10 min
# ---------------------------------------------------------------------------


class TestBuildJwt:
    def test_iss_equals_app_id(self, rsa_key_pair, fake_now):
        """iss claim must equal the numeric app_id string."""
        token = github_auth._build_jwt("123456", rsa_key_pair, now=fake_now)
        decoded = jwt.decode(token, options={"verify_signature": False})
        assert decoded["iss"] == "123456"

    def test_exp_at_most_10_minutes_from_now(self, rsa_key_pair, fake_now):
        """exp must not exceed now + 10 min (GitHub rejects longer-lived JWTs)."""
        token = github_auth._build_jwt("123456", rsa_key_pair, now=fake_now)
        decoded = jwt.decode(token, options={"verify_signature": False})
        assert decoded["exp"] <= fake_now + 10 * 60

    def test_exp_in_the_future(self, rsa_key_pair, fake_now):
        """exp must be strictly after iat so the JWT is not immediately invalid."""
        token = github_auth._build_jwt("123456", rsa_key_pair, now=fake_now)
        decoded = jwt.decode(token, options={"verify_signature": False})
        assert decoded["exp"] > fake_now

    def test_raises_on_invalid_app_id(self, rsa_key_pair, fake_now):
        """Non-integer app_id must raise ValueError."""
        with pytest.raises(ValueError, match="GITHUB_APP_ID"):
            github_auth._build_jwt("not-a-number", rsa_key_pair, now=fake_now)

    def test_raises_on_zero_app_id(self, rsa_key_pair, fake_now):
        """Zero is not a valid GitHub App ID."""
        with pytest.raises(ValueError):
            github_auth._build_jwt("0", rsa_key_pair, now=fake_now)

    def test_jwt_is_rs256_signed(self, rsa_key_pair, fake_now):
        """The JWT header algorithm field must be RS256 (not HS256 or none)."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        token = github_auth._build_jwt("999", rsa_key_pair, now=fake_now)
        private_key_obj = load_pem_private_key(rsa_key_pair.encode(), password=None)
        public_key = private_key_obj.public_key()
        decoded = jwt.decode(token, public_key, algorithms=["RS256"])
        assert decoded["iss"] == "999"


# ---------------------------------------------------------------------------
# (b) Installation-token endpoint is called with the JWT as Bearer
# ---------------------------------------------------------------------------


class TestRequestInstallationToken:
    def test_endpoint_url_contains_installation_id(self, good_token_response):
        """The POST URL must contain the installation ID."""
        captured = {}

        def fake_post(url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return good_token_response

        github_auth._request_installation_token("987654", "jwt.stub.sig", _http_post=fake_post)
        assert "987654" in captured["url"]

    def test_authorization_header_uses_bearer_jwt(self, good_token_response):
        """Authorization: Bearer <jwt> is sent to the GitHub endpoint."""
        captured = {}

        def fake_post(url, headers):
            captured["auth"] = headers.get("Authorization", "")
            return good_token_response

        github_auth._request_installation_token("1", "myjwt.header.sig", _http_post=fake_post)
        assert captured["auth"] == "Bearer myjwt.header.sig"

    def test_accept_header_is_github_json(self, good_token_response):
        """Accept: application/vnd.github+json must be present."""
        captured = {}

        def fake_post(url, headers):
            captured["accept"] = headers.get("Accept", "")
            return good_token_response

        github_auth._request_installation_token("1", "jwt", _http_post=fake_post)
        assert captured["accept"] == "application/vnd.github+json"

    def test_real_http_post_raises_runtime_error_on_http_error(self, monkeypatch):
        """An HTTPError (e.g. 401/403 on revoked credentials) must raise RuntimeError."""
        import urllib.error
        import urllib.request
        from io import BytesIO

        def mock_urlopen(*args, **kwargs):
            fp = BytesIO(b"Bad credentials (revoked token)")
            raise urllib.error.HTTPError(
                url="https://api.github.com",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=fp,
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        with pytest.raises(RuntimeError, match="HTTP 401"):
            github_auth._real_http_post("https://api.github.com", {})


# ---------------------------------------------------------------------------
# (c) Returned token + expiry are surfaced
# ---------------------------------------------------------------------------


class TestInstallationTokenFields:
    def test_token_field_matches_api_response(self, good_token_response):
        """``InstallationToken.token`` must equal what the GitHub API returned."""

        def fake_post(url, headers):
            return good_token_response

        itok = github_auth._request_installation_token("1", "jwt", _http_post=fake_post)
        assert itok.token == good_token_response["token"]

    def test_expires_at_is_tz_aware_datetime(self, good_token_response):
        """``InstallationToken.expires_at`` must be a tz-aware UTC datetime."""

        def fake_post(url, headers):
            return good_token_response

        itok = github_auth._request_installation_token("1", "jwt", _http_post=fake_post)
        assert isinstance(itok.expires_at, datetime.datetime)
        assert itok.expires_at.tzinfo is not None

    def test_missing_token_field_raises_runtime_error(self):
        """A response without 'token' must raise RuntimeError."""

        def fake_post(url, headers):
            return {"expires_at": "2099-01-01T00:00:00Z"}

        with pytest.raises(RuntimeError, match="token"):
            github_auth._request_installation_token("1", "jwt", _http_post=fake_post)

    def test_missing_expires_at_raises_runtime_error(self):
        """A response without 'expires_at' must raise RuntimeError."""

        def fake_post(url, headers):
            return {"token": "tok"}

        with pytest.raises(RuntimeError, match="expires_at"):
            github_auth._request_installation_token("1", "jwt", _http_post=fake_post)


# ---------------------------------------------------------------------------
# (d) expiry < 2 h
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expiry_less_than_2_hours(self, good_token_response):
        """lifetime_seconds must be < 7200 (2 h)."""

        def fake_post(url, headers):
            return good_token_response

        itok = github_auth._request_installation_token("1", "jwt", _http_post=fake_post)
        assert itok.lifetime_seconds < 2 * 3600

    def test_mint_rejects_expiry_over_2_hours(self, rsa_key_pair, key_file, fake_now):
        """mint_installation_token() must raise if GitHub somehow returns >2h expiry."""
        far_future = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(hours=3)
        bad_response = {
            "token": "ghs_bad",
            "expires_at": far_future.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        def fake_post(url, headers):
            return bad_response

        with pytest.raises(RuntimeError, match="2 h"):
            github_auth.mint_installation_token(
                app_id="123",
                installation_id="456",
                private_key_path=key_file,
                _http_post=fake_post,
                _now=fake_now,
            )


# ---------------------------------------------------------------------------
# mint_installation_token() integration-style unit tests
# ---------------------------------------------------------------------------


class TestMintInstallationToken:
    def test_happy_path_returns_installation_token(
        self, rsa_key_pair, key_file, good_token_response, fake_now
    ):
        """Full mint_installation_token() call returns InstallationToken with token + expiry."""

        def fake_post(url, headers):
            return good_token_response

        itok = github_auth.mint_installation_token(
            app_id="123456",
            installation_id="789",
            private_key_path=key_file,
            _http_post=fake_post,
            _now=fake_now,
        )
        assert itok.token == good_token_response["token"]
        assert itok.lifetime_seconds < 2 * 3600

    def test_reads_config_from_env_vars(
        self, rsa_key_pair, key_file, good_token_response, fake_now, monkeypatch
    ):
        """When no args supplied, credentials are read from env vars."""
        monkeypatch.setenv("GITHUB_APP_ID", "111")
        monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "222")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", key_file)

        def fake_post(url, headers):
            return good_token_response

        itok = github_auth.mint_installation_token(_http_post=fake_post, _now=fake_now)
        assert itok.token == good_token_response["token"]

    def test_raises_on_missing_app_id_env(self, monkeypatch):
        """EnvironmentError when GITHUB_APP_ID is absent."""
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        with pytest.raises(EnvironmentError, match="GITHUB_APP_ID"):
            github_auth.mint_installation_token()

    def test_raises_on_missing_key_file(self, monkeypatch):
        """FileNotFoundError when the key path does not exist."""
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        with pytest.raises(FileNotFoundError):
            github_auth.mint_installation_token(
                app_id="1",
                installation_id="2",
                private_key_path="/nonexistent/path/key.pem",
            )

    def test_jwt_iss_matches_app_id_in_bearer(
        self, rsa_key_pair, key_file, good_token_response, fake_now
    ):
        """End-to-end: the Bearer JWT sent to GitHub has iss == app_id."""
        captured_jwt = {}

        def fake_post(url, headers):
            captured_jwt["auth"] = headers.get("Authorization", "")
            return good_token_response

        github_auth.mint_installation_token(
            app_id="55555",
            installation_id="99",
            private_key_path=key_file,
            _http_post=fake_post,
            _now=fake_now,
        )
        bearer = captured_jwt["auth"]
        assert bearer.startswith("Bearer ")
        raw_jwt = bearer[len("Bearer ") :]
        decoded = jwt.decode(raw_jwt, options={"verify_signature": False})
        assert decoded["iss"] == "55555"
