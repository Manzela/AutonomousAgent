"""Unit tests for the 33-mode failure matrix lookup table."""

from lib.durability.failure_matrix import FAILURE_MATRIX, TrichotomyClass, lookup


def test_all_33_codes_present():
    expected_codes = {f"F{i}" for i in range(1, 34)}
    assert set(FAILURE_MATRIX.keys()) == expected_codes


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
