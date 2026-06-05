import os
import sys
import pytest
from unittest.mock import MagicMock, patch, mock_open

# Add deploy/scripts to path to import the entrypoint
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../deploy/scripts")))

import github_mcp_entrypoint


class DummyExecException(BaseException):
    """Exception to prevent os.execv from actually replacing the test process."""

    pass


@pytest.fixture
def clean_env():
    """Ensure environment is cleaned up after each test."""
    original_env = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original_env)


def test_entrypoint_happy_path(clean_env):
    """Test that when App credentials are set, token is minted and written to tmpfs, and the binary is exec-ed."""
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "67890"
    os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = "/path/to/key.pem"
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN_FILE"] = "/tmp/test_token"
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = "old_pat_to_be_cleared"

    mock_token = MagicMock()
    mock_token.token = "ghs_installation_token_abc"

    # Mock the mint_installation_token function
    mock_mint = MagicMock(return_value=mock_token)

    # Mock os.execv to raise DummyExecException so we can verify the call without executing
    mock_execv = MagicMock(side_effect=DummyExecException("execv called"))

    # Mock file writing
    m_open = mock_open()

    with (
        patch("lib.github_auth.mint_installation_token", mock_mint),
        patch("os.execv", mock_execv),
        patch("os.path.exists", return_value=True),
        patch("os.makedirs") as mock_makedirs,
        patch("builtins.open", m_open),
        patch.object(sys, "argv", ["github_mcp_entrypoint.py"]),
    ):
        with pytest.raises(DummyExecException, match="execv called"):
            github_mcp_entrypoint.main()

        # Check mint was called with the correct parameters
        mock_mint.assert_called_once_with(
            app_id="12345", installation_id="67890", private_key_path="/path/to/key.pem"
        )

        # Check directory creation and file writing
        mock_makedirs.assert_called_once_with("/tmp", exist_ok=True)
        m_open.assert_called_once_with("/tmp/test_token", "w")
        m_open().write.assert_called_once_with("ghs_installation_token_abc")

        # Check environment mutations
        assert os.environ["GITHUB_PERSONAL_ACCESS_TOKEN_FILE"] == "/tmp/test_token"
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in os.environ

        # Check execv parameters
        mock_execv.assert_called_once()
        args = mock_execv.call_args[0]
        assert args[0] == "/server/github-mcp-server"
        assert args[1] == ["/server/github-mcp-server"]


def test_entrypoint_fail_soft_mint_exception(clean_env):
    """Test that when App credentials fail to mint a token, we log error and still exec."""
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "67890"
    os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = "/path/to/key.pem"
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN_FILE"] = "/tmp/test_token"

    mock_mint = MagicMock(side_effect=RuntimeError("API error"))
    mock_execv = MagicMock(side_effect=DummyExecException("execv called"))

    with (
        patch("lib.github_auth.mint_installation_token", mock_mint),
        patch("os.execv", mock_execv),
        patch("os.path.exists", return_value=True),
        patch("os.makedirs") as mock_makedirs,
        patch("builtins.open") as mock_open_func,
        patch.object(sys, "argv", ["github_mcp_entrypoint.py"]),
    ):
        with pytest.raises(DummyExecException, match="execv called"):
            github_mcp_entrypoint.main()

        # Check that we did not write any file
        mock_open_func.assert_not_called()
        mock_makedirs.assert_not_called()

        # Ensure we still execv
        mock_execv.assert_called_once()


def test_entrypoint_fallback_no_env(clean_env):
    """Test that when App credentials are not set, we bypass token minting and exec directly."""
    # Ensure no App env vars are set
    for var in ["GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY_PATH"]:
        if var in os.environ:
            del os.environ[var]

    mock_mint = MagicMock()
    mock_execv = MagicMock(side_effect=DummyExecException("execv called"))

    with (
        patch("lib.github_auth.mint_installation_token", mock_mint),
        patch("os.execv", mock_execv),
        patch("os.path.exists", return_value=True),
        patch.object(sys, "argv", ["github_mcp_entrypoint.py"]),
    ):
        with pytest.raises(DummyExecException, match="execv called"):
            github_mcp_entrypoint.main()

        # Ensure mint was not called
        mock_mint.assert_not_called()

        # Ensure we still execv
        mock_execv.assert_called_once()
