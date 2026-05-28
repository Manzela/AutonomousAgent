#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
SOPS_RECIPIENTS_FILE=".sops.yaml"
INPUT_PLAINTEXT="secrets/hermes-provider.env"
OUTPUT_ENCRYPTED="secrets/hermes-provider.env.sops"

if [ ! -f "$KEY_FILE" ]; then
  echo "FATAL: SOPS age key file not found at $KEY_FILE" >&2
  echo "       Refusing to silently generate a new key (this would orphan every existing .sops file)." >&2
  exit 1
fi

if [ ! -f "$SOPS_RECIPIENTS_FILE" ]; then
  echo "FATAL: $SOPS_RECIPIENTS_FILE missing — cannot determine recipients." >&2
  exit 1
fi

if [ ! -f "$INPUT_PLAINTEXT" ]; then
  echo "Nothing to encrypt at $INPUT_PLAINTEXT — exiting cleanly." >&2
  exit 0
fi

if [ -f "$OUTPUT_ENCRYPTED" ] \
   && SOPS_AGE_KEY_FILE="$KEY_FILE" sops --decrypt "$OUTPUT_ENCRYPTED" \
      | diff -q - "$INPUT_PLAINTEXT" >/dev/null 2>&1; then
  echo "Encrypted file already matches plaintext — idempotent no-op." >&2
  exit 0
fi

SOPS_AGE_KEY_FILE="$KEY_FILE" sops --encrypt \
  --config "$SOPS_RECIPIENTS_FILE" \
  --output "$OUTPUT_ENCRYPTED" \
  "$INPUT_PLAINTEXT"

echo "Encrypted $INPUT_PLAINTEXT -> $OUTPUT_ENCRYPTED" >&2
echo "REMINDER: delete the plaintext via 'rm $INPUT_PLAINTEXT' once you have verified the encrypted file." >&2
