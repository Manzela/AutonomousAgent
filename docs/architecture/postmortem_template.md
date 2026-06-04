# Agent Incident Post-Mortem Template

## 1. Trace Collection
- **Incident ID:**
- **Date/Time:**
- **Affected Subsystem:** (e.g., LiteLLM Proxy, Evaluator Judge, MALT Replay)
- **Trace IDs:** (List Langfuse/OTel span IDs)

## 2. Failure Clustering
Categorize the failure mode into one of the known 6 agent-specific failures:
- [ ] Persistence Trap (Agent hangs indefinitely without calling tools)
- [ ] Sybil Cascade (Unbounded sub-agent cloning)
- [ ] Deceptive Alignment (Sandbox-vs-Prod policy switching)
- [ ] Tool Hallucination (Calling `rm` when it does not exist)
- [ ] Context Window Exhaustion (Losing initial constraints in a 50+ turn loop)
- [ ] Malformed Output (JSON parsing failure)

## 3. Root Cause Analysis
- **What happened?**
- **Why did the guardrails (promptfoo, inspect, deepeval) not catch this?**
- **Was this a loud failure (crashed loudly) or silent failure (succeeded but did wrong thing)?**

## 4. Eval Generation (Action Items)
- **New Regression Test:** (Link to new `evals/auto_regression.yaml` or pytest test suite)
- **Prompt Adjustments:** (Did we need to adjust the system prompt?)
- **Code Fixes:**
