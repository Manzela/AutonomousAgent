#!/usr/bin/env bash
# Update the SOUL.md integrity pin in config/limits.yaml.
#
# Use when you intentionally edit config/hermes/SOUL.md. This script:
#   1. computes the live sha256 of config/hermes/SOUL.md
#   2. rewrites the `soul_md_sha256:` line in config/limits.yaml in place
#   3. prints a confirmation diff (or "already current" if the pin matches)
#
# Idempotent: re-running with no SOUL.md changes leaves the working tree
# clean. Stage the diff and commit it in the SAME commit as your SOUL.md
# edit so reviewers can see the intent (see CONTRIBUTING.md ->
# "Updating pinned hashes (SOUL.md)").
#
# Hash tool: `shasum -a 256` is portable across macOS (BSD) and Linux
# (GNU coreutils). Do NOT switch to `sha256sum` — that's Linux-only and
# silently absent on most contributor laptops.
#
# Closes audit P2 #36 follow-up (operator footgun).

set -euo pipefail

# Resolve repo root from this script's location so the script works no
# matter where it's invoked from (CWD-independent).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SOUL_MD="${REPO_ROOT}/config/hermes/SOUL.md"
LIMITS_YAML="${REPO_ROOT}/config/limits.yaml"

err() { echo "update_soul_pin: error: $*" >&2; exit 1; }

[ -f "${SOUL_MD}" ]      || err "missing: ${SOUL_MD}"
[ -f "${LIMITS_YAML}" ]  || err "missing: ${LIMITS_YAML}"
command -v shasum >/dev/null 2>&1 || err "shasum not on PATH (install perl/coreutils)"
command -v awk    >/dev/null 2>&1 || err "awk not on PATH"
command -v python3 >/dev/null 2>&1 || err "python3 not on PATH"

# Compute live sha256. shasum -a 256 is Mac+Linux compatible; awk strips
# the trailing filename column so we get just the 64-char hex.
new_hash="$(shasum -a 256 "${SOUL_MD}" | awk '{print $1}')"

if [ "${#new_hash}" -ne 64 ]; then
  err "computed hash has unexpected length ${#new_hash}: ${new_hash}"
fi

# Read the current pin via Python (avoids a yaml/sed shootout for a value
# that can legitimately appear in comments elsewhere in the file).
old_hash="$(
  python3 - "${LIMITS_YAML}" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)
print(cfg.get("integrity", {}).get("soul_md_sha256", ""))
PY
)"

if [ "${old_hash}" = "${new_hash}" ]; then
  echo "update_soul_pin: pin already current (${new_hash}); no changes."
  exit 0
fi

# In-place rewrite via Python — portable across BSD and GNU sed, and we
# only touch the single `soul_md_sha256:` line under the `integrity:` key.
python3 - "${LIMITS_YAML}" "${new_hash}" <<'PY'
import re, sys
path, new_hash = sys.argv[1], sys.argv[2]
with open(path) as f:
    src = f.read()
pattern = re.compile(
    r"(^\s*soul_md_sha256:\s*)[0-9a-fA-F]{64}",
    re.MULTILINE,
)
new_src, n = pattern.subn(lambda m: m.group(1) + new_hash, src, count=1)
if n != 1:
    sys.stderr.write(
        "update_soul_pin: failed to locate `soul_md_sha256:` line in "
        f"{path} (expected exactly 1, found {n}).\n"
    )
    sys.exit(3)
with open(path, "w") as f:
    f.write(new_src)
PY

echo "update_soul_pin: ${old_hash:-<unset>} -> ${new_hash}"
echo
# Diff the change so the operator can eyeball it before committing.
# `git diff --no-color` works inside or outside a worktree; if git isn't
# available or the file isn't tracked we silently skip.
if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "${REPO_ROOT}" --no-pager diff --no-color -- config/limits.yaml || true
fi
