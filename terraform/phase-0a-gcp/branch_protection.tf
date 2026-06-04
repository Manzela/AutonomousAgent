
provider "github" {
  owner = "Manzela"
}

# DESIRED STATE — must be applied by an operator.
#
# To apply:
#   terraform -chdir=terraform/phase-0a-gcp apply -target=github_branch_protection.main
#
# Or imperatively (admin token required):
#   gh api repos/Manzela/AutonomousAgent/branches/main/protection \
#     -X PATCH --input - <<'EOF'
#   {"required_status_checks":{"strict":true,"contexts":[<paste contexts list>]}}
#   EOF
#
# HIGH-STAKES NOTE: a required context that does not emit on a given PR leaves that
# PR in "pending" forever → blocks the merge.  Every context in this list has been
# verified UNCONDITIONAL (runs on every pull_request, no paths: filter, no
# continue-on-error at the job level) in config/required_gates.txt.
# The drift-guard test tests/ci/test_required_gates_coverage.py asserts that this
# list stays in sync with config/required_gates.txt and that no path-filtered or
# advisory job was accidentally added.
resource "github_branch_protection" "main" {
  repository_id = "AutonomousAgent"
  pattern       = "main"

  require_signed_commits  = true
  required_linear_history = true
  enforce_admins          = true
  allows_force_pushes     = false
  allows_deletions        = false

  required_pull_request_reviews {
    required_approving_review_count = 2
    require_code_owner_reviews      = true
    dismiss_stale_reviews           = true
    require_last_push_approval      = true
  }

  required_status_checks {
    strict = true
    # Source of truth: config/required_gates.txt (curated + commented allowlist).
    # Each name below maps to a real job `name:` in .github/workflows/*.yml.
    # Sync is enforced by tests/ci/test_required_gates_coverage.py.
    contexts = [
      # ── Existing 11 ────────────────────────────────────────────────────────
      # ci.yml — unconditional pull_request, no paths filter
      "Lint Python",
      # ci.yml — unconditional pull_request, no paths filter
      "Lint Shell",
      # ci.yml — unconditional pull_request, no paths filter
      "Lint YAML",
      # ci.yml — unconditional pull_request; hadolint steps advisory per-step, job is blocking
      "Lint Dockerfiles",
      # ci.yml — unconditional pull_request, no paths filter
      "Unit Tests",
      # ci.yml — unconditional pull_request, no paths filter
      "Validate config/limits.yaml",
      # ci.yml — unconditional pull_request, no paths filter
      "Validate docker-compose",
      # pr-validation.yml — unconditional pull_request, no paths filter
      "Conventional Commit title",
      # pr-validation.yml — unconditional pull_request, no paths filter
      "Branch name follows convention",
      # secret-scan.yml — unconditional pull_request; SARIF upload steps advisory, scan steps blocking
      "gitleaks",
      # secret-scan.yml — same rationale as gitleaks above
      "detect-secrets",

      # ── New safety/enforcement gates ───────────────────────────────────────
      # dead-code-gate.yml — no paths filter, no continue-on-error; enforces C4
      "dead-code reachability (C4)",
      # test-integrity-gate.yml — no paths filter; job if: only true on PRs (always runs); enforces C6
      "no unexplained test deletion/weakening (C6)",
      # c9-reviewer-class-gate.yml — no paths filter; enforces C-04/H-05 cross-model reviewer rule
      "C9 reviewer-model-class gate (C-04)",
      # acceptance-frozen.yml — no paths filter; enforces C8/C10 rubric tamper-evidence
      "Verify acceptance files match the sha-pinned manifest (C8)",
      # pr-meta-checks.yml — no paths filter; job if: only true on PRs (always runs); enforces C1+C7
      "evidence-present (C1) + test-truth (C7)",
      # import-hygiene.yml — no paths filter; enforces C5 first-party module importability
      "import every first-party module (C5)",
      # sp-00e6-provenance-antidrift.yml (job: provenance-check) — no paths filter; enforces C11
      "provenance honesty (C11)",
      # sp-00e6-provenance-antidrift.yml (job: no-sentinel-termination) — no paths filter, no if:
      "no-sentinel-termination (anti-drift)",
      # no-sa-key-gate.yml — no paths filter; enforces SP-00b WIF-only auth
      "no-sa-key-gate (SP-00b)",
      # license-dep-gate.yml — no paths filter; enforces SP-00h license + dep hygiene
      "license + dep allowlist (SP-00h)",
      # ci.yml (job: enforce-sha-pinning) — no paths filter; supply-chain SHA pinning
      "Enforce Action SHA-pinning",
      # ci.yml (job: import-smoke) — no paths filter; catches import-time crashes
      "Import Smoke Gate",
      # sp-g1-golden.yml — no paths filter; enforces SP-G1 golden eval corpus integrity
      "golden eval corpus regression (SP-G1)",
      # sp-o1-cost-recorder.yml — no paths filter; enforces SP-O1 cost sourcing
      "llm.call.cost sourced + recorded (SP-O1)",
    ]
  }
}
