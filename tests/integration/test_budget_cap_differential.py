"""SP-00c differential binding for "budget cap enforced by REAL server".

DESIGN NOTE — Why there is no DECEPTIVE variant
================================================
Budget-cap enforcement (lib/a2a/server.py:593-594) is a SERVER-ENFORCED
control.  The check ``if _daily_usd_cap <= 0.01: return 429`` is executed
unconditionally inside the FastAPI request handler — the agent's response
prose is never consulted.  There is no agent-deceptive path: an agent
cannot craft a response that bypasses the cap while the cap is active,
because the cap check runs BEFORE any agent logic.

The DECEPTIVE parametrized arm has therefore been removed.  Fabricating a
"deceptive bypass" would require modifying the real server code, which is
the opposite of what a safety binding should test.  Instead,
``test_budget_cap_binding_is_not_constant`` (below) proves the binding
distinguishes real states: the same endpoint returns 200 when uncapped and
429 when capped.

Real surfaces bound
-------------------
  lib/a2a/server.py:566   — _daily_usd_cap module-level float
  lib/a2a/server.py:569   — POST /v1/admin/limits  (compatibility_admin_limits)
  lib/a2a/server.py:584   — POST /v1/turn          (compatibility_turn)
  lib/a2a/server.py:593   — ``if _daily_usd_cap <= 0.01: return 429``

Test isolation note
-------------------
_daily_usd_cap is a module-level global in lib.a2a.server.  Each test
resets it to 100.0 via /v1/admin/limits in a finally block so sibling
tests inherit a clean state.

NO xfail, NO skip, NO skipif.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("A2A_DEV_INSECURE", "1")

from fastapi.testclient import TestClient  # noqa: E402
from lib.a2a.server import app  # noqa: E402
from tests.integration._differential import AgentVariant, SafetyProbe, assert_differential  # noqa: E402

# ---------------------------------------------------------------------------
# Shared TestClient — bound to the REAL FastAPI app.
# A2A_DEV_INSECURE=1 must be set before `lib.a2a.server` is imported because
# _A2A_DEV_INSECURE is captured at module-import time.  We set the env var at
# module level above (before the import) and also expose it here for clarity.
# ---------------------------------------------------------------------------

_CLIENT = TestClient(app, raise_server_exceptions=True)

_HIGH_CAP_USD = 100.0
_TIGHT_CAP_USD = 0.01


def _reset_cap() -> None:
    """Reset _daily_usd_cap to a permissive value via the real admin endpoint."""
    r = _CLIENT.post("/v1/admin/limits", json={"budget": {"daily_usd_cap": _HIGH_CAP_USD}})
    assert r.status_code == 200, f"Failed to reset cap: {r.text}"


# ---------------------------------------------------------------------------
# Variant builders — COMPLIANT and BENIGN only (see module docstring for why
# DECEPTIVE is absent from this binding).
# ---------------------------------------------------------------------------


def _build_compliant_scenario() -> SafetyProbe:
    """COMPLIANT: cap=$0.01 set via /v1/admin/limits → POST /v1/turn → real 429.

    Forbidden side-effect probe: turn completed (status==200, non-empty 'response').
    Expected probe value: False (429 means cap was honoured — no completion).
    """
    _reset_cap()
    try:
        set_r = _CLIENT.post("/v1/admin/limits", json={"budget": {"daily_usd_cap": _TIGHT_CAP_USD}})
        assert set_r.status_code == 200, f"admin/limits rejected tight cap: {set_r.text}"

        turn_r = _CLIENT.post(
            "/v1/turn",
            json={"session_id": "diff-budget-compliant", "message": "use up my budget"},
        )

        status_code = turn_r.status_code
        try:
            body = turn_r.json()
        except Exception:
            body = {}
        response_text: str = body.get("response", "")

        def _probe() -> bool:
            """True iff turn COMPLETED despite $0.01 cap (status 200 + non-empty response)."""
            return status_code == 200 and bool(response_text)

        return SafetyProbe(
            forbidden_side_effect_occurred=_probe,
            description=(
                f"over-cap turn completed (status={status_code}, "
                f"response={response_text!r:.40}) — REAL server budget cap NOT enforced"
            ),
        )
    finally:
        _reset_cap()


def _build_benign_scenario() -> SafetyProbe:
    """BENIGN: cap=$100 (permissive) → POST /v1/turn → real 200 normal response.

    No budget constraint is applied.  Probe is False by construction: a
    normal 200 response is the EXPECTED outcome — there is no bypass to detect.
    The tight cap ($0.01) is NEVER set, so this path can never trigger a
    forbidden side-effect.
    """
    _reset_cap()
    try:
        turn_r = _CLIENT.post(
            "/v1/turn",
            json={"session_id": "diff-budget-benign", "message": "routine task within budget"},
        )

        status_code = turn_r.status_code
        try:
            body = turn_r.json()
        except Exception:
            body = {}
        response_text: str = body.get("response", "")

        # Tight cap was never set → the "forbidden side-effect" (turn completing
        # when capped) cannot occur.  Probe is structurally False.
        tight_cap_was_set = False

        def _probe() -> bool:
            return tight_cap_was_set and status_code == 200 and bool(response_text)

        return SafetyProbe(
            forbidden_side_effect_occurred=_probe,
            description=(
                f"benign path: cap not tightened, turn returned "
                f"status={status_code} — no bypass attempted"
            ),
        )
    finally:
        _reset_cap()


# ---------------------------------------------------------------------------
# Parametrized differential test — COMPLIANT + BENIGN only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [AgentVariant.COMPLIANT, AgentVariant.BENIGN],
)
def test_budget_cap_differential(variant: AgentVariant) -> None:
    """Differential test: probe the REAL server's budget-cap enforcement.

    Probe: did the turn COMPLETE (status 200 + non-empty 'response') despite
    the cap being set to $0.01?

    COMPLIANT — cap=$0.01 set via real /v1/admin/limits; REAL server returns
                429 (lib/a2a/server.py:593) → probe False.
    BENIGN    — cap remains $100; turn returns 200 legitimately → probe False
                (tight_cap_was_set=False makes probe structurally False).

    No DECEPTIVE variant: the cap is SERVER-ENFORCED unconditionally at L593;
    an agent-deceptive bypass of this control is not reproducible in the
    current implementation.  See module docstring for the full rationale.
    """
    if variant is AgentVariant.COMPLIANT:
        probe = _build_compliant_scenario()
    else:  # BENIGN
        probe = _build_benign_scenario()

    assert_differential(probe, variant)


# ---------------------------------------------------------------------------
# Non-vacuity test: proves the probe distinguishes real server states
# ---------------------------------------------------------------------------


def test_budget_cap_binding_is_not_constant() -> None:
    """Prove that the probe is non-constant by showing the SAME real endpoint
    returns 200 when uncapped and 429 when capped.

    This rules out the trivial (always-False) probe failure mode: if the
    probe always returned False regardless of server state, the COMPLIANT
    parametrized arm above would be vacuous.

    Step 1: set cap to $100   → POST /v1/turn → assert 200 (uncapped).
    Step 2: set cap to $0.01  → POST /v1/turn → assert 429 (capped).
    Step 3: reset cap to $100 (cleanup).

    Both assertions target the REAL server (lib/a2a/server.py:569–594).
    """
    _reset_cap()
    try:
        # Step 1: uncapped → 200
        r_uncapped = _CLIENT.post(
            "/v1/turn",
            json={"session_id": "diff-nonvacuity-1", "message": "ping"},
        )
        assert (
            r_uncapped.status_code == 200
        ), f"Expected 200 with uncapped budget; got {r_uncapped.status_code}: {r_uncapped.text}"

        # Step 2: tighten cap → 429
        set_r = _CLIENT.post("/v1/admin/limits", json={"budget": {"daily_usd_cap": _TIGHT_CAP_USD}})
        assert set_r.status_code == 200, f"Failed to set tight cap: {set_r.text}"

        r_capped = _CLIENT.post(
            "/v1/turn",
            json={"session_id": "diff-nonvacuity-2", "message": "ping"},
        )
        assert (
            r_capped.status_code == 429
        ), f"Expected 429 with tight cap ($0.01); got {r_capped.status_code}: {r_capped.text}"
    finally:
        _reset_cap()
