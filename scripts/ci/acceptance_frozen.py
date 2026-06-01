#!/usr/bin/env python3
"""SP-00e `acceptance-frozen` gate — enforce the C8/C10 immutable-acceptance contract.

Every task's acceptance lives in `audit/acceptance/<task-id>.yaml` and is sha-pinned
(C8). The CODEOWNERS-guarded manifest `audit/acceptance/SHA256SUMS` records the
sha256 of each acceptance file. This gate recomputes those shas and FAILS the PR if:

  - an acceptance file's content drifts from its manifest sha (the goalpost-move a PR
    would make to its own acceptance — C8: "fails if the acceptance file changed in
    the PR that claims the task done"),
  - an acceptance file exists with no manifest entry (untracked), or
  - a manifest entry has no corresponding file (deleted/renamed).

Because both `audit/acceptance/` AND this manifest are CODEOWNERS-guarded, a *deliberate*
acceptance change requires a manifest update approved by a different actor (4-eyes / C10)
— the executor cannot self-grade. Drift detection here catches the accidental/sneaky case.

Usage:
    acceptance_frozen.py [--dir audit/acceptance] [--manifest audit/acceptance/SHA256SUMS]
    acceptance_frozen.py --write   # regenerate the manifest (a CODEOWNERS-guarded act)

Exit 0 iff every acceptance file matches the manifest exactly.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sys

MANIFEST_HEADER = (
    "# SHA256SUMS — sha256 of every audit/acceptance/*.yaml (C8 acceptance-frozen).\n"
    "# CODEOWNERS-guarded: a change here needs a different-actor approval (C10, 4-eyes).\n"
    "# Regenerate deliberately with: scripts/ci/acceptance_frozen.py --write\n"
)


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    """Parse standard `sha256sum` format lines: '<64-hex>  <relpath>'. Skips #comments."""
    manifest: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, name = parts[0], parts[1].strip()
        manifest[name] = sha
    return manifest


def verify(manifest: dict[str, str], files: dict[str, bytes]) -> list[str]:
    """Return a list of human-readable error strings; empty list == frozen/clean."""
    errors: list[str] = []
    for name, sha in sorted(manifest.items()):
        if name not in files:
            errors.append(f"missing file for manifest entry: {name}")
        elif compute_sha256(files[name]) != sha:
            errors.append(f"sha drift: {name} content does not match the pinned manifest sha")
    for name in sorted(files):
        if name not in manifest:
            errors.append(
                f"untracked acceptance file: {name} (add it to SHA256SUMS via "
                f"--write, CODEOWNERS-approved)"
            )
    return errors


def _load_files(acc_dir: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in glob.glob(os.path.join(acc_dir, "*.yaml")):
        with open(path, "rb") as fh:
            files[os.path.basename(path)] = fh.read()
    return files


def _render_manifest(files: dict[str, bytes]) -> str:
    lines = [f"{compute_sha256(body)}  {name}" for name, body in sorted(files.items())]
    return MANIFEST_HEADER + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="acceptance-frozen gate (C8)")
    ap.add_argument("--dir", default="audit/acceptance")
    ap.add_argument("--manifest", default="audit/acceptance/SHA256SUMS")
    ap.add_argument(
        "--write", action="store_true", help="regenerate the manifest (CODEOWNERS-guarded)"
    )
    args = ap.parse_args(argv)

    files = _load_files(args.dir)

    if args.write:
        with open(args.manifest, "w") as fh:
            fh.write(_render_manifest(files))
        print(f"wrote {args.manifest} ({len(files)} acceptance file(s))")
        return 0

    if not os.path.exists(args.manifest):
        print(f"::error::acceptance manifest missing: {args.manifest}")
        return 1
    with open(args.manifest) as fh:
        manifest = parse_manifest(fh.read())

    errors = verify(manifest, files)
    if errors:
        for e in errors:
            print(f"::error::acceptance-frozen: {e}")
        print(f"== acceptance-frozen: FAIL ({len(errors)} error(s)) ==")
        return 1
    print(f"== acceptance-frozen: PASS ({len(files)} acceptance file(s) match manifest) ==")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
