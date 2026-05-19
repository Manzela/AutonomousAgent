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
  # sops cannot infer format from the .sops extension at decrypt time, so we
  # pass an explicit type when the original suffix is a structured format.
  case "$out" in
    *.env)
      sops -d --input-type dotenv --output-type dotenv "$enc" > "$out"
      ;;
    *.json)
      sops -d --input-type json --output-type json "$enc" > "$out"
      ;;
    *.yaml | *.yml)
      sops -d --input-type yaml --output-type yaml "$enc" > "$out"
      ;;
    *)
      # Plain text / binary — sops auto-detects from content
      sops -d "$enc" > "$out"
      ;;
  esac
  chmod 600 "$out"
done

# Source the env file format secrets so subsequent docker compose can reference vars
if [ -f telegram.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./telegram.env
  set +a
fi

# Honcho hosted SDK (issue #54). HONCHO_API_KEY is consumed by
# hermes-agent/plugins/memory/honcho via HonchoClientConfig.from_env.
# The hermes service also references this file via env_file in compose,
# so this sourcing is for any host-side tooling that needs the key.
if [ -f honcho.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./honcho.env
  set +a
fi

if [ -f honcho-db-password ]; then
  HONCHO_DB_PASSWORD="$(cat honcho-db-password)"
  export HONCHO_DB_PASSWORD
fi

# Derive secrets/hermes-provider.env so the `hermes` container sees the
# LiteLLM proxy as an OpenAI-compatible provider. Hermes' provider auto-
# detection reads OPENAI_API_KEY + OPENAI_BASE_URL from env, not from
# cli-config.yaml's `llm:` block.
if [ -f litellm-master-key ]; then
  cat > hermes-provider.env <<INNER
OPENAI_API_KEY=$(cat litellm-master-key)
OPENAI_BASE_URL=http://litellm-proxy:4000
HERMES_DEFAULT_MODEL=vertex_ai/claude-opus-4-7
HERMES_FALLBACK_MODEL=vertex_ai/claude-sonnet-4-6
INNER
  chmod 600 hermes-provider.env
fi

echo "Secrets decrypted. Plaintext files are gitignored."
