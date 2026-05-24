# A2A Spec Version Pin — Day 1 Stamp

**Pin date:** 2026-05-21
**Pinned commit:** `e997516542bd6e3a12ecb6b4939aa0bae3b13a21`
**Repo:** https://github.com/a2aproject/A2A
**Commit URL:** https://github.com/a2aproject/A2A/commit/e997516542bd6e3a12ecb6b4939aa0bae3b13a21
**Spec markdown (read-only):** https://github.com/a2aproject/A2A/blob/e997516542bd6e3a12ecb6b4939aa0bae3b13a21/docs/specification.md
**Normative proto:** https://github.com/a2aproject/A2A/blob/e997516542bd6e3a12ecb6b4939aa0bae3b13a21/spec/a2a.proto

## Pin metadata

| Field | Value |
|---|---|
| Pin owner | A2A spike (Days 1-10) |
| Pin rationale | Per spike-plan.md §Day 1: pin the spec SHA so all 10 days of build-out target a single, frozen surface. Spec drift mid-spike is out of scope per Q-meta hard timebox. |
| Survey artifact | `audit/2026-05-21-a2a-spike-plan/protocol-survey.md` (surveyed v1.0.0 against this branch) |
| Commit subject | `fix: TaskStatus values in the specification (#1801)` |
| Commit author date | 2026-05-19T16:56:43Z |
| Days behind HEAD at pin | 2 |
| Re-pin policy | Only if a §5 kill-criterion blocker traces to a spec ambiguity that HEAD has since clarified. Otherwise, hand-off note records the gap and v2 of the spike re-pins. |

## Why pin (vs. tracking main)

1. **Day-by-day acceptance gates require a stable target.** Day 6 (telemetry) asserts span-attribute schemas defined in §7 — if §7 mutates mid-spike, Day 6's tests are evaluating against a moving spec.
2. **Q-meta locks the 10-day timebox.** Spec-drift triage is not in the budget; halt-on-blocker (Q11) is the right tool, not chase-the-spec.
3. **Q3 default (GCP-only federation)** means we are following the spec at this SHA, not the future direction the WG may pivot to.

## Reference artifacts at this SHA

- `docs/specification.md` (3,610 lines per protocol-survey.md) — the human-readable spec.
- `spec/a2a.proto` (796 lines) — the normative gRPC definitions; JSON-RPC variants derive from this.
- Reference Python SDK (`a2a-python`): not pinned by SHA here (it's a stretch-goal Day-10 peer per Q2). When/if used, pin separately.

## Drift detection

To check whether HEAD has moved since this pin:

```bash
gh api repos/a2aproject/A2A/commits/main --jq '{sha: .sha, date: .commit.committer.date}'
# Compare .sha against e997516542bd6e3a12ecb6b4939aa0bae3b13a21
```

If HEAD has diverged AND a spec-related blocker is observed during Days 2-9, raise per Q11 (kill-criterion) before silently re-pinning.

## Cross-links

- [`spike-plan.md`](./spike-plan.md) — day-by-day plan referencing this SHA's spec sections.
- [`protocol-survey.md`](./protocol-survey.md) — initial spec read at v1.0.0; this SHA is a few commits past v1.0.0 and the survey notes any addenda.
- [`DEFAULTS-ACCEPTED.md`](./DEFAULTS-ACCEPTED.md) — Q1/Q3/Q7 defaults that depend on this spec's stability.
- [`open-questions.md`](./open-questions.md) — Q7 (`_meta.traceparent` SSE convention) is a WG proposal that targets a post-this-SHA spec version.
