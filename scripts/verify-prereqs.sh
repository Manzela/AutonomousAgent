#!/usr/bin/env bash
# Verifies all host prerequisites for running Hermes Agent locally.
# Exits non-zero if any prereq is missing or misconfigured.
set -euo pipefail

errors=0
check() {
  local name="$1" cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    echo "✓ $name"
  else
    echo "✗ $name — install or fix"
    errors=$((errors+1))
  fi
}

echo "Checking host prerequisites..."
check "docker"          "docker --version"
check "docker-compose"  "docker compose version"
check "docker daemon"   "docker info"
check "uv"              "uv --version"
check "jq"              "jq --version"
check "gcloud"          "gcloud --version"
check "sops"            "sops --version"
check "age"             "age --version"
check "git"             "git --version"

echo
echo "Checking GCP authentication..."
if gcloud config get-value project 2>/dev/null | grep -q autonomous-agent-2026; then
  echo "✓ gcloud project is autonomous-agent-2026"
else
  echo "✗ gcloud project is not autonomous-agent-2026 (run: gcloud config set project autonomous-agent-2026)"
  errors=$((errors+1))
fi

if gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "✓ Application Default Credentials are valid"
else
  echo "✗ ADC missing (run: gcloud auth application-default login)"
  errors=$((errors+1))
fi

echo
if [ "$errors" -gt 0 ]; then
  echo "$errors prerequisite(s) failed. See above."
  exit 1
fi
echo "All prerequisites satisfied."
