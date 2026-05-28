"""Atomic JSON persistence for TaskSpec + sha256 stamping.

Writes are atomic via os.replace (POSIX rename that overwrites target;
also works on Windows). sha256 is computed over normalized JSON: spec_sha
field nulled, sorted keys, no whitespace, ISO datetimes. This makes the
sha stable across serialization round-trips.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from lib.anchors.task_spec import TaskSpec

logger = logging.getLogger(__name__)

_SHA_PLACEHOLDER = "placeholder"


def _normalized_dict(spec: TaskSpec) -> dict:
    """Return a dict with spec_sha nulled, suitable for hashing."""
    d = spec.model_dump(mode="json")  # mode="json" → ISO datetimes, str UUIDs
    d["spec_sha"] = _SHA_PLACEHOLDER
    return d


def compute_spec_sha(spec: TaskSpec) -> str:
    """Compute sha256 over normalized JSON (sorted keys, no whitespace)."""
    norm = _normalized_dict(spec)
    canonical = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SpecStore:
    """Persistence layer for TaskSpec instances.

    File layout: ``<root>/<spec_id>.json``. Atomic via tmp + os.replace
    (POSIX rename that overwrites target; works on Windows too).
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, spec: TaskSpec) -> TaskSpec:
        """Stamp the sha + write atomically. Returns the stamped spec."""
        stamped = spec.model_copy(update={"spec_sha": compute_spec_sha(spec)})
        target = self.root / f"{stamped.spec_id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(stamped.model_dump_json(indent=2))
        try:
            os.replace(tmp, target)  # atomic on POSIX same-filesystem
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return stamped

    def load(self, spec_id: UUID) -> TaskSpec:
        """Load by spec_id. Raises FileNotFoundError if missing."""
        path = self.root / f"{spec_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"TaskSpec not found: {path}")
        return TaskSpec.model_validate_json(path.read_text())

    def get_by_id(self, spec_id: str) -> "TaskSpec | None":
        """Return TaskSpec by string spec_id, or None if not found or invalid UUID."""
        try:
            return self.load(UUID(spec_id))
        except (FileNotFoundError, ValueError):
            return None

    def cancel_by_id(self, spec_id: str) -> bool:
        """Mark a TaskSpec as superseded. Returns True if found+cancelled, False if not found."""
        spec = self.get_by_id(spec_id)
        if spec is None:
            return False
        cancelled = spec.model_copy(update={"status": "superseded"})
        self.save(cancelled)
        return True

    def list_active(self) -> list[TaskSpec]:
        """Return all specs with status in {'draft', 'draft_locked', 'locked'}.

        Skips files that fail to parse as valid TaskSpec JSON (logged as warning),
        so a single corrupted file doesn't take down the whole list. Catches
        only json/validation/OS errors — programming bugs (e.g. KeyError) propagate.
        """
        out = []
        for p in self.root.glob("*.json"):
            try:
                spec = TaskSpec.model_validate_json(p.read_text())
            except (json.JSONDecodeError, ValidationError, OSError) as exc:
                logger.warning("Skipping corrupted spec file %s: %s", p, exc)
                continue
            if spec.status in ("draft", "draft_locked", "locked"):
                out.append(spec)
        return out
