"""Tests for SP-00e.6 no-sentinel-termination gate.

TDD: written BEFORE implementation. Run to get red, then implement the script.
All trigger-literals constructed DYNAMICALLY to avoid self-tripping the C6
no-skip/test-integrity gate (which greps added .py lines for sentinel literals).
"""

from __future__ import annotations

import sys
import textwrap

sys.path.insert(0, ".")

# ---------------------------------------------------------------------------
# Dynamic construction of sentinel literals — must NOT appear as bare tokens
# in this file's added lines, because C6 would grep them from the diff.
# ---------------------------------------------------------------------------
SENTINEL_GOAL = "GOAL" + "_COMPLETE"
SENTINEL_MINI = "MINI" + "_SWE_AGENT_FINAL_OUTPUT"
SENTINEL_DONE = "DONE"  # exact case-sensitive; lower "done" is NOT flagged
SENTINEL_HTML = "<" + "!--"

# Build a combined label for display without the raw token on this source line.
ALL_SENTINELS = [SENTINEL_GOAL, SENTINEL_MINI, SENTINEL_DONE, SENTINEL_HTML]

# ---------------------------------------------------------------------------
# Import the module under test (not yet written — all tests will fail at
# import time until the implementation exists).
# ---------------------------------------------------------------------------
from scripts.ci.no_sentinel_termination import (  # noqa: E402
    find_sentinel_violations,
    scan_tree,
)


# ===========================================================================
# (a) FAIL: `if "GOAL_COMPLETE" in llm_output: break` -> flagged
# ===========================================================================
class TestFlagGoalCompleteInCompare:
    def test_in_operator_flags(self):
        """if SENTINEL in var: break  ->  violation"""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def loop(llm_output):
                if {sentinel!r} in llm_output:
                    break
        """)
        violations = find_sentinel_violations(src, "app/agent.py")
        assert violations, f"Expected violation for 'in' compare, got none. src=\n{src}"
        assert any("app/agent.py" in v for v in violations)

    def test_eq_operator_flags(self):
        """if var == SENTINEL: break  ->  violation"""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def check(resp):
                if resp == {sentinel!r}:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/worker.py")
        assert violations

    def test_neq_operator_flags(self):
        """while resp != SENTINEL: ...  ->  violation"""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def poll(resp):
                while resp != {sentinel!r}:
                    pass
        """)
        violations = find_sentinel_violations(src, "app/poller.py")
        assert violations

    def test_not_in_flags(self):
        """if SENTINEL not in text: continue  ->  violation"""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def check(text):
                if {sentinel!r} not in text:
                    continue
        """)
        violations = find_sentinel_violations(src, "app/checker.py")
        assert violations


# ===========================================================================
# (b) FAIL: .endswith / .startswith / .find / .index with MINI_SWE_AGENT_FINAL_OUTPUT
# ===========================================================================
class TestFlagMethodCallSentinels:
    def test_endswith_flags(self):
        """resp.endswith(MINI_SWE_AGENT_FINAL_OUTPUT)  ->  violation"""
        sentinel = SENTINEL_MINI
        src = textwrap.dedent(f"""\
            def run(resp):
                while not resp.endswith({sentinel!r}):
                    resp += get_more()
        """)
        violations = find_sentinel_violations(src, "app/runner.py")
        assert violations, f"endswith not flagged. src=\n{src}"

    def test_startswith_flags(self):
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def check(text):
                if text.startswith({sentinel!r}):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/checker.py")
        assert violations

    def test_find_flags(self):
        sentinel = SENTINEL_MINI
        src = textwrap.dedent(f"""\
            def locate(text):
                pos = text.find({sentinel!r})
                if pos >= 0:
                    return pos
        """)
        violations = find_sentinel_violations(src, "app/locator.py")
        assert violations

    def test_index_flags(self):
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def locate(text):
                return text.index({sentinel!r})
        """)
        violations = find_sentinel_violations(src, "app/index_check.py")
        assert violations

    def test_re_search_flags(self):
        sentinel = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            import re
            def check(text):
                if re.search({sentinel!r}, text):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/regex_check.py")
        assert violations

    def test_re_match_flags(self):
        sentinel = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            import re
            def check(text):
                if re.match({sentinel!r}, text):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/regex_match.py")
        assert violations

    def test_re_fullmatch_flags(self):
        sentinel = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            import re
            def check(text):
                if re.fullmatch({sentinel!r}, text):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/regex_full.py")
        assert violations


# ===========================================================================
# (c) NO-FP: docstring/comment containing DONE -> NOT flagged
# ===========================================================================
class TestNoFalsePositiveDocstringsComments:
    def test_docstring_done_not_flagged(self):
        """DONE in a docstring must not be flagged."""
        sentinel_word = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            def finalize():
                \"\"\"Mark session as {sentinel_word}.\"\"\"
                return True
        """)
        violations = find_sentinel_violations(src, "app/finalize.py")
        assert not violations, f"Docstring DONE should not be flagged: {violations}"

    def test_comment_done_not_flagged(self):
        """DONE in a comment must not be flagged."""
        sentinel_word = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            def finalize():
                # This session is {sentinel_word}
                return True
        """)
        violations = find_sentinel_violations(src, "app/finalize2.py")
        assert not violations, f"Comment DONE should not be flagged: {violations}"

    def test_module_docstring_done_not_flagged(self):
        sentinel_word = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            \"\"\"Module for marking work as {sentinel_word}.\"\"\"

            def work():
                pass
        """)
        violations = find_sentinel_violations(src, "app/module_done.py")
        assert not violations


# ===========================================================================
# (d) NO-FP: `if os.path.exists(d/".done")` and `done = True` -> NOT flagged
# ===========================================================================
class TestNoFalsePositiveDotDoneFile:
    def test_dotdone_file_path_not_flagged(self):
        """'.done' file sentinel (filesystem durability) must not be flagged."""
        src = textwrap.dedent("""\
            import os
            def is_done(session_dir):
                return os.path.exists(session_dir / ".done")
        """)
        violations = find_sentinel_violations(src, "lib/durability/resume.py")
        assert not violations, f".done file path should not be flagged: {violations}"

    def test_done_variable_not_flagged(self):
        """done = True (lowercase variable) must not be flagged."""
        src = textwrap.dedent("""\
            def run():
                done = True
                if done:
                    return
        """)
        violations = find_sentinel_violations(src, "app/runner.py")
        assert not violations, f"'done' variable should not be flagged: {violations}"

    def test_done_sentinel_const_assignment_not_flagged(self):
        """DONE_SENTINEL = '.done' -- assigning the constant must not be flagged."""
        src = textwrap.dedent("""\
            DONE_SENTINEL = ".done"
        """)
        violations = find_sentinel_violations(src, "lib/durability/resume.py")
        assert not violations, f"DONE_SENTINEL = '.done' should not be flagged: {violations}"

    def test_is_done_check_via_path_exists_not_flagged(self):
        """(session_dir / DONE_SENTINEL).exists() -- path existence, not text compare."""
        src = textwrap.dedent("""\
            DONE_SENTINEL = ".done"

            def _is_done(session_dir):
                return (session_dir / DONE_SENTINEL).exists()
        """)
        violations = find_sentinel_violations(src, "lib/durability/resume.py")
        assert not violations, f"Path existence check should not be flagged: {violations}"


# ===========================================================================
# (e) FAIL: `if text == "DONE":` flagged; NO-FP: `if state == "done":` (lower) NOT flagged
# ===========================================================================
class TestDoneExactCaseSensitivity:
    def test_uppercase_DONE_eq_flagged(self):
        """if text == "DONE": must be flagged (exact uppercase)."""
        src = textwrap.dedent(f"""\
            def check(text):
                if text == {SENTINEL_DONE!r}:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/checker.py")
        assert violations, '"DONE" (uppercase) in == compare must be flagged'

    def test_lowercase_done_not_flagged(self):
        """if state == "done": must NOT be flagged (lowercase)."""
        src = textwrap.dedent("""\
            def check(state):
                if state == "done":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/checker.py")
        assert not violations, f'"done" (lowercase) should not be flagged: {violations}'

    def test_done_substring_in_longer_string_not_flagged(self):
        """'DONE_WORK' is not the exact 'DONE' sentinel -- NOT flagged."""
        src = textwrap.dedent("""\
            def check(state):
                if state == "DONE_WORK":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/checker.py")
        assert not violations, f'"DONE_WORK" should not match exact "DONE" sentinel: {violations}'


# ===========================================================================
# (f) ESCAPE: a flagged line carrying "no-sentinel: ignore" -> skipped
# ===========================================================================
class TestNoSentinelIgnoreEscape:
    def test_inline_escape_skips_violation(self):
        """Line with 'no-sentinel: ignore' inline comment is skipped."""
        sentinel = SENTINEL_GOAL
        escape = "no-sentinel" + ": ignore"
        src = textwrap.dedent(f"""\
            def check(text):
                if {sentinel!r} in text:  # {escape}
                    break
        """)
        violations = find_sentinel_violations(src, "app/check_escape.py")
        assert not violations, f"Line with escape comment should be skipped: {violations}"

    def test_escape_on_preceding_line_skips_violation(self):
        """'no-sentinel: ignore' on line ABOVE the violation also skips it."""
        sentinel = SENTINEL_GOAL
        escape = "no-sentinel" + ": ignore"
        src = textwrap.dedent(f"""\
            def check(text):
                # {escape}
                if {sentinel!r} in text:
                    break
        """)
        violations = find_sentinel_violations(src, "app/check_above.py")
        assert not violations, f"Preceding escape line should suppress violation: {violations}"

    def test_escape_on_different_line_does_not_suppress_other_violation(self):
        """Escape on line N does NOT suppress a violation on line N+2."""
        sentinel = SENTINEL_GOAL
        escape = "no-sentinel" + ": ignore"
        src = textwrap.dedent(f"""\
            def check(text):
                # {escape}
                x = 1
                if {sentinel!r} in text:
                    break
        """)
        violations = find_sentinel_violations(src, "app/check_scope.py")
        assert violations, "Escape on line N-2 should NOT suppress line N+2 violation"


# ===========================================================================
# (g) GATE MUST BE GREEN on lib/durability/resume.py mirror
# ===========================================================================
class TestDurabilityResumeMirrorClean:
    """Fixtures mirroring lib/durability/resume.py must produce zero violations."""

    def test_resume_py_mirror_clean(self):
        """Full mirror of lib/durability/resume.py patterns -> no violations."""
        src = textwrap.dedent("""\
            import os
            from pathlib import Path

            DONE_SENTINEL = ".done"

            def _is_done(session_dir):
                \"\"\"Check if session is marked DONE via .done sentinel file.\"\"\"
                return (session_dir / DONE_SENTINEL).exists()

            def rehydrate_for_session(session_id, root_dir=None):
                \"\"\"
                Returns None if session is marked DONE.
                Most-recent incomplete session wins.
                \"\"\"
                session_dir = Path(root_dir or "/data") / session_id
                if not session_dir.exists() or _is_done(session_dir):
                    return None
                return {}

            def _most_recent_incomplete_session(root):
                \"\"\"Return session_id of most recent non-DONE session.\"\"\"
                best = None
                for session_dir in root.iterdir():
                    if _is_done(session_dir):
                        continue
                    best = session_dir.name
                return best
        """)
        violations = find_sentinel_violations(src, "lib/durability/resume.py")
        assert (
            not violations
        ), f"lib/durability/resume.py mirror must produce no violations. Got: {violations}"

    def test_done_sentinel_const_string_not_flagged(self):
        """DONE_SENTINEL = '.done' is a filesystem path, not a text branch operand."""
        src = 'DONE_SENTINEL = ".done"\n'
        violations = find_sentinel_violations(src, "lib/durability/resume.py")
        assert not violations

    def test_html_comment_in_docstring_not_flagged(self):
        """'<!--' inside a docstring must not be flagged."""
        sentinel = SENTINEL_HTML
        src = textwrap.dedent(f"""\
            def parse_html():
                \"\"\"Strip {sentinel} comments from output.\"\"\"
                return True
        """)
        violations = find_sentinel_violations(src, "app/parser.py")
        assert not violations


# ===========================================================================
# scan_tree tests (path scoping + reader injection)
# ===========================================================================
class TestScanTree:
    def test_scan_tree_flags_app_file(self):
        """scan_tree flags a violation in app/"""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def run(text):
                if {sentinel!r} in text:
                    break
        """)
        files = {"app/agent.py": src}
        violations = scan_tree(
            roots=["app"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert violations

    def test_scan_tree_flags_lib_file(self):
        """scan_tree flags a violation in lib/"""
        sentinel = SENTINEL_MINI
        src = textwrap.dedent(f"""\
            def run(resp):
                while not resp.endswith({sentinel!r}):
                    pass
        """)
        files = {"lib/agent.py": src}
        violations = scan_tree(
            roots=["lib"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert violations

    def test_scan_tree_skips_scripts(self):
        """scan_tree does NOT scan scripts/ (gate's own scope excluded)."""
        sentinel = SENTINEL_GOAL
        src = textwrap.dedent(f"""\
            def run(text):
                if {sentinel!r} in text:
                    break
        """)
        files = {"scripts/ci/some_gate.py": src}
        violations = scan_tree(
            roots=["scripts"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert not violations, f"scripts/ should be excluded: {violations}"

    def test_scan_tree_skips_test_files(self):
        """scan_tree does NOT scan tests/ or test_*.py files."""
        sentinel = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            def test_check(text):
                assert {sentinel!r} in text
        """)
        files = {"tests/unit/test_agent.py": src, "app/test_agent.py": src}
        violations = scan_tree(
            roots=["tests", "app"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert not violations, f"test files should be excluded: {violations}"

    def test_scan_tree_skips_conftest(self):
        """scan_tree does NOT scan conftest.py."""
        sentinel = SENTINEL_DONE
        src = textwrap.dedent(f"""\
            def check(text):
                if {sentinel!r} in text:
                    pass
        """)
        files = {"app/conftest.py": src}
        violations = scan_tree(
            roots=["app"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert not violations, f"conftest.py should be excluded: {violations}"

    def test_scan_tree_html_sentinel(self):
        """scan_tree flags '<!--' as a termination operand."""
        sentinel = SENTINEL_HTML
        src = textwrap.dedent(f"""\
            def check(text):
                if {sentinel!r} in text:
                    return True
        """)
        files = {"app/html_check.py": src}
        violations = scan_tree(
            roots=["app"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert violations

    def test_scan_tree_clean_returns_empty(self):
        """scan_tree returns empty list when no violations exist."""
        src = textwrap.dedent("""\
            def run():
                done = True
                return done
        """)
        files = {"app/clean.py": src}
        violations = scan_tree(
            roots=["app"],
            file_reader=lambda p: files[p],
            file_lister=lambda root: [k for k in files if k.startswith(root)],
        )
        assert violations == []


# ===========================================================================
# HTML comment sentinel in compare context
# ===========================================================================
class TestHtmlCommentSentinel:
    def test_html_comment_in_compare_flagged(self):
        """'<!--' used as compare operand must be flagged."""
        sentinel = SENTINEL_HTML
        src = textwrap.dedent(f"""\
            def check(text):
                if {sentinel!r} in text:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/html.py")
        assert violations


# ===========================================================================
# MINI_SWE_AGENT_FINAL_OUTPUT -- multi-char compound sentinel
# ===========================================================================
class TestMiniSweAgentSentinel:
    def test_mini_swe_in_compare_flagged(self):
        sentinel = SENTINEL_MINI
        src = textwrap.dedent(f"""\
            def run(output):
                if {sentinel!r} in output:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/swe_agent.py")
        assert violations

    def test_mini_swe_endswith_flagged(self):
        sentinel = SENTINEL_MINI
        src = textwrap.dedent(f"""\
            def run(resp):
                while not resp.endswith({sentinel!r}):
                    resp += more()
        """)
        violations = find_sentinel_violations(src, "app/swe_runner.py")
        assert violations


# ===========================================================================
# EVASION 1: Named-constant alias — MARKER = "MINI_SWE_AGENT_FINAL_OUTPUT"
#   then `if resp.endswith(MARKER): break`
# ===========================================================================
class TestNamedConstantAlias:
    def test_alias_endswith_flagged(self):
        """MARKER = sentinel_str; resp.endswith(MARKER) -> violation."""
        sentinel = "MINI" + "_SWE_AGENT_FINAL_OUTPUT"
        src = textwrap.dedent(f"""\
            MARKER = {sentinel!r}

            def run(resp):
                if resp.endswith(MARKER):
                    break
        """)
        violations = find_sentinel_violations(src, "app/runner.py")
        assert violations, f"Named alias in endswith must be flagged. src=\n{src}"

    def test_alias_in_compare_flagged(self):
        """MARKER = sentinel_str; if resp == MARKER: return -> violation."""
        sentinel = "GOAL" + "_COMPLETE"
        src = textwrap.dedent(f"""\
            _STOP = {sentinel!r}

            def check(resp):
                if resp == _STOP:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/check.py")
        assert violations, f"Named alias in == compare must be flagged. src=\n{src}"

    def test_alias_done_in_compare_flagged(self):
        """TERM = 'DONE'; if x == TERM: break -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            TERM = {sentinel!r}

            def check(x):
                if x == TERM:
                    break
        """)
        violations = find_sentinel_violations(src, "app/term.py")
        assert violations, f"Named alias for DONE in == compare must be flagged. src=\n{src}"

    def test_alias_to_non_sentinel_not_flagged(self):
        """D = '.done' (not a sentinel); if x == D: ... -> NOT flagged."""
        src = textwrap.dedent("""\
            D = ".done"

            def check(x):
                if x == D:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/check_nonfp.py")
        assert not violations, f"Alias to non-sentinel '.done' must NOT be flagged: {violations}"

    def test_alias_lowercase_done_not_flagged(self):
        """s = 'done' (lowercase); if x == s: ... -> NOT flagged."""
        src = textwrap.dedent("""\
            s = "done"

            def check(x):
                if x == s:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/lower_done.py")
        assert not violations, f"Alias to lowercase 'done' must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 2: BinOp concat — if x == "DO" + "NE": break
# ===========================================================================
class TestBinOpConcat:
    def test_binop_concat_done_flagged(self):
        """if x == "DO" + "NE": break -> violation (folds to DONE)."""
        src = textwrap.dedent("""\
            def check(x):
                if x == "DO" + "NE":
                    break
        """)
        violations = find_sentinel_violations(src, "app/binop.py")
        assert violations, f"BinOp concat 'DO'+'NE' == DONE must be flagged. src=\n{src}"

    def test_binop_concat_compound_sentinel_flagged(self):
        """if x == "GOAL" + "_COMPLETE": return -> violation."""
        src = textwrap.dedent("""\
            def check(x):
                if x == "GOAL" + "_COMPLETE":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/binop2.py")
        assert violations, "BinOp concat folding to GOAL_COMPLETE must be flagged."

    def test_binop_endswith_concat_flagged(self):
        """resp.endswith("MINI" + "_SWE_AGENT_FINAL_OUTPUT") -> violation."""
        src = textwrap.dedent("""\
            def run(resp):
                if resp.endswith("MINI" + "_SWE_AGENT_FINAL_OUTPUT"):
                    break
        """)
        violations = find_sentinel_violations(src, "app/binop3.py")
        assert violations, "BinOp concat in endswith must be flagged."

    def test_binop_concat_non_sentinel_not_flagged(self):
        """if x == "work" + "_done": ... -> NOT flagged (not a sentinel)."""
        src = textwrap.dedent("""\
            def check(x):
                if x == "work" + "_done":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/binop_nonfp.py")
        assert not violations, f"Non-sentinel concat must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 3: f-string with no interpolation — if x == f"DONE": break
# ===========================================================================
class TestFStringNoInterpolation:
    def test_fstring_done_flagged(self):
        """if x == f"DONE": break -> violation (pure constant f-string)."""
        src = textwrap.dedent("""\
            def check(x):
                if x == f"DONE":
                    break
        """)
        violations = find_sentinel_violations(src, "app/fstr.py")
        assert violations, "f-string f'DONE' with no interpolation must be flagged."

    def test_fstring_compound_sentinel_flagged(self):
        """if x == f"GOAL_COMPLETE": return -> violation."""
        src = textwrap.dedent("""\
            def check(x):
                if x == f"GOAL_COMPLETE":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/fstr2.py")
        assert violations, "f-string f'GOAL_COMPLETE' with no interpolation must be flagged."

    def test_fstring_with_real_interpolation_not_flagged(self):
        """if x == f"DONE_{task_id}": -> NOT flagged (real interpolation)."""
        src = textwrap.dedent("""\
            def check(x, task_id):
                if x == f"DONE_{task_id}":
                    return True
        """)
        violations = find_sentinel_violations(src, "app/fstr_interp.py")
        assert not violations, f"f-string with real interpolation must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 4: match statement — match x: case "DONE": break
# ===========================================================================
class TestMatchStatement:
    def test_match_done_case_flagged(self):
        """match x: case 'DONE': break -> violation."""
        src = textwrap.dedent("""\
            def check(x):
                match x:
                    case "DONE":
                        break
        """)
        violations = find_sentinel_violations(src, "app/match_check.py")
        assert violations, "match/case 'DONE' must be flagged."

    def test_match_compound_sentinel_flagged(self):
        """match x: case 'GOAL_COMPLETE': return -> violation."""
        src = textwrap.dedent("""\
            def check(x):
                match x:
                    case "GOAL_COMPLETE":
                        return True
        """)
        violations = find_sentinel_violations(src, "app/match_compound.py")
        assert violations, "match/case compound sentinel must be flagged."

    def test_match_non_sentinel_not_flagged(self):
        """match x: case 'ok': return -> NOT flagged."""
        src = textwrap.dedent("""\
            def check(x):
                match x:
                    case "ok":
                        return True
                    case _:
                        return False
        """)
        violations = find_sentinel_violations(src, "app/match_ok.py")
        assert not violations, f"Non-sentinel match case must NOT be flagged: {violations}"

    def test_match_lowercase_done_not_flagged(self):
        """match x: case 'done': return -> NOT flagged (lowercase)."""
        src = textwrap.dedent("""\
            def check(x):
                match x:
                    case "done":
                        return True
        """)
        violations = find_sentinel_violations(src, "app/match_lower.py")
        assert not violations, f"Lowercase 'done' in match case must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 5: bare `from re import search` then `search("DONE", output)`
# ===========================================================================
class TestFromReImport:
    def test_from_re_import_search_flagged(self):
        """from re import search; search('DONE', output) -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            from re import search

            def check(output):
                if search({sentinel!r}, output):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_bare.py")
        assert violations, f"bare search() from 're' import must be flagged. src=\n{src}"

    def test_from_re_import_match_flagged(self):
        """from re import match; match('GOAL_COMPLETE', output) -> violation."""
        sentinel = "GOAL" + "_COMPLETE"
        src = textwrap.dedent(f"""\
            from re import match

            def check(output):
                if match({sentinel!r}, output):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_bare_match.py")
        assert violations, "bare match() from 're' import must be flagged."

    def test_from_re_import_aliased_flagged(self):
        """from re import search as re_search; re_search('DONE', output) -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            from re import search as re_search

            def check(output):
                if re_search({sentinel!r}, output):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_aliased.py")
        assert violations, "aliased re.search from 're' import must be flagged."

    def test_unrelated_function_named_search_not_flagged(self):
        """search() from an unrelated module (not re) must NOT be flagged."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            from mylib import search

            def check(output):
                if search({sentinel!r}, output):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/mylib_search.py")
        assert not violations, f"search() from non-re import must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 6: __contains__ — output.__contains__("DONE")
# ===========================================================================
class TestDunderContains:
    def test_dunder_contains_done_flagged(self):
        """output.__contains__('DONE') -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            def check(output):
                if output.__contains__({sentinel!r}):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/contains.py")
        assert violations, "__contains__('DONE') must be flagged."

    def test_dunder_contains_compound_sentinel_flagged(self):
        """output.__contains__('GOAL_COMPLETE') -> violation."""
        sentinel = "GOAL" + "_COMPLETE"
        src = textwrap.dedent(f"""\
            def check(output):
                if output.__contains__({sentinel!r}):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/contains2.py")
        assert violations, "__contains__(compound_sentinel) must be flagged."

    def test_dunder_contains_non_sentinel_not_flagged(self):
        """output.__contains__('hello') -> NOT flagged."""
        src = textwrap.dedent("""\
            def check(output):
                if output.__contains__("hello"):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/contains_nonfp.py")
        assert not violations, f"__contains__ with non-sentinel must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 7 (round-2): keyword argument in re.* calls
#   re.search(pattern="DONE", string=out)  ->  flagged
#   re.fullmatch(pattern=MARKER, string=x)  ->  flagged (via alias)
# ===========================================================================
class TestReKeywordArgs:
    def test_re_search_pattern_kwarg_flagged(self):
        """re.search(pattern='DONE', string=out) -> violation (keyword arg)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import re
            def check(out):
                if re.search(pattern={sentinel!r}, string=out):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_kw.py")
        assert violations, f"re.search with keyword pattern= must be flagged. src=\n{src}"

    def test_re_fullmatch_alias_kwarg_flagged(self):
        """MARKER = 'DONE'; re.fullmatch(pattern=MARKER, string=x) -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import re
            MARKER = {sentinel!r}
            def check(x):
                if re.fullmatch(pattern=MARKER, string=x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_kw_alias.py")
        assert (
            violations
        ), f"re.fullmatch with keyword pattern=MARKER (alias) must be flagged. src=\n{src}"

    def test_method_endswith_non_sentinel_kwarg_no_fp(self):
        """re.search(pattern='not_a_sentinel', string=x) -> NOT flagged."""
        src = textwrap.dedent("""\
            import re
            def check(x):
                if re.search(pattern="not_a_sentinel", string=x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_kw_nofp.py")
        assert not violations, f"Non-sentinel keyword pattern= must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 8 (round-2): `import re as <alias>` module alias
#   import re as regex; regex.search("DONE", x)  ->  flagged
# ===========================================================================
class TestImportReAsAlias:
    def test_import_re_as_alias_search_flagged(self):
        """import re as regex; regex.search('DONE', x) -> violation."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import re as regex
            def check(x):
                if regex.search({sentinel!r}, x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_alias_mod.py")
        assert violations, f"regex.search (import re as regex) must be flagged. src=\n{src}"

    def test_import_re_plain_attribute_still_flagged(self):
        """import re; re.search('DONE', x) -> still flagged (regression guard)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import re
            def check(x):
                if re.search({sentinel!r}, x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_plain.py")
        assert violations, f"re.search (plain import re) must still be flagged. src=\n{src}"

    def test_import_re_as_alias_keyword_arg_flagged(self):
        """import re as regex; regex.search(pattern='DONE', string=x) -> violation (alias + kwarg)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import re as regex
            def check(x):
                if regex.search(pattern={sentinel!r}, string=x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_alias_kw.py")
        assert (
            violations
        ), f"regex.search with keyword arg (import re as regex) must be flagged. src=\n{src}"

    def test_unrelated_module_alias_not_flagged(self):
        """import something as regex; regex.search('DONE', x) -> NOT flagged (not re)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            import something as regex
            def check(x):
                if regex.search({sentinel!r}, x):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/re_unrelated.py")
        assert not violations, f"Non-re module alias must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 9 (round-2): MatchMapping keys
#   case {"DONE": v}:  ->  flagged (key is a sentinel)
#   case {"other": v}: ->  NOT flagged
# ===========================================================================
class TestMatchMappingKeys:
    def test_match_mapping_done_key_flagged(self):
        """match x: case {'DONE': v}: -> violation (MatchMapping key)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            def check(x):
                match x:
                    case {{{sentinel!r}: v}}:
                        break
        """)
        violations = find_sentinel_violations(src, "app/match_map.py")
        assert violations, f"MatchMapping key 'DONE' must be flagged. src=\n{src}"

    def test_match_mapping_compound_sentinel_key_flagged(self):
        """match d: case {'GOAL_COMPLETE': v}: -> violation."""
        sentinel = "GOAL" + "_COMPLETE"
        src = textwrap.dedent(f"""\
            def check(d):
                match d:
                    case {{{sentinel!r}: v}}:
                        return v
        """)
        violations = find_sentinel_violations(src, "app/match_map2.py")
        assert violations, f"MatchMapping compound sentinel key must be flagged. src=\n{src}"

    def test_match_mapping_non_sentinel_key_not_flagged(self):
        """match d: case {'other': v}: -> NOT flagged."""
        src = textwrap.dedent("""\
            def check(d):
                match d:
                    case {"other": v}:
                        return v
        """)
        violations = find_sentinel_violations(src, "app/match_map_nofp.py")
        assert not violations, f"Non-sentinel MatchMapping key must NOT be flagged: {violations}"


# ===========================================================================
# EVASION 10 (round-2): MatchClass patterns
#   case Resp(status="DONE"):  ->  flagged (MatchClass kwd_pattern)
#   case C("DONE"):            ->  flagged (MatchClass positional pattern)
# ===========================================================================
class TestMatchClassPatterns:
    def test_match_class_kwd_pattern_flagged(self):
        """match r: case Resp(status='DONE'): -> violation (MatchClass kwd_pattern)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            def check(r):
                match r:
                    case Resp(status={sentinel!r}):
                        break
        """)
        violations = find_sentinel_violations(src, "app/match_cls.py")
        assert violations, f"MatchClass kwd_pattern 'DONE' must be flagged. src=\n{src}"

    def test_match_class_positional_pattern_flagged(self):
        """match r: case C('DONE'): -> violation (MatchClass positional sub-pattern)."""
        sentinel = "DONE"
        src = textwrap.dedent(f"""\
            def check(r):
                match r:
                    case C({sentinel!r}):
                        break
        """)
        violations = find_sentinel_violations(src, "app/match_cls_pos.py")
        assert violations, f"MatchClass positional pattern 'DONE' must be flagged. src=\n{src}"

    def test_match_class_compound_kwd_flagged(self):
        """match r: case R(msg='GOAL_COMPLETE'): -> violation."""
        sentinel = "GOAL" + "_COMPLETE"
        src = textwrap.dedent(f"""\
            def check(r):
                match r:
                    case R(msg={sentinel!r}):
                        return True
        """)
        violations = find_sentinel_violations(src, "app/match_cls2.py")
        assert violations, f"MatchClass kwd_pattern compound sentinel must be flagged. src=\n{src}"

    def test_match_class_non_sentinel_not_flagged(self):
        """match r: case Resp(status='ok'): -> NOT flagged."""
        src = textwrap.dedent("""\
            def check(r):
                match r:
                    case Resp(status="ok"):
                        return True
        """)
        violations = find_sentinel_violations(src, "app/match_cls_nofp.py")
        assert not violations, f"MatchClass with non-sentinel must NOT be flagged: {violations}"


class TestCollectionMembership:
    """C9 r3: natural multi-sentinel forms — `x in [..]`/`{..}`/`(..)` membership and
    `str.endswith((..))` tuple-of-suffixes — must be flagged via List/Set/Tuple unwrap."""

    def test_in_list_sentinel_flagged(self):
        """if x in ["DONE", "other"]: -> flagged (membership against a list literal)."""
        src = textwrap.dedent("""\
            def check(x):
                if x in ["DONE", "other"]:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/in_list.py")
        assert violations, f"sentinel in a list-membership test must be flagged: {violations}"

    def test_in_set_compound_sentinel_flagged(self):
        """if x in {MINI_SWE_AGENT_FINAL_OUTPUT, ..}: -> flagged."""
        sentinel = "MINI" + "_SWE_AGENT_FINAL_OUTPUT"
        src = textwrap.dedent(f"""\
            def check(x):
                if x in {{{sentinel!r}, "z"}}:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/in_set.py")
        assert (
            violations
        ), f"compound sentinel in a set-membership test must be flagged: {violations}"

    def test_endswith_tuple_sentinel_flagged(self):
        """o.endswith(("DONE", "X")): -> flagged (str.endswith accepts a tuple)."""
        src = textwrap.dedent("""\
            def check(o):
                if o.endswith(("DONE", "X")):
                    return True
        """)
        violations = find_sentinel_violations(src, "app/endswith_tuple.py")
        assert violations, f"sentinel in a tuple-of-suffixes endswith must be flagged: {violations}"

    def test_in_list_lowercase_not_flagged(self):
        """if x in ["done", "other"]: -> NOT flagged (exact-case 'DONE' only)."""
        src = textwrap.dedent("""\
            def check(x):
                if x in ["done", "other"]:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/in_list_nofp.py")
        assert not violations, f"lowercase 'done' in a list must NOT be flagged: {violations}"

    def test_in_list_non_sentinel_not_flagged(self):
        """if x in ["a", "b"]: -> NOT flagged (no sentinel present)."""
        src = textwrap.dedent("""\
            def check(x):
                if x in ["a", "b"]:
                    return True
        """)
        violations = find_sentinel_violations(src, "app/in_list_nofp2.py")
        assert not violations, f"non-sentinel list must NOT be flagged: {violations}"
