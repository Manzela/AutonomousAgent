<!-- Executor Operating Contract (PRD §4). Fill every applicable section.
     "Done" is gate-derived (C14), not asserted here. CI RE-RUNS your Evidence (C1);
     executor-pasted numbers are advisory — the junitxml / CI artifact is source of truth. -->

## Summary
<!-- One paragraph: what changed and why. -->

## Acceptance (C8)
<!-- The sha-pinned audit/acceptance/<task-id>.yaml this PR satisfies.
     You MUST NOT edit that acceptance file in this PR (acceptance-frozen gate). -->
- Task:
- Acceptance file + sha256:

## Evidence (C1)
<!-- Fenced commands whose AUTHORITATIVE output is the CI re-run (bot comment / artifact). -->

```
```

## Red-Green (C2)
<!-- The committed FAIL-without-change then PASS-with-change pair (paths or CI run links). -->

## Test Truth (C7)
<!-- collected / passed / failed / skipped — must byte-match the CI junitxml artifact. -->

## Refutation attempted (C12)
<!-- The deliberately-broken input your acceptance assertion catches (a red run). -->

## Test Changes (C6)
<!-- REQUIRED if this PR deletes a test, relaxes an assertion, or drops coverage below
     baseline. A different-model-class reviewer (C9) must APPROVE this block. Else "None". -->
None

## Reviewer model
<!-- C9 / CLAUDE.md: a DIFFERENT model class reviews every P0/P1 PR. Record it literally below. -->
Reviewer model:

## Security
- [ ] No plaintext secrets (sops-encrypted only); pre-commit clean
- [ ] No new egress endpoints without an allowlist update (C16)
- [ ] Signed, squash-only, conventional title; targets `autonomous-agent-2026` (C13)

## Related
- PRD §6:
- Closes #
