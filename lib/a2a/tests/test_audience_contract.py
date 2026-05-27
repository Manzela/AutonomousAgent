import pytest
from unittest.mock import patch
from lib.a2a.audience_validator import validate_all_peers


def test_sa_email_audience_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")

    yaml_content = """
peers:
  - name: canary
    audience: agent-canary@autonomous-agent-2026.iam.gserviceaccount.com
    """
    peers_path = tmp_path / "peers.yaml"
    peers_path.write_text(yaml_content)

    with patch("lib.a2a.audience_validator._PEERS_CONFIG_PATH", peers_path):
        validate_all_peers()  # Should not raise


def test_url_form_audience_rejected_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")

    yaml_content = """
peers:
  - name: canary
    audience: https://agent-canary.example.test
    """
    peers_path = tmp_path / "peers.yaml"
    peers_path.write_text(yaml_content)

    with patch("lib.a2a.audience_validator._PEERS_CONFIG_PATH", peers_path):
        with pytest.raises(RuntimeError, match="Must be a Service Account email"):
            validate_all_peers()


def test_url_form_audience_warned_in_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "development")

    yaml_content = """
peers:
  - name: canary
    audience: https://agent-canary.example.test
    """
    peers_path = tmp_path / "peers.yaml"
    peers_path.write_text(yaml_content)

    with patch("lib.a2a.audience_validator._PEERS_CONFIG_PATH", peers_path):
        validate_all_peers()  # Should not raise in dev
