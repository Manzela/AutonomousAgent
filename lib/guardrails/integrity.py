"""Prompt and Model Config Integrity Verification.

Validates that prompt templates and model configuration files have not been
tampered with at load/startup time.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IntegrityError(RuntimeError):
    """Raised when an integrity check fails."""

    pass


def verify_integrity(
    *,
    manifest_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
    fail_closed: bool = True,
) -> bool:
    """Validate prompt templates and model configurations against a signed/pinned manifest.

    Args:
        manifest_path:  Path to the MANIFEST.sha256 file. Defaults to
                        config/hermes/MANIFEST.sha256.
        workspace_root: Path to the workspace root directory.
        fail_closed:    If True, raises IntegrityError on mismatch. If False,
                        logs a warning and returns False.

    Returns:
        True if all files match the manifest; False if any mismatch and fail_closed=False.
    """
    if workspace_root is None:
        # Resolve workspace root (parent of lib/ directory)
        workspace_root = Path(__file__).parent.parent.parent
    else:
        workspace_root = Path(workspace_root)

    if manifest_path is None:
        manifest_path = workspace_root / "config" / "hermes" / "MANIFEST.sha256"
    else:
        manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        msg = f"Integrity manifest not found at: {manifest_path}"
        if fail_closed:
            raise IntegrityError(msg)
        logger.warning(msg)
        return False

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as exc:
        msg = f"Failed to read integrity manifest: {exc!r}"
        if fail_closed:
            raise IntegrityError(msg) from exc
        logger.warning(msg)
        return False

    failures = []
    checked_count = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            continue

        expected_sha = parts[0].strip().lower()
        rel_path = parts[1].strip()
        full_path = workspace_root / rel_path

        if not full_path.exists():
            failures.append(f"File missing: {rel_path}")
            continue

        try:
            with open(full_path, "rb") as fh:
                content = fh.read()
            actual_sha = hashlib.sha256(content).hexdigest().lower()
            checked_count += 1
            if actual_sha != expected_sha:
                failures.append(
                    f"SHA mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"
                )
        except Exception as exc:
            failures.append(f"Failed to read {rel_path}: {exc!r}")

    if failures:
        msg = f"Integrity check failed: {'; '.join(failures)}"
        if fail_closed:
            raise IntegrityError(msg)
        logger.warning(msg)
        return False

    logger.info("Integrity check passed: verified %d files successfully", checked_count)
    return True
