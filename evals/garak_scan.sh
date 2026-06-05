#!/usr/bin/env bash
# garak Vulnerability Scanning (Phase 2.5)
#
# Runs garak to scan the LLM proxy for known vulnerabilities (e.g., prompt injection,
# hallucination probes, encoding bypasses).

set -euo pipefail

echo "Starting garak vulnerability scan..."

# Assume litellm is running locally at :4000
TARGET_URI="http://localhost:4000/v1"

# We use the garak CLI installed via uv
# Probe standard injections and prompt overflows
uv run garak \
    --model_type openai \
    --model_name gpt-4o \
    --generations 1 \
    --probes promptinject,encoding,leakplay \
    --report_prefix evals/garak_report \
    --parallel-requests 2

echo "garak scan complete. Reports saved to evals/"
