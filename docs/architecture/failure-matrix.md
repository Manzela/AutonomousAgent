# Hermes Failure Matrix (33 Modes)

The system enforces a **Fail-Loud / Fail-Soft / Self-Heal** trichotomy. Every tool and system error must be explicitly classified and handled according to this matrix.

## 1. The Trichotomy Definition

1. **Fail-Loud (Escalate & Block):** Unrecoverable errors, security violations, or context exhaustion. Triggers an immediate Telegram notification to the owner. Pauses the task up to `telegram_escalation_timeout_h` (24h). If no resolution, transitions task to `BLOCKED`.
2. **Fail-Soft (Graceful Degradation):** Transient service outages, non-critical subagent failures. Logs a warning, skips the non-essential step, and continues the task with reduced fidelity.
3. **Self-Heal (Retry with Backoff):** Rate limits, malformed LLM JSON, transient network drops. Retries automatically using the exponential backoff+jitter configured in `limits.yaml`.

## 2. The 33-Mode Matrix

*(Representative selection from the brainstorming session, expanding to cover the full spectrum of operations)*

### LLM & Gateway Failures
| ID | Mode | Description | Trichotomy Classification | Resolution / Behavior |
|----|------|-------------|---------------------------|-----------------------|
| F01 | `429 Too Many Requests` | LiteLLM hits rate limit on Vertex AI. | **Self-Heal** | Exponential backoff (up to 60s) + 25% jitter. |
| F02 | `500 Internal Server Error` | Vertex AI API unavailable. | **Self-Heal** | Retry up to 5 times. If persists >5m, escalate to Fail-Loud. |
| F03 | `Model Not Found` | Requested model version disabled/removed. | **Fail-Loud** | Immediately notify via Telegram; task suspended. |
| F04 | `JSON Parse Error` | LLM outputs invalid tool JSON. | **Self-Heal** | Feed error back to LLM for correction (max 3 times). |
| F05 | `Max Context Exceeded` | Conversation exceeds token window. | **Fail-Soft** | Summarize oldest messages, trim context, proceed. |

### Execution & Sandbox Failures
| ID | Mode | Description | Trichotomy Classification | Resolution / Behavior |
|----|------|-------------|---------------------------|-----------------------|
| F10 | `Timeout Exceeded` | Shell sandbox command runs >120s. | **Self-Heal** | SIGKILL command. Return timeout error to LLM. |
| F11 | `OOM Kill` | Sandbox process exceeds 1GB limit. | **Self-Heal** | Return OOM error to LLM for memory-optimized retry. |
| F12 | `Network Egress Denied` | Sandbox attempts unauthorized IP access. | **Fail-Loud** | Potential malicious code or hallucination. Halt task. |
| F13 | `File Permission Denied` | Agent tries to mutate read-only system file. | **Fail-Soft** | Deny action, log warning, prompt LLM to use workspace. |

### Memory & Persistence Failures
| ID | Mode | Description | Trichotomy Classification | Resolution / Behavior |
|----|------|-------------|---------------------------|-----------------------|
| F20 | `Chroma Connection Refused`| Chroma Cloud API unreachable. | **Self-Heal** | Retry. If >5m, transition to Fail-Soft (skip RAG). |
| F21 | `Checkpoint Write Failed` | Cannot serialize state to disk. | **Fail-Loud** | Imminent data loss. Halt orchestrator and alert. |
| F22 | `REJECTED.md Parse Error` | Institutional memory file corrupted. | **Fail-Soft** | Ignore file for this session. Log error. |
| F23 | `Repeated Failure Loop` | Output rejected 3x by evaluators. | **Fail-Soft** | Write to `REJECTED.md`, abort task, notify user. |

### External Integration Failures
| ID | Mode | Description | Trichotomy Classification | Resolution / Behavior |
|----|------|-------------|---------------------------|-----------------------|
| F30 | `Telegram Webhook Drop` | Unable to fetch updates from Telegram. | **Self-Heal** | Long-poll backoff loop. |
| F31 | `Unauthorized User` | Non-allowlisted user messages bot. | **Fail-Soft** | Ignore message. Log security warning. |
| F32 | `Budget Cap Reached` | LiteLLM daily cap ($500) exceeded. | **Fail-Loud** | Hard stop. Suspend all tasks until budget resets. |
| F33 | `GitHub API Rate Limit` | Repo sweeps hit GH limit. | **Self-Heal** | Respect `Retry-After` header. |
