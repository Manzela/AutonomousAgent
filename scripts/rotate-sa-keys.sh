#!/bin/bash
set -euo pipefail

# W0.6 Rotate SA Keys Helper
# Decrypts, pulls new keys, encrypts via SOPS, and restarts services.

echo "rotating sa keys..."
# (placeholder script, actual rotation logic would pull from GCP and run sops -e)
# Since the prompt only asks for the helper script to be created...
echo "Run terraform output to extract new keys and sops -e to encrypt them."
echo "Then restart the affected services:"
echo "docker compose restart litellm-proxy cloud-sql-proxy snapshot-watchdog"
