#!/usr/bin/env bash
# P1-4 (Go-Live audit): Verify that branch_protection.tf's 25 required CI
# contexts have been applied to the GitHub repo.
#
# Usage:
#   ./scripts/verify_branch_protection.sh [OWNER/REPO] [BRANCH]
#
# Defaults:
#   OWNER/REPO: Manzela/AutonomousAgent
#   BRANCH: main
#
# Requires: gh CLI (authenticated), jq
#
# Exit codes:
#   0 — all expected contexts are active
#   1 — one or more contexts are missing
#   2 — pre-flight check failure (gh/jq not installed, auth failure)

set -euo pipefail

REPO="${1:-Manzela/AutonomousAgent}"
BRANCH="${2:-main}"

# ── Pre-flight checks ────────────────────────────────────────────────────────

if ! command -v gh &>/dev/null; then
    echo "ERROR: gh CLI not found. Install: https://cli.github.com/" >&2
    exit 2
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq not found. Install: brew install jq / apt install jq" >&2
    exit 2
fi

if ! gh auth status &>/dev/null; then
    echo "ERROR: gh not authenticated. Run: gh auth login" >&2
    exit 2
fi

# ── Expected required status checks (from branch_protection.tf) ──────────────

EXPECTED_CONTEXTS=(
    "Unit Tests"
    "Ruff Lint"
    "Mypy Type Check"
    "Trivy Vulnerability Scan"
    "OSV Scanner"
    "CodeQL"
    "SBOM + Cosign"
    "License & Dependency Gate"
    "No SA Key Gate"
    "Acceptance (Frozen)"
    "SP-G1 Golden Eval"
    "SP-O1 Cost Recorder"
    "Lint Cost Tiers"
    "Integration Tests"
    "Schema Compat Check"
    "Checkpoint Compat"
    "Dependency Audit"
    "Prompt Injection Scan"
    "SP-R1 PII Scrubber"
    "Container Build"
    "Compose Health"
    "Memory Contract"
    "E2E Smoke"
    "Canary Gate"
    "DR Drill"
)

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  Branch Protection Verification — ${REPO} (${BRANCH})            ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Fetching branch protection rules..."

# ── Fetch actual required status checks ──────────────────────────────────────

ACTUAL_JSON=$(gh api "/repos/${REPO}/branches/${BRANCH}/protection/required_status_checks" 2>/dev/null || true)

if [ -z "$ACTUAL_JSON" ] || echo "$ACTUAL_JSON" | jq -e '.message' &>/dev/null; then
    echo "⚠️  Could not fetch branch protection rules."
    echo "   Either branch protection is not enabled, or you lack admin access."
    echo "   Response: $(echo "$ACTUAL_JSON" | jq -r '.message // "empty"')"
    echo ""
    echo "ACTION REQUIRED: Run 'terraform apply -target=github_branch_protection.main'"
    exit 1
fi

# Extract the list of required context names
ACTUAL_CONTEXTS=$(echo "$ACTUAL_JSON" | jq -r '.contexts[]' 2>/dev/null || true)

if [ -z "$ACTUAL_CONTEXTS" ]; then
    # Try the newer checks API format
    ACTUAL_CONTEXTS=$(echo "$ACTUAL_JSON" | jq -r '.checks[]?.context // empty' 2>/dev/null || true)
fi

# ── Compare expected vs actual ───────────────────────────────────────────────

MISSING=()
FOUND=()
TOTAL=${#EXPECTED_CONTEXTS[@]}

for ctx in "${EXPECTED_CONTEXTS[@]}"; do
    if echo "$ACTUAL_CONTEXTS" | grep -qxF "$ctx"; then
        FOUND+=("$ctx")
    else
        MISSING+=("$ctx")
    fi
done

# ── Report ───────────────────────────────────────────────────────────────────

echo ""
echo "Expected: ${TOTAL} required status checks"
echo "Found:    ${#FOUND[@]}"
echo "Missing:  ${#MISSING[@]}"
echo ""

if [ ${#FOUND[@]} -gt 0 ]; then
    echo "✅ Present (${#FOUND[@]}):"
    for ctx in "${FOUND[@]}"; do
        echo "   ✓ ${ctx}"
    done
    echo ""
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "❌ Missing (${#MISSING[@]}):"
    for ctx in "${MISSING[@]}"; do
        echo "   ✗ ${ctx}"
    done
    echo ""
    echo "ACTION REQUIRED: Run 'terraform apply -target=github_branch_protection.main'"
    echo "                 in terraform/phase-0a-gcp/"
    exit 1
fi

# Check for EXTRA contexts not in our expected list (informational)
EXTRA=()
while IFS= read -r ctx; do
    found=false
    for expected in "${EXPECTED_CONTEXTS[@]}"; do
        if [ "$ctx" = "$expected" ]; then
            found=true
            break
        fi
    done
    if [ "$found" = "false" ] && [ -n "$ctx" ]; then
        EXTRA+=("$ctx")
    fi
done <<< "$ACTUAL_CONTEXTS"

if [ ${#EXTRA[@]} -gt 0 ]; then
    echo "ℹ️  Additional contexts (not in expected list):"
    for ctx in "${EXTRA[@]}"; do
        echo "   + ${ctx}"
    done
    echo ""
fi

echo "✅ All ${TOTAL} required status checks are active."
exit 0
