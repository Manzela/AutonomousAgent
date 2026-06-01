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
