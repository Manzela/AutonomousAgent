# P0-4 closeout — FALSE POSITIVE + the real gap (SP-0d)

**Task:** SP-0d (PRD §6, Gate-0). **Date:** 2026-06-01. **Status:** P0-4 closed as a false
positive; the real underlying gap is recorded below as a tracked issue (ready to file).

## 1. The P0-4 finding (as written in `audit-plan.md` §P0-4)

> **P0-4 — Delete or wire dead observability code.** Files: `lib/observability/ledger.py`,
> `lib/observability/failure_detectors.py`. Problem: zero importers; `ledger.py` has a latent
> `mkdir(parents=True)` at import time → `OSError` on read-only containers;
> `failure_detectors.detect_persistence_trap` has a name collision with the F37 contract. Action:
> delete both, or wire them.

## 2. Determination: **FALSE POSITIVE**

Neither file has ever existed in the repository, on any ref. The finding — including the specific
defects it attributes to those files (the import-time `mkdir` `OSError`, the `detect_persistence_trap`
name collision) — was **fabricated**. It is itself an instance of the hallucinated-artifact theatre
the 2026-05-28 meta-audit set out to catch.

**Evidence (machine-checked, see `audit/acceptance/SP-0d.yaml`):**

```
git log --all --oneline -- lib/observability/ledger.py             # -> (empty: never existed)
git log --all --oneline -- lib/observability/failure_detectors.py  # -> (empty: never existed)
grep -rnE 'TamperEvidentLedger|FailureDetectors' lib/ app/         # -> (no matches)
```

`lib/observability/` exists but contains no `ledger.py` / `failure_detectors.py`. The genuine durability
components — `lib/durability/runtime_detectors.py` and `lib/durability/failure_matrix.py` — do exist and
are distinct from the phantom files P0-4 named.

**Independent corroboration:** PRD v1.4 §6 **SP-22** already states this explicitly —
*"(`lib/observability/{ledger,failure_detectors}.py` never existed — nothing to delete; see SP-0d.)"* —
so there is no "dead code to delete" and no action A/B from the audit-plan applies. P0-4 requires **no
code change**; it is closed here as a documented false positive.

## 3. The real gap (do NOT drop it) — TRACKED ISSUE, ready to file

P0-4 fabricated the *files*, but it gestured at two components the system genuinely lacks. Per the PRD
(SP-0d: *"the real gap (no TamperEvidentLedger/FailureDetectors impl) filed as a tracked issue, not
dropped"*), the real gap is recorded here as a ready-to-file issue.

> **Title:** Implement TamperEvidentLedger + FailureDetectors (the real components P0-4 hallucinated)
>
> **Body:**
> The 2026-05-28 meta-audit's P0-4 referenced `lib/observability/{ledger,failure_detectors}.py` as
> "dead code." Those files never existed (false positive, closed in `audit/2026-05-28-meta-audit/
> P0-4-closeout.md`). However, the underlying capability is a genuine, *un-built* gap:
>
> 1. **TamperEvidentLedger** — a tamper-evident audit ledger for tool-call / state-transition provenance
>    (hash-chained append-only records; verify on read). Relates to the Persistence-Trap contract
>    (J3 shipper canary tokens + halt-LOUD posture) and is distinct from the SP-01/SP-17 idempotency
>    ledgers (which key on `(thread_id,node_id,super_step)` / `(channel,origin_id)` for exactly-once,
>    not tamper-evidence).
> 2. **FailureDetectors** — a detector that sweeps OTel spans / loop traces for failure signatures, in
>    particular the **text-only / sentinel-driven loop** (the anti-drift clause-(e) hazard the new
>    `no-sentinel-termination` gate, SP-00e.6, statically forbids — this would be the *runtime* sibling).
>    The audit-plan's own option-B named `detect_text_only_loop` for exactly this.
>
> **Acceptance (when built):** `grep -rnE "TamperEvidentLedger|FailureDetectors" lib/ app/` shows real,
> callgraph-reachable callers (C4); the ledger has a `verify()` red-green (tamper → fail); the detector
> has a red-green on a planted text-only loop. **Owner:** Observability. **Priority:** P2 (not on the
> Gate-0 critical path; the static `no-sentinel-termination` gate already blocks the dominant hazard at
> CI time).

**Filing status:** `gh issue create` is blocked in this autonomous session by the action classifier
(external write outside the PR/merge scope). The issue text above is committed here so the gap is **not
dropped**; the operator (or a session with issue-create authority) files it verbatim. SP-0d's
git-log-empty proof is satisfied autonomously; the "a GitHub issue exists" criterion is operator-gated.
