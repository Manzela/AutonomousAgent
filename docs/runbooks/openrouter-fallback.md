# OpenRouter Fallback — Operator Runbook

**Purpose:** Eliminate single-provider risk (R3) for the LiteLLM proxy. When
Vertex AI returns 5xx or quota-exhausted errors, the router promotes the
same model family to OpenRouter automatically. The fallback is a
**resilience measure** — primary traffic stays on Vertex while Vertex is
healthy.

**Owner:** Solo operator (`@Manzela`).
**Defined:** Wave-4 audit task #35 (closes R3).
**Source-of-record:** `deploy/litellm/config.yaml` `model_list` +
`litellm_settings.fallbacks`; `deploy/docker-compose.yml` LiteLLM service
`env_file` block.

---

## Architecture

```text
        ┌──────────────────────────┐
Hermes →│ vertex_ai/claude-opus-4-7│──[5xx / 429 / 529]──┐
        └──────────────────────────┘                     │
                       │  primary (Vertex healthy)        │
                       ▼                                  ▼
                  Vertex AI                  openrouter/anthropic/claude-opus-4
                                                          │
                                                          ▼
                                                   OpenRouter →
                                              Anthropic native / partners
```

LiteLLM's router compares the originating model name against the
`fallbacks:` map; on a retriable error (5xx, 408, 425, 429, 502, 503,
504, 529) AFTER the per-call `num_retries: 5` budget is exhausted, it
re-issues the call against the fallback model with the same prompt.
There is no prompt translation — both providers serve the same model
weights, so the request body is portable as-is.

---

## One-time secret provisioning

### 1. Mint the API key

Sign in to <https://openrouter.ai/keys>. Click **Create key**. Name it
`autonomousagent-prod-fallback` and set:

- **Credit limit:** $50 USD/month (LiteLLM's `max_budget: 100` USD/day is
  the primary spend cap; this is a defense-in-depth fence specific to the
  fallback path, which should rarely fire).
- **Models:** restrict to the three flagships in scope —
  `anthropic/claude-opus-4`, `anthropic/claude-sonnet-4`,
  `google/gemini-2.5-pro`. Any other model is out-of-scope and signals a
  config drift.
- **Allowed origins:** leave empty (server-side use; no browser CORS).

Copy the resulting `sk-or-v1-...` string. It is shown **once** — if you
miss it, revoke and re-mint.

### 2. Verify model IDs against OpenRouter's live catalog

OpenRouter model slugs are part of their public `/api/v1/models` contract
and have been stable for >12 months for the listed flagships, but verify
before merge in case of recent renames:

```bash
curl -s https://openrouter.ai/api/v1/models \
  | jq -r '.data[] | select(.id | test("^(anthropic/claude-opus-4|anthropic/claude-sonnet-4|google/gemini-2.5-pro)$")) | .id'
```

Expected output (exact, no extra entries):

```text
anthropic/claude-opus-4
anthropic/claude-sonnet-4
google/gemini-2.5-pro
```

If any ID differs (e.g. version suffix changed, model deprecated):

1. Update the matching `model:` line in `deploy/litellm/config.yaml`
   `model_list`.
2. Update the matching value in `litellm_settings.fallbacks`.
3. Re-run the `curl ... | jq` above to confirm the new ID is canonical.

### 3. Encrypt and commit the secret skeleton

The `.env` file pattern matches the existing `secrets/*.env.sops` convention.

```bash
cat > /tmp/openrouter.env <<EOF
OPENROUTER_API_KEY=sk-or-v1-REPLACE_WITH_REAL_KEY
EOF

sops --encrypt --age "$(cat secrets/.age.pub)" /tmp/openrouter.env \
  > secrets/openrouter.env.sops

shred -u /tmp/openrouter.env
git add secrets/openrouter.env.sops
git commit -m "chore(secrets): add openrouter api key (encrypted)"
```

The plaintext `secrets/openrouter.env` (consumed by Compose) is created
locally only and is covered by the deny-by-default rule in
`secrets/.gitignore`. It is NEVER committed.

### 4. Decrypt locally for Compose

```bash
sops --decrypt secrets/openrouter.env.sops > secrets/openrouter.env
chmod 600 secrets/openrouter.env
```

### 5. Restart the LiteLLM proxy

```bash
docker compose -f deploy/docker-compose.yml up -d litellm-proxy
docker compose -f deploy/docker-compose.yml logs --tail=50 litellm-proxy \
  | grep -iE "openrouter|fallback|loaded"
```

Expected: log lines referencing `openrouter/anthropic/claude-opus-4` (etc)
as added models, no auth errors. The proxy does NOT validate the OpenRouter
key at startup — auth happens on first fallback call.

---

## Verification

### Smoke-test the primary path (Vertex, no fallback)

```bash
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $(cat secrets/litellm-master-key)" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vertex_ai/claude-opus-4-7",
    "messages": [{"role":"user","content":"reply with the single word PONG"}],
    "max_tokens": 10
  }' | jq -r '.choices[0].message.content'
```

Expected: `PONG`. The fallback should NOT fire.

### Force-test the fallback path

The cleanest way to exercise the fallback without breaking Vertex is to
issue a request against a model alias that does not exist on Vertex but
does have a fallback mapping. Since all three model aliases map 1:1, the
production path is to wait for an actual Vertex degradation. For a manual
test, temporarily set an invalid `vertex_project` in `config.yaml`, reload,
issue the same request, and confirm the response comes back via the
fallback (LiteLLM adds an `x-litellm-model-name` response header showing
which model actually served the call). Revert the config change after.

### Confirm fallback is wired in router_settings

```bash
docker compose -f deploy/docker-compose.yml exec litellm-proxy \
  /app/.venv/bin/python -c "
import yaml
cfg = yaml.safe_load(open('/app/config.yaml'))
print('fallbacks:', cfg.get('router_settings', {}).get('fallbacks', '<MISSING>'))
"
```

Expected:

```text
fallbacks: [
  {'vertex_ai/claude-opus-4-7': ['openrouter/anthropic/claude-opus-4']},
  {'vertex_ai/claude-sonnet-4-6': ['openrouter/anthropic/claude-sonnet-4']},
  {'vertex_ai/gemini-3.1-pro-preview': ['openrouter/google/gemini-2.5-pro']}
]
```

---

## Rollback

Two surfaces to revert, in order:

### 1. Disable the fallback (keep the secret)

Comment out the `router_settings.fallbacks` block in
`deploy/litellm/config.yaml` and the three `openrouter/*` entries in
`model_list`, then `docker compose up -d litellm-proxy`. This stops new
fallback dispatches without removing the secret.

### 2. Revoke the API key

If the secret is suspected to be compromised:

1. Sign in to <https://openrouter.ai/keys>.
2. Click **Revoke** on the `autonomousagent-prod-fallback` key.
3. Re-mint per "One-time secret provisioning" §1 above.
4. Re-encrypt per §3.
5. `sops --decrypt secrets/openrouter.env.sops > secrets/openrouter.env`.
6. `docker compose restart litellm-proxy`.

### 3. Remove the secret file entirely

```bash
rm secrets/openrouter.env
docker compose restart litellm-proxy
```

The Compose `env_file` entry is `required: false`, so the stack continues
to boot. Any fallback dispatch fails-loud with 401, surfacing the
misconfiguration immediately rather than silently failing-over.

---

## Cost monitoring

Fallback dispatches are visible in two places:

- **LiteLLM `/spend/logs`** (DB-backed since Phase 1.1 — issue #55): each
  fallback call appears with `model` = the openrouter alias. The weekly
  cost summary (#29, PR #108) reports them under their own line items.
- **OpenRouter dashboard:** <https://openrouter.ai/credits> shows real-time
  spend against the $50/mo limit set during key creation.

If fallback spend exceeds $5/week for two consecutive weeks, investigate
Vertex availability — a healthy primary should keep fallback usage near
zero.

---

## Related

- Audit task #35 — closes R3 (single-provider risk).
- `docs/spec/phase2.md` §F-codes — fallback dispatches do NOT trigger an
  F-code by default; the underlying retried-then-failed call is what the
  trichotomy router classifies. A fallback success is a non-event.
- `audit/2026-05-19-resume-orchestration/audit-plan.md` §R3.
