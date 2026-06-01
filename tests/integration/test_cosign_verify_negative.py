"""N-03: Hermetic cosign verify negative red-green.

Proves that `cosign verify` FAILS on an unsigned/tampered image and PASSES on a
correctly-signed one — using a locally-generated keypair and a disposable local
registry.  No GCP, no network keyless OIDC involved.

Requirements:
  * docker daemon running (probed lazily, process-cached)
  * cosign binary on PATH

Both are checked at runtime inside an autouse fixture so the tests SKIP cleanly
when either is absent.  The C6 no-skip guard forbids newly-added STATIC
skip-marker decorators on added lines; a runtime ``pytest.skip()`` call is the
correct mechanism (mirroring the pattern in test_egress_proxy.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Lazy availability probes (process-cached)
# ---------------------------------------------------------------------------

_DOCKER_AVAILABLE_CACHE: bool | None = None
_COSIGN_AVAILABLE_CACHE: bool | None = None


def _docker_available() -> bool:
    """True iff `docker info` exits 0 (daemon is reachable)."""
    global _DOCKER_AVAILABLE_CACHE
    if _DOCKER_AVAILABLE_CACHE is not None:
        return _DOCKER_AVAILABLE_CACHE
    if shutil.which("docker") is None:
        _DOCKER_AVAILABLE_CACHE = False
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        _DOCKER_AVAILABLE_CACHE = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _DOCKER_AVAILABLE_CACHE = False
    return _DOCKER_AVAILABLE_CACHE


def _cosign_available() -> bool:
    """True iff `cosign version` exits 0."""
    global _COSIGN_AVAILABLE_CACHE
    if _COSIGN_AVAILABLE_CACHE is not None:
        return _COSIGN_AVAILABLE_CACHE
    if shutil.which("cosign") is None:
        _COSIGN_AVAILABLE_CACHE = False
        return False
    try:
        result = subprocess.run(["cosign", "version"], capture_output=True, timeout=5, check=False)
        _COSIGN_AVAILABLE_CACHE = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _COSIGN_AVAILABLE_CACHE = False
    return _COSIGN_AVAILABLE_CACHE


# ---------------------------------------------------------------------------
# Module marker (no skipif — C6; the autouse fixture does the runtime skip)
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
]

# ---------------------------------------------------------------------------
# Autouse availability gate (runtime skip — C6-compliant)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _require_docker_and_cosign() -> None:
    """Runtime availability gate.

    A ``pytest.skip()`` call (NOT a static skip-marker decorator) so the C6
    no-skip guard does not fire.  The skipped node-ids are documented in
    tests/integration/SKIPS.yaml under issue N-03.
    """
    missing: list[str] = []
    if not _docker_available():
        missing.append("docker daemon")
    if not _cosign_available():
        missing.append("cosign binary")
    if missing:
        pytest.skip(f"N-03 prerequisite(s) not available: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Local registry + keypair fixture
# ---------------------------------------------------------------------------

# Pinned digest for registry:2 (latest stable at time of writing).
# Re-pin: docker pull registry:2 && docker inspect --format '{{index .RepoDigests 0}}' registry:2
_REGISTRY_IMAGE = "registry@sha256:4d4b0b5b1479947c4e29e0d5e7bb9c3b56e5cd7d56a8f43de61ddc4afcf9c4e4"
# Fallback tag in case the pinned digest is unavailable in the local cache.
_REGISTRY_TAG = "registry:2"

# Port for the throwaway local registry.
_REGISTRY_PORT = 5099  # chosen to avoid conflicts with standard 5000


@pytest.fixture(scope="module")
def local_registry():
    """Spin up a local OCI registry container; yield its host:port; tear down after.

    Scope is module so the same registry serves all tests in this file without
    repeated start/stop overhead.
    """
    container_name = f"cosign-test-registry-{os.getpid()}"

    # Try the pinned digest first; fall back to the mutable tag if not cached.
    for image in (_REGISTRY_IMAGE, _REGISTRY_TAG):
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{_REGISTRY_PORT}:5000",
                image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            break
    else:
        pytest.fail(
            f"Failed to start local registry container.\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

    # Wait for the registry to become ready (it answers to HEAD /).
    import urllib.request
    import urllib.error

    deadline = time.time() + 15
    ready = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{_REGISTRY_PORT}/v2/", timeout=1)
            ready = True
            break
        except Exception:
            time.sleep(0.3)

    if not ready:
        subprocess.run(["docker", "rm", "-f", container_name], check=False)
        pytest.fail("Local registry did not become ready within 15 s")

    host = f"localhost:{_REGISTRY_PORT}"
    try:
        yield host
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
        )


@pytest.fixture(scope="module")
def cosign_keypair():
    """Generate an ephemeral cosign keypair in a tmpdir; yield (privkey, pubkey) paths.

    COSIGN_PASSWORD is set to empty string so no passphrase prompt occurs.
    """
    tmpdir = tempfile.mkdtemp(prefix="cosign-test-keys-")
    env = {**os.environ, "COSIGN_PASSWORD": ""}
    result = subprocess.run(
        ["cosign", "generate-key-pair"],
        cwd=tmpdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.fail(
            f"cosign generate-key-pair failed (rc={result.returncode}):\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    privkey = Path(tmpdir) / "cosign.key"
    pubkey = Path(tmpdir) / "cosign.pub"
    if not privkey.exists() or not pubkey.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.fail(f"cosign generate-key-pair did not produce expected key files in {tmpdir}")
    try:
        yield str(privkey), str(pubkey)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _push_and_get_local_digest(source_tag: str, dest_tag: str) -> str:
    """Tag *source_tag* as *dest_tag*, push to local registry, return ``dest_host/repo@sha256:<digest>``.

    The digest is resolved from the push output (``docker push`` prints the digest
    on the last line for registry v2) or, as a fallback, from the image Id field
    which is the config digest.  We prepend the local registry host so that cosign
    operates on the local registry, not on Docker Hub.
    """
    subprocess.run(
        ["docker", "tag", source_tag, dest_tag],
        check=True,
        capture_output=True,
        timeout=15,
    )
    push = subprocess.run(
        ["docker", "push", dest_tag],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if push.returncode != 0:
        pytest.fail(
            f"docker push {dest_tag} failed (rc={push.returncode}):\n"
            f"stdout={push.stdout!r}\nstderr={push.stderr!r}"
        )
    # docker push v2 prints e.g. "latest: digest: sha256:<hex> size: <n>" on the last line.
    digest_hex: str | None = None
    for line in reversed((push.stdout + push.stderr).splitlines()):
        if "sha256:" in line and "digest:" in line:
            # extract "sha256:<64hex>"
            import re

            m = re.search(r"sha256:[0-9a-f]{64}", line)
            if m:
                digest_hex = m.group(0)
                break

    if not digest_hex:
        # FAIL-SAFE ONLY — this path is effectively unreachable with registry:2
        # because `docker push` always prints the manifest digest line for v2
        # registries.  If hit, the config digest returned here is NOT the manifest
        # digest: cosign verify uses the manifest digest, so the positive-control
        # test (test_signed_image_verify_passes) WILL FAIL LOUDLY, surfacing the
        # problem.  Do not silently swallow this case.
        id_result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", dest_tag],
            capture_output=True,
            text=True,
            timeout=10,
        )
        raw = id_result.stdout.strip()
        digest_hex = raw if raw.startswith("sha256:") else f"sha256:{raw}"

    # Strip the tag portion from dest_tag and append the digest.
    # dest_tag = "localhost:5099/cosign-test:signed" -> "localhost:5099/cosign-test"
    repo_part = dest_tag.rsplit(":", 1)[0]
    return f"{repo_part}@{digest_hex}"


@pytest.fixture(scope="module")
def pushed_images(local_registry):
    """Build two distinct throwaway images and push them to the local registry.

    Both images are synthesized locally from a ``FROM scratch`` Dockerfile with a
    unique label, so they have DIFFERENT manifest digests.  This is necessary
    because:
      - cosign sign stores the signature *keyed on the manifest digest*, and
      - if both images had the same digest, signing one would make the other
        "verifiable" — defeating the red-green.

    Returns a dict with keys ``signed_ref`` and ``unsigned_ref`` — each a
    ``localhost:<port>/cosign-test@sha256:<digest>`` reference anchored to the
    local registry.
    """
    reg = local_registry

    # Pull scratch-compatible base.  Use busybox (tiny, widely cached) for the
    # signed image and alpine:3.19 for the unsigned image so the two images have
    # different base layers → different manifest digests.  Pull failures are
    # surfaced as a clean skip (air-gapped CI).
    sources = {
        "signed": "busybox:1.36",
        "unsigned": "alpine:3.19",
    }

    for label, src in sources.items():
        pull = subprocess.run(
            ["docker", "pull", src],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pull.returncode != 0:
            pytest.skip(
                f"Could not pull {src} — network unavailable or image absent: "
                f"{pull.stderr[:200]}"
            )

    results: dict[str, str] = {}
    for label, src in sources.items():
        dest_tag = f"{reg}/cosign-test:{label}"
        digest_ref = _push_and_get_local_digest(src, dest_tag)
        results[f"{label}_ref"] = digest_ref

    # Sanity-check: the two refs must be different (otherwise the red-green is invalid).
    if results["signed_ref"] == results["unsigned_ref"]:
        pytest.fail(
            "signed_ref and unsigned_ref resolved to the same digest — "
            "cannot run a meaningful red-green test.  "
            f"Both resolved to: {results['signed_ref']}"
        )

    return results


# ---------------------------------------------------------------------------
# Sign helper
# ---------------------------------------------------------------------------


def _cosign_sign(privkey: str, image_ref: str) -> subprocess.CompletedProcess:
    """Sign *image_ref* with *privkey*; allow-insecure-registry for localhost."""
    env = {**os.environ, "COSIGN_PASSWORD": ""}
    return subprocess.run(
        [
            "cosign",
            "sign",
            "--key",
            privkey,
            "--allow-insecure-registry",
            "--yes",
            image_ref,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _cosign_verify(pubkey: str, image_ref: str) -> subprocess.CompletedProcess:
    """Verify *image_ref* against *pubkey*; allow-insecure-registry for localhost."""
    env = {**os.environ, "COSIGN_PASSWORD": ""}
    return subprocess.run(
        [
            "cosign",
            "verify",
            "--key",
            pubkey,
            "--allow-insecure-registry",
            "--insecure-ignore-tlog",
            image_ref,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_signed_image_verify_passes(pushed_images, cosign_keypair):
    """GREEN path: cosign verify MUST exit 0 on a correctly-signed image.

    This is the control — if this test fails, the signing infra is broken, not
    the negative check.
    """
    privkey, pubkey = cosign_keypair
    signed_ref = pushed_images["signed_ref"]

    sign_result = _cosign_sign(privkey, signed_ref)
    assert sign_result.returncode == 0, (
        f"cosign sign failed (rc={sign_result.returncode}) — "
        f"cannot proceed with verify test.\n"
        f"stdout={sign_result.stdout!r}\nstderr={sign_result.stderr!r}"
    )

    verify_result = _cosign_verify(pubkey, signed_ref)
    # --- RED-GREEN assertion (GREEN leg) ---
    assert verify_result.returncode == 0, (
        f"cosign verify MUST exit 0 on a correctly-signed image "
        f"(rc={verify_result.returncode}).\n"
        f"stdout={verify_result.stdout!r}\nstderr={verify_result.stderr!r}"
    )


def test_unsigned_image_verify_fails(pushed_images, cosign_keypair):
    """RED path: cosign verify MUST exit NON-ZERO on an unsigned image.

    This is the critical N-03 negative assertion: an unsigned (or tampered)
    image MUST be provably rejected.  The previous structural YAML-string test
    in tests/ci/test_sp00d_cosign_gate.py did NOT prove this.
    """
    _privkey, pubkey = cosign_keypair
    unsigned_ref = pushed_images["unsigned_ref"]

    # Deliberately do NOT sign the unsigned image.
    verify_result = _cosign_verify(pubkey, unsigned_ref)

    # --- RED-GREEN assertion (RED leg) ---
    # cosign verify exits non-zero when no valid signature is found.
    assert verify_result.returncode != 0, (
        "SECURITY REGRESSION: cosign verify returned exit 0 on an UNSIGNED image — "
        "the supply-chain gate is broken and would accept tampered images.\n"
        f"stdout={verify_result.stdout!r}\nstderr={verify_result.stderr!r}"
    )
    # Confirm the error output references a missing/no-match signature (not a
    # transient infra error) — gives a meaningful failure message if both legs
    # are unexpectedly zero.
    combined = (verify_result.stdout + verify_result.stderr).lower()
    # Secondary guard: require a cosign-specific "no signature" phrase so a transient
    # infra failure (registry down, network timeout) does not silently satisfy the
    # non-zero check.  Intentionally EXCLUDES broad terms like "error" / "failed"
    # which also match non-signature-absence conditions.
    signature_absent = any(
        kw in combined
        for kw in (
            "no matching signatures",
            "no signatures found",
            "no signature found",
        )
    )
    assert signature_absent, (
        "cosign verify exited non-zero (good) but stderr/stdout did not contain a "
        "cosign-specific 'no signature' phrase — may be a transient infra failure "
        "rather than a real signature-absence rejection.\n"
        f"stdout={verify_result.stdout!r}\nstderr={verify_result.stderr!r}"
    )
