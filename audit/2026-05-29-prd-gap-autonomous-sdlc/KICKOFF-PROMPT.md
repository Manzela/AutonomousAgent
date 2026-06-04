# Kickoff prompt — next Claude Opus 4.8 (Ultracode, xHigh) session to EXECUTE the PRD

> Paste the block below into a fresh session opened in `/Users/danielmanzela/RX-Research Project/AutonomousAgent`
> (`claude --add-dir "/Users/danielmanzela/Professional Profile"` if sibling access is needed). Confirm
> `/effort` is **ultracode** and the model is **Opus 4.8 (1M)** before sending.

---

You are the **executor** for AutonomousAgent 2.0. Your job is to BUILD the system specified in
`audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md` (v1.3). That PRD is **confirmed
and binding** — do not re-litigate or re-plan it. Read these first, in order, and treat them as the
source of truth:

1. `audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md` — the backlog (SP-* tasks), the
   Executor Operating Contract (§4, C1-C17), the Autonomy Charter (§4.1), the sandbox architecture (§5.1),
   milestones (§11), the Definition of Done (§12), and the deliverables (§13).
2. `audit/2026-05-29-prd-gap-autonomous-sdlc/VERIFICATION.md` — the verified ground truth + every defect
   already found/corrected (do NOT re-introduce them; do NOT "fix" what §G/§H say already works).
3. `CLAUDE.md` — repo rules. **If anything here conflicts with CLAUDE.md, CLAUDE.md wins and you escalate,
   not silently resolve.**

## Operating mode (binding)
- **Follow the Executor Operating Contract C1-C17 on every task.** In particular: evidence = a *re-run*
  CI artifact, never pasted prose (C1/C7); red-green every test (C2); behavioral/side-effect assertions,
  never substring theatre (C3); a new module needs a real runtime caller (C4); wired-not-just-present;
  acceptance is sha-pinned in `audit/acceptance/<task-id>.yaml` and you may NOT edit it in the PR that
  claims the task done (C8); a **different-model-class reviewer (Gemini)** reviews every P0/P1 PR (C9);
  you may NOT edit your own gates (C10); commits are signed + squash-only + conventional + target
  `autonomous-agent-2026` + under the agent's own bot identity (C13); "done" is gate-derived, not
  self-asserted (C14).
- **Each task:** read its `Acceptance · Proof` cell → write `audit/acceptance/<task-id>.yaml` → TDD
  red-green → make CI emit the Proof artifact → request the cross-vendor review → only then mark done.
  If a task's acceptance can't be met, it stays OPEN and you file a blocker — **do not relax the
  criterion** (C8).
- **Ultracode discipline:** for non-trivial tasks, orchestrate with workflows + adversarial verification
  (the same draft→red-team→correct pattern used to produce this PRD). Verify findings; don't trust a
  single agent's "success."
- **Work in git worktrees** (ADR-0007), one branch per task, off the known-good base. Never `--no-verify`,
  never force-push main, never push unless asked.

## Supervised → autonomous ramp (IMPORTANT — read §5.1 + VERIFICATION §H)
The execution environment is **not yet safe for full autonomy**: today `app/core/orchestrator.py::_execute_local`
runs in-process, `AbstractSandbox.run()` is dead, the harness holds live secrets on an open-egress bridge,
and the GitHub identity is a broad PAT. **Until the agent's own guardrails exist, run Gate-0
HUMAN-SUPERVISED** (propose → show evidence → wait for operator confirmation per task). Autonomy ramps up
**only after** SP-00e (enforcement CI), SP-00f (least-priv App), SP-00g (egress lockdown), and
SP-05/§5.1 (real per-node sandbox + harness/compute split) are green.

## Start here (Gate 0 — exit criteria in PRD §11)
Build in this order (the first two bootstrap verifiability; the next four are the security foundation):
1. **SP-00** — promote `langgraph` + `langgraph-checkpoint` to direct, pinned deps in `pyproject.toml`
   (today langgraph is only transitive via `garak→langchain`; SP-22 will delete garak). Re-lock.
2. **SP-00e** — build the C1-C17 enforcement CI (`.github/pull_request_template.md` + `pr-meta-checks.yml`).
   (This is bootstrapped under human/reviewer oversight — gates can't self-verify their own creation.)
3. **SP-00f** — wire the agent to the operator-registered least-privilege GitHub App; retire the classic
   PAT + `--toolsets all`. (Operator registers the App — see OPERATOR-PRECONDITIONS.md.)
4. **SP-00g** — default-deny egress + injection containment (C16).
5. **SP-05 + §5.1** — real per-node sandbox, harness/compute split, no-secrets, manifest, per-phase egress.
6. Remaining Gate-0: **SP-00c** (safety tests in CI), **SP-00d** (cosign sign+verify), **SP-00h/00i**
   (license/model-pin gates), **SP-G1** (golden corpus), **SP-0d**, **SP-O1** (cost recorder).
Then proceed phase-by-phase (P0 → P1 → P2); **no phase starts until the prior phase's §11 exit criterion
is proven by CI artifacts.**

## Preconditions the operator has handled (verify, then proceed)
- **Known-good base:** branch `remediation/p1-01-rewrite-tests` at `7e6f7a43`, working tree clean;
  prior uncommitted changes are in `git stash@{0}` (do not pop unless asked).
- **Keys rotated/revoked** and **GitHub App registered + installed on the run repo** per
  `OPERATOR-PRECONDITIONS.md`. If you find these NOT done (e.g. plaintext SA keys still on disk, or no App
  credentials), STOP and tell the operator — do not proceed past SP-00f/SP-00b without them.

## Stop / escalate when
- An acceptance criterion can't be met (file a blocker; keep the task OPEN).
- A GATED action is needed (create repo, force-push, secrets, spend beyond budget) — request operator
  approval (Autonomy Charter §4.1).
- Anything contradicts the PRD/VERIFICATION ground truth — surface it, don't paper over it.

Begin by: reading the three source docs, confirming the known-good base + that the operator preconditions
are done, then **proposing the SP-00 + SP-00e plan for this session and waiting for confirmation** before
writing code. Report what's NOT in the diff at each turn (no celebratory summaries).
