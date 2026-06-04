#!/usr/bin/env python3
"""compose-interpolation-lint — regression guard for the OD-2 ``$VAR``-blanking trap.

Docker Compose interpolates ``${VAR}`` at config-PARSE time. An unset, *un-defaulted*
``${VAR}`` silently expands to ``""`` with only a stderr ``WARN`` — that blanked ``$PY`` in
the nightly live-stack across **9 dispatch cycles** before the root cause was found. This
lint forbids a BARE ``${VAR}`` in compose files: every interpolation must carry an explicit
default (``${VAR:-x}`` / ``${VAR-x}``) or a loud-fail (``${VAR:?msg}`` / ``${VAR?msg}``).
``$${VAR}`` (an escaped literal handed to the *container* shell) is exempt, as are YAML
comments.

Why a *source* scan and not ``docker compose config``: the source scan is pure-stdlib,
hermetic (no docker daemon, no ``env_file`` resolution that would error in a bare CI runner),
deterministic, and it flags a bare ``${VAR}`` even when the var *happens* to be set in the
ambient environment — which is exactly the silent dependency that made the trap invisible.

Modes:
  (default)            SOURCE scan (hermetic): flag any bare ``${VAR}`` in the given compose
                       files, or ``deploy/docker-compose*.yml`` when none are given.
  --from-stderr FILE   Parse a captured ``docker compose config`` stderr for
                       "... variable is not set ..." warnings (hermetic test seam; also lets
                       another job pipe a live ``config`` run through the same matcher).
  --docker-config      Live: run ``docker compose -f <each> config`` and scan its stderr
                       (defense-in-depth; requires docker — NOT used by the hermetic gate).

  --allow VAR          Exempt VAR (repeatable). Use sparingly, with a comment in the workflow.

Exit: 0 clean · 1 bare interpolation / unset-var warning found · 2 usage/IO/tooling error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# ``${...}`` NOT preceded by ``$`` (so ``$${VAR}`` — an escaped literal — is skipped).
_INTERP_RE = re.compile(r"(?<!\$)\$\{([^}]*)\}")
# An interpolation is SAFE iff its content is a var name immediately followed by a default
# (``:-`` / ``-``) or loud-fail (``:?`` / ``?``) operator. EVERYTHING ELSE is flagged: a bare
# ``${VAR}``, an alternate ``${VAR:+x}`` / ``${VAR+x}`` (blanks when unset), a bash-style
# ``${VAR/a/b}`` (not a Compose-supported form), or a malformed ``${}``. Flagging by the
# ABSENCE of a safe operator — rather than only matching a bare identifier — closes the
# silent-blank false-negatives a "bare-identifier-only" check would miss (C9, gemini-2-5-pro).
# (Nested ``${A:-${B}}`` is not a Compose-supported form and is out of scope.)
_SAFE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(:-|:\?|-|\?)")
# The leading var name, for reporting (e.g. ``VAR`` out of ``VAR:+x``).
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")
# Docker Compose's unset-variable warning, both quoted and unquoted spellings:
#   The "PY" variable is not set. Defaulting to a blank string.
#   The PY variable is not set. Defaulting to a blank string.
_UNSET_RE = re.compile(r'The\s+"?(?P<var>[A-Za-z_][A-Za-z0-9_]*)"?\s+variable is not set')


def _strip_comment(line: str) -> str:
    """Drop a YAML comment so a ``${VAR}`` mentioned in prose is not flagged. A full-line
    comment becomes ``""``; an inline ``#`` is treated as a comment only when it is outside
    quotes and preceded by whitespace (YAML's inline-comment rule). A backslash escape inside
    a double-quoted scalar (``\\"``) is consumed as a pair so an escaped quote does not falsely
    close the string and unmask a ``#`` (C9: false-negative hardening). YAML single-quotes do
    not use backslash escaping (``''`` is the escape), so they are toggled as-is."""
    if line.lstrip().startswith("#"):
        return ""
    out: list[str] = []
    in_single = in_double = False
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if in_double and c == "\\" and i + 1 < n:
            out.append(c)
            out.append(line[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(c)
        i += 1
    return "".join(out)


def find_bare_interpolations(
    text: str, allow: set[str] | None = None
) -> list[tuple[int, str, str]]:
    """Return ``(lineno, var, raw_token)`` for each interpolation that can silently blank — any
    ``${...}`` that is not ``$$``-escaped, not in a comment, and lacks a ``:-``/``-`` default or
    ``:?``/``?`` loud-fail operator (so a bare ``${VAR}``, an alternate ``${VAR:+x}``, etc.)."""
    allow = allow or set()
    findings: list[tuple[int, str, str]] = []
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw_line)
        for m in _INTERP_RE.finditer(line):
            content = m.group(1)
            if _SAFE_RE.match(content):
                continue
            name_m = _NAME_RE.match(content)
            var = name_m.group(0) if name_m else content
            if var in allow:
                continue
            findings.append((lineno, var, m.group(0)))
    return findings


def lint_files(paths: list[str], allow: set[str] | None = None) -> list[tuple[str, int, str, str]]:
    """Flatten :func:`find_bare_interpolations` over ``paths`` → ``(path, lineno, var, raw)``."""
    allow = allow or set()
    results: list[tuple[str, int, str, str]] = []
    for p in paths:
        text = pathlib.Path(p).read_text(encoding="utf-8")
        for lineno, var, raw in find_bare_interpolations(text, allow):
            results.append((p, lineno, var, raw))
    return results


def extract_unset_vars(stderr: str) -> list[str]:
    """Unique (order-preserving) variable names from ``docker compose config`` unset-var
    warnings in ``stderr``."""
    return list(dict.fromkeys(m.group("var") for m in _UNSET_RE.finditer(stderr)))


def lint_stderr(stderr: str, allow: set[str] | None = None) -> tuple[bool, list[str]]:
    """``(clean, offending_vars)`` for a captured ``docker compose config`` stderr."""
    allow = allow or set()
    offending = [v for v in extract_unset_vars(stderr) if v not in allow]
    return (not offending, offending)


def run_docker_config(paths: list[str]) -> str | None:
    """Run ``docker compose -f <each> config`` and return its stderr, or ``None`` if docker
    is unavailable. The exit code is intentionally ignored — a missing ``env_file`` makes
    ``config`` exit non-zero for reasons unrelated to interpolation, but the unset-var
    warnings are still emitted to stderr during interpolation."""
    cmd = ["docker", "compose"]
    for p in paths:
        cmd += ["-f", p]
    cmd.append("config")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return proc.stderr


def _repo_root() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "."


def _default_files(base: str) -> list[str]:
    return sorted(str(p) for p in pathlib.Path(base).glob("deploy/docker-compose*.yml"))


_REMEDY = (
    "use ${{{var}:-<default>}} (default) or ${{{var}:?<msg>}} (loud-fail), "
    "or $${{{var}}} for a literal passed to the container shell"
)


def _report_unset(offending: list[str], source: str) -> int:
    for v in offending:
        print(
            f"::error::compose-interpolation ({source}): ${{{v}}} is unset and un-defaulted "
            f"— it silently blanks. " + _REMEDY.format(var=v)
        )
    print(f"== compose-interpolation: FAIL ({len(offending)} unset var(s)) ==")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="compose-interpolation lint (OD-2 regression guard)")
    ap.add_argument("files", nargs="*", help="compose files (default: deploy/docker-compose*.yml)")
    ap.add_argument("--allow", action="append", default=[], help="exempt VAR (repeatable)")
    ap.add_argument("--from-stderr", help="parse a captured `docker compose config` stderr file")
    ap.add_argument(
        "--docker-config", action="store_true", help="live: shell `docker compose config`"
    )
    ap.add_argument("--base", default=None, help="repo root (default: git toplevel)")
    args = ap.parse_args(argv)
    allow = set(args.allow)

    # Mode 1: parse a captured stderr (hermetic seam).
    if args.from_stderr:
        try:
            stderr = pathlib.Path(args.from_stderr).read_text(encoding="utf-8")
        except OSError as e:
            print(f"::error::compose-interpolation: cannot read {args.from_stderr}: {e}")
            return 2
        clean, offending = lint_stderr(stderr, allow)
        if clean:
            print("[PASS] compose-interpolation: no 'variable is not set' warnings in stderr")
            return 0
        return _report_unset(offending, "stderr")

    base = args.base or _repo_root()
    files = args.files or _default_files(base)
    if not files:
        print(
            "::error::compose-interpolation: no compose files given and none matched "
            "deploy/docker-compose*.yml"
        )
        return 2

    # Mode 2: live docker compose config (defense-in-depth).
    if args.docker_config:
        dc_stderr = run_docker_config(files)
        if dc_stderr is None:
            print("::error::compose-interpolation: docker not available for --docker-config")
            return 2
        clean, offending = lint_stderr(dc_stderr, allow)
        if clean:
            print(
                f"[PASS] compose-interpolation: `docker compose config` of {len(files)} "
                "file(s) emitted no unset-var warnings"
            )
            return 0
        return _report_unset(offending, "docker-config")

    # Mode 3 (default): hermetic source scan.
    results = lint_files(files, allow)
    if not results:
        print(
            f"[PASS] compose-interpolation: {len(files)} file(s), no bare ${{VAR}} interpolations"
        )
        return 0
    for p, lineno, var, raw in results:
        print(
            f"::error file={p},line={lineno}::compose-interpolation: bare {raw} silently blanks "
            f"when {var} is unset — " + _REMEDY.format(var=var)
        )
    print(f"== compose-interpolation: FAIL ({len(results)} bare interpolation(s)) ==")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
