# MCP server inventory

Source of record for every Model Context Protocol (MCP) server wired into
Hermes. The audit item this closes — "MCP servers wired (if applicable) —
`hermes mcp list` — declare external tool dependencies explicitly, not
ad-hoc" — demands a single document where any reviewer can answer:

* Which MCP servers does Hermes actually call?
* Who owns each one?
* What does it cost to keep alive, and what would trigger its removal?

The runtime list is whatever `mcp_servers:` carries in
`config/hermes/cli-config.yaml`. This document is the human-readable
mirror. **Keep both in lockstep** — every PR that adds or removes an
`mcp_servers:` entry must update the table below in the same diff. The
companion check is in [`docs/runbooks/allowed-actions-restriction.md`](runbooks/allowed-actions-restriction.md)
for workflow actions; the equivalent pattern applies here.

## Standards alignment

* ISO/IEC/IEEE 12207 §6.4.5 — Configuration management item identification
  for external software dependencies.
* IEEE 829 — A regression suite (the [nightly eval workflow](../.github/workflows/nightly-eval.yml))
  exercises every wired MCP path indirectly via the evaluators plugin.
* NIST SSDF PW.7 — Automated testing of every component before release.

## Inventory (as of 2026-05-20)

Snapshot from `config/hermes/cli-config.yaml` `mcp_servers:` block plus
the corresponding `deploy/docker-compose.yml` sidecar definitions.

| Name | Purpose | Transport | URL / command | Auth method | Owner |
| --- | --- | --- | --- | --- | --- |
| `github` | GitHub repo/PR/issue/Actions access for autonomous PR creation, code search, dependency review. Backed by `ghcr.io/github/github-mcp-server` running `--toolsets all` (repos, pull_requests, issues, actions, code_security, dependabot, secret_protection, …). | HTTP sidecar | `http://github-mcp:8003` | Bearer PAT via `GITHUB_PERSONAL_ACCESS_TOKEN_FILE=/run/secrets/github_pat` (sops-encrypted, see `secrets/github-pat.sops`) | `@Manzela` |
| `context7` | Library / framework documentation lookup (React, Vite, Vertex AI, etc.). Used by skills that need post-training-cutoff API references. | HTTP (SSE) | `https://mcp.context7.com/sse` | None (public hosted endpoint) | `@Manzela` |
| `fetch` | Fetch a URL and convert HTML→markdown for the model. Respects `robots.txt` by default. Identifies as `AutonomousAgent-Hermes/0.1` user-agent for upstream rate-limiting / abuse-report correlation. PyPI: [`mcp-server-fetch`](https://pypi.org/project/mcp-server-fetch/). | stdio subprocess (uvx) | `uvx mcp-server-fetch --user-agent=…` (see `config/hermes/cli-config.yaml`) | None (public endpoints only; egress filtered by Hermes container's network policy) | `@Manzela` |
| `time` | Timezone-aware datetime utilities. Hermes container clock is UTC; server default tz=UTC for a reliable "now" reference. Model may request any IANA tz per call. PyPI: [`mcp-server-time`](https://pypi.org/project/mcp-server-time/). | stdio subprocess (uvx) | `uvx mcp-server-time --local-timezone=UTC` | None (no external network) | `@Manzela` |

### stdio vs HTTP MCPs

Two transport patterns coexist in the inventory; the choice is per-MCP based on
what upstream supplies, not a global rule:

- **HTTP sidecar pattern** (`github`, `context7`): MCP runs as a separate container
  in `deploy/docker-compose.yml`, on its own network. Hermes connects via
  `type: http` + `url:`. Required when the upstream MCP exposes only an HTTP
  endpoint (Context7 is a hosted service) or when isolating a sidecar's blast
  radius is a goal (GitHub MCP holds a high-scope PAT).
- **stdio subprocess pattern** (`fetch`, `time`): MCP runs as a subprocess of
  Hermes, launched via `command:` + `args:`. Required when the upstream MCP
  ships as a stdio-only entrypoint (the official Anthropic Python MCPs do).
  No docker-compose entry; Hermes' base image must include the launcher
  (`uvx` for Python MCPs — see `deploy/Dockerfile.hermes:23-24`; `npx` for
  npm MCPs — not currently installed, see "Deferred" below).

A stdio MCP inherits the Hermes container's network policy, filesystem view,
and capability set. There is no separate sandbox tier for stdio MCPs — they
run in `external_https` posture (the same tier Hermes runs in), with whatever
network egress and tmpfs/volume mounts Hermes has. If a stdio MCP needs
tighter isolation than that, promote it to an HTTP sidecar.

### Deferred / commented-out entries

| Name | Status | Reason |
| --- | --- | --- |
| `playwright` | Deferred from Phase 1 | `mcr.microsoft.com/playwright/mcp:latest` exits cleanly when no client connects under its default entrypoint. We have not yet researched the correct flag-set to keep it running long-lived under compose. Re-enable in a follow-up once the right invocation is confirmed. The compose-level note lives next to the `# ---- Playwright MCP (deferred from Phase 1) ----` block in `deploy/docker-compose.yml`. |
| `filesystem` | Deferred from J5 (2026-05-20) | The official Anthropic `@modelcontextprotocol/server-filesystem` is **npm-only** (no Python port). The Hermes image is Python-only — adding nodejs+npm would expand the supply-chain surface by ~150MB and introduce a second package manager to scan and pin. Re-enable when (a) a Python port lands upstream, or (b) the npm surface is justified by a second npm-only MCP making the Node addition cheaper amortised. |
| `git` | Deferred from J5 (2026-05-20) | Upstream `mcp-server-git` (Python via uvx) requires a checked-out repository path. The Hermes container is `read_only: true` with only `/data` and `/home/hermes/.hermes` writable (state, not workspace) — no repo is reachable. Wiring it requires an ADR-level decision about whether the agent should self-modify its own source tree or operate on a sandboxed workspace clone. |

**Count: 4 active, 3 deferred.**

## Sunset criteria

A wired MCP server should be removed (from `cli-config.yaml`, from
`docker-compose.yml`, and from this table) when **any** of the following
holds for ≥ 30 consecutive days:

1. **Zero call traffic.** OpenTelemetry traces (Phoenix dashboard,
   `tool.dispatch` spans whose tool name is namespaced under the MCP
   server) show no invocations from any plugin or skill.
2. **Functional replacement landed.** A first-party Hermes tool (or
   another already-wired MCP) covers ≥ 95% of the call surface, and the
   remaining 5% is out-of-scope or trivially portable.
3. **Owner unresponsive on incidents.** Two consecutive Sev-2-or-higher
   incidents go unresolved by the upstream owner inside the agreed SLA
   (default 5 business days), or upstream archives the repo.
4. **Auth posture degraded.** The upstream switches auth from
   bearer-PAT/OAuth to a method we cannot satisfy under sops (e.g.
   interactive browser flow only), and the workaround would require
   bypassing our secret-management pipeline.
5. **Compliance / supply-chain fail.** Trivy / cosign verification fails
   for two consecutive monthly scans and upstream declines to remediate.

Removing an MCP is a deliberate scope change. Pair the deletion with:

* a release-notes entry under the next `CHANGELOG.md` entry,
* an ADR (in `docs/decisions/`) only if the MCP carried a non-obvious
  capability that other systems will need to know is gone,
* a sweep of `lib/` and `hermes-agent/plugins/` for any code path that
  references the MCP by name.

## Add-a-new-MCP checklist

Run through this list **before opening the PR** that wires a new
`mcp_servers:` entry. Every item is mandatory unless explicitly waived
in the PR description.

1. [ ] **Owner attestation.** A named owner (GitHub handle) accepts
   incident response for this MCP. Recorded in the inventory table
   above with a backing comment in the PR description.
2. [ ] **Auth review.** Auth method documented in the table; the secret
   (PAT, API key, OAuth client secret) lands in `secrets/*.sops` via
   the standard sops/age workflow — never inline in `cli-config.yaml`
   or `docker-compose.yml`. Cross-reference the sops file path in the
   table.
3. [ ] **Transport pinned.** For HTTP MCP servers running as sidecars,
   the image is pinned by digest (`image@sha256:…`, see audit C3). For
   stdio MCP servers, the entrypoint command is fully specified.
4. [ ] **Network surface justified.** The sidecar joins only the
   `internal` and (if required) `egress` Docker networks — never the
   host network. Outbound egress to third-party APIs is documented.
5. [ ] **Eval test added.** A test under `tests/integration/` exercises
   at least one round-trip call through the new MCP. The
   [nightly eval workflow](../.github/workflows/nightly-eval.yml)
   currently runs a single hardcoded file
   (`tests/integration/test_evaluators_smoke.py`); to wire new MCP
   coverage into the nightly cadence, either add an assertion to that
   file or extend the workflow's `pytest` invocation with an additional
   path. Without this, regressions in the upstream MCP go undetected.
6. [ ] **Sunset hook reviewed.** Confirm at least one of the sunset
   criteria above is monitorable for this MCP (typically the OTel
   call-count signal under "Zero call traffic"). If the MCP carries no
   trace coverage, add a follow-up issue to wire it before merging.
7. [ ] **README and inventory updated in the same PR.** Both this file
   and the top-level service inventory in `README.md` reflect the new
   entry. Stale rows are a known audit failure mode.

## Operator commands

Smoke-check connectivity to every wired MCP from a developer laptop with
the production stack running locally (`deploy/docker-compose.yml`):

```bash
# Confirm github-mcp returns a valid challenge.
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8003/  # expect 401 (auth required)

# Confirm context7 SSE endpoint is reachable (public).
curl -sS -o /dev/null -w "%{http_code}\n" https://mcp.context7.com/  # expect 200/301
```

A 401 from `github-mcp` is the correct healthy response — the sidecar
runs on a distroless image with no probe binaries, so the parent compose
stack uses `service_started` (not `service_healthy`) as its dependency
condition. Downstream services (Hermes) verify connectivity by
authenticated probes during startup.
