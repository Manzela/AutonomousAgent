"""Tests for the LiteLLM scrubber callback wrapper.

Verifies the lib.scrubber_callback module wires lib/scrubber.py as a LiteLLM
CustomLogger correctly: it loads the patterns file, exposes a module-level
`proxy_handler_instance` of the right type, redacts secrets in both the
sync and async hooks, and appends one JSON line per hit to the leak log
(without ever writing the original secret value).

Audit reference: phase1-to-phase2-readiness-2026-05-19/security-audit.md C1.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS = REPO_ROOT / "config" / "scrubber-patterns.yaml"


@pytest.fixture
def callback_module(tmp_path, monkeypatch):
    """Reload the callback module with the test patterns + leak-log paths."""
    leak_log = tmp_path / "leak.log"
    monkeypatch.setenv("SCRUBBER_PATTERNS_PATH", str(PATTERNS))
    monkeypatch.setenv("SCRUBBER_LEAK_LOG_PATH", str(leak_log))
    sys.path.insert(0, str(REPO_ROOT))
    if "lib.scrubber_callback" in sys.modules:
        del sys.modules["lib.scrubber_callback"]
    module = importlib.import_module("lib.scrubber_callback")
    yield module, leak_log
    # cleanup: drop the cached module so subsequent tests get fresh env
    if "lib.scrubber_callback" in sys.modules:
        del sys.modules["lib.scrubber_callback"]


def test_module_exports_proxy_handler_instance(callback_module):
    """LiteLLM's get_instance_fn requires `proxy_handler_instance` on the module."""
    module, _ = callback_module
    assert hasattr(module, "proxy_handler_instance")
    assert module.proxy_handler_instance is not None


def test_proxy_handler_is_a_custom_logger(callback_module):
    """The handler must subclass LiteLLM's CustomLogger (or the test shim)."""
    module, _ = callback_module
    cls_names = [c.__name__ for c in type(module.proxy_handler_instance).__mro__]
    assert "CustomLogger" in cls_names


def test_sync_logging_hook_redacts_aws_key(callback_module):
    """logging_hook must strip AWS keys from kwargs['messages']."""
    module, leak_log = callback_module
    kwargs = {
        "messages": [{"role": "user", "content": "My fake AWS key is AKIA1234567890123456 here"}]
    }
    new_kwargs, _ = module.proxy_handler_instance.logging_hook(kwargs, None, "completion")
    assert "[REDACTED:aws_access_key_id]" in new_kwargs["messages"][0]["content"]
    assert "AKIA1234567890123456" not in new_kwargs["messages"][0]["content"]
    assert leak_log.exists()
    lines = [json.loads(line) for line in leak_log.read_text().splitlines() if line]
    assert any(h["pattern_name"] == "aws_access_key_id" for h in lines)


@pytest.mark.asyncio
async def test_async_logging_hook_redacts_anthropic_key(callback_module):
    """async_logging_hook must work for /v1/chat/completions which hits acompletion."""
    module, leak_log = callback_module
    kwargs = {
        "messages": [
            {
                "role": "user",
                "content": "Test sk-ant-api03-abcdefghijklmnopqrstuvwxyz now",
            }
        ]
    }
    new_kwargs, _ = await module.proxy_handler_instance.async_logging_hook(
        kwargs, None, "acompletion"
    )
    assert "[REDACTED:" in new_kwargs["messages"][0]["content"]
    assert "sk-ant-api03-abcdefghijklmnopqrstuvwxyz" not in new_kwargs["messages"][0]["content"]
    assert leak_log.exists()
    lines = [json.loads(line) for line in leak_log.read_text().splitlines() if line]
    assert any(h["pattern_name"] == "anthropic_api_key" for h in lines)


def test_leak_log_never_contains_secret_value(callback_module):
    """Defence in depth: the leak log must record metadata only, never the secret."""
    module, leak_log = callback_module
    secret = "AKIA1234567890123456"
    kwargs = {"messages": [{"role": "user", "content": f"Token {secret}"}]}
    module.proxy_handler_instance.logging_hook(kwargs, None, "completion")
    assert leak_log.exists()
    text = leak_log.read_text()
    assert secret not in text, "secret value must NEVER appear in leak log"


def test_clean_prompt_is_passthrough(callback_module):
    """No hits, no log entry, kwargs unchanged."""
    module, leak_log = callback_module
    kwargs = {"messages": [{"role": "user", "content": "Hello, world."}]}
    new_kwargs, _ = module.proxy_handler_instance.logging_hook(kwargs, None, "completion")
    assert new_kwargs["messages"][0]["content"] == "Hello, world."
    assert not leak_log.exists() or leak_log.read_text() == ""


def test_response_scrub_redacts_assistant_content(callback_module):
    """A response that leaks a secret in choices[0].message.content must be scrubbed."""
    module, leak_log = callback_module
    response = {
        "choices": [
            {"message": {"role": "assistant", "content": "Here is AKIA1234567890123456 for you"}}
        ]
    }
    _, new_response = module.proxy_handler_instance.logging_hook(
        {"messages": []}, response, "completion"
    )
    assert "[REDACTED:" in new_response["choices"][0]["message"]["content"]


def test_missing_patterns_file_disables_scrubber_gracefully(monkeypatch, tmp_path):
    """If patterns file is missing the callback must NOT crash the proxy."""
    monkeypatch.setenv("SCRUBBER_PATTERNS_PATH", str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("SCRUBBER_LEAK_LOG_PATH", str(tmp_path / "leak.log"))
    sys.path.insert(0, str(REPO_ROOT))
    if "lib.scrubber_callback" in sys.modules:
        del sys.modules["lib.scrubber_callback"]
    module = importlib.import_module("lib.scrubber_callback")
    # Must still expose the instance, just disabled.
    kwargs = {"messages": [{"role": "user", "content": "AKIA1234567890123456"}]}
    new_kwargs, _ = module.proxy_handler_instance.logging_hook(kwargs, None, "completion")
    # With no patterns the prompt is passed through verbatim.
    assert "AKIA1234567890123456" in new_kwargs["messages"][0]["content"]
    if "lib.scrubber_callback" in sys.modules:
        del sys.modules["lib.scrubber_callback"]
