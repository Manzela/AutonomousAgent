import pytest
import socket


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """
    1.1 Deterministic Orchestration Audit:
    Block all un-mocked external network calls. Tests that intentionally need
    network must explicitly use vcr cassettes or opt-out.
    """
    import os

    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"

    def _raise_on_connect(*args, **kwargs):
        raise RuntimeError(f"Network calls are blocked in tests. Blocked call to: {args}")

    # We patch socket.socket.connect but allow loopback (127.0.0.1 / localhost / ::1)
    # so localhost test servers and in-process fixtures remain reachable.
    _original_connect = socket.socket.connect

    def _safe_connect(self, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "localhost", "::1"):
            return _original_connect(self, address)
        raise RuntimeError(f"Network calls blocked: {address}")

    monkeypatch.setattr("socket.socket.connect", _safe_connect)


@pytest.fixture(scope="session")
def vcr_config():
    return {
        "record_mode": "none",
        "filter_headers": ["authorization", "api-key"],
    }
