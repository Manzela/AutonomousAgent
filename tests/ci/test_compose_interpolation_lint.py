"""Red-green unit tests for scripts/ci/compose_interpolation_lint.py.

The OD-2 trap: Docker Compose interpolates ``${VAR}`` at config-parse time; an unset,
un-defaulted ``${VAR}`` silently blanks to ``""`` (only a stderr WARN) — that blanked
``$PY`` in the nightly across 9 dispatches. This gate forbids a BARE ``${VAR}``; an explicit
``:-`` default / ``:?`` loud-fail / ``$$``-escape is required.

All tests are pure-stdlib expected-green assertions (no docker, no skip/xfail/skipif — the
C6 no-skip gate stays green). The final test is the LIVE regression guard: the real repo
compose files must contain ZERO bare interpolations, so 340-341 can't silently regress.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CI = Path(__file__).resolve().parents[2] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

from compose_interpolation_lint import (  # noqa: E402
    extract_unset_vars,
    find_bare_interpolations,
    lint_files,
    lint_stderr,
    main,
)

_REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Source scan — what is and isn't a "bare" interpolation                        #
# --------------------------------------------------------------------------- #
def test_bare_var_is_flagged():
    findings = find_bare_interpolations("      KEY: ${GITHUB_APP_ID}\n")
    assert [(v) for (_ln, v, _raw) in findings] == ["GITHUB_APP_ID"]
    assert findings[0][0] == 1  # line number
    assert findings[0][2] == "${GITHUB_APP_ID}"  # raw token


def test_default_modifier_is_safe():
    # ${VAR:-x} and ${VAR-x} both supply a fallback → never silently blank.
    assert find_bare_interpolations("a: ${VAR:-fallback}\nb: ${VAR-fallback}\n") == []


def test_loud_fail_modifier_is_safe():
    # ${VAR:?msg} / ${VAR?msg} error LOUDLY when unset — the opposite of a silent blank.
    assert find_bare_interpolations("a: ${VAR:?must be set}\nb: ${VAR?must be set}\n") == []


def test_empty_default_is_safe():
    # ${VAR:-} (the fix applied to 340-341) is an explicit empty default → safe.
    assert find_bare_interpolations("KEY: ${GITHUB_APP_ID:-}\n") == []


def test_double_dollar_escape_is_exempt():
    # $${VAR} is an escaped literal handed to the container shell, not Compose interpolation.
    assert (
        find_bare_interpolations('test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]\n') == []
    )


def test_full_line_comment_is_ignored():
    # A ${VAR} mentioned in a YAML comment is never interpolated.
    assert (
        find_bare_interpolations("      # uploads to ${GCS_SNAPSHOT_BUCKET}. Behind a flag\n") == []
    )


def test_inline_comment_is_ignored():
    assert find_bare_interpolations("KEY: ${VAR:-x}   # see ${OTHER_VAR} note\n") == []


def test_hash_inside_quotes_is_not_a_comment():
    # A '#' inside a quoted value is not a comment — a bare ${VAR} after it must still flag.
    findings = find_bare_interpolations('url: "http://h/#frag ${BARE}"\n')
    assert [v for (_ln, v, _raw) in findings] == ["BARE"]


def test_multiple_bare_on_one_line_all_flagged():
    findings = find_bare_interpolations("x: ${A}-${B}-${C:-ok}\n")
    assert [v for (_ln, v, _raw) in findings] == ["A", "B"]


def test_line_numbers_are_accurate():
    text = "a: ${OK:-1}\nb: 2\nc: ${BARE}\n"
    findings = find_bare_interpolations(text)
    assert findings == [(3, "BARE", "${BARE}")]


def test_allow_exempts_a_var():
    assert find_bare_interpolations("k: ${LEGACY}\n", allow={"LEGACY"}) == []
    assert [v for (_ln, v, _raw) in find_bare_interpolations("k: ${LEGACY}\n")] == ["LEGACY"]


# --------------------------------------------------------------------------- #
# C9 hardening — silent-blank forms a "bare identifier only" check would miss   #
# --------------------------------------------------------------------------- #
def test_alternate_modifier_is_flagged():
    # ${VAR:+x} / ${VAR+x} evaluate to "" when VAR is unset — a silent blank, NOT a default.
    a = find_bare_interpolations("a: ${VAR:+x}\nb: ${VAR+x}\n")
    assert [(ln, v) for (ln, v, _raw) in a] == [(1, "VAR"), (2, "VAR")]


def test_substitution_form_is_flagged():
    # ${VAR/a/b} is a bash substitution, not a Compose default — must not be treated safe.
    findings = find_bare_interpolations("x: ${VAR/foo/bar}\n")
    assert [v for (_ln, v, _raw) in findings] == ["VAR"]


def test_empty_braces_is_flagged():
    # Malformed ${} has no name and no operator — flag it rather than silently pass.
    findings = find_bare_interpolations("x: ${}\n")
    assert len(findings) == 1 and findings[0][2] == "${}"


def test_escaped_quote_in_double_string_does_not_unmask_comment():
    # A backslash-escaped quote inside a double-quoted scalar must NOT be read as closing the
    # string — otherwise the following '#' looks like a comment and the ${BARE} is missed
    # (the dangerous false-negative direction). The bare interpolation must still be flagged.
    findings = find_bare_interpolations('k: "a \\" # still in string ${BARE}"\n')
    assert [v for (_ln, v, _raw) in findings] == ["BARE"]


def test_alternate_modifier_var_name_reported_not_full_content():
    # The report shows the var NAME (VAR), not the raw operator content (VAR:+x).
    ((_ln, var, raw),) = find_bare_interpolations("k: ${VAR:+x}\n")
    assert var == "VAR" and raw == "${VAR:+x}"


# --------------------------------------------------------------------------- #
# stderr matcher — for the --from-stderr / --docker-config paths                #
# --------------------------------------------------------------------------- #
def test_extract_unset_vars_quoted_and_unquoted():
    stderr = (
        'WARN[0000] The "PY" variable is not set. Defaulting to a blank string.\n'
        "WARNING: The HERMES variable is not set. Defaulting to a blank string.\n"
    )
    assert extract_unset_vars(stderr) == ["PY", "HERMES"]


def test_lint_stderr_clean_and_dirty():
    assert lint_stderr("no warnings here") == (True, [])
    clean, offending = lint_stderr('The "X" variable is not set.', allow={"Y"})
    assert clean is False and offending == ["X"]
    assert lint_stderr('The "Y" variable is not set.', allow={"Y"}) == (True, [])


# --------------------------------------------------------------------------- #
# main() exit codes                                                             #
# --------------------------------------------------------------------------- #
def test_main_source_scan_red_then_green(tmp_path, capsys):
    bad = tmp_path / "docker-compose.bad.yml"
    bad.write_text("services:\n  s:\n    environment:\n      K: ${UNSET_VAR}\n")
    assert main([str(bad)]) == 1
    assert "UNSET_VAR" in capsys.readouterr().out

    good = tmp_path / "docker-compose.good.yml"
    good.write_text("services:\n  s:\n    environment:\n      K: ${UNSET_VAR:-}\n")
    assert main([str(good)]) == 0


def test_main_from_stderr_red_then_green(tmp_path):
    f = tmp_path / "stderr.txt"
    f.write_text('WARN[0000] The "PY" variable is not set. Defaulting to a blank string.\n')
    assert main(["--from-stderr", str(f)]) == 1
    f.write_text("everything resolved, no warnings\n")
    assert main(["--from-stderr", str(f)]) == 0


def test_main_no_files_is_usage_error(tmp_path):
    # An empty dir → no compose files matched → exit 2 (distinct from a lint failure).
    assert main(["--base", str(tmp_path)]) == 2


def test_lint_files_over_multiple_paths(tmp_path):
    a = tmp_path / "a.yml"
    a.write_text("x: ${A}\n")
    b = tmp_path / "b.yml"
    b.write_text("y: ${B:-ok}\n")
    results = lint_files([str(a), str(b)])
    assert [(Path(p).name, var) for (p, _ln, var, _raw) in results] == [("a.yml", "A")]


# --------------------------------------------------------------------------- #
# LIVE regression guard — the real repo compose files must stay clean          #
# --------------------------------------------------------------------------- #
def test_repo_compose_files_have_no_bare_interpolations():
    files = sorted(str(p) for p in (_REPO / "deploy").glob("docker-compose*.yml"))
    assert files, "expected deploy/docker-compose*.yml to exist"
    results = lint_files(files)
    assert results == [], (
        "bare ${VAR} interpolation(s) in committed compose files — they silently blank when "
        f"unset (the OD-2 $PY trap). Add a :- default or :? loud-fail: {results}"
    )
