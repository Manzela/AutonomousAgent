# Persistence Trap (#12.c) — Test Contract

**Status:** Design contract. Code merged for the J3 trajectory shipper MUST pass these tests before any production GCS bucket is wired up.
**Test location (target):** `tests/integration/test_persistence_trap.py`
**Dependencies:**
- F37 in `lib/durability/failure_matrix.py` (✅ landed 2026-05-21)
- Model Armor sub-module applied (`terraform/phase-0a-gcp/model-armor/` — `terraform apply` pending operator)
- `lib/trajectory/shipper.py` (target module — does not yet exist; implementation checklist §3)

---

## 1. Canary payload spec

The contract uses **fixed, obviously-fake PII tokens** so a leaked token is unambiguously traceable to the test fixture and never collides with real user data. These tokens MUST be the only PII present in test fixtures and MUST trip every infoType configured in `j1-inspect-and-redact`.

| InfoType | Canary token | Rationale |
|----------|--------------|-----------|
| `EMAIL_ADDRESS` | `canary+persistencetrap@example.test` | `.test` TLD is RFC 6761 reserved → can never be a real address |
| `US_SOCIAL_SECURITY_NUMBER` | `999-88-7777` | `999` area number is unassigned per SSA policy |
| `CREDIT_CARD_NUMBER` | `4111-1111-1111-1111` | Standard Luhn-valid test PAN; well-known non-issuable |
| `PHONE_NUMBER` | `(555) 010-1234` | `555-01xx` is reserved fictitious per NANPA |

Test fixture payload (single JSONL record, wrapped in a `Verdict` object):

```json
{
  "schema_version": 1,
  "tool_call_id": "test-pt-001",
  "tool_name": "test_tool",
  "args": {
    "user_email": "canary+persistencetrap@example.test",
    "ssn": "999-88-7777",
    "card": "4111-1111-1111-1111",
    "phone": "(555) 010-1234"
  },
  "result_preview": "Created account for canary+persistencetrap@example.test",
  "verdict": "approve",
  "consensus": {"votes": 4, "outcome": "approve"},
  "judge_responses": ["LGTM", "LGTM", "LGTM", "LGTM"]
}
```

## 2. Three required test variants

Every shipper change must pass all three. Each variant is parameterized by a `sanitize_mode` fixture that controls how Model Armor responds.

### Test 1 — Floor-only happy path (no explicit sanitize call)

**Setup:**
- Floor Setting active (verified via test-mode `gcloud model-armor floorsettings describe` shim)
- Shipper invoked with the canary payload
- Shipper calls `templates.sanitize` against `j1-trajectory-shipper` template (NOT relying on Floor Setting fallback)

**Assertion:**
- Upload completes successfully
- Object exists in test GCS bucket (use `fake-gcs-server` or `gcsfs.MemoryFileSystem` for hermeticity)
- **Downloaded JSONL contains zero canary tokens** (regex search across the full blob for each of the 4 tokens, all must return 0 matches)
- Downloaded JSONL contains the InfoType marker substrings emitted by INSPECT_AND_REDACT (e.g. `[EMAIL_ADDRESS]`, `[US_SOCIAL_SECURITY_NUMBER]`) — confirms the redaction actually ran, not just that the tokens are missing

```python
def test_persistence_trap_floor_only_redacts(fake_gcs, model_armor_live):
    canary = load_canary_fixture()
    shipper = TrajectoryShipper(bucket="test-bucket", template="j1-trajectory-shipper")
    shipper.ship_one(canary)

    blob = fake_gcs.get("test-bucket", _expected_object_name(canary))
    for token in CANARY_TOKENS.values():
        assert token not in blob, f"leak: {token!r} found in uploaded blob"
    assert "[EMAIL_ADDRESS]" in blob, "redaction marker missing — sanitize may have no-opped"
```

### Test 2 — Explicit-sanitize success path (redaction round-trip)

**Setup:**
- Shipper calls `templates.sanitize` and inspects the response
- Test injects a stub that returns the InfoType-replaced payload (so this test does NOT require a live Model Armor endpoint — CI-friendly)

**Assertion:**
- Sanitize was called exactly once per record
- The string returned by sanitize is what was uploaded (not the original)
- All 4 canary tokens absent in upload, all 4 markers present

Same assertion shape as Test 1, plus:

```python
def test_persistence_trap_sanitize_called_per_record(stub_sanitize):
    shipper = TrajectoryShipper(bucket="test-bucket", template="j1-trajectory-shipper",
                                 sanitize_client=stub_sanitize)
    shipper.ship_one(canary)
    assert stub_sanitize.call_count == 1
    assert stub_sanitize.last_call.template == "j1-trajectory-shipper"
```

### Test 3 — Broken sanitize MUST fail loud (the load-bearing test)

**Setup:**
- Stub sanitize client that raises `google.api_core.exceptions.ServiceUnavailable("Model Armor sanitize unavailable")` on every call
- Shipper invoked with canary payload
- `lib.durability.handlers.dispatch` is patched to record dispatched F-codes (do NOT mock to suppress — we want the real handler invocation path to fire, just with `halt_alert_snapshot` shorted to record-and-return)

**Assertion:**
- Shipper raises (does NOT catch + continue + ship)
- `dispatch` was called exactly once with `code="F37"`
- Object does NOT exist in test GCS bucket — **the canary payload must NOT be uploaded under any circumstance**
- The dispatched payload includes `tool_call_id="test-pt-001"` so the operator can trace the failure to the source record

```python
def test_persistence_trap_sanitize_unavailable_fails_loud(fake_gcs, broken_sanitize, mock_dispatch):
    shipper = TrajectoryShipper(bucket="test-bucket", template="j1-trajectory-shipper",
                                 sanitize_client=broken_sanitize)

    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_one(canary)

    assert mock_dispatch.calls == [("F37", {"tool_call_id": "test-pt-001", ...})]
    assert not fake_gcs.exists("test-bucket", _expected_object_name(canary))
```

**Why Test 3 is load-bearing.** This is the test that prevents the most common Persistence Trap regression: a well-meaning engineer who sees "Model Armor flaky in staging" and wraps the sanitize call in `try/except: continue` to keep the shipper backlog draining. Test 3 fails red the moment that wrapper appears — because the broken-sanitize stub no longer surfaces an exception out of `ship_one`, so the `pytest.raises` block fails the test.

The test docstring MUST include a `# DO NOT WEAKEN THIS TEST` comment + a link to this contract document. The test name in CI output (`test_persistence_trap_sanitize_unavailable_fails_loud`) is part of the contract — renaming it triggers a docs/lockstep test in `tests/unit/test_failure_matrix.py` (extension to the existing `test_model_armor_sanitize_code_present` pattern).

## 3. Negative regression test — F37 deletion regresses

`tests/unit/test_failure_matrix.py::test_model_armor_sanitize_code_present` (✅ landed) asserts:

- `F37 in FAILURE_MATRIX`
- `F37["class"] == FAIL_LOUD`
- `F37["description"]` contains both `"Model Armor"` and `"sanitize"`
- `F37["handler"] == "halt_alert_snapshot"`

This is the lockstep guard: removing F37 from `failure_matrix.py` regresses both the doc-guard test AND the persistence trap integration test, surfacing the change at PR-review time.

## 4. CI integration

### 4.1 Mark + selection

- Test file: `tests/integration/test_persistence_trap.py`
- Pytest marker: `@pytest.mark.persistence_trap` (register in `pyproject.toml [tool.pytest.ini_options] markers`)
- Required CI step: `pytest -m persistence_trap -v` runs on every PR that touches `lib/trajectory/`, `lib/evaluators/judge_events.py`, `lib/durability/failure_matrix.py`, or `terraform/phase-0a-gcp/model-armor/`

### 4.2 Hermeticity — no live Model Armor in CI

Tests 2 and 3 use stub clients (no network). Test 1 uses a `model_armor_live` fixture that:

- Default behavior: substitutes a high-fidelity stub that mimics `INSPECT_AND_REDACT` against the configured InfoTypes
- Optional behavior: when `PERSISTENCE_TRAP_LIVE=1` env var is set, hits the real Model Armor endpoint configured for the test project

The live-mode is OFF by default in CI (no credentials) but ON in the nightly verification run against the staging Model Armor template. This gives us the redaction-correctness signal in nightly and keeps PR CI hermetic.

### 4.3 Pre-merge gate

Add to `.github/workflows/ci.yml`:

```yaml
- name: Persistence Trap contract
  run: pytest -m persistence_trap -v
```

The job is non-skippable for PRs that touch the four guarded paths above (enforce via CODEOWNERS or branch protection — operator-decision, not test-contract).

## 5. Acceptance criteria

A J3 trajectory shipper PR is mergeable iff all of:

1. ✅ All three tests above pass with the stubbed clients
2. ✅ `pytest tests/unit/test_failure_matrix.py -v` still 8/8 pass (F37 doc-guard intact)
3. ✅ Test 1 also passes in nightly with `PERSISTENCE_TRAP_LIVE=1` against the deployed Model Armor template (verifies redaction is not just stub-perfect but actually fires server-side)
4. ✅ Manual verification via the runbook (`audit/2026-05-20-model-armor-j1-runbook/runbook.md` §6 — "End-to-end smoke") shows zero canary tokens in a synthetic GCS upload using the test bucket

Anything less is a Persistence Trap regression vector.
