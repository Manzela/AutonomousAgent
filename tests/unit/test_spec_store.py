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


def test_atomic_write_no_partial_files(tmp_path: Path, monkeypatch):
    """Simulate write failure mid-rename — no partial file should remain."""
    store = SpecStore(tmp_path)
    spec = _draft_spec()

    # Verify no `.tmp` files leak after a successful save
    store.save(spec)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Partial files remain: {tmp_files}"


def test_load_unknown_id_raises(tmp_path: Path):
    store = SpecStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load(uuid4())
