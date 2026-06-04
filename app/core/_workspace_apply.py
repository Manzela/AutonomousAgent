"""SP-05 (F-1) per-node workspace applier — the deterministic, trifecta-free stand-in
for the external Hermes code→test→fix drive.

Run AS A SUBPROCESS INSIDE the sandbox (``python -m app.core._workspace_apply <manifest>``)
with the per-node git worktree as CWD. It reads a JSON manifest of {"path","content"}
entries and writes each ``content`` to ``path`` RELATIVE TO CWD, creating parent dirs.

Why this exists (and why NOT lib/hermes_bridge.invoke_hermes_cli): the real Hermes drive
is deferred. ``invoke_hermes_cli`` runs the ``hermes`` binary ON THE HOST via
``subprocess.run(..., check=True)`` with NO ``env=`` (so it inherits the FULL unscrubbed
``os.environ``) and RAISES on a non-zero exit — both violate the sandbox contract
(scrubbed env; returncode drives status, never an exception). This applier instead runs
INSIDE ``sandbox.run`` with the scrubbed env and signals failure via the EXIT CODE.

Security posture (defense-in-depth — NOT kernel confinement; the host has no chroot/
mount-ns, so real EROFS isolation is SP-05c/docker, integration-tier):
  - An ABSOLUTE path or any path containing a ``..`` segment is REFUSED (exit 2). This
    stops the applier itself from escaping CWD; it does NOT stop a different malicious
    binary, which is why eval_gate ALSO scope-scores the resulting git diff + symlinks.

stdout is UNTRUSTED diagnostic only — the authoritative change set is the git diff the
node extracts from the worktree, never this stdout (anti-fabrication: a lying line here
must not move changed_paths).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath


def _unsafe(path: str) -> bool:
    """True if ``path`` is absolute or contains a ``..`` segment (escape attempt)."""
    if not path or Path(path).is_absolute() or PurePosixPath(path).is_absolute():
        return True
    return ".." in PurePosixPath(path).parts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m app.core._workspace_apply <manifest-json>", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(argv[1])
    except (ValueError, TypeError) as exc:
        print(f"manifest is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(manifest, list):
        print("manifest must be a JSON list of {path, content}", file=sys.stderr)
        return 2

    for entry in manifest:
        if not isinstance(entry, dict) or "path" not in entry:
            print(f"bad manifest entry (need a 'path'): {entry!r}", file=sys.stderr)
            return 2
        rel = entry["path"]
        if not isinstance(rel, str) or _unsafe(rel):
            # Refuse to escape CWD — absolute or '..'-bearing paths exit non-zero so the
            # orchestrator maps the run to FAILED (returncode drives status).
            print(f"refusing unsafe path (absolute or '..'): {rel!r}", file=sys.stderr)
            return 2
        content = entry.get("content", "")
        if not isinstance(content, str):
            print(f"content for {rel!r} must be a string", file=sys.stderr)
            return 2
        target = Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        # UNTRUSTED diagnostic — NOT the source of truth for the diff.
        print(f"wrote {rel} ({len(content)} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
