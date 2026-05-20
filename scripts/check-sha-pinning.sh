#!/usr/bin/env bash
# Enforce SHA-pinning on every `uses:` line in `.github/workflows/*.yml`.
#
# Why this exists
# ---------------
# Tag refs (`@v3`, `@main`, etc.) are mutable — the upstream owner can
# repoint them at any commit. A compromised marketplace action published
# under the same tag would execute on the next CI run with full
# `GITHUB_TOKEN` scope and the repo's secrets store. SHA pinning anchors
# the workflow to immutable content addressed by the commit hash.
#
# Industry references
#   - OpenSSF Scorecard "pinned-dependencies" check
#       https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies
#   - OWASP CICD-SEC-05 (Insufficient Pipeline-Based Access Controls)
#       https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-05
#   - GitHub hardening guide: "Using third-party actions"
#       https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#using-third-party-actions
#   - SLSA L2 (Build Provenance) — pinning is a prerequisite to provenance
#
# What is allowed
#   - `uses: <repo>@<40-char-hex-sha>  # comment (optional)` — third-party
#     or first-party actions, both must SHA-pin
#   - `uses: ./<path>` — local actions in this repo (always trusted)
#   - `uses: docker://<image>` — Docker image references (pinned via
#     content digest in the image registry; out of scope for this check)
#
# What is rejected
#   - `uses: <repo>@<tag>` (e.g. `@v3`, `@v4.1.1`, `@main`)
#   - `uses: <repo>@<branch>` (e.g. `@main`, `@master`)
#   - `uses: <repo>@<short-sha>` (fewer than 40 hex chars)
#
# Escape hatch (default OFF)
#   If `ALLOW_GITHUB_OWNED_TAGS=1` is set in the environment, references to
#   `actions/*` and `github/*` MAY be tag-pinned per GitHub's own
#   recommendation that github-owned actions are safe to track by tag
#   (https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#using-third-party-actions).
#   This is OFF by default because the repo currently SHA-pins github-owned
#   actions too (see docs/runbooks/allowed-actions-restriction.md inventory),
#   and we don't want to silently regress.
#
# Exit codes
#   0 — every `uses:` line is SHA-pinned (or in the allowed escape-hatch set)
#   1 — one or more violations found; CI must fail

set -euo pipefail

WORKFLOWS_DIR="${1:-.github/workflows}"
ALLOW_GITHUB_OWNED_TAGS="${ALLOW_GITHUB_OWNED_TAGS:-0}"

if [ ! -d "$WORKFLOWS_DIR" ]; then
  echo "ERROR: workflows directory not found: $WORKFLOWS_DIR" >&2
  exit 1
fi

# Collect every `uses:` line from every .yml/.yaml under WORKFLOWS_DIR.
# Using -h to drop filename prefix; we rebuild it below with file:line context.
violations=0
checked=0

# Use a temporary file to count and iterate (avoids subshell scope issues with
# while-read pipelines). shellcheck disable=SC2155 is intentional.
tmp_violations="$(mktemp)"
trap 'rm -f "$tmp_violations"' EXIT

# Loop over every yml file (no nullglob — handle empty dir gracefully)
shopt -s nullglob
files=("$WORKFLOWS_DIR"/*.yml "$WORKFLOWS_DIR"/*.yaml)
shopt -u nullglob

if [ "${#files[@]}" -eq 0 ]; then
  echo "WARN: no workflow files in $WORKFLOWS_DIR — nothing to check" >&2
  exit 0
fi

for file in "${files[@]}"; do
  # grep -nE: line-numbered, extended regex. -H: print filename even with one file.
  # Match `uses:` indented or top-of-line, followed by the action ref.
  while IFS= read -r match; do
    # match looks like: ".github/workflows/ci.yml:48:        uses: actions/checkout@de0fac2... # v6"
    file_line="${match%%:*}"
    rest="${match#*:}"
    line_no="${rest%%:*}"
    content="${rest#*:}"

    # Strip leading whitespace and the literal "uses:" prefix
    uses_ref="${content#*uses:}"
    uses_ref="${uses_ref# }"
    uses_ref="${uses_ref# }"  # tolerate double-space

    # Strip trailing comment (everything from " #" onward)
    uses_ref_no_comment="${uses_ref%% #*}"
    # Strip trailing whitespace
    uses_ref_no_comment="${uses_ref_no_comment%"${uses_ref_no_comment##*[![:space:]]}"}"

    # Skip local actions
    case "$uses_ref_no_comment" in
      ./*|../*) continue ;;
      docker://*) continue ;;
    esac

    # Must contain @
    if [[ "$uses_ref_no_comment" != *"@"* ]]; then
      echo "$file_line:$line_no:MISSING_REF: $uses_ref_no_comment" >> "$tmp_violations"
      continue
    fi

    action_name="${uses_ref_no_comment%@*}"
    ref="${uses_ref_no_comment##*@}"

    checked=$((checked + 1))

    # Is this a 40-char hex SHA?
    if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
      # SHA pin — accept
      continue
    fi

    # Not a SHA. Check escape hatch.
    if [ "$ALLOW_GITHUB_OWNED_TAGS" = "1" ]; then
      case "$action_name" in
        actions/*|github/*)
          # github-owned, tag pin tolerated under the escape hatch
          continue
          ;;
      esac
    fi

    # Violation
    echo "$file_line:$line_no:TAG_PIN: $action_name@$ref" >> "$tmp_violations"
  done < <(grep -nHE "^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]" "$file" || true)
done

violations=$(wc -l < "$tmp_violations" | tr -d '[:space:]')

echo "SHA-pinning check: scanned $checked uses: references in ${#files[@]} workflow file(s)."

if [ "$violations" -gt 0 ]; then
  echo ""
  echo "FAIL — $violations unpinned reference(s):" >&2
  echo "" >&2
  # Decorate each violation with a fix hint
  while IFS=: read -r vfile vline vkind vrest; do
    case "$vkind" in
      TAG_PIN)
        action_at_ref="${vrest# }"
        action_name="${action_at_ref%@*}"
        ref="${action_at_ref##*@}"
        echo "  $vfile:$vline" >&2
        echo "    uses: $action_at_ref" >&2
        echo "    fix:  pin to a 40-char commit SHA. To resolve the SHA for tag '$ref':" >&2
        echo "          gh api /repos/$action_name/git/refs/tags/$ref --jq '.object.sha'" >&2
        echo "          (then prefer the underlying commit SHA: 'gh api /repos/$action_name/git/tags/<sha>' if the tag is annotated)" >&2
        ;;
      MISSING_REF)
        echo "  $vfile:$vline" >&2
        echo "    uses: $vrest" >&2
        echo "    fix:  add an explicit '@<40-char-sha>' suffix" >&2
        ;;
    esac
    echo "" >&2
  done < "$tmp_violations"

  echo "See docs/runbooks/allowed-actions-restriction.md for the project policy." >&2
  echo "See scripts/check-sha-pinning.sh header for the rationale and references." >&2
  exit 1
fi

echo "OK — every uses: reference is SHA-pinned."
exit 0
