#!/usr/bin/env python3
"""Run an acceptance file's assertions and report pass/fail honestly.

This is the C1 "evidence = re-run" mechanism: a task's CI proof invokes it to
RE-RUN every assertion in audit/acceptance/<task>.yaml and emit the result as
the authoritative artifact (executor-pasted numbers are advisory only).

Usage:
    run_acceptance.py audit/acceptance/<task-id>.yaml

Each assertion is {id, cmd, expect_exit (default 0)}. Each cmd runs via `bash -c`
with CWD = git repo root; the REAL exit code is captured (no pipe masking) and
compared to expect_exit. Process exit is 0 iff every assertion matched.

NOTE: SP-00e generalizes/owns the richer enforcement infra (acceptance-frozen,
pr-meta-checks). This minimal runner is the bootstrap substrate SP-00 needs to
produce a trustworthy, CI-produced proof before that infra exists.
"""

from __future__ import annotations

import subprocess
import sys

import yaml


def _repo_root() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main(path: str) -> int:
    with open(path) as fh:
        spec = yaml.safe_load(fh)
    root = _repo_root()
    overall_ok = True
    print(f"== acceptance: {spec.get('task')} ({path}) ==", flush=True)
    for a in spec["assertions"]:
        expected = a.get("expect_exit", 0)
        result = subprocess.run(
            ["bash", "-c", a["cmd"]],
            cwd=root,
            capture_output=True,
            text=True,
        )
        passed = result.returncode == expected
        overall_ok &= passed
        print(
            f"[{'PASS' if passed else 'FAIL'}] {a['id']}: "
            f"exit={result.returncode} expected={expected}",
            flush=True,
        )
        # Surface tail of output for any assertion (so the artifact is legible).
        tail_out = result.stdout[-2000:]
        tail_err = result.stderr[-2000:]
        if tail_out.strip():
            print("    stdout:", tail_out.replace("\n", "\n    "), flush=True)
        if tail_err.strip():
            print("    stderr:", tail_err.replace("\n", "\n    "), flush=True)
    print(f"== overall: {'PASS' if overall_ok else 'FAIL'} ==", flush=True)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: run_acceptance.py audit/acceptance/<task-id>.yaml")
    sys.exit(main(sys.argv[1]))
