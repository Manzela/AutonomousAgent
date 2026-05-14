# 0002. Vertex AI via LiteLLM proxy

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

Hermes Agent expects an OpenAI-format chat completions endpoint. Our existing GCP project (`i-for-ai`) is configured for Anthropic Claude 4.7 via Vertex AI, which is a Google IAM–authenticated endpoint, not an OpenAI-compatible one. We want to consume Vertex AI without modifying Hermes' provider code.

## Decision

We will run [LiteLLM](https://github.com/BerriAI/litellm) as a sidecar proxy that exposes an OpenAI-format `/v1/chat/completions` endpoint internally and translates requests to Vertex AI on the backend. Hermes connects to LiteLLM at `http://litellm-proxy:4000`. LiteLLM authenticates to Vertex AI via Application Default Credentials (Phase 1, mounted from host) or Workload Identity Federation (Phase 2).

LiteLLM also handles: per-day budget cap enforcement, exponential-backoff retries on 429/503, OTel cost telemetry export.

## Consequences

### Positive
- Zero changes to Hermes' provider code
- Consistent with the Claude Code backend already used for this project (Anthropic via Vertex AI)
- Single chokepoint for budget enforcement
- Easy to add multi-model routing later (cheap/strong split) without touching the agent
- LiteLLM emits cost metrics natively

### Negative
- One more service in the compose stack
- Vertex AI auth complexity (ADC vs WIF) hits LiteLLM rather than Hermes
- LiteLLM is a moving target; pin a tag rather than `:latest` if stability matters more than features

### Neutral
- LiteLLM supports many providers, so future additions (OpenAI fallback, OpenRouter, etc.) are config-only changes

## Alternatives considered

### Option A: Patch Hermes' provider layer to call Vertex AI directly
- Pros: One less service
- Cons: Would diverge from upstream; lose upgrade path; our wrap-don't-fork rule (ADR 0001)
- Why rejected: Violates ADR 0001

### Option B: Use Anthropic API key directly (skip Vertex AI)
- Pros: Simplest; Hermes supports Anthropic out of the box
- Cons: No reuse of `i-for-ai` GCP project; data residency / billing fragmented; we already use Vertex AI for Claude Code
- Why rejected: User explicitly asked for Vertex AI consistency

### Option C: AI Gateway (Cloudflare AI Gateway, Portkey, etc.)
- Pros: Some have similar features
- Cons: Adds a third party; LiteLLM is OSS / self-hostable; no benefit over LiteLLM here
- Why rejected: LiteLLM is sufficient and self-owned

## References

- [LiteLLM Vertex AI provider docs](https://docs.litellm.ai/docs/providers/vertex)
- Spec §2 (component table)
