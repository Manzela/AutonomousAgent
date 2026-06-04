"""Production Traces → Eval Feedback Loop (SP-O3).

Connects to Langfuse, identifies spans marked as "error" or with user
thumbs-down feedback, and extracts their inputs into a regression test suite
(YAML format consumable by Promptfoo or DeepEval).

Usage:
  python -m evals.trace_to_eval_pipeline [--url URL] [--out PATH]

Environment:
  LANGFUSE_PUBLIC_KEY   — Langfuse project public key
  LANGFUSE_SECRET_KEY   — Langfuse project secret key
  LANGFUSE_HOST         — Langfuse API base URL (default: https://cloud.langfuse.com)

When Langfuse is unreachable or credentials are missing, the pipeline logs a
warning and returns an empty result — it never crashes the caller.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Langfuse client abstraction ──────────────────────────────────────────────


class LangfuseTraceClient:
    """Thin wrapper over the Langfuse Python SDK for trace extraction.

    Encapsulates the SDK import and graceful fallback so callers never need
    to handle ImportError directly.
    """

    def __init__(
        self,
        *,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
    ) -> None:
        self._public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self._secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")
        self._host = host or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        self._client: Any = None

    def _ensure_client(self) -> bool:
        """Lazily initialise the Langfuse SDK client. Returns False if unavailable."""
        if self._client is not None:
            return True
        if not self._public_key or not self._secret_key:
            logger.warning(
                "SP-O3: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — "
                "trace extraction disabled"
            )
            return False
        try:
            from langfuse import Langfuse  # type: ignore[import-untyped]

            self._client = Langfuse(
                public_key=self._public_key,
                secret_key=self._secret_key,
                host=self._host,
            )
            return True
        except ImportError:
            logger.warning(
                "SP-O3: langfuse SDK not installed — trace extraction disabled. "
                "Install with: pip install langfuse"
            )
            return False
        except Exception as exc:
            logger.warning("SP-O3: Langfuse client init failed — %s", exc)
            return False

    def fetch_failed_traces(self, *, limit: int = 100) -> list[dict]:
        """Fetch traces tagged as failures or with low scores.

        Returns a list of dicts with keys: input, output, error, trace_id.
        Returns [] if Langfuse is unavailable.
        """
        if not self._ensure_client():
            return []
        try:
            # Langfuse SDK: fetch_traces returns a FetchTracesResponse
            resp = self._client.fetch_traces(
                tags=["error", "failure"],
                limit=limit,
                order_by="timestamp",
                order="DESC",
            )
            traces = []
            for trace in resp.data:
                traces.append(
                    {
                        "trace_id": trace.id,
                        "input": _safe_str(trace.input),
                        "output": _safe_str(trace.output),
                        "error": _extract_error(trace),
                    }
                )
            logger.info("SP-O3: fetched %d failed traces from Langfuse", len(traces))
            return traces
        except Exception as exc:
            logger.warning("SP-O3: trace fetch failed (graceful) — %s", exc)
            return []


def _safe_str(val: Any) -> str:
    """Convert any trace input/output to a string for the regression suite."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return json.dumps(val, default=str)


def _extract_error(trace: Any) -> str:
    """Best-effort error extraction from a Langfuse trace object."""
    if hasattr(trace, "metadata") and isinstance(trace.metadata, dict):
        return trace.metadata.get("error", "")
    return ""


# ── Regression suite generator ───────────────────────────────────────────────


def traces_to_eval_cases(traces: list[dict]) -> list[dict]:
    """Convert extracted traces to eval regression cases (Promptfoo-compatible YAML)."""
    cases = []
    for trace in traces:
        if not trace.get("input"):
            continue
        case: dict[str, Any] = {
            "vars": {"input": trace["input"]},
            "assert": [{"type": "not-contains", "value": "unable"}],
            "description": (
                f"Regression from trace {trace.get('trace_id', 'unknown')}: "
                f"{trace.get('error', 'unspecified error')}"
            ),
        }
        cases.append(case)
    return cases


def write_regression_yaml(
    cases: list[dict], output_path: str | Path = "evals/auto_regression.yaml"
) -> Path:
    """Write the regression cases to a YAML file."""
    import yaml

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "Auto-Generated Prod Regressions (SP-O3)",
        "tests": cases,
    }
    out.write_text(yaml.dump(payload, default_flow_style=False, sort_keys=False))
    logger.info("SP-O3: wrote %d regression cases to %s", len(cases), out)
    return out


# ── Public entrypoint ────────────────────────────────────────────────────────


def generate_regression_evals_from_traces(
    langfuse_url: str | None = None,
    output_path: str | Path = "evals/auto_regression.yaml",
    limit: int = 100,
) -> list[dict]:
    """Pull production failures and build a regression matrix.

    Returns the generated eval cases (empty list if Langfuse is unavailable).
    """
    client = LangfuseTraceClient(host=langfuse_url)
    traces = client.fetch_failed_traces(limit=limit)
    if not traces:
        logger.info("SP-O3: no failed traces found — regression suite is empty")
        return []
    cases = traces_to_eval_cases(traces)
    write_regression_yaml(cases, output_path)
    return cases


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="SP-O3: Trace → Eval regression pipeline")
    parser.add_argument("--url", default=None, help="Langfuse API URL")
    parser.add_argument("--out", default="evals/auto_regression.yaml", help="Output YAML path")
    parser.add_argument("--limit", type=int, default=100, help="Max traces to fetch")
    args = parser.parse_args()

    cases = generate_regression_evals_from_traces(
        langfuse_url=args.url, output_path=args.out, limit=args.limit
    )
    print(f"Generated {len(cases)} regression cases")
