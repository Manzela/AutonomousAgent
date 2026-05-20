# 0001. Bump `hermes-agent` submodule from `ddb8d8fa8` to `5e743559e`

**Status:** Accepted
**Date:** 2026-05-19
**Decision-makers:** Daniel Manzela (+ Claude Opus 4.7)
**Audit anchor:** [`audit/2026-05-19-resume-orchestration/audit-plan.md` §P2-6](../../../audit/2026-05-19-resume-orchestration/audit-plan.md)
**Standard:** NIST SSDF PW.4 ("Keep only needed software — current")
**Companion ADR series:** This is the inaugural entry under `docs/architecture/decisions/`, used for
infrastructure-tracking decisions (submodule pins, vendor SHAs, supply-chain
baselines). The existing `docs/decisions/` series tracks architecture-shaping
choices (e.g. ADR 0001 "Use hermes-agent as base"); these two series run in
parallel.

## Context

`hermes-agent/` was pinned at `ddb8d8fa842283ef651a6e4514f8f561f736c72e`
(committed 2026-05-14). The 2026-05-19 audit ([`audit-plan.md` §P2-6](../../../audit/2026-05-19-resume-orchestration/audit-plan.md))
flagged the pin as **790 commits / 5 days behind** upstream `main`, with one
release tag (`v2026.5.16` at `a91a57fa5`) and one backward-compatible feature
landing in the delta (`016c772e7` — `feat(plugins): tool override flag for
replacing built-in tools (closes #11049)`).

Audit Pass-2 reviewed the hook-contract surface (`hermes_cli/plugins.py`) and
declared the delta **STABLE** — no breaking signature changes. Two
hook-contract-touching commits in the delta:

1. **`c6e6909e5`** — `feat(browser): add BrowserProvider ABC mirroring web_search_provider template` (new optional registry, no callsite changes).
2. **`016c772e7`** — `feat(plugins): tool override flag for replacing built-in tools` (additive optional kwarg: `override: bool = False`).

The audit set a 30-day soft cap: if the pin stays untouched past that
window, P2-6 escalates to P1 (per audit-plan.md §P2-6 "Why: routine
maintenance; not urgent. But will be P1 if it stays untouched for 30+ days").

## Decision

**We will** bump the `hermes-agent` submodule from `ddb8d8fa842283ef651a6e4514f8f561f736c72e`
to `5e743559e0157df42e0f640cd06d736e898370d0` (upstream `main` HEAD as of
2026-05-20 01:46 -0500), capturing the bump under this ADR and a `chore(deps)`
commit on branch `chore/hermes-submodule-bump`.

The bumped HEAD `5e743559e0157df42e0f640cd06d736e898370d0` is the commit
`fix(lint): skip per-file shell linter when LSP will handle the file (#29054)`.

## Consequences

### Positive
- **Audit closure:** Resolves P2-6, removes the 30-day-to-P1 escalation timer.
- **Supply-chain currency:** Pulls in the `v2026.5.16` release tag and 790
  upstream fixes / refactors / dependency updates.
- **New capabilities available** (not yet wired):
  - `PluginContext.register_tool(..., override=True)` — lets future plugins
    replace built-in tools by name (e.g. swap `web_search` for a custom
    provider). Audit-logged at INFO.
  - `BrowserProvider` ABC mirroring `web_search_provider` — for future
    plugin-supplied browser backends.
  - Auto-launch Chromium-family browser for CDP (`697d38a3f`) + Brave CDP
    binary detection coverage (`6a6766fb8`).

### Negative
- **Larger build surface:** 790 commits include some new dependencies
  (browser-related). The Docker image build (`deploy/Dockerfile.hermes`)
  re-runs `uv pip install -e ".[all]"`, so any new transitive deps are
  pulled in automatically — Trivy + SBOM CI will surface any new CVEs.
- **Line-drift in tests:** Three of our local tests pin `hermes_cli/plugins.py`
  to specific line numbers in docstrings (e.g. `hermes_cli/plugins.py:1253`).
  These are documentation-only references (asserting on hook-name strings,
  not on line offsets), so they continue to pass — but the line numbers are
  now stale by ~50 lines and should be refreshed opportunistically.

### Neutral
- **Hook contract unchanged at runtime:** `invoke_hook(hook_name: str,
  **kwargs: Any) -> List[Any]` — identical signature. `register_tool` gained
  an optional `override=False` kwarg, fully backward-compatible.
- **No config changes required:** `config/hermes/cli-config.yaml` plugin
  allowlist + `lib/*` mounts are unchanged.

## Regression evidence

Local pytest run on the bumped submodule (2026-05-20, against
`5e743559e0157df42e0f640cd06d736e898370d0`):

### Unit tests (`tests/unit`)

```
$ .venv/bin/python -m pytest tests/unit -q --tb=short
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
.....s........ssssssss.................................................. [ 85%]
................................................                         [100%]
327 passed, 9 skipped in 0.54s
```

### Integration tests (`tests/integration`, excluding env-dependent `test_sandbox_isolation`)

```
$ .venv/bin/python -m pytest tests/integration --deselect tests/integration/test_sandbox_isolation.py -q --tb=short
ssss......ss.............                                                [100%]
19 passed, 7 skipped, 2 deselected, 2 warnings in 3.54s
```

`test_sandbox_isolation.py` was deselected because it requires a live
`docker compose` stack with decrypted `secrets/*.env` files (sops-encrypted
in worktrees). The same test failure occurs identically on the pre-bump
baseline (`ddb8d8fa8`), confirming it is **environmental, not caused by the
bump**:

```
$ git submodule update --init hermes-agent    # checks out ddb8d8fa8
$ .venv/bin/python -m pytest tests/integration/test_sandbox_isolation.py -q
FAILED tests/integration/test_sandbox_isolation.py::test_shell_sandbox_no_root_fs_write
1 failed, 1 passed in 0.22s
```

### Hook contract smoke test

```
$ PYTHONPATH=hermes-agent .venv/bin/python -c "from hermes_cli.plugins import ..."
register_tool signature: (self, name: 'str', toolset: 'str', schema: 'dict', handler: 'Callable',
  check_fn: 'Callable | None' = None, requires_env: 'list | None' = None, is_async: 'bool' = False,
  description: 'str' = '', emoji: 'str' = '', override: 'bool' = False) -> 'None'
Has override param: True
invoke_hook signature: (hook_name: 'str', **kwargs: 'Any') -> 'List[Any]'
OK — bumped hermes_cli.plugins imports & contract intact
```

### CI coverage

The autonomous-agent CI deliberately skips the submodule for unit tests
(`.github/workflows/ci.yml:186` — `submodules: false`) because our test
suite uses no upstream code paths beyond the hook contract. The submodule
*is* pulled for:

- **Trivy** (`.github/workflows/trivy.yml:38`) — scans the built hermes
  image for CVEs after rebuilding from the bumped source.
- **SBOM + cosign** (`.github/workflows/sbom-cosign.yml:41`) — regenerates
  the SBOM with the bumped commit SHA pinned in.

A clean run of both is the merge gate. The Docker image rebuild itself
exercises `uv pip install -e ".[all]"` against the bumped pyproject, which
is the second integration check.

## Rollback procedure

If a downstream regression surfaces post-merge (Trivy CVE, runtime hook
breakage in production, etc.):

```bash
# In a fresh checkout / worktree of main:
git submodule update --init hermes-agent

# Pin the submodule pointer back to the old SHA:
cd hermes-agent
git checkout ddb8d8fa842283ef651a6e4514f8f561f736c72e
cd ..

# Stage the revert and commit:
git add hermes-agent
git commit -m "revert(deps): roll back hermes-agent to ddb8d8fa8 (rollback of #NN)"
```

Then open a follow-up issue documenting the regression and adding it to
the audit plan so the next bump pass can patch the upstream issue before
re-applying.

The bumped SHA `5e743559e0157df42e0f640cd06d736e898370d0` is preserved in
this ADR for future bisection.

## Alternatives considered

### Option A: Pin to release tag `v2026.5.16` instead of `main` HEAD
- Pros: Slightly safer (released, version-stamped); audit-plan calls out
  the tag explicitly.
- Cons: Loses 590 commits of post-release fixes (`v2026.5.16-590-g5e743559e`).
  Several of those fixes are CVE-class (lint hardening, OSV scanner
  updates). Tag is only 4 days old; tag-vs-HEAD risk delta is small for the
  benefit we gain.
- Why rejected: Wave-3 cadence assumes monthly bump checks; pinning to a
  release tag would force a second bump cycle within days to get the
  same lint/security fixes.

### Option B: Defer the bump 30 more days
- Pros: Zero work this wave.
- Cons: Per audit-plan, deferring past 2026-06-18 escalates to P1 with the
  delta only growing. Audit Pass-2 already verified the contract is stable
  — the work cannot get cheaper than "now".
- Why rejected: Audit recommendation is explicit; the 30-day clock is a
  feature, not a deadline to game.

## References

- Audit anchor: [`audit/2026-05-19-resume-orchestration/audit-plan.md` §P2-6](../../../audit/2026-05-19-resume-orchestration/audit-plan.md)
- Upstream release notes: `hermes-agent/RELEASE_v0.14.0.md` (tag `v2026.5.16`)
- Backward-compat commit: `hermes-agent` @ `016c772e7` — `feat(plugins): tool override flag for replacing built-in tools (closes #11049) (#26759)`
- Hook contract callsite: `hermes-agent/hermes_cli/plugins.py:1296` (`PluginManager.invoke_hook`) — unchanged signature
- NIST SSDF PW.4: <https://csrc.nist.gov/Projects/ssdf>
- Related ADR (architecture series, separate sequence): [`docs/decisions/0001-use-hermes-agent-as-base.md`](../../decisions/0001-use-hermes-agent-as-base.md)
