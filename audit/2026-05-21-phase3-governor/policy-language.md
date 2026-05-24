# Phase 3 Metacog Governor — Policy Language

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Overview

Policies are declarative rules that define:
1. **When** to detect an anomaly (trigger conditions).
2. **What** to do about it (actions: warn / throttle / kill / escalate / custom webhook).
3. **Who** approves it (auto-execute vs human-in-the-loop).

Policies are written in **YAML** (v1 recommendation; revisit Rego/CEL if expressive needs grow). YAML is:
- **Human-readable** — operators can review policies without code knowledge.
- **Audit-friendly** — version-controlled in Git; changes tracked via diffs.
- **Composable** — policies can reference other policies (inheritance, mixins).

**Alternative considered (deferred to v2):**
- **OPA Rego** — more expressive (first-class logic programming), but steeper learning curve and harder to audit for non-programmers.
- **Google CEL (Common Expression Language)** — simpler than Rego, but still requires expression syntax; YAML is more accessible for v1.

---

## 2. Policy Schema (YAML)

### 2.1 Top-Level Structure

```yaml
policies:
  - name: <policy-name>
    version: <int>                # auto-incremented by Governor on update
    trigger:
      <trigger-spec>
    actions:
      - <action-spec>
      - <action-spec>
    severity: <high|medium|low>
    enabled: <bool>               # default: true
    metadata:
      description: <string>
      owner: <string>             # email or team name
      created_at: <timestamp>
      updated_at: <timestamp>
```

### 2.2 Trigger Specification

Triggers define **when** an anomaly is detected. Multiple trigger types are supported; compose with `and` / `or` / `not`.

#### Single Trigger (F-code)

```yaml
trigger:
  f_code: F34
  occurrence: 3                   # fire after 3 occurrences
  window_minutes: 10              # within 10-minute window
```

**Semantics:** If agent emits F34 three times within any 10-minute sliding window, this trigger fires.

#### Single Trigger (Metric Threshold)

```yaml
trigger:
  metric: cost_per_agent_per_day
  condition: "> 50"               # string expression (parsed server-side)
```

**Supported operators:** `>`, `>=`, `<`, `<=`, `==`, `!=`.

**Metric names (predefined):**
- `cost_per_agent_per_day` — daily cumulative cost in USD for a single agent.
- `session_duration_minutes` — duration of the current session.
- `tool_call_rate_per_minute` — tool calls per minute (averaged over 5 minutes).
- `context_usage_ratio` — prompt tokens / context window size.

**Custom metrics:** Future extension allows referencing custom Prometheus metrics (e.g., `agent_request_queue_depth{agent_id="hermes-1"} > 100`).

#### Composite Trigger (AND)

```yaml
trigger:
  and:
    - f_code: F34
      occurrence: 2
      window_minutes: 5
    - metric: session_duration_minutes
      condition: "> 30"
```

**Semantics:** Both conditions must be true simultaneously. (F34 occurred 2+ times in 5 minutes AND session duration >30 minutes.)

#### Composite Trigger (OR)

```yaml
trigger:
  or:
    - f_code: F34
      occurrence: 3
      window_minutes: 10
    - f_code: F35
      occurrence: 1
```

**Semantics:** Either condition triggers the policy. (F34 3+ times in 10 minutes OR F35 once.)

#### Composite Trigger (NOT)

```yaml
trigger:
  not:
    metric: agent_health_status
    condition: "== healthy"
```

**Semantics:** Trigger fires if condition is false. (Agent health status is NOT healthy.)

#### A2A Pattern Trigger (Cross-Agent)

```yaml
trigger:
  a2a_pattern: delegation_loop
  window_minutes: 5
```

**Predefined A2A patterns:**
- `delegation_loop` — agent X delegates to Y, Y delegates back to X (cycle detected).
- `fan_out_explosion` — agent sends >10 delegation messages in <60s.
- `no_response_stall` — agent sends `DELEGATE_TASK` but receiver never responds within 10 minutes.

**Future extension:** Allow custom A2A pattern matching (e.g., SQL-like query over `a2a_message_log` table).

---

### 2.3 Action Specification

Actions define **what** to do when a trigger fires. Actions are executed in order.

#### Action: Warn

```yaml
actions:
  - warn:
      message: "F-LOOP detected — consider intervention"
```

**Effect:** Governor sends `GOVERNOR_WARNING` A2A message to target agent. Agent logs the warning and continues.

**Requires approval:** No (always auto-execute).

---

#### Action: Throttle

```yaml
actions:
  - throttle:
      rate: 0.5                   # reduce tool-call rate to 50% of baseline
      duration_minutes: 10        # throttle for 10 minutes
```

**Effect:** Governor sends `GOVERNOR_THROTTLE` A2A message to target agent. Agent applies rate limit (e.g., max 1 tool-call per 5s instead of 1 per 2s).

**Requires approval:** Configurable (default: no).

```yaml
actions:
  - throttle:
      rate: 0.5
      duration_minutes: 10
      requires_human_approval: true
```

---

#### Action: Kill

```yaml
actions:
  - kill:
      requires_human_approval: true
```

**Effect:** Governor sends `GOVERNOR_KILL_SESSION` A2A message to target agent. Agent terminates the session (releases resources, writes checkpoint).

**Requires approval:** **Always** (default: `requires_human_approval: true`). Killing a session is destructive; human must confirm.

**Approval flow:**
1. Governor emits `pending_intervention` event.
2. Telegram bot sends inline keyboard to operator: "Approve kill for hermes-1 session abc-123? [Yes] [No]".
3. Operator clicks [Yes] → Governor executes kill.
4. Operator clicks [No] → Governor logs rejection and skips kill.

---

#### Action: Escalate

```yaml
actions:
  - escalate:
      channel: telegram          # or pagerduty
      severity: high
```

**Effect:** Governor sends alert to human operator via specified channel.

**Channels:**
- `telegram` — existing Telegram bot (reused from F21/F32 handlers).
- `pagerduty` — creates PagerDuty incident.
- `webhook` — POST to custom URL (see Custom Webhook below).

**Requires approval:** No (escalation is informational, not destructive).

---

#### Action: Custom Webhook

```yaml
actions:
  - custom_webhook:
      url: "https://example.com/governor/webhook"
      method: POST
      headers:
        Authorization: "Bearer <secret>"
      body:
        agent_id: "{{ agent_id }}"
        session_id: "{{ session_id }}"
        f_code: "{{ f_code }}"
```

**Effect:** Governor sends HTTP request to specified URL. Response is logged but not acted upon (fire-and-forget).

**Template variables:** Use `{{ variable }}` syntax to inject runtime values:
- `{{ agent_id }}` — target agent ID
- `{{ session_id }}` — target session ID
- `{{ f_code }}` — F-code that triggered this policy (if applicable)
- `{{ anomaly_id }}` — UUID of the anomaly
- `{{ timestamp }}` — ISO 8601 timestamp

**Requires approval:** Configurable (default: no).

---

### 2.4 Severity Tiers

| Severity | SLA for human review | Auto-escalate if no action in | Example use case |
|----------|----------------------|-------------------------------|------------------|
| `high` | 15 minutes | 30 minutes (escalate to PagerDuty) | F34 loop, budget exceeded, security violation |
| `medium` | 1 hour | 2 hours (Telegram reminder) | Agent degraded, high context usage |
| `low` | 24 hours | None (log only) | Minor transient failures, informational |

**Auto-escalation:** If a `high`-severity incident has no human acknowledgment within 30 minutes, Governor auto-escalates to PagerDuty (even if policy doesn't explicitly include `escalate` action).

---

### 2.5 Version Field

Each policy has a `version` field (integer, starts at 1). When a policy is updated via `PolicyService.Update` gRPC call:
1. Governor inserts a new row in `policy_registry` table with `version = old_version + 1`.
2. Old version remains in DB (immutable for audit).
3. Only the latest version is active (triggers anomalies).

**Pinning to a specific version (future extension):**

```yaml
policies:
  - name: loop-detection-aggressive
    version: 3
    pinned: true                  # this version remains active even if newer versions exist
```

**Use case:** Rollback to a known-good policy after a bad update.

---

## 3. Complete Policy Examples

### Example 1: Loop Detection (Aggressive)

```yaml
policies:
  - name: loop-detection-aggressive
    trigger:
      f_code: F34
      occurrence: 3
      window_minutes: 10
    actions:
      - warn:
          message: "F-LOOP detected 3+ times in 10 minutes"
      - escalate:
          channel: telegram
          severity: high
      - kill:
          requires_human_approval: true
    severity: high
    metadata:
      description: "Kill session if F34 (F-LOOP) fires 3+ times in 10 minutes"
      owner: "daniel@example.com"
```

**Behavior:**
1. If agent emits F34 three times in 10 minutes → warn agent.
2. Escalate to Telegram (human gets alert).
3. Governor waits for human approval to kill session.

---

### Example 2: Cost Ceiling (Daily)

```yaml
policies:
  - name: cost-ceiling-daily
    trigger:
      metric: cost_per_agent_per_day
      condition: "> 50"
    actions:
      - throttle:
          rate: 0.5
          duration_minutes: 60
      - escalate:
          channel: telegram
          severity: medium
    severity: medium
    metadata:
      description: "Throttle agent if daily cost exceeds $50"
      owner: "ops-team@example.com"
```

**Behavior:**
1. If agent's daily cost exceeds $50 → throttle tool-call rate to 50% for 60 minutes.
2. Escalate to Telegram (informational alert).
3. No kill (cost overage is not critical; throttle is sufficient).

---

### Example 3: Stall Detection (Fleet-Level)

```yaml
policies:
  - name: stall-detection-fleet
    trigger:
      and:
        - f_code: F35
          occurrence: 1
        - metric: session_duration_minutes
          condition: "> 30"
    actions:
      - escalate:
          channel: pagerduty
          severity: high
      - kill:
          requires_human_approval: true
    severity: high
    metadata:
      description: "Kill session if F35 (F-STALL) fires and session >30 minutes"
      owner: "daniel@example.com"
```

**Behavior:**
1. If agent emits F35 (no activity for `idle_timeout_s`) AND session has been running for >30 minutes → escalate to PagerDuty.
2. Governor waits for human approval to kill session.

---

### Example 4: A2A Delegation Loop

```yaml
policies:
  - name: a2a-delegation-loop
    trigger:
      a2a_pattern: delegation_loop
      window_minutes: 5
    actions:
      - warn:
          message: "Delegation loop detected between agents"
      - escalate:
          channel: telegram
          severity: high
      - custom_webhook:
          url: "https://example.com/governor/delegation-loop"
          method: POST
          body:
            sender_agent_id: "{{ sender_agent_id }}"
            receiver_agent_id: "{{ receiver_agent_id }}"
            timestamp: "{{ timestamp }}"
    severity: high
    metadata:
      description: "Detect and alert on A2A delegation loops"
      owner: "a2a-team@example.com"
```

**Behavior:**
1. If agents form a delegation loop (A → B → A) within 5 minutes → warn both agents.
2. Escalate to Telegram.
3. Send webhook to external service (custom handler for delegation loops).

---

### Example 5: Context Pressure (Compaction Ineffective)

```yaml
policies:
  - name: context-pressure-warn
    trigger:
      f_code: F36
      occurrence: 1
    actions:
      - warn:
          message: "Context usage >90% — compaction may be ineffective"
      - escalate:
          channel: telegram
          severity: medium
    severity: medium
    metadata:
      description: "Alert when F36 (F-CONTEXT) fires (context usage >90%)"
      owner: "daniel@example.com"
```

**Behavior:**
1. If agent emits F36 (context usage >90% warn threshold) → warn agent.
2. Escalate to Telegram (informational — operator may need to investigate why compaction isn't working).
3. No kill (high context usage is not immediately critical).

---

## 4. Policy Composition (Inheritance & Mixins)

**Future extension (v2):** Allow policies to reference other policies for DRY (Don't Repeat Yourself).

### Example: Mixin for Standard Escalation

```yaml
mixins:
  - name: standard-escalation
    actions:
      - escalate:
          channel: telegram
          severity: high

policies:
  - name: loop-detection-aggressive
    trigger:
      f_code: F34
      occurrence: 3
      window_minutes: 10
    actions:
      - warn:
          message: "F-LOOP detected"
      - include: standard-escalation   # inject mixin actions here
      - kill:
          requires_human_approval: true
    severity: high
```

**Benefit:** Common action sequences (e.g., "escalate to Telegram + PagerDuty") are defined once and reused across policies.

---

## 5. Policy Validation

Before a policy is persisted to `policy_registry`, Governor validates:

1. **Syntax** — YAML parses correctly.
2. **Semantics** — all referenced fields exist (e.g., `f_code: F999` → error if F999 doesn't exist in failure matrix).
3. **Trigger condition parsability** — metric conditions (e.g., `"> 50"`) parse to a valid expression.
4. **Action verb support** — if `throttle` is specified, Governor checks that the target agent supports throttling (via `capability_cache` table). If not, policy is rejected or auto-downgraded to `warn`.

**Validation errors returned via gRPC `INVALID_ARGUMENT` error code.**

---

## 6. Policy Lifecycle

### 6.1 Creation

1. Operator writes YAML policy definition.
2. Calls `PolicyService.Create` gRPC endpoint.
3. Governor validates policy → inserts into `policy_registry` table with `version=1`, `enabled=true`.
4. Policy is immediately active (triggers anomalies on the next Analyze cycle).

### 6.2 Update

1. Operator modifies YAML.
2. Calls `PolicyService.Update` gRPC endpoint.
3. Governor validates policy → inserts new row with `version=old_version+1`.
4. Old version remains in DB (immutable for audit) but is no longer active.
5. New version is immediately active.

### 6.3 Deletion (Soft Delete)

1. Operator calls `PolicyService.Delete` gRPC endpoint.
2. Governor sets `deleted=true` on the policy row (does NOT remove from DB).
3. Policy no longer triggers, but remains in DB for audit.

### 6.4 Rollback

If a policy update causes unintended behavior (e.g., false positives):

1. Operator calls `PolicyService.Update` with the old version's YAML (copied from audit log).
2. Governor inserts a new version (version number still increments, but content is the old version).

**Alternative (future extension):** `PolicyService.Rollback(policy_name, target_version)` API that directly re-activates an old version.

---

## 7. Why YAML (vs Rego / CEL)?

| Criterion | YAML | OPA Rego | Google CEL |
|-----------|------|----------|------------|
| **Readability** | ✅ High (operators without code knowledge can review) | ❌ Low (requires logic programming knowledge) | ⚠️ Medium (expression syntax, but simpler than Rego) |
| **Audit-friendly** | ✅ Diffs in Git are human-readable | ⚠️ Diffs are readable but require Rego knowledge | ⚠️ Diffs are readable but require CEL syntax knowledge |
| **Expressiveness** | ⚠️ Limited (no custom functions, no complex logic) | ✅ Very expressive (first-class logic, custom functions) | ⚠️ Moderate (no custom functions in v1) |
| **Tooling** | ✅ Standard YAML linters/validators | ⚠️ Requires OPA CLI for validation | ⚠️ Requires CEL library for validation |
| **Learning curve** | ✅ Low (YAML is universal) | ❌ High (Rego-specific) | ⚠️ Medium (CEL is simpler than Rego but still new) |

**Recommendation for v1:** YAML for simplicity and audit-friendliness.

**Revisit for v2 (if needed):**
- If policies grow to require custom logic (e.g., "if agent X has emitted F34 >5 times AND agent Y has emitted F35, then..."), migrate to Rego.
- If only expression evaluation is needed (e.g., "if (cost_per_agent_per_day > 50 AND session_duration_minutes < 10)"), migrate to CEL.

---

**Next:** See `failure-modes.md` for Governor failure scenarios and mitigations.
