"""Unit tests for spec_store atomic persistence + sha-stamping."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from lib.anchors.spec_store import SpecStore, compute_spec_sha
from lib.anchors.task_spec import Scope, TaskSpec


def _draft_spec() -> TaskSpec:
    return TaskSpec(
        title="Audit my repo",
        intent="Find security issues.",
        acceptance_criteria=["Done"],
        scope=Scope(in_scope=["lib/"], out_of_scope=["hermes-agent/"]),
        success_metrics=["No P0 issues"],
        constraints=[],
        spec_id=uuid4(),
        spec_sha="placeholder",
        created_at=datetime.now(timezone.utc),
        created_by=7217166969,
    )


def test_compute_spec_sha_deterministic():
    spec = _draft_spec()
    sha_a = compute_spec_sha(spec)
    sha_b = compute_spec_sha(spec)
    assert sha_a == sha_b
    assert len(sha_a) == 64  # sha256 hex


def test_compute_spec_sha_differs_on_field_change():
    spec_a = _draft_spec()
    spec_b = spec_a.model_copy(update={"title": "Different title"})
    assert compute_spec_sha(spec_a) != compute_spec_sha(spec_b)


def test_save_and_load(tmp_path: Path):
    store = SpecStore(tmp_path)
    spec = _draft_spec()
    saved = store.save(spec)

    assert saved.spec_sha != "placeholder"
    assert saved.spec_sha == compute_spec_sha(spec.model_copy(update={"spec_sha": "placeholder"}))
    assert (tmp_path / f"{saved.spec_id}.json").exists()

    loaded = store.load(saved.spec_id)
    assert loaded.title == saved.title
    assert loaded.spec_sha == saved.spec_sha


def test_atomic_write_no_partial_files_on_success(tmp_path: Path):
    """Successful save leaves no .tmp files."""
    store = SpecStore(tmp_path)
    spec = _draft_spec()
    store.save(spec)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Partial files remain: {tmp_files}"


def test_atomic_write_no_partial_files_on_failure(tmp_path: Path, monkeypatch):
    """If os.replace fails mid-save, neither the target file nor the .tmp file should exist.

    Both invariants are now enforced:
    1. A failed save MUST NOT leave a corrupted target file.
    2. A failed save MUST NOT leave a stale .tmp sibling (spec_store.save
       cleans it up in a try/finally block after the os.replace call).
    """
    import os

    store = SpecStore(tmp_path)
    spec = _draft_spec()

    def _failing_replace(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        store.save(spec)

    # The target file should NOT exist (save failed before atomic swap)
    target_files = [f for f in tmp_path.glob("*.json") if not f.name.endswith(".tmp")]
    assert target_files == [], f"Target file leaked despite save failure: {target_files}"

    # The .tmp file must also be cleaned up (try/finally in spec_store.SpecStore.save).
    assert not list(tmp_path.glob("*.tmp")), "tmp file leaked after failed save"


def test_load_unknown_id_raises(tmp_path: Path):
    store = SpecStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load(uuid4())


# --- Task 6: get_by_id + cancel_by_id ---


def test_get_by_id_returns_none_for_missing(tmp_path: Path):
    """get_by_id returns None for an unknown spec_id."""
    store = SpecStore(tmp_path)
    result = store.get_by_id("00000000-0000-0000-0000-000000000000")
    assert result is None


def test_get_by_id_returns_none_for_invalid_uuid(tmp_path: Path):
    """get_by_id returns None for a non-UUID string (ValueError swallowed)."""
    store = SpecStore(tmp_path)
    result = store.get_by_id("not-a-valid-uuid")
    assert result is None


def test_get_by_id_returns_saved_spec(tmp_path: Path):
    """get_by_id returns the spec that was previously saved."""
    store = SpecStore(tmp_path)
    spec = _draft_spec()
    saved = store.save(spec)
    result = store.get_by_id(str(saved.spec_id))
    assert result is not None
    assert result.spec_id == saved.spec_id
    assert result.title == saved.title


def test_cancel_by_id_returns_false_for_missing(tmp_path: Path):
    """cancel_by_id returns False when spec_id is not found."""
    store = SpecStore(tmp_path)
    result = store.cancel_by_id("00000000-0000-0000-0000-000000000000")
    assert result is False


def test_cancel_by_id_marks_spec_superseded(tmp_path: Path):
    """cancel_by_id marks an existing spec as superseded and returns True."""
    store = SpecStore(tmp_path)
    spec = _draft_spec()
    saved = store.save(spec)
    assert saved.status == "draft"

    result = store.cancel_by_id(str(saved.spec_id))
    assert result is True

    loaded = store.get_by_id(str(saved.spec_id))
    assert loaded is not None
    assert loaded.status == "superseded"
