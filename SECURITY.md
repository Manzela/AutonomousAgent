# Security Policy

## Supported Versions

This is a single-deployment project (not a published library). The supported version is always the latest commit on `main`. Phase-specific branches (`phase/1`–`phase/4`) are pre-release and receive security fixes via cherry-pick from `main`.

| Branch | Status | Security fixes |
|---|---|---|
| `main` | Supported | Yes — cherry-picked from hotfix branches |
| `phase/N` (active phase) | Pre-release | Yes — cherry-picked from `main` |
| `phase/N` (superseded) | Archived | No — branch deleted after acceptance |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.** Use one of these private channels:

1. **GitHub private vulnerability reporting** (preferred): https://github.com/Manzela/AutonomousAgent/security/advisories/new
2. **Direct email**: open the repo owner's GitHub profile and use the email address there

When reporting, please include:

- **Description** of the vulnerability and the impact
- **Reproduction steps** with minimum required setup
- **Affected components** (e.g., `lib/scrubber.py`, `deploy/docker-compose.yml`, `scripts/bootstrap.sh`)
- **Suggested fix** if you have one
- **Disclosure timeline expectations** if any

## Response timeline

- **Acknowledgement**: within 72 hours of report
- **Triage and severity classification**: within 7 days
- **Fix or mitigation plan**: within 30 days for critical/high severity; 90 days for medium/low
- **Public disclosure**: coordinated with the reporter; default 90 days after fix lands

## Severity classification

We use a simplified four-tier model.

| Tier | Examples | Response |
|---|---|---|
| **Critical** | RCE in agent loop; secret exfiltration via tool dispatch; sandbox escape; supply-chain compromise | Patch within days; advisory published immediately |
| **High** | Authentication bypass on Telegram gateway; budget cap bypass; logged secrets reaching disk | Patch within 30 days |
| **Medium** | Missing rate limit; verbose error messages leaking internal state; incorrect approval-gate default | Patch in next minor release |
| **Low** | Informational disclosure that requires elevated access; weak default in advisory config | Tracked, fixed opportunistically |

## Defense-in-depth boundaries

If you find a vulnerability, please indicate which boundary it crosses:

- **Tier 0**: Agent process itself (Python/`hermes-agent`)
- **Tier 1**: Compose internal network → external internet (egress allowlist)
- **Tier 2**: External user (Telegram) → agent
- **Tier 3**: Tool sandbox isolation (shell-sandbox / Modal / Daytona)
- **Tier 4**: Secret exposure (sops-encrypted at rest, scrubbed at egress)
- **Tier 5**: Supply chain (Hermes upstream, LiteLLM, base images)

Each tier has its own threat model documented in [`docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md) §5.

## Known security non-goals (out of scope)

- Multi-tenant isolation (single-user project; not designed for hostile co-tenants)
- Resistance to a compromised host OS (sops keys are on the host; host compromise == game over)
- Resistance to a malicious GitHub Actions runner (we trust GitHub's runner isolation)
- Side-channel attacks (timing, cache, microarchitectural)
- Defense against the LLM provider itself (Anthropic via Vertex AI is in our trust boundary)

## Acknowledgements

We will credit reporters in release notes unless they request anonymity.
