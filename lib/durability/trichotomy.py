"""Failure classifier + retry policy. Consumes config/limits.yaml retries.self_heal.*."""

import random
import re

from lib.durability.failure_matrix import lookup, TrichotomyClass


# Pattern-based classifier — order matters: more specific patterns first.
_CLASSIFIERS = [
    (re.compile(r"rate.?limit|429|too many requests", re.I), "F1"),
    (re.compile(r"timed? out|timeout|deadline exceeded", re.I), "F2"),
    (re.compile(r"name or service not known|dns|nxdomain", re.I), "F3"),
    (re.compile(r"5\d\d|internal server error|bad gateway", re.I), "F4"),
    (re.compile(r"connection reset", re.I), "F5"),
    (re.compile(r"sandbox.*(crash|exit)", re.I), "F6"),
    (re.compile(r"chroma.*unavailable", re.I), "F7"),
    (re.compile(r"vertex.*(auth|credentials)|invalid token", re.I), "F8"),
    (re.compile(r"claim.?lock|claim contention", re.I), "F9"),
    (re.compile(r"checkpoint.*(contention|locked)", re.I), "F10"),
    (re.compile(r"max_tokens too low|thinking tokens truncated", re.I), "F11"),
    (re.compile(r"chroma.*down", re.I), "F12"),
    (re.compile(r"otel.*unreachable", re.I), "F13"),
    (re.compile(r"github.?mcp.*unavailable", re.I), "F14"),
    (re.compile(r"skill.?extractor.*fail", re.I), "F15"),
    (re.compile(r"judge.*timeout", re.I), "F16"),
    (re.compile(r"daily.*budget.*exceeded", re.I), "F21"),
    (re.compile(r"secret.?leak|REDACTED:critical", re.I), "F22"),
    (re.compile(r"sandbox.*escape", re.I), "F23"),
    (re.compile(r"consensus.*(fail|split)", re.I), "F24"),
    (re.compile(r"clarification.*max.*questions", re.I), "F25"),
    (re.compile(r"3.?strike|consecutive_rejections.*3", re.I), "F26"),
    (re.compile(r"disk full|no space left", re.I), "F28"),
    (re.compile(r"kanban.*(corrupt|migration)", re.I), "F29"),
    (re.compile(r"approval.*required.*without", re.I), "F30"),
    (re.compile(r"egress.*denied|allowlist.*violation", re.I), "F31"),
]


def classify(err: Exception) -> str:
    """Classify an exception to an F-code. Falls through to F33 (fail-loud unknown)."""
    msg = f"{type(err).__name__}: {err}"
    for pat, code in _CLASSIFIERS:
        if pat.search(msg):
            return code
    return "F33"


def trichotomy_class(err: Exception) -> TrichotomyClass:
    return lookup(classify(err))["class"]


def backoff_delay(
    attempt: int, base_ms: int = 500, max_ms: int = 30000, jitter_pct: int = 25
) -> int:
    """Exponential backoff with jitter. attempt is 1-indexed.

    Final delay is clamped to [0, max_ms] *after* applying jitter so the
    contract that ``delay <= max_ms`` holds for any attempt count.
    """
    raw = base_ms * (2 ** (attempt - 1))
    raw = min(raw, max_ms)
    jitter = raw * (jitter_pct / 100.0)
    delay = raw + random.uniform(-jitter, jitter)
    return max(0, min(int(delay), max_ms))


def before_tool_call(ctx, tool_call):
    """Hook registered as pre_tool_call. Currently no-op; reserved."""
    return None


def after_tool_call(ctx, tool_call, result_or_error):
    """Hook registered as post_tool_call. Classifies errors + emits OTel span."""
    if isinstance(result_or_error, Exception):
        code = classify(result_or_error)
        cls = lookup(code)["class"]
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer("hermes.durability")
            with tracer.start_as_current_span("durability.classify") as span:
                span.set_attribute("f_code", code)
                span.set_attribute("trichotomy_class", cls.value)
        except ImportError:
            pass
    return None
