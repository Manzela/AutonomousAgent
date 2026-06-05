import yaml
from pathlib import Path


def test_deploy_workflow_gate_structure():
    """Verify that phase-0a-deploy.yml has the SP-00d supply-chain gate logic."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    assert wf_path.exists()

    with open(wf_path, "r") as f:
        wf = yaml.safe_load(f)

    jobs = wf.get("jobs", {})

    # 1. Check all required gate jobs exist in the workflow
    assert "build-and-push" in jobs
    assert "osv-scanner" in jobs
    assert "trivy" in jobs
    assert "deploy-staging" in jobs
    assert "deploy-production" in jobs

    # 2. Check that both deploy jobs enforce the gate by needing all scans
    for job_name in ["deploy-staging", "deploy-production"]:
        deploy_job = jobs[job_name]
        needs = deploy_job.get("needs", [])
        assert "build-and-push" in needs
        assert "osv-scanner" in needs
        assert "trivy" in needs

    # Also verify deploy-production depends on deploy-staging
    assert "deploy-staging" in jobs["deploy-production"].get("needs", [])

    # 3. Check build-and-push job signs images after push
    bp_steps = jobs["build-and-push"].get("steps", [])
    sign_steps = [s for s in bp_steps if s.get("run") and "cosign sign" in s["run"]]
    # We expect at least 2 signing commands (one for hermes, one for shell-sandbox)
    assert len(sign_steps) >= 2

    # 4. Check that both deploy jobs verify signatures and use IAP SSH verification
    for job_name in ["deploy-staging", "deploy-production"]:
        deploy_job = jobs[job_name]
        deploy_steps = deploy_job.get("steps", [])
        verify_step = next(
            (s for s in deploy_steps if s.get("name") == "Verify image signatures"), None
        )
        assert verify_step is not None
        assert "cosign verify" in verify_step["run"]
        assert "phase-0a-deploy" in verify_step["run"]

        # 5. Check VM script also has verification logic
        ssh_step = next((s for s in deploy_steps if s.get("name") == "Deploy via IAP SSH"), None)
        assert ssh_step is not None
        ssh_cmd = ssh_step.get("run", "")
        assert "cosign verify" in ssh_cmd
        assert "phase-0a-deploy" in ssh_cmd


def test_workflow_permissions():
    """Verify that the workflow jobs have the required permissions for OIDC/signing."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    with open(wf_path, "r") as f:
        wf = yaml.safe_load(f)

    # build-and-push needs id-token: write for keyless signing via Fulcio/Rekor
    bp_job = wf["jobs"]["build-and-push"]
    assert bp_job.get("permissions", {}).get("id-token") == "write"

    # deploy jobs need id-token: write for GCP WIF auth
    for job_name in ["deploy-staging", "deploy-production"]:
        deploy_job = wf["jobs"][job_name]
        assert deploy_job.get("permissions", {}).get("id-token") == "write"


def test_no_mutable_latest_tag_in_deploy_workflow():
    """H-19a: :latest must not appear in the build-push tags or anywhere the workflow
    resolves image references. A mutable :latest tag enables a TOCTOU tag-swap that
    can bypass the cosign verify gate.

    This test reads the raw workflow YAML text (before safe_load strips comments) so
    it also catches any :latest reference in inline shell scripts or comments that
    would indicate a residual mutable-tag reference.
    """
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    assert wf_path.exists()

    raw_text = wf_path.read_text()

    # The only allowed occurrence of the string ":latest" is inside the
    # docker-compose.gcp.override.yml guard string which is defined in that file,
    # not in this workflow. Any occurrence here is a violation.
    offending_lines = [
        f"  line {i + 1}: {line.rstrip()}"
        for i, line in enumerate(raw_text.splitlines())
        if ":latest" in line
    ]
    assert not offending_lines, (
        "Mutable ':latest' tag found in phase-0a-deploy.yml — use digest or :<sha> only.\n"
        + "\n".join(offending_lines)
    )


def test_no_registry_buildcache_in_deploy_workflow():
    """H-19a review: immutable_tags=true is repo-scoped — any :buildcache registry
    ref in cache-from/cache-to would fail on the second content-changing build.
    Verify all BuildKit cache directives use type=gha, not type=registry."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    assert wf_path.exists()

    raw_text = wf_path.read_text()

    # No line may contain a registry :buildcache ref in a cache-from/cache-to value.
    # The only allowed mention of "buildcache" is inside prose comments.
    registry_buildcache_lines = [
        f"  line {i + 1}: {line.rstrip()}"
        for i, line in enumerate(raw_text.splitlines())
        if "buildcache" in line and ("cache-from:" in line or "cache-to:" in line)
    ]
    assert not registry_buildcache_lines, (
        "Registry :buildcache reference found in a cache-from/cache-to directive — "
        "immutable_tags=true is repo-scoped and will reject the overwrite. "
        "Use type=gha instead.\n" + "\n".join(registry_buildcache_lines)
    )


def test_deploy_pulls_by_digest():
    """H-19a: both deploy jobs must thread HERMES_DIGEST and SANDBOX_DIGEST from the
    build job into the remote script and pull images by digest, not by tag."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    with open(wf_path, "r") as f:
        wf = yaml.safe_load(f)

    for job_name in ["deploy-staging", "deploy-production"]:
        deploy_job = wf["jobs"][job_name]
        deploy_env = deploy_job.get("env", {})

        # HERMES_DIGEST and SANDBOX_DIGEST must be wired from build-and-push outputs
        assert "HERMES_DIGEST" in deploy_env, f"HERMES_DIGEST not in {job_name} env"
        assert "SANDBOX_DIGEST" in deploy_env, f"SANDBOX_DIGEST not in {job_name} env"
        assert (
            "hermes_digest" in deploy_env["HERMES_DIGEST"]
        ), f"HERMES_DIGEST must reference build-and-push.outputs.hermes_digest in {job_name}"
        assert (
            "sandbox_digest" in deploy_env["SANDBOX_DIGEST"]
        ), f"SANDBOX_DIGEST must reference build-and-push.outputs.sandbox_digest in {job_name}"

        # The IAP SSH step must pass the digests into the remote script
        deploy_steps = deploy_job.get("steps", [])
        ssh_step = next((s for s in deploy_steps if s.get("name") == "Deploy via IAP SSH"), None)
        assert ssh_step is not None, f"Deploy via IAP SSH step not found in {job_name}"

        ssh_run = ssh_step.get("run", "")
        # Digests must be forwarded as positional args to bash on the VM
        assert (
            "HERMES_DIGEST" in ssh_run
        ), f"HERMES_DIGEST not threaded into remote script in {job_name}"
        assert (
            "SANDBOX_DIGEST" in ssh_run
        ), f"SANDBOX_DIGEST not threaded into remote script in {job_name}"
        # The VM must docker pull by digest, not by floating tag
        assert "docker pull" in ssh_run, f"remote script must docker pull by digest in {job_name}"
        assert (
            "@${HERMES_DIGEST}" in ssh_run or "@$HERMES_DIGEST" in ssh_run
        ), f"remote script must pull hermes by @digest reference in {job_name}"
        assert (
            "@${SANDBOX_DIGEST}" in ssh_run or "@$SANDBOX_DIGEST" in ssh_run
        ), f"remote script must pull shell-sandbox by @digest reference in {job_name}"
