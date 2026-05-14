#!/usr/bin/env bash
# Decrypts all secrets/*.sops files into adjacent plaintext files used by docker compose.
# Plaintext files are gitignored. Re-run after pulling new encrypted secrets.
set -euo pipefail

# macOS sops looks at ~/Library/Application Support/sops/age/keys.txt by default.
# Pin to the canonical XDG path used by the rest of the project.
export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/secrets"

shopt -s nullglob
for enc in *.sops; do
  out="${enc%.sops}"
  echo "Decrypting $enc -> $out"
  sops -d "$enc" > "$out"
  chmod 600 "$out"
done

# Source the env file format secrets so subsequent docker compose can reference vars
if [ -f telegram.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./telegram.env
  set +a
fi

if [ -f honcho-db-password ]; then
  export HONCHO_DB_PASSWORD="$(cat honcho-db-password)"
fi

echo "Secrets decrypted. Plaintext files are gitignored."
