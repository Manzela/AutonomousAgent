#!/usr/bin/env bash
# Pings Healthchecks.io with the Hermes container health status.
# Called by cron on the host every 5 minutes.
set -euo pipefail

# Cron runs with an empty PATH on macOS, so `docker` (installed under
# /usr/local/bin by Docker Desktop) is not resolvable when this script
# fires from crontab. Prepend the standard system bin paths so cron
# invocations succeed without the user having to edit their crontab
# (closes issues #38, #39; same root cause as #29). Manual / interactive
# invocations are unaffected — they already have these on PATH.
export PATH=/usr/local/bin:/usr/bin:/bin:$PATH

# macOS sops looks at ~/Library/Application Support/sops/age/keys.txt by default.
# Pin to the canonical XDG path used by the rest of the project.
export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL_FILE="$ROOT/secrets/healthchecks-url"
URL_SOPS="$ROOT/secrets/healthchecks-url.sops"

# Ensure logs directory exists for the cron output redirect
mkdir -p "$ROOT/logs"

if [ ! -f "$URL_FILE" ]; then
  if [ ! -f "$URL_SOPS" ]; then
    echo "ERROR: $URL_SOPS does not exist." >&2
    echo "Complete the manual step described in secrets/healthchecks-url.template.txt" >&2
    echo "(Phase-1 plan T30 step 1) before this script can ping Healthchecks.io." >&2
    exit 1
  fi
  echo "Decrypting healthchecks URL"
  sops -d "$URL_SOPS" > "$URL_FILE"
  chmod 600 "$URL_FILE"
fi

URL="$(cat "$URL_FILE")"

# Check that hermes container is healthy
if docker compose -f "$ROOT/deploy/docker-compose.yml" ps hermes --format json | grep -q '"Health":"healthy"'; then
  curl -fsS -m 10 "$URL" > /dev/null
  echo "Pinged healthy"
else
  curl -fsS -m 10 "${URL}/fail" > /dev/null
  echo "Reported failure"
  exit 1
fi
