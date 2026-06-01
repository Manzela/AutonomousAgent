#!/usr/bin/env bash
# SP-00 acceptance probe — prove the LangGraph spine survived garak's removal.
#
# WHY: SP-22 permanently removed garak from pyproject.toml (dev extra). This
# probe is the POST-removal red-green gate: it asserts (a) garak is ABSENT from
# pyproject.toml so the removal is not accidentally reverted, and (b) langgraph
# + langgraph-checkpoint are still present as direct dependencies in the lock —
# confirming the spine is no longer bound to the garak->langchain->langgraph path.
#
# Modes:
#   default              Assertion-only mode: verify pyproject + lock offline.
#   SP00_IMPORT_PROOF=1  Additionally run `import langgraph; import langgraph.checkpoint`
#                        against the real environment (used in CI with network access).
#   SP00_UV_FLAGS        Override uv flags (default: --frozen). CI can set "" for
#                        network mode if a full re-sync is needed.
#
# Exit 0 = spine is intact and garak is absent. Non-zero = FAIL.
#
# History: before SP-22 this probe STRIPPED garak from a scratch pyproject and
# re-resolved, asserting the spine survived that hypothetical removal. After SP-22
# the removal is permanent — so the probe now asserts the real tree has no garak
# and the lock still carries langgraph as a direct dep.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# --- 1. Assert garak is ABSENT from pyproject.toml --------------------------------
echo "== checking pyproject.toml has no garak dependency =="
python3 - <<'PY'
import re, sys
src = open("pyproject.toml").read()
# Match any live (non-comment) garak dep line
if re.search(r'^[^#\n]*"garak', src, re.M):
    sys.exit("FAIL: garak dependency line found in pyproject.toml -- SP-22 removal was reverted")
print("PASS (absent): garak is not present in pyproject.toml.")
PY

# --- 2. Assert langgraph + langgraph-checkpoint are DIRECT, version-pinned deps ----
echo "== checking langgraph + langgraph-checkpoint are direct deps in pyproject.toml =="
if ! grep -Eq '^[[:space:]]*"langgraph(==|>=)[0-9]' pyproject.toml; then
    echo "FAIL: langgraph is not a direct, version-pinned dep in pyproject.toml."
    exit 1
fi
echo "PASS (direct dep): langgraph is a direct, version-pinned dependency."

if ! grep -Eq '^[[:space:]]*"langgraph-checkpoint(==|>=)[0-9]' pyproject.toml; then
    echo "FAIL: langgraph-checkpoint is not a direct, version-pinned dep in pyproject.toml."
    exit 1
fi
echo "PASS (direct dep): langgraph-checkpoint is a direct, version-pinned dependency."

# --- 3. Assert langgraph + langgraph-checkpoint remain in the lock -----------------
echo "== checking langgraph + langgraph-checkpoint are in uv.lock =="
if ! grep -Eq '^name = "langgraph"$' uv.lock; then
    echo "FAIL: langgraph is ABSENT from the lock -- the spine was broken."
    exit 1
fi
echo "PASS (lock): langgraph present in uv.lock."

if ! grep -Eq '^name = "langgraph-checkpoint"$' uv.lock; then
    echo "FAIL: langgraph-checkpoint ABSENT from uv.lock."
    exit 1
fi
echo "PASS (lock): langgraph-checkpoint present in uv.lock."

# --- 4. Assert garak is NOT in the lock either ------------------------------------
echo "== checking garak is absent from uv.lock =="
if grep -Eq '^name = "garak"$' uv.lock; then
    echo "FAIL: garak is still present in uv.lock -- lock has not been regenerated after removal."
    exit 1
fi
echo "PASS (lock-absent): garak is not in uv.lock."

# --- 5. Import proof (CI / network) -----------------------------------------------
if [ "${SP00_IMPORT_PROOF:-0}" = "1" ]; then
    UV_RUN_FLAGS="${SP00_UV_FLAGS---frozen}"
    echo "== import proof: uv run python -c 'import langgraph; import langgraph.checkpoint' =="
    uv run ${UV_RUN_FLAGS} python -c \
        "import langgraph, langgraph.checkpoint; from importlib.metadata import version; print('PASS (import): langgraph', version('langgraph'), '+ langgraph.checkpoint', version('langgraph-checkpoint'), 'both importable post-garak-removal')"
fi

echo "SP-00 probe: OK -- garak absent; spine intact."
