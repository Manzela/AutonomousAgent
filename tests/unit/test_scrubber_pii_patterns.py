"""SP-R1 PII coverage — the MAIN scrubber (lib.scrubber.scrub_string, used by the spine
checkpoint + logs via SP-R1) now redacts email / US-SSN / US-phone, closing the gap vs the
A2A path which already covered them (Day 9 PHI set). Patterns are ported VERBATIM from
config/a2a/scrubber-patterns.yaml, so they are already production-proven.

NON-VACUOUS: on origin/main config/scrubber-patterns.yaml has NO email/SSN/phone pattern,
so these strings survive the main scrub (RED); GREEN once the patterns are added.
"""

from __future__ import annotations

import pytest

from lib.scrubber import _reset_singleton_for_tests, scrub_string


@pytest.fixture(autouse=True)
def _fresh_scrubber():
    # ensure the patterns singleton is (re)loaded from the on-disk config for each test.
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


def test_email_redacted():
    out = scrub_string("please contact victim@example.com about the incident")
    assert "victim@example.com" not in out
    assert "[REDACTED:email]" in out


def test_ssn_redacted():
    out = scrub_string("the SSN on file is 123-45-6789 per record")
    assert "123-45-6789" not in out


def test_us_phone_redacted():
    for ph in ["(555) 123-4567", "555-123-4567", "555.123.4567"]:
        out = scrub_string(f"call {ph} for support")
        assert ph not in out, f"phone {ph!r} survived the main scrub"


def test_benign_text_not_over_redacted():
    # control: ordinary build output (no PII shape) is preserved byte-for-byte.
    text = "the build produced 42 widgets in 3.5 seconds across 7 nodes"
    assert scrub_string(text) == text


def test_main_scrubber_parity_with_a2a_phi():
    # the main scrubber now redacts the same PHI shapes the A2A path does.
    blob = scrub_string("user@example.com 123-45-6789 (555) 123-4567")
    for leaked in ["user@example.com", "123-45-6789", "555"]:
        assert leaked not in blob, f"{leaked!r} survived"
