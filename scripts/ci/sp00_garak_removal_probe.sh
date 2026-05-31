#!/usr/bin/env bash
# SP-00 acceptance probe — prove the LangGraph spine survives garak's removal.
#
# WHY: today langgraph reaches the environment ONLY via garak->langchain->langgraph
# (PRD §6 SP-00, verified). SP-22 will delete garak. If langgraph is not a DIRECT
# dependency, that deletion silently removes the spine. This probe re-resolves the
# project with garak stripped and asserts langgraph is still there.
#
# Modes:
#   default        resolution proof — strip garak, `uv lock`, assert langgraph in
#                  the garak-free lock. Offline against uv's cache by default.
#   SP00_IMPORT_PROOF=1   additionally `uv sync --no-dev` + `import langgraph`
#                  (the faithful PRD assertion; needs network — used in CI).
#   SP00_UV_FLAGS  override uv flags (default: --offline). CI sets "" for network.
#
# Exit 0 = spine survives garak removal. Non-zero = FAIL (spine still garak-bound).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

UV_FLAGS="${SP00_UV_FLAGS---offline}"   # note: ---offline => default "--offline"; set SP00_UV_FLAGS="" to disable

SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

cp pyproject.toml "$SCRATCH/pyproject.toml"
[ -f uv.lock ] && cp uv.lock "$SCRATCH/uv.lock"

# --- strip the garak dependency line from the scratch pyproject -----------------
python3 - "$SCRATCH/pyproject.toml" <<'PY'
import re, sys
path = sys.argv[1]
src = open(path).read()
stripped = re.sub(r'^[ \t]*"garak[^"]*",[ \t]*\r?\n', '', src, flags=re.M)
if stripped == src:
    sys.exit("FAIL: no garak dependency line found to strip — probe assumption broken")
open(path, "w").write(stripped)
PY

# --- re-resolve WITHOUT garak ----------------------------------------------------
echo "== re-resolving lock with garak removed (uv lock ${UV_FLAGS}) =="
( cd "$SCRATCH" && uv lock ${UV_FLAGS} )

# --- resolution proof: langgraph must remain in the garak-free lock --------------
if ! grep -Eq '^name = "langgraph"$' "$SCRATCH/uv.lock"; then
    echo "FAIL: langgraph is ABSENT from the garak-free lock — the spine still"
    echo "      depends on the garak->langchain->langgraph path. Add langgraph as"
    echo "      a direct dependency in pyproject.toml [project.dependencies]."
    exit 1
fi
echo "PASS (resolution): langgraph present in the garak-free lock."

# also assert the checkpoint package survives, since SP-01 builds on it
if ! grep -Eq '^name = "langgraph-checkpoint"$' "$SCRATCH/uv.lock"; then
    echo "FAIL: langgraph-checkpoint ABSENT from the garak-free lock."
    exit 1
fi
echo "PASS (resolution): langgraph-checkpoint present in the garak-free lock."

# --- import proof (CI / network) -------------------------------------------------
if [ "${SP00_IMPORT_PROOF:-0}" = "1" ]; then
    echo "== import proof: uv sync --no-dev (garak-free base env) + import langgraph =="
    ( cd "$SCRATCH" \
        && uv sync --no-dev ${UV_FLAGS} \
        && uv run --no-dev python -c "import langgraph, langgraph.checkpoint; print('PASS (import): langgraph + langgraph.checkpoint import without garak/dev extra')" )
fi

echo "SP-00 probe: OK — the spine survives garak removal."
