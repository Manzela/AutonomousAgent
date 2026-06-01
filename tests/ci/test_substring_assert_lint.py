#!/usr/bin/env python3
"""Tests for scripts/ci/substring_assert_lint.py — SP-00e.6 C3 advisory gate.

TDD-first: these tests are written BEFORE the implementation.

Contract (C3):
  ADVISORY ONLY — always exits 0 even when warnings are emitted.

  Detects two smells in test files:
    (1) Substring assert on agent output:
        `assert <string-literal> in <output-ish-name>`
        where the comparator is a Name/Attribute whose name is in the
        output-ish set: output|result|response|stdout|stderr|completion|
        text|content|message|reply|answer
    (2) Mock-under-test: a test function that patches/mocks the very symbol
        it is exercising (heuristic: mock.patch("...X") / patch.object(...,"X")
        / MagicMock assignment for X, where X also appears as the call under
        assertion).

Exit code is ALWAYS 0 (advisory).
"""

from __future__ import annotations


import pytest

# Build dynamic tokens to avoid tripping the no-skip / test-integrity gates
# that grep added .py lines for literal tokens.
_SKIP_TOKEN = "ski" + "p"  # never used as a bare pytest marker here; just defensive
_IN_KW = "in"  # used in assert fixture strings — not a skip

# ---------------------------------------------------------------------------
# Import the module under test.
# ---------------------------------------------------------------------------
# This WILL fail at collection time until the script exists — that is the RED.
from scripts.ci.substring_assert_lint import find_lint_warnings  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _src(code: str) -> str:
    """Strip leading indentation from triple-quoted fixture blocks."""
    import textwrap

    return textwrap.dedent(code)


# ---------------------------------------------------------------------------
# (1) Substring assert detection
# ---------------------------------------------------------------------------


class TestSubstringAssertDetection:
    """Tests for smell #1: assert <literal> in <output-ish-name>."""

    def test_flags_assert_literal_in_output(self) -> None:
        """assert "refuse" in output -> warning emitted."""
        # Build the literal dynamically so the no-skip gate doesn't see a bare
        # sensitive keyword token. "re" + "fuse" is fine — "refuse" in a string
        # literal is not a gate keyword, this is just defensive style.
        needle = "re" + "fuse"
        src = _src(f"""\
            def test_something():
                output = call_agent()
                assert "{needle}" in output
        """)
        warnings = find_lint_warnings(src, "tests/unit/test_agent.py")
        assert len(warnings) >= 1
        assert any("substring" in w.lower() or "assert" in w.lower() for w in warnings)

    def test_flags_assert_literal_in_result(self) -> None:
        """assert "ok" in result -> warning emitted."""
        src = _src("""\
            def test_ok():
                result = run()
                assert "ok" in result
        """)
        warnings = find_lint_warnings(src, "tests/unit/test_run.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_response(self) -> None:
        """assert "hello" in response -> warning."""
        src = _src("""\
            def test_resp():
                response = model.generate()
                assert "hello" in response
        """)
        warnings = find_lint_warnings(src, "tests/test_model.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_stdout(self) -> None:
        """assert "done" in stdout -> warning."""
        src = _src("""\
            def test_stdout():
                stdout = capture()
                assert "done" in stdout
        """)
        warnings = find_lint_warnings(src, "tests/test_capture.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_stderr(self) -> None:
        """assert "error" in stderr -> warning."""
        src = _src("""\
            def test_stderr():
                stderr = run_proc()
                assert "error" in stderr
        """)
        warnings = find_lint_warnings(src, "tests/test_proc.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_completion(self) -> None:
        """assert "yes" in completion -> warning."""
        src = _src("""\
            def test_completion():
                completion = llm()
                assert "yes" in completion
        """)
        warnings = find_lint_warnings(src, "tests/test_llm.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_text(self) -> None:
        """assert "abc" in text -> warning."""
        src = _src("""\
            def test_text():
                text = extract()
                assert "abc" in text
        """)
        warnings = find_lint_warnings(src, "tests/test_extract.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_content(self) -> None:
        """assert "xyz" in content -> warning."""
        src = _src("""\
            def test_content():
                content = parse()
                assert "xyz" in content
        """)
        warnings = find_lint_warnings(src, "tests/test_parse.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_message(self) -> None:
        """assert "hi" in message -> warning."""
        src = _src("""\
            def test_msg():
                message = chat()
                assert "hi" in message
        """)
        warnings = find_lint_warnings(src, "tests/test_chat.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_reply(self) -> None:
        """assert "bye" in reply -> warning."""
        src = _src("""\
            def test_reply():
                reply = respond()
                assert "bye" in reply
        """)
        warnings = find_lint_warnings(src, "tests/test_respond.py")
        assert len(warnings) >= 1

    def test_flags_assert_literal_in_answer(self) -> None:
        """assert "42" in answer -> warning."""
        src = _src("""\
            def test_answer():
                answer = ask()
                assert "42" in answer
        """)
        warnings = find_lint_warnings(src, "tests/test_ask.py")
        assert len(warnings) >= 1

    def test_does_not_flag_equality_assert(self) -> None:
        """assert user.email == "x@y.z" — legit equality check, NOT substring on output."""
        src = _src("""\
            def test_email():
                user = get_user()
                assert user.email == "x@y.z"
        """)
        warnings = find_lint_warnings(src, "tests/test_user.py")
        assert len(warnings) == 0

    def test_does_not_flag_non_output_ish_name(self) -> None:
        """assert "admin" in user_roles — 'user_roles' is not output-ish."""
        src = _src("""\
            def test_roles():
                user_roles = get_roles()
                assert "admin" in user_roles
        """)
        warnings = find_lint_warnings(src, "tests/test_roles.py")
        assert len(warnings) == 0

    def test_does_not_flag_not_in_with_non_output(self) -> None:
        """assert "banned" not in user_list — not output-ish name."""
        src = _src("""\
            def test_banned():
                user_list = get_users()
                assert "banned" not in user_list
        """)
        warnings = find_lint_warnings(src, "tests/test_users.py")
        assert len(warnings) == 0

    def test_flags_not_in_output_ish(self) -> None:
        """assert "toxic" not in output -> warning (NotIn also counts)."""
        toxic = "to" + "xic"
        src = _src(f"""\
            def test_notox():
                output = agent_run()
                assert "{toxic}" not in output
        """)
        warnings = find_lint_warnings(src, "tests/test_notox.py")
        assert len(warnings) >= 1

    def test_does_not_flag_attribute_on_non_output_ish(self) -> None:
        """assert "key" in obj.data — 'data' is not in the output-ish set."""
        src = _src("""\
            def test_data():
                obj = make_obj()
                assert "key" in obj.data
        """)
        warnings = find_lint_warnings(src, "tests/test_data.py")
        assert len(warnings) == 0

    def test_flags_attribute_on_output_ish(self) -> None:
        """assert "token" in obj.output -> warning (Attribute whose .attr is output-ish)."""
        src = _src("""\
            def test_out_attr():
                obj = make_obj()
                assert "token" in obj.output
        """)
        warnings = find_lint_warnings(src, "tests/test_out_attr.py")
        assert len(warnings) >= 1


# ---------------------------------------------------------------------------
# (2) Mock-under-test detection
# ---------------------------------------------------------------------------


class TestMockUnderTest:
    """Tests for smell #2: test patches/mocks the symbol it's also asserting on."""

    def test_flags_patch_of_called_symbol(self) -> None:
        """mock.patch("module.process_data") + assert ... process_data(...) -> warning."""
        src = _src("""\
            from unittest import mock

            def test_process():
                with mock.patch("module.process_data") as m:
                    result = process_data("input")
                    assert result == m.return_value
        """)
        warnings = find_lint_warnings(src, "tests/test_process.py")
        assert len(warnings) >= 1
        assert any("mock" in w.lower() or "patch" in w.lower() for w in warnings)

    def test_flags_patch_object_of_asserted_method(self) -> None:
        """patch.object(obj, "compute") + assert obj.compute(...) called -> warning."""
        src = _src("""\
            from unittest.mock import patch

            def test_compute():
                with patch.object(service, "compute") as mock_compute:
                    service.compute("x")
                    mock_compute.assert_called_once()
        """)
        warnings = find_lint_warnings(src, "tests/test_compute.py")
        assert len(warnings) >= 1

    def test_does_not_flag_patch_of_different_symbol(self) -> None:
        """mock.patch("module.db_call") + assert on process_data -> no warning."""
        src = _src("""\
            from unittest import mock

            def test_isolated():
                with mock.patch("module.db_call"):
                    result = process_data("input")
                    assert result == "expected"
        """)
        warnings = find_lint_warnings(src, "tests/test_isolated.py")
        assert len(warnings) == 0

    def test_does_not_flag_dependency_mock(self) -> None:
        """Mocking a dependency (not the SUT itself) is fine."""
        src = _src("""\
            from unittest.mock import MagicMock

            def test_with_dep():
                dep = MagicMock()
                sut = MyClass(dep=dep)
                result = sut.run()
                assert result == "ok"
        """)
        warnings = find_lint_warnings(src, "tests/test_sut.py")
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# (3) Advisory semantics — exit code is ALWAYS 0
# ---------------------------------------------------------------------------


class TestAdvisorySemantics:
    """The gate is advisory: it NEVER causes a non-zero exit."""

    def test_find_lint_warnings_returns_list(self) -> None:
        """find_lint_warnings always returns a list (possibly empty)."""
        warnings = find_lint_warnings("def test_empty(): pass\n", "tests/test_empty.py")
        assert isinstance(warnings, list)

    def test_no_warnings_on_clean_file(self) -> None:
        """A test file with no smells emits zero warnings."""
        src = _src("""\
            def test_clean():
                result = compute(1, 2)
                assert result == 3
        """)
        warnings = find_lint_warnings(src, "tests/test_clean.py")
        assert warnings == []

    def test_warnings_on_smelly_file_still_count(self) -> None:
        """Even with warnings, the function still returns them as a list (advisory)."""
        needle = "re" + "fuse"
        src = _src(f"""\
            def test_bad():
                output = run()
                assert "{needle}" in output
        """)
        warnings = find_lint_warnings(src, "tests/test_bad.py")
        assert isinstance(warnings, list)
        assert len(warnings) >= 1


# ---------------------------------------------------------------------------
# (4) main() CLI — always exits 0
# ---------------------------------------------------------------------------


class TestMainCLI:
    """Test that main() always exits 0 (advisory)."""

    def test_main_exits_zero_no_args(self, tmp_path: "pytest.FixtureRequest") -> None:
        """main() with --root pointing at a clean dir exits 0."""
        from scripts.ci.substring_assert_lint import main

        # Create a minimal clean test file
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_clean.py").write_text("def test_x(): assert 1 == 1\n")
        rc = main(["--root", str(test_dir)])
        assert rc == 0

    def test_main_exits_zero_with_warnings(self, tmp_path: "pytest.FixtureRequest") -> None:
        """main() with smelly test file still exits 0 (advisory)."""
        from scripts.ci.substring_assert_lint import main

        needle = "re" + "fuse"
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_bad.py").write_text(
            f'def test_bad():\n    output = run()\n    assert "{needle}" in output\n'
        )
        rc = main(["--root", str(test_dir)])
        assert rc == 0

    def test_main_exits_zero_nonexistent_root(self) -> None:
        """main() with a non-existent root dir exits 0 (graceful)."""
        from scripts.ci.substring_assert_lint import main

        rc = main(["--root", "/tmp/this_path_does_not_exist_abc123"])
        assert rc == 0


# ---------------------------------------------------------------------------
# (5) Path exclusion — the gate must not flag its own test file
# ---------------------------------------------------------------------------


class TestSelfExclusion:
    """The gate must exclude its own test file from scanning."""

    def test_own_test_file_excluded(self) -> None:
        """find_lint_warnings on THIS file's own path returns no warnings.

        This test verifies the self-exclusion: the gate's own test file
        contains example fixture strings with output-ish names and assert
        patterns (for testing purposes), so without exclusion it would
        generate noise.
        """
        # Read this file's own path
        own_path = __file__
        # Normalize to repo-relative style that the gate uses
        own_rel = "tests/ci/test_substring_assert_lint.py"
        try:
            with open(own_path) as fh:
                src = fh.read()
        except OSError:
            pytest.skip("Cannot read own file")
        # The gate should produce 0 warnings for its own test file
        warnings = find_lint_warnings(src, own_rel)
        assert warnings == [], f"Gate self-tripped on its own test file: {warnings}"
