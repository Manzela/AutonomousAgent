# Persistence Trap Contract — User Approval Record

**Purpose.** This file satisfies pre-requisite #3 of `docs/runbooks/j1-launch-flip.md`. It is the durable, in-repo audit trail proving the operator (the user) consented to the J3 trajectory shipper's sanitize-before-GCS contract as specified by:

- `audit/2026-05-21-persistence-trap-12c/findings.md` — root-cause analysis of the Persistence Trap (PII leak via un-redacted judge events landing in GCS).
- `audit/2026-05-21-persistence-trap-12c/test-contract.md` — the 8-variant test contract the J3 shipper MUST hold against on every PR touching `lib/trajectory/`, `lib/durability/failure_matrix.py`, `lib/evaluators/judge_events.py`, or `terraform/phase-0a-gcp/model-armor/`.
- `audit/2026-05-21-persistence-trap-12c/implementation-checklist.md` — the per-file checklist that connects the contract to concrete code-paths.

The contract — stated for the record so this file is interpretable without chasing links:

1. **Per-record sanitize.** Every judge-event record handed to `TrajectoryShipper.ship_batch` must be routed through the Model Armor `sanitizeUserPrompt` API (template `projects/autonomous-agent-2026/locations/us-central1/templates/j1-trajectory-shipper`, configured for `INSPECT_AND_REDACT` over PII categories: email, SSN, PAN, US phone) BEFORE any byte is written to `gs://autonomous-agent-2026-j3-trajectories`. There is no batch-level shortcut, no "sample only the first N", no client-side regex bypass.
2. **Strict response parsing.** A response missing `sanitizationResult.findings` or whose `sanitizedText` is absent counts as an unavailability event, not as "clean". The shipper must NOT fall back to the raw input.
3. **Halt-LOUD on unavailability.** When sanitize is unavailable for any reason (transport error, missing field, regional template drift, IAM denial), the shipper dispatches a Hermes Failure Matrix `F37` event AND re-raises `ModelArmorSanitizeUnavailable`. The systemd unit's oneshot exit propagates the failure to journald; the timer's next tick re-tries; nothing is uploaded with un-redacted PII.
4. **Canary-token verification.** Before the J1 flip writes secret v2 with `feature_flag_enabled=true`, the operator runs Stage B of the runbook with the four canary tokens (`canary+persistencetrap@example.test`, `999-88-7777`, `4111-1111-1111-1111`, `(555) 010-1234`) and verifies all four are absent in the uploaded GCS object. Any survival = HALT IMMEDIATELY + disable v2 + P0 incident.

This is the contract the user is approving. Approval is scoped to this specification only — any future change to the per-record sanitize requirement, the halt-LOUD posture, or the canary-token list requires a new approval record (new dated subdirectory under `audit/`).

---

## Approval phrase

The user's verbatim approval (delivered in the brainstorming session that produced the spec set above):

> **"Approved approach 3."**

— and the multi-part follow-up:

> **"Regarding your questions: 1. Approved. 2. Correct. 3. Approved."**

Where **Question 3** was the explicit ask: *"Do you approve the Persistence Trap test contract at `audit/2026-05-21-persistence-trap-12c/test-contract.md` (8-variant suite + canary tokens + halt-LOUD on sanitize unavailability) as the J1 launch gate?"* — and the user's "3. Approved." is the consent to gate J1 on this contract.

Both quotes are preserved verbatim. There is no separate "verbatim launch phrase" required by the contract itself — the contract is the spec, the approval is the user's literal "Approved" responses above, and this file is the durable cross-reference.

## Approval metadata

| Field | Value |
|---|---|
| Approver | Daniel Manzela (sole maintainer / operator) |
| Approval medium | Claude Code interactive session, brainstorming → spec → approval flow |
| Approval date | 2026-05-21 |
| Spec commit at approval | `audit/2026-05-21-persistence-trap-12c/{findings,test-contract,implementation-checklist}.md` as of 2026-05-21 15:46Z (initial drop) |
| Cross-link (memory) | `~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/persistence_trap_contract.md` |
| Cross-link (runbook) | `docs/runbooks/j1-launch-flip.md` — pre-requisite #3 (this file's existence) and Stage B (canary-token enforcement) |
| Scope of approval | The 4-clause contract above. Specifically NOT a blanket approval of any future change to redaction behavior, the canary list, the F37 dispatch wiring, or the halt-LOUD posture. |

## Rollback / revocation

Revoking this approval = deleting this file AND disabling the J3 shipper secret v2 in Secret Manager:

```bash
gcloud secrets versions disable 2 --secret=autonomousagent-j3-shipper-config --project=autonomous-agent-2026
```

The systemd timer's next tick will then see `feature_flag_enabled=false` (from v1) and no-op. This file is the spec-side of that rollback; the gcloud command is the runtime side. Both must be inverted to re-enable.

## Why this file exists (and why it is not the contract itself)

The contract lives in `test-contract.md` and is enforced in code at `lib/trajectory/shipper.py` + `tests/integration/test_persistence_trap.py`. This file exists for one purpose: to make the operator's consent to that contract a discoverable, dated, citable artifact in the audit trail, so that any future reviewer (human or agent) can verify in one `cat audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` that the J1 flip was not rubber-stamped.

If you are reading this file and the spec has since changed — re-check whether the change is in-scope of the approval above. If not, surface the gap. Approval is not a license to drift.
