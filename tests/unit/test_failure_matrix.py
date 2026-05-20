"""Unit tests for the failure matrix lookup table.

Matrix size: F1-F33 original (AA-Atelier sweep) + F34/F35 runtime detectors (J4).
Adding new F-codes is expected — these tests assert the baseline plus contracts
(unique, valid class, baseline codes preserved).
"""

from lib.durability.failure_matrix import FAILURE_MATRIX, TrichotomyClass, lookup


def test_baseline_codes_f1_to_f33_present():
    """F1-F33 are the locked baseline; any addition must preserve all of them."""
    baseline = {f"F{i}" for i in range(1, 34)}
    assert baseline.issubset(set(FAILURE_MATRIX.keys()))


def test_loop_and_stall_codes_present():
    """J4 (Framing #2) added F34 = F-LOOP and F35 = F-STALL."""
    assert "F34" in FAILURE_MATRIX
    assert "F35" in FAILURE_MATRIX
    assert FAILURE_MATRIX["F34"]["class"] == TrichotomyClass.FAIL_SOFT
    assert FAILURE_MATRIX["F35"]["class"] == TrichotomyClass.FAIL_LOUD
    assert "F-LOOP" in FAILURE_MATRIX["F34"]["description"]
    assert "F-STALL" in FAILURE_MATRIX["F35"]["description"]


def test_every_code_maps_to_valid_class():
    valid_classes = {
        TrichotomyClass.FAIL_LOUD,
        TrichotomyClass.FAIL_SOFT,
        TrichotomyClass.SELF_HEAL,
    }
    for code, entry in FAILURE_MATRIX.items():
        assert entry["class"] in valid_classes, f"{code} maps to invalid class {entry['class']}"


def test_no_duplicate_codes():
    codes_in_matrix = list(FAILURE_MATRIX.keys())
    assert len(codes_in_matrix) == len(set(codes_in_matrix))


def test_lookup_returns_entry():
    entry = lookup("F1")
    assert entry["class"] in {
        TrichotomyClass.FAIL_LOUD,
        TrichotomyClass.FAIL_SOFT,
        TrichotomyClass.SELF_HEAL,
    }
    assert "description" in entry


def test_lookup_unknown_code_raises():
    import pytest

    with pytest.raises(KeyError):
        lookup("F999")
