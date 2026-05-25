# Multi-LLM Specialization Mesh — Locked Picks (May 2026)

> Audit decision artifact. Will be promoted to `docs/decisions/0008-multi-llm-specialization-mesh.md` (ADR) when the audit-plan ships into actual implementation.

## Methodology

User intent: **highest quality + highest reliability, no budget cap initially**, optimize spend later "where it makes sense per industry highest standards and BEST best practices."

Research grounded in May 2026 leaderboards:
- **BenchLM coding leaderboard** (https://benchlm.ai/blog/posts/best-llm-coding) — composite of SWE-Rebench, SWE-bench Pro, LiveCodeBench, SWE-bench Verified
- **Iternal AI 2026 LLM Selection Guide** (https://iternal.ai/llm-selection-guide) — frontier model comparison + task-to-model framework
- **ClickRank LLM Leaderboard 2026** (https://www.clickrank.ai/llm-leaderboard/) — multi-benchmark composite
- **vLLM Qwen3-Coder docs** (https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-Coder-480B-A35B.html) — self-host fit
- **dev.to Qwen3-Coder-Next 2026 guide** — Feb 2026 release for single-GPU self-host

## Frontier landscape — May 2026 (key data points)

| Capability | Leader | Score / Note |
|---|---|---|
| Coding overall (composite) | **GPT-5.4** | 73.9 BenchLM composite |
| Coding (specialized variant) | **GPT-5.3 Codex** | 90 SWE-bench Pro (highest single benchmark) |
| Coding (close 2nd, non-OpenAI) | **Claude Opus 4.6/4.7** | 72.5 BenchLM, 74 SWE-bench Pro |
| Math reasoning | **GPT-5** | Perfect AIME 2026 |
| Science reasoning | **Claude Mythos Preview** | 94.6% GPQA Diamond, 64.7% Humanity's Last Exam |
| Long-context (>200K) | **Gemini 3.1 Pro** | Best cost-efficiency at frontier; 1M+ context native |
| Open-source coding | **DeepSeek Coder 2.0** | $0.27/$1.10 per M tokens; 61 SWE-bench Pro; falls apart on multi-file |
| Self-hosted (single GPU) | **Qwen3-Coder-Next-FP8** | Released Feb 2026, designed for coding agents, fits A100 80GB |

## Locked mesh (recommended primary path: Vertex-AI-first + self-hosted Qwen for diversity)

| Class | Primary | Fallback chain | Why this model |
|---|---|---|---|
| `class:reasoning` / `class:orchestrator` / `class:headline` | **`vertex_ai/claude-opus-4-7`** | sonnet-4-6 → gemini-3.1-pro | Frontier agentic reasoning; already wired; constitutional safety; 1M ctx ext |
| `class:long-context` | **`vertex_ai/gemini-3.1-pro`** *(NEW — enable in autonomous-agent-2026)* | claude-opus-4-7 (1M) → claude-sonnet-4-6 | Best 1M+ context per leaderboards; native multimodal; cost-efficient at frontier |
| `class:coding` (high-stakes) | **`vertex_ai/claude-opus-4-7`** *(see OpenAI option below)* | qwen-coder-next → sonnet-4-6 | Close 2nd on SWE-bench Pro (74); already wired; no new vendor |
| `class:coding` (high-volume / non-headline) | **`vllm/qwen3-coder-next`** *(self-hosted A100 80GB)* | claude-sonnet-4-6 → claude-opus-4-7 | Code-specialist; near-zero marginal cost; quality gap small for routine code |
| `class:chatter` (sub-agent dispatch) | **`vertex_ai/claude-sonnet-4-6`** | gemini-3.1-flash → qwen-coder-next | Fast Anthropic, high quality; Sonnet is not a "compromise" — it's frontier-tier for routine tasks |
| `class:memory.consolidate` / `class:summary` | **`vertex_ai/claude-sonnet-4-6`** | gemini-3.1-flash | "Thinking about thinking" — no compromise; Sonnet quality is sufficient |
| `class:judge.code-correctness` | **`vllm/qwen3-coder-next`** *(family diversity)* | claude-opus-4-7 → gemini-3.1-pro | Different family from other judges → real consensus; code-specialized |
| `class:judge.safety` | **`vertex_ai/claude-opus-4-7`** | claude-mythos-preview *(when GA)* → gemini-3.1-pro | Constitutional AI heritage; designed for safety judgments |
| `class:judge.scope-fit` | **`vertex_ai/gemini-3.1-pro`** | claude-opus-4-7 → qwen-coder-next | Different family from other judges; strong general reasoning |
| `class:judge.completeness` | **`vertex_ai/gemini-3.1-pro`** *(1M ctx)* | claude-opus-4-7 (1M) → claude-sonnet-4-6 | 1M context window = can hold full TaskSpec + full agent trajectory in single call |

**Family diversity in 4-judge consensus**: Anthropic (Opus 4.7) + Google (Gemini 3.1 Pro × 2 different judges) + Self-hosted Alibaba (Qwen3-Coder-Next). 3 distinct model families = real cross-family validation, kills evaluator-collapse risk.

## Optional augmentation: OpenAI GPT-5.3 Codex (if user has API access)

If you have OpenAI API access (or Azure OpenAI Service), we can promote GPT-5.3 Codex into the mesh and get a **4th model family** for ultimate evaluator diversity:

| Class | With GPT-5.3 Codex | Without GPT-5.3 Codex |
|---|---|---|
| `class:coding` (high-stakes) | GPT-5.3 Codex (90 SWE-bench Pro) | Claude Opus 4.7 (74) |
| `class:judge.code-correctness` | GPT-5.3 Codex (different family from Anthropic + Google + Qwen) | Qwen3-Coder-Next (still 3-family diversity) |

Quality delta on code: GPT-5.3 Codex is **+16 points SWE-bench Pro vs Claude Opus 4.7** — meaningful for high-stakes work. But: if you don't already have OpenAI access, the operational cost of a new vendor (separate API key, separate billing, separate rate limits, separate alerts) may outweigh the +16-point benefit on the subset of work that's high-stakes code.

**Decision needed from user**: do you want to add OpenAI API to the mesh (`class:coding` + `class:judge.code-correctness` upgraded), or stay Vertex-AI-only + self-hosted Qwen?

## Self-hosted choice: Qwen3-Coder-Next-FP8 on A100 80GB

- **Model**: `Qwen/Qwen3-Coder-Next-FP8` (released Feb 2026 by Alibaba Qwen team for coding agents specifically)
- **Why this variant**: The 480B-A35B big version requires multi-GPU; the "Next-FP8" variant is the single-GPU-targeted release. FP8 weights fit A100 80GB with room for 32K context.
- **Throughput**: ~120 tokens/sec on A100 (per vLLM benchmarks) — sufficient for our `max_parallel_subagents: 6` cap
- **Serving stack**: vLLM 0.15+ (per Qwen3-Coder-Next official guide)
- **Why not Qwen 2.5-Coder-32B** (the older option I had in pass 2.6): Qwen3-Coder-Next is a 2026-released coding-agent-specific model from the same family. It's strictly better for our use case.

## Cost-aware degradation: DISABLED initially

User intent is "unlimited budget initially, optimize spend down the road." So:

- **Phase A (P3 launch)**: cost-aware degradation **OFF**. All routing follows the mesh as defined; no auto-downgrade based on monthly-budget %.
- **Phase B (after 30d of cost data)**: enable cost-aware degradation with thresholds tuned to actual spend curve. Add this to a follow-up ADR.

## Optional 2nd vLLM (Qwen 7B for cheap-tier): SKIPPED for now

Per user "no quality compromise." The 2nd vLLM was originally proposed for memory-curation / vector-consolidation classes, but Sonnet 4.6 handles those at frontier quality with reasonable cost. Defer the cheap-tier vLLM unless A100 saturates AND post-30d cost data shows a specific class where the 2nd vLLM would deliver meaningful savings.

## Per-class call routing — implementation plan

LiteLLM router config (`deploy/litellm/config.yaml`):

```yaml
model_list:
  - model_name: claude-opus-4-7
    litellm_params:
      model: vertex_ai/claude-opus-4-7
      vertex_project: autonomous-agent-2026
      vertex_location: us-east5
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: vertex_ai/claude-sonnet-4-6
      vertex_project: autonomous-agent-2026
      vertex_location: us-east5
  - model_name: gemini-3.1-pro
    litellm_params:
      model: vertex_ai/gemini-3.1-pro
      vertex_project: autonomous-agent-2026
      vertex_location: us-central1   # check actual Gemini region
  - model_name: gemini-3.1-flash
    litellm_params:
      model: vertex_ai/gemini-3.1-flash
      vertex_project: autonomous-agent-2026
      vertex_location: us-central1
  - model_name: qwen3-coder-next
    litellm_params:
      model: openai/qwen3-coder-next
      api_base: http://qwen-vllm.internal:8000/v1
      api_key: os.environ/QWEN_VLLM_KEY

router_settings:
  routing_strategy: tag-based
  fallbacks:
    - class:reasoning:           [claude-opus-4-7, claude-sonnet-4-6, gemini-3.1-pro]
    - class:orchestrator:        [claude-opus-4-7, claude-sonnet-4-6, gemini-3.1-pro]
    - class:headline:            [claude-opus-4-7, claude-sonnet-4-6, gemini-3.1-pro]
    - class:long-context:        [gemini-3.1-pro, claude-opus-4-7, claude-sonnet-4-6]
    - class:coding:              [claude-opus-4-7, qwen3-coder-next, claude-sonnet-4-6]
    - class:coding-volume:       [qwen3-coder-next, claude-sonnet-4-6, claude-opus-4-7]
    - class:chatter:             [claude-sonnet-4-6, gemini-3.1-flash, qwen3-coder-next]
    - class:memory.consolidate:  [claude-sonnet-4-6, gemini-3.1-flash]
    - class:judge.code-correctness:  [qwen3-coder-next, claude-opus-4-7, gemini-3.1-pro]
    - class:judge.safety:        [claude-opus-4-7, gemini-3.1-pro]
    - class:judge.scope-fit:     [gemini-3.1-pro, claude-opus-4-7, qwen3-coder-next]
    - class:judge.completeness:  [gemini-3.1-pro, claude-opus-4-7, claude-sonnet-4-6]
  num_retries_per_model: 3
  request_timeout: 600
  cooldown_time: 30   # seconds before retrying a 429'd model
  # cost_aware_degradation: DISABLED initially per ADR
```

Hermes plugin (`lib/routing/task_class_tagger.py`) tags every outbound LLM call with appropriate `x-task-class` header based on call site, using Hermes' `pre_llm_call` lifecycle hook.

## Open questions remaining for user

1. **OpenAI augmentation**: yes/no on adding GPT-5.3 Codex to the mesh? If yes, you need OpenAI API key + billing setup before P3-2.
2. **Gemini region**: which Vertex AI region for Gemini? (Affects latency. `us-central1` is typical default.)
3. **Self-host start mode**: on-demand A100 ($2.7K/mo, kill-anytime) for first month while measuring, then commit to 1-year ($1.5K/mo) — confirm?
4. **Telegram bot status (still pending re-share)**: is the gateway path actually working post-`1a284de`? P0-1 acceptance is unknown.
