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

| Name | Purpose | Transport | URL | Auth method | Owner |
| --- | --- | --- | --- | --- | --- |
| `github` | GitHub repo/PR/issue/Actions access for autonomous PR creation, code search, dependency review. Backed by `ghcr.io/github/github-mcp-server` running `--toolsets all` (repos, pull_requests, issues, actions, code_security, dependabot, secret_protection, …). | HTTP | `http://github-mcp:8003` | Bearer PAT via `GITHUB_PERSONAL_ACCESS_TOKEN_FILE=/run/secrets/github_pat` (sops-encrypted, see `secrets/github-pat.sops`) | `@Manzela` |
| `context7` | Library / framework documentation lookup (React, Vite, Vertex AI, etc.). Used by skills that need post-training-cutoff API references. | HTTP (SSE) | `https://mcp.context7.com/sse` | None (public hosted endpoint) | `@Manzela` |

### Deferred / commented-out entries

| Name | Status | Reason |
| --- | --- | --- |
| `playwright` | Deferred from Phase 1 | `mcr.microsoft.com/playwright/mcp:latest` exits cleanly when no client connects under its default entrypoint. We have not yet researched the correct flag-set to keep it running long-lived under compose. Re-enable in a follow-up once the right invocation is confirmed. The compose-level note lives next to the `# ---- Playwright MCP (deferred from Phase 1) ----` block in `deploy/docker-compose.yml`. |

**Count: 2 active, 1 deferred.**

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
