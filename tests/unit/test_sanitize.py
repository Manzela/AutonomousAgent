"""Unit tests for the sanitization library (HTML, Markdown, and Command Injection)."""

from __future__ import annotations

from lib.guardrails.sanitize import (
    sanitize_html,
    sanitize_markdown,
    sanitize_shell_command,
    sanitize_command_args,
)


def test_sanitize_html():
    assert sanitize_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert sanitize_html("Hello & Welcome") == "Hello &amp; Welcome"
    assert sanitize_html('"quote"') == "&quot;quote&quot;"
    assert sanitize_html("'single'") == "&#x27;single&#x27;"
    assert sanitize_html("") == ""


def test_sanitize_markdown():
    # Escapes raw HTML tags
    assert "lt;iframe" in sanitize_markdown("Click here <iframe src='bad'></iframe>")

    # Allows safe markdown links
    safe_link = "[Google](https://google.com)"
    assert sanitize_markdown(safe_link) == safe_link

    # Allows relative markdown links
    relative_link = "[Readme](./README.md)"
    assert sanitize_markdown(relative_link) == relative_link

    # Neutralizes javascript URLs in markdown links
    unsafe_link = "[Hack](javascript:alert(XSS))"
    assert sanitize_markdown(unsafe_link) == "[Hack](#unsafe-url)"

    # Neutralizes data URLs in markdown links
    unsafe_data_link = "[Data](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)"
    assert sanitize_markdown(unsafe_data_link) == "[Data](#unsafe-url)"


def test_sanitize_shell_command():
    # Escapes dangerous metacharacters
    assert sanitize_shell_command("rm -rf /; echo 'hacked'") == r"rm -rf /\; echo 'hacked'"
    assert (
        sanitize_shell_command("cat file.txt | grep 'secret'") == r"cat file.txt \| grep 'secret'"
    )
    assert sanitize_shell_command("echo `whoami`") == r"echo \`whoami\`"
    assert sanitize_shell_command("echo $(whoami)") == r"echo \$\(whoami\)"
    assert sanitize_shell_command("cmd1 && cmd2") == r"cmd1 \&\& cmd2"
    assert sanitize_shell_command("") == ""


def test_sanitize_command_args():
    args = ["arg1", "arg2;rm -rf", "arg3|grep"]
    sanitized = sanitize_command_args(args)
    assert sanitized[0] == "arg1"
    assert sanitized[1] == r"arg2\;rm -rf"
    assert sanitized[2] == r"arg3\|grep"
