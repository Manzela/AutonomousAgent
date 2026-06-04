import pytest
import socket


@pytest.fixture(autouse=True)
def _isolate_spine_persistence(monkeypatch, tmp_path):
    """Keep every test's DURABLE spine writes out of the real repo + shared /tmp (post-audit).

    The decision-record JSONL (ship_effect -> append_decision) defaults to the repo's
    ``trajectories/decision-record.jsonl`` and the SpecStore defaults to a SHARED ``/tmp/spine-specs``
    (via ``SPINE_DATA_DIR/specs``); the SP-16/SP-R2 live-spine tests exercise both. Point them at a
    PER-TEST tmp so no test pollutes the working tree or collides cross-file (a real flake source the
    audit traced). We set ``SPINE_DATA_DIR`` (NOT ``SPINE_SPEC_STORE``) so a test that controls the
    spec store via either var still wins: ``SPINE_SPEC_STORE`` (set by some tests) takes precedence,
    and a test that sets only ``SPINE_DATA_DIR`` overrides this default.
    """
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "decision-record.jsonl"))
    monkeypatch.setenv("SPINE_DATA_DIR", str(tmp_path / "data"))


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
