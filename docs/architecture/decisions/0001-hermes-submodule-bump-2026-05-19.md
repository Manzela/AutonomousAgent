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

## Additional Delta Inventory (carry-over for completeness)

This section records three concrete deltas that were summarized but not enumerated in the
initial PR #92 ADR draft. They are factual extracts from `git -C hermes-agent` queries against
the `ddb8d8fa8..5e743559e` range and are preserved here so future bumps can diff against a
complete baseline.

### Removed: rl-extras packages

All five rl-extras packages were removed in a single upstream commit
`5af672c75` (`chore: remove Atropos RL environments and tinker-atropos integration (#26106)`,
Siddharth Balyan, 2026-05-15). The verbatim removed block from `hermes-agent/pyproject.toml`
(`git -C hermes-agent diff ddb8d8fa8 HEAD -- pyproject.toml | grep '^-'`):

```
-rl = [
-  "atroposlib @ git+https://github.com/NousResearch/atropos.git@c20c85256e5a45ad31edf8b7276e9c5ee1995a30",
-  "tinker @ git+https://github.com/thinking-machines-lab/tinker.git@30517b667f18a3dfb7ef33fb56cf686d5820ba2b",
-  "fastapi==0.133.1",
-  "uvicorn[standard]==0.41.0",
-  "wandb==0.25.1",
-]
-yc-bench = ["yc-bench @ git+https://github.com/collinear-ai/yc-bench.git@bfb0c88062450f46341bd9a5298903fc2e952a5c ; python_version >= '3.12'"]
-py-modules = [..., "rl_cli", ...]
-exclude = ["tinker-atropos"]    # (tool.ty.src)
-exclude = ["tinker-atropos"]    # (tool.ruff)
```

| Package | Source | One-line rationale (upstream PR #26106) |
|---|---|---|
| `atroposlib` | NousResearch/atropos git URL | Atropos RL environments removed from Hermes core; downstream consumers move to standalone Atropos repo. |
| `tinker` | thinking-machines-lab/tinker git URL | Atropos-only training backend; orphaned by Atropos removal. |
| `wandb==0.25.1` | PyPI | RL experiment-tracking dep, only used by the deleted `rl_training_tool` + `agent_loop` benches. |
| `yc-bench` | collinear-ai/yc-bench git URL | RL benchmark suite; entire `[yc-bench]` extra dropped alongside `[rl]`. |
| `rl_cli` (py-module) | local `rl_cli.py` | Standalone RL training CLI deleted in commit `5af672c75` first hunk. |

Side effects of the same commit (verified via `git show 5af672c75 --stat`):
- `environments/` (43 files: base env, agent loops, tool-call parsers, benchmarks) deleted.
- `tools/rl_training_tool.py` and all 10 `rl_*` tools deleted.
- `tinker-atropos/` git submodule + `.gitmodules` entry removed.
- 7 RL-specific tests deleted (`test_rl_training_tool`, `test_tool_call_parsers`,
  `test_managed_server_tool_support`, `test_agent_loop`, `test_agent_loop_vllm`,
  `test_agent_loop_tool_calling`, `test_terminalbench2_env_security`).
- Install scripts (`setup-hermes.sh`, `scripts/install.{sh,ps1}`, `nix/hermes-agent.nix`)
  no longer reference `tinker-atropos`.
- `cli-config.yaml.example`, `README`, `CONTRIBUTING`, `AGENTS.md`, and `website/docs/`
  pages scrubbed of `rl` / `atropos` references.

**Impact on this repo:** We never enabled `[rl]` or `[yc-bench]` extras
(`deploy/Dockerfile.hermes` installs `".[all]"`, but the `rl` group was already a
no-op in our deployment because we don't expose `rl_*` tools through
`config/hermes/cli-config.yaml`). No action required downstream beyond noting
the dependency-surface shrink (5 deps + 1 submodule + 43 env files gone).

### Added: environment variables

Five env vars verified added or first introduced upstream in the `ddb8d8fa8..5e743559e`
range (verified via `git log --oneline ddb8d8fa8..HEAD -S '<VAR>'` for each).

| Variable | Default | Controls | Introduced in | Consumed by our `.env.sops` / compose? |
|---|---|---|---|---|
| `HERMES_KANBAN_BOARD` | unset | Pins the active Kanban board for a `hermes kanban` invocation; overrides the `current` symlink. Read at `hermes_cli/kanban.py:836`. | `8a64e1580` (`fix(kanban): ignore stale HERMES_KANBAN_BOARD for removed boards`), `641e40c4b` (`fix(kanban): restore HERMES_KANBAN_BOARD after scoped slash override`). | **No.** Not present in `secrets/*.env.sops` nor `deploy/docker-compose*.yml`. We are not using Kanban tools in the autonomous-agent runtime today. |
| `XAI_BASE_URL` | `https://api.x.ai/v1` (constant `DEFAULT_XAI_BASE_URL` in `tools/tts_tool.py:170` and `tools/x_search_tool.py:40`); `https://api.x.ai` enforced as origin allowlist for OAuth (commit `64a9a199b`) | Override xAI Grok inference base URL — handy for staging/local proxy. Also used by `XAI_STT_BASE_URL` / `HERMES_XAI_BASE_URL` fallbacks (`hermes_cli/auth.py:363`, `:3770`). Flagged in code as a credential-leak vector when paired with OAuth bearer. | `b62c99797` (`feat(xai-oauth): add xAI Grok OAuth (SuperGrok Subscription) provider`), `64a9a199b` (`fix(xai-oauth): pin inference base_url to x.ai origin`). | **No.** Not present in our secrets or compose; we don't ship Grok credentials in this deployment. |
| `DISCORD_HISTORY_BACKFILL` | `"true"` (`gateway/platforms/discord.py:3672`) | When true, prepend recent channel scrollback (since the bot's last response) to the user message on @mention, recovering context lost to `require_mention`. Skipped in DMs and free-response channels. Companion var `DISCORD_HISTORY_BACKFILL_LIMIT` defaults to `50`. | `e84fe483b` (`feat(discord): channel history backfill for multi-user sessions`), `4abfb6bc2` (`feat(discord): default history backfill on, expand to per-user + threads`). | **No.** No Discord platform deployment in this repo. |
| `SIGNAL_REQUIRE_MENTION` | `"false"` (`gateway/platforms/signal.py:201`) | When true, only respond in Signal groups when the bot account is @mentioned. Config-extra `require_mention` overrides the env var. | `7f4076739` (`feat(signal): add require_mention filter for group chats`). | **No.** No Signal platform deployment in this repo. |
| `TELEGRAM_ALLOWED_TOPICS` | unset → empty set (no allowlist) (`gateway/platforms/telegram.py:4232`) | Comma-separated list of allowed forum topic IDs for Telegram supergroups; gates profile bots so they only respond in named topics. | `46ce3453c` (`fix(telegram): gate profile bots by allowed topics`). | **No.** Our `secrets/telegram.env.sops` does not declare `TELEGRAM_ALLOWED_TOPICS` today; if we expose the bot to a forum-mode supergroup we'll need to add it. |

All five are **purely additive** with safe defaults, so no autonomous-agent compose
or env file needs to change to take this bump. They are recorded here so the next
audit pass (or anyone wiring Discord/Signal/Telegram-forum integrations) knows
the surface exists.

### Added: `PluginContext.register_browser_provider`

A new method on `hermes_cli.plugins.PluginContext` added in upstream commit
`c6e6909e5` (`feat(browser): add BrowserProvider ABC mirroring web_search_provider template`,
kshitijk4poor, 2026-05-14, hermes-agent PR #25214). Located at
`hermes-agent/hermes_cli/plugins.py:613`:

```python
def register_browser_provider(self, provider) -> None:
    """Register a cloud browser backend.

    ``provider`` must be an instance of
    :class:`agent.browser_provider.BrowserProvider`. The
    ``provider.name`` attribute is what ``browser.cloud_provider`` in
    ``config.yaml`` matches against when routing cloud-mode
    ``browser_*`` tool calls.

    Mirrors :meth:`register_web_search_provider` exactly — same
    registration shape, same gating, same logging. The browser
    subsystem's dispatcher (:func:`tools.browser_tool._get_cloud_provider`)
    consults the registry built up by these calls.
    """
    from agent.browser_provider import BrowserProvider
    from agent.browser_registry import register_provider as _register_browser_provider

    if not isinstance(provider, BrowserProvider):
        logger.warning(
            "Plugin '%s' tried to register a browser provider that does "
            "not inherit from BrowserProvider. Ignoring.",
            self.manifest.name,
        )
        return
    _register_browser_provider(provider)
    logger.info(
        "Plugin '%s' registered browser provider: %s",
        self.manifest.name, provider.name,
    )
```

**When our plugins would call it:** any plugin that wants to plug a cloud browser
backend (Browserbase, browser-use, Firecrawl, etc.) into the `browser_*` tool
dispatcher would do so in its `on_load(ctx)` hook:

```python
def on_load(ctx):
    ctx.register_browser_provider(MyCloudBrowserProvider())
```

Selection follows the same three-rule resolution as `register_web_search_provider`:
explicit `browser.cloud_provider` in `config.yaml` wins → single-eligible shortcut
(removed in commit `a15cdfb05`; explicit config now always required when ≥1 provider
registered) → fallback. Upstream already ships three sample plugins exercising
this contract: `browserbase` (`b8138ac40`), `browser-use`, and `firecrawl`
(`a15cdfb05`).

**Backward compatibility:** This method is purely additive on `PluginContext`. No
existing plugin contract method changed signature; plugins that never call
`register_browser_provider` are unaffected. Our current plugin set under
`hermes-agent/plugins/` and `lib/*` does **not** register a browser provider — the
local `tools/browser_tool._get_cloud_provider()` dispatcher still falls back to
the legacy `CloudBrowserProvider` resolution when no plugin-supplied provider is
in the registry. The new ABC (`agent.browser_provider.BrowserProvider`) preserves
the legacy `CloudBrowserProvider` lifecycle contract bit-for-bit (`create_session`,
`close_session`, `emergency_cleanup`, session-metadata shape), so legacy callers
keep working unchanged.

**Verification commands** (reproducible from this commit):

```bash
git -C hermes-agent log --all --oneline -S 'register_browser_provider' | head -5
# a15cdfb05 feat(browser): browser-use + firecrawl plugins; drop single-eligible shortcut
# b8138ac40 feat(browser): browserbase plugin (spike — first migration)
# c6e6909e5 feat(browser): add BrowserProvider ABC mirroring web_search_provider template

git -C hermes-agent grep -n 'register_browser_provider' -- 'hermes_cli/'
# hermes_cli/plugins.py:613:    def register_browser_provider(self, provider) -> None:
# hermes_cli/plugins.py:628:        from agent.browser_registry import register_provider as _register_browser_provider
# hermes_cli/plugins.py:637:        _register_browser_provider(provider)
```

## References

- Audit anchor: [`audit/2026-05-19-resume-orchestration/audit-plan.md` §P2-6](../../../audit/2026-05-19-resume-orchestration/audit-plan.md)
- Upstream release notes: `hermes-agent/RELEASE_v0.14.0.md` (tag `v2026.5.16`)
- Backward-compat commit: `hermes-agent` @ `016c772e7` — `feat(plugins): tool override flag for replacing built-in tools (closes #11049) (#26759)`
- Hook contract callsite: `hermes-agent/hermes_cli/plugins.py:1296` (`PluginManager.invoke_hook`) — unchanged signature
- rl-extras removal: `hermes-agent` @ `5af672c75` — `chore: remove Atropos RL environments and tinker-atropos integration (#26106)`
- BrowserProvider ABC: `hermes-agent` @ `c6e6909e5` — `feat(browser): add BrowserProvider ABC mirroring web_search_provider template`
- NIST SSDF PW.4: <https://csrc.nist.gov/Projects/ssdf>
- Related ADR (architecture series, separate sequence): [`docs/decisions/0001-use-hermes-agent-as-base.md`](../../decisions/0001-use-hermes-agent-as-base.md)
