"""Persistence Trap (#12.c) integration tests — the load-bearing contract
for the J3 trajectory shipper.

# DO NOT WEAKEN ANY TEST IN THIS FILE.
# See audit/2026-05-21-persistence-trap-12c/test-contract.md.

The three test variants together prove:

* T1 (Floor-only happy path) — sanitize is called, response is uploaded,
  canary PII tokens are absent in the uploaded blob, and the redaction
  markers ARE present (proves the sanitize wasn't a no-op).
* T2 (Explicit-sanitize success) — sanitize is invoked exactly once with
  the expected template, and the string it returns is what was uploaded.
* T3 (Broken-sanitize MUST fail loud) — the load-bearing test: when
  ``templates.sanitize`` raises, the shipper MUST raise
  ``ModelArmorSanitizeUnavailable``, ``dispatch("F37", ...)`` MUST be
  called exactly once with the failing record's ``tool_call_id``, and the
  unredacted payload MUST NOT be uploaded to GCS.

Hermeticity: stub clients are used throughout this file. A live Model
Armor endpoint is opt-in via ``PERSISTENCE_TRAP_LIVE=1`` env var (covered
by the nightly workflow, not by per-PR CI).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from lib.trajectory import ModelArmorSanitizeUnavailable, TrajectoryShipper
from lib.trajectory.shipper import _object_name_for


# ---------------------------------------------------------------------------
# Canary payload — must trip every InfoType in the j1-trajectory-shipper
# template. Tokens are RFC/SSA/NANPA-reserved so a leak is unambiguously
# fixture-traceable.
# ---------------------------------------------------------------------------

CANARY_TOKENS = {
    "EMAIL_ADDRESS": "canary+persistencetrap@example.test",
    "US_SOCIAL_SECURITY_NUMBER": "999-88-7777",
    "CREDIT_CARD_NUMBER": "4111-1111-1111-1111",
    "PHONE_NUMBER": "(555) 010-1234",
}


def _canary_payload() -> dict:
    return {
        "schema_version": 1,
        "tool_call_id": "test-pt-001",
        "tool_name": "test_tool",
        "timestamp": "2026-05-21T12:00:00Z",
        "args": {
            "user_email": CANARY_TOKENS["EMAIL_ADDRESS"],
            "ssn": CANARY_TOKENS["US_SOCIAL_SECURITY_NUMBER"],
            "card": CANARY_TOKENS["CREDIT_CARD_NUMBER"],
            "phone": CANARY_TOKENS["PHONE_NUMBER"],
        },
        "result_preview": f"Created account for {CANARY_TOKENS['EMAIL_ADDRESS']}",
        "verdict": "approve",
        "consensus": {"votes": 4, "outcome": "approve"},
        "judge_responses": ["LGTM", "LGTM", "LGTM", "LGTM"],
    }


# ---------------------------------------------------------------------------
# In-memory fake GCS — matches the surface that lib.trajectory.shipper uses
# (``client.bucket(name).blob(name).upload_from_string``) and exposes
# ``get`` / ``exists`` for assertions. Avoids fake-gcs-server / gcsfs deps.
# ---------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, store: dict, key: tuple[str, str]) -> None:
        self._store = store
        self._key = key
        self.content_type: Optional[str] = None

    def upload_from_string(self, payload: str, content_type: Optional[str] = None) -> None:
        self._store[self._key] = payload
        self.content_type = content_type


class _FakeBucket:
    def __init__(self, store: dict, name: str) -> None:
        self._store = store
        self._name = name

    def blob(self, object_name: str) -> _FakeBlob:
        return _FakeBlob(self._store, (self._name, object_name))


class FakeGCSClient:
    """In-memory GCS double. Same shape as ``google.cloud.storage.Client``
    for the calls the shipper makes (``bucket().blob().upload_from_string``).

    Test-only helpers ``get`` / ``exists`` are NOT on the real client; they
    are explicit assertion helpers, named so a reviewer can't mistake them
    for real GCS API surface.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._store, name)

    # --- test assertion helpers --------------------------------------------
    def get(self, bucket: str, object_name: str) -> str:
        return self._store[(bucket, object_name)]

    def exists(self, bucket: str, object_name: str) -> bool:
        return (bucket, object_name) in self._store

    @property
    def stored_keys(self) -> list[tuple[str, str]]:
        return list(self._store.keys())


# ---------------------------------------------------------------------------
# Sanitize stubs — call-recording wrappers around configurable responses.
# ---------------------------------------------------------------------------


@dataclass
class _SanitizeCall:
    template: str
    content: str


class _StubSanitize:
    """Minimal call-recording stub. ``last_call.template`` / ``call_count``
    are asserted directly by the test contract."""

    def __init__(self, response_factory) -> None:
        self._response_factory = response_factory
        self.calls: list[_SanitizeCall] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> _SanitizeCall:
        return self.calls[-1]

    def sanitize(self, *, template: str, content: str) -> Any:
        self.calls.append(_SanitizeCall(template=template, content=content))
        return self._response_factory(template=template, content=content)


def _redaction_stub_response(*, template: str, content: str) -> str:
    """High-fidelity INSPECT_AND_REDACT stub.

    Substitutes each canary token with its ``[INFOTYPE]`` marker — mimics
    the real Model Armor + DLP response well enough for the contract
    assertions (token absence + marker presence).
    """

    redacted = content
    for info_type, token in CANARY_TOKENS.items():
        # Phone token has parens that need re.escape.
        redacted = re.sub(re.escape(token), f"[{info_type}]", redacted)
    return redacted


# ---------------------------------------------------------------------------
# F37 dispatch recorder. The test contract is explicit: do NOT mock to
# suppress; we want to see what was dispatched so the trace ties back to
# the failing record's tool_call_id.
# ---------------------------------------------------------------------------


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, f_code: str, **kwargs: Any):
        # Record (f_code, kwargs) so tests can pattern-match exact code +
        # presence of tool_call_id without taking a dependency on the
        # full HandlerResult shape.
        self.calls.append((f_code, kwargs))

        class _Sentinel:  # tiny stand-in so the caller's ``raise`` still happens.
            action = "halt"

        return _Sentinel()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_gcs() -> FakeGCSClient:
    return FakeGCSClient()


@pytest.fixture
def canary() -> dict:
    return _canary_payload()


@pytest.fixture
def model_armor_live() -> _StubSanitize:
    """Default behavior: high-fidelity INSPECT_AND_REDACT stub. When
    ``PERSISTENCE_TRAP_LIVE=1`` is set, switches to a real Model Armor
    client (nightly job only — never in per-PR CI).
    """

    if os.environ.get("PERSISTENCE_TRAP_LIVE") == "1":
        # Live mode — caller is responsible for credentials. The same
        # _StubSanitize wrapper records calls for inspection; the
        # response_factory delegates to the real client.
        from google.cloud import modelarmor_v1  # type: ignore[import-not-found]

        real_client = modelarmor_v1.ModelArmorClient()

        def _live(*, template: str, content: str) -> Any:
            return real_client.sanitize(template=template, content=content)

        return _StubSanitize(_live)

    return _StubSanitize(_redaction_stub_response)


@pytest.fixture
def stub_sanitize() -> _StubSanitize:
    """Plain INSPECT_AND_REDACT stub (no live escape hatch). Used by
    Test 2."""

    return _StubSanitize(_redaction_stub_response)


@pytest.fixture
def broken_sanitize() -> _StubSanitize:
    """Sanitize stub that raises ``google.api_core.exceptions.ServiceUnavailable``
    on every call. Used by Test 3."""

    def _raise(*, template: str, content: str) -> Any:
        try:
            from google.api_core import exceptions as gax_exc  # type: ignore[import-not-found]

            raise gax_exc.ServiceUnavailable("Model Armor sanitize unavailable")
        except ImportError:
            # google-api-core may not be installed in pure-unit-test envs;
            # the contract is about loud-failure on ANY sanitize exception.
            raise RuntimeError("Model Armor sanitize unavailable")

    return _StubSanitize(_raise)


@pytest.fixture
def mock_dispatch(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    """Patch ``lib.durability.handlers.dispatch`` with a call recorder.

    Patches the symbol that ``lib.trajectory.shipper.ship_batch`` does the
    inline import of, NOT the originally-defined symbol. This is the same
    pattern as ``monkeypatch.setattr("module.where_it_is_used.func", ...)``
    rather than ``module.where_it_was_defined``.
    """

    recorder = _DispatchRecorder()
    # Patch at definition site — shipper imports it inline at call time, so
    # patching the module attribute is what the inline import resolves to.
    monkeypatch.setattr("lib.durability.handlers.dispatch", recorder)
    return recorder


def _expected_object_name(verdict: dict) -> str:
    return _object_name_for(verdict)


# ---------------------------------------------------------------------------
# TEST 1 — Floor-only happy path
# ---------------------------------------------------------------------------


@pytest.mark.persistence_trap
def test_persistence_trap_floor_only_redacts(
    fake_gcs: FakeGCSClient,
    model_armor_live: _StubSanitize,
    canary: dict,
) -> None:
    """T1: shipper calls templates.sanitize (NOT relying on Floor Setting
    fallback). Canary tokens MUST be absent from the uploaded blob; the
    INFOTYPE markers MUST be present (proves redaction actually ran, not
    just that the tokens are missing).
    """

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=model_armor_live,
        gcs_client=fake_gcs,
    )

    shipper.ship_one(canary)

    blob = fake_gcs.get("test-bucket", _expected_object_name(canary))
    for info_type, token in CANARY_TOKENS.items():
        assert token not in blob, (
            f"leak: {token!r} ({info_type}) found in uploaded blob — "
            f"sanitize bypassed or template misconfigured"
        )
    for info_type in CANARY_TOKENS:
        assert (
            f"[{info_type}]" in blob
        ), f"redaction marker [{info_type}] missing — sanitize may have no-opped"


# ---------------------------------------------------------------------------
# TEST 2 — Explicit-sanitize success path
# ---------------------------------------------------------------------------


@pytest.mark.persistence_trap
def test_persistence_trap_sanitize_called_per_record(
    fake_gcs: FakeGCSClient,
    stub_sanitize: _StubSanitize,
    canary: dict,
) -> None:
    """T2: sanitize is called exactly once per record with the configured
    template, and the string sanitize returns is what's uploaded (not the
    original record)."""

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=stub_sanitize,
        gcs_client=fake_gcs,
    )

    shipper.ship_one(canary)

    assert stub_sanitize.call_count == 1, "sanitize must be called exactly once per record"
    assert (
        stub_sanitize.last_call.template == "j1-trajectory-shipper"
    ), "sanitize must use the configured template, not a default"

    blob = fake_gcs.get("test-bucket", _expected_object_name(canary))
    for token in CANARY_TOKENS.values():
        assert token not in blob, f"leak: {token!r} found in uploaded blob"
    for info_type in CANARY_TOKENS:
        assert f"[{info_type}]" in blob, f"redaction marker [{info_type}] missing"


@pytest.mark.persistence_trap
def test_persistence_trap_batch_calls_sanitize_per_record(
    fake_gcs: FakeGCSClient,
    stub_sanitize: _StubSanitize,
) -> None:
    """T2 extension: per-record (not per-batch) sanitize is the contract.
    A 3-record batch MUST produce exactly 3 sanitize calls."""

    records = [{**_canary_payload(), "tool_call_id": f"test-pt-batch-{i}"} for i in range(3)]
    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=stub_sanitize,
        gcs_client=fake_gcs,
    )

    shipper.ship_batch(records)

    assert (
        stub_sanitize.call_count == 3
    ), "per-record sanitize is the contract — batch-level sanitize is rejected"
    assert len(fake_gcs.stored_keys) == 3, "all 3 records must have been uploaded"


# ---------------------------------------------------------------------------
# TEST 3 — Broken sanitize MUST fail loud (the load-bearing test)
# ---------------------------------------------------------------------------


@pytest.mark.persistence_trap
def test_persistence_trap_sanitize_unavailable_fails_loud(
    fake_gcs: FakeGCSClient,
    broken_sanitize: _StubSanitize,
    mock_dispatch: _DispatchRecorder,
    canary: dict,
) -> None:
    """T3 — DO NOT WEAKEN THIS TEST.

    See audit/2026-05-21-persistence-trap-12c/test-contract.md §2.3.

    If a future PR wraps the sanitize call in ``try/except: continue``,
    or downgrades F37 to ``fallback_local_log``, or otherwise lets the
    un-redacted payload through when sanitize is unavailable, this test
    will fail red. The failure mode it guards against is
    PROD-IRREVERSIBLE because the GCS bucket feeds the RLAIF training
    substrate, which Phase 4 will memorize — the leak is unrecallable.
    """

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=broken_sanitize,
        gcs_client=fake_gcs,
    )

    # The shipper MUST raise. If this assertion ever stops firing, the
    # Persistence Trap is broken.
    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_batch([canary])

    # Exactly one F37 dispatch with the failing record's tool_call_id.
    assert len(mock_dispatch.calls) == 1, (
        f"expected exactly 1 F37 dispatch, got {len(mock_dispatch.calls)}: "
        f"{mock_dispatch.calls!r}"
    )
    f_code, kwargs = mock_dispatch.calls[0]
    assert f_code == "F37", f"expected F37, got {f_code!r}"
    assert (
        kwargs.get("tool_call_id") == "test-pt-001"
    ), f"dispatched payload missing/incorrect tool_call_id: {kwargs!r}"
    assert (
        kwargs.get("payload", {}).get("shipper") == "trajectory"
    ), f"dispatched payload must identify the shipper: {kwargs!r}"

    # Canary payload MUST NOT exist in GCS — this is the contract.
    assert not fake_gcs.exists(
        "test-bucket", _expected_object_name(canary)
    ), "un-redacted canary payload was uploaded — Persistence Trap is broken"
    assert (
        fake_gcs.stored_keys == []
    ), "no records should have been uploaded under known-unavailable sanitize"


@pytest.mark.persistence_trap
def test_persistence_trap_batch_halts_on_first_failure(
    fake_gcs: FakeGCSClient,
    broken_sanitize: _StubSanitize,
    mock_dispatch: _DispatchRecorder,
) -> None:
    """T3 extension: a 3-record batch where sanitize is broken MUST stop
    after the first record's F37 dispatch. The other 2 records MUST NOT
    be shipped under a known-unavailable sanitize endpoint."""

    records = [{**_canary_payload(), "tool_call_id": f"test-pt-halt-{i}"} for i in range(3)]
    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=broken_sanitize,
        gcs_client=fake_gcs,
    )

    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_batch(records)

    assert len(mock_dispatch.calls) == 1, "only one dispatch — loop must halt on first failure"
    assert mock_dispatch.calls[0][1]["tool_call_id"] == "test-pt-halt-0"
    assert (
        fake_gcs.stored_keys == []
    ), "no records should have been uploaded under known-unavailable sanitize"


# ---------------------------------------------------------------------------
# Strict-extractor coverage — the third strictness layer (response shape).
# ---------------------------------------------------------------------------


@pytest.mark.persistence_trap
def test_persistence_trap_unrecognizable_response_fails_loud(
    fake_gcs: FakeGCSClient,
    canary: dict,
) -> None:
    """A sanitize response with no string-typed sanitized content (e.g. a
    bare object, a None, a number) MUST be treated as sanitize-unavailable.
    This prevents a future SDK upgrade from silently shipping the original
    record when the response shape changes."""

    class _UnrecognizableResponse:
        # No `sanitized_content`, no `content`, no `text` string attrs.
        some_other_field = 42

    class _UnrecognizableSanitize:
        def sanitize(self, *, template: str, content: str) -> Any:
            return _UnrecognizableResponse()

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=_UnrecognizableSanitize(),
        gcs_client=fake_gcs,
    )

    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_one(canary)

    assert (
        fake_gcs.stored_keys == []
    ), "unrecognizable sanitize response must not result in any upload"


# ---------------------------------------------------------------------------
# Shipper construction guard — bucket + template are required.
# ---------------------------------------------------------------------------


@pytest.mark.persistence_trap
@pytest.mark.parametrize(
    "bucket, template",
    [
        ("", "j1-trajectory-shipper"),
        ("test-bucket", ""),
    ],
)
def test_persistence_trap_construction_requires_bucket_and_template(
    bucket: str, template: str
) -> None:
    """No production binding without an explicit bucket and template. Both
    are required at construction so a misconfigured shipper cannot start
    silently with a default."""

    with pytest.raises(ValueError):
        TrajectoryShipper(bucket=bucket, template=template)
