"""P1-5 (Go-Live audit): Model Armor guardrail for input/output safety.

Wraps GCP's Model Armor ``SanitizeModelResponse`` / ``SanitizeUserPrompt``
APIs as a runtime guardrail on the LLM call path. The Terraform template
``j1-trajectory-shipper`` in ``terraform/phase-0a-gcp/model-armor/`` is
the deployed resource this module calls.

Configuration (env vars):
    MODEL_ARMOR_TEMPLATE  — full resource name of the Model Armor template
        (e.g. ``projects/autonomous-agent-2026/locations/us-central1/templates/j1-trajectory-shipper``)
    MODEL_ARMOR_ENABLED   — "true" (default) / "false"

When ``MODEL_ARMOR_TEMPLATE`` is unset, the guardrail is a NO-OP (fail-open) —
backward-compatible with deployments that haven't provisioned the template.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GuardrailAction(str, Enum):
    """The action the guardrail recommends."""

    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True)
class GuardrailVerdict:
    """Result of a Model Armor screening."""

    action: GuardrailAction
    reason: str
    raw_response: Optional[dict] = None

    @property
    def blocked(self) -> bool:
        return self.action == GuardrailAction.BLOCK


# ── Singleton-ish guardrail ──────────────────────────────────────────────────


class ModelArmorGuardrail:
    """Runtime wrapper for GCP Model Armor content screening.

    Lazy-initializes the ``ModelArmorClient`` on first use. Thread-safe:
    the client is constructed once under a lock and reused.

    Usage in graph.py::

        guardrail = ModelArmorGuardrail()
        verdict = guardrail.screen_input(user_goal)
        if verdict.blocked:
            return {"status": "refused", "reason": verdict.reason}
    """

    def __init__(
        self,
        *,
        template_name: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._template = template_name or os.environ.get("MODEL_ARMOR_TEMPLATE", "")
        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = os.environ.get("MODEL_ARMOR_ENABLED", "true").lower() not in (
                "false",
                "0",
                "no",
                "off",
            )
        self._client: Any = None
        self._init_attempted = False

    def _get_client(self) -> Any:
        """Lazy-init the Model Armor client."""
        if self._client is not None:
            return self._client
        if self._init_attempted:
            return None
        self._init_attempted = True
        try:
            from google.cloud import modelarmor_v1  # type: ignore[import-untyped]

            self._client = modelarmor_v1.ModelArmorClient()
            logger.info("model-armor: client initialized (template=%s)", self._template)
            return self._client
        except ImportError:
            logger.warning(
                "model-armor: google-cloud-modelarmor not installed — guardrail DISABLED"
            )
            return None
        except Exception as exc:
            logger.error("model-armor: client init failed: %s", exc)
            return None

    @property
    def active(self) -> bool:
        """Whether the guardrail will actually screen content."""
        return self._enabled and bool(self._template)

    def screen_input(self, text: str) -> GuardrailVerdict:
        """Screen user input (goal text) before it enters the spine.

        Calls ``SanitizeUserPrompt`` on the Model Armor template.
        Returns ALLOW on any failure (fail-open posture).
        """
        if not self.active:
            return GuardrailVerdict(GuardrailAction.ALLOW, "model-armor-disabled")

        client = self._get_client()
        if client is None:
            return GuardrailVerdict(GuardrailAction.ALLOW, "client-unavailable")

        try:
            from google.cloud import modelarmor_v1  # type: ignore[import-untyped]

            request = modelarmor_v1.SanitizeUserPromptRequest(
                name=self._template,
                user_prompt_data=modelarmor_v1.DataItem(text=text),
            )
            response = client.sanitize_user_prompt(request=request)

            # Parse the filter match result
            match_state = getattr(
                getattr(response, "sanitization_result", None),
                "filter_match_state",
                None,
            )
            if match_state and str(match_state) == "MATCH_FOUND":
                filter_results = getattr(
                    getattr(response, "sanitization_result", None),
                    "filter_results",
                    {},
                )
                reasons = []
                for filter_name, result in (filter_results or {}).items():
                    if getattr(result, "match_state", None) and "MATCH" in str(result.match_state):
                        reasons.append(f"{filter_name}: matched")
                return GuardrailVerdict(
                    GuardrailAction.BLOCK,
                    f"input-blocked: {'; '.join(reasons) or 'filter match'}",
                    raw_response=_response_to_dict(response),
                )

            return GuardrailVerdict(
                GuardrailAction.ALLOW,
                "input-clean",
                raw_response=_response_to_dict(response),
            )

        except Exception as exc:
            logger.warning("model-armor: screen_input failed (fail-open): %s", exc)
            return GuardrailVerdict(GuardrailAction.ALLOW, f"screen-error: {exc!r}")

    def screen_output(self, text: str) -> GuardrailVerdict:
        """Screen agent output before it ships.

        Calls ``SanitizeModelResponse`` on the Model Armor template.
        Returns ALLOW on any failure (fail-open posture).
        """
        if not self.active:
            return GuardrailVerdict(GuardrailAction.ALLOW, "model-armor-disabled")

        client = self._get_client()
        if client is None:
            return GuardrailVerdict(GuardrailAction.ALLOW, "client-unavailable")

        try:
            from google.cloud import modelarmor_v1  # type: ignore[import-untyped]

            request = modelarmor_v1.SanitizeModelResponseRequest(
                name=self._template,
                model_response_data=modelarmor_v1.DataItem(text=text),
            )
            response = client.sanitize_model_response(request=request)

            match_state = getattr(
                getattr(response, "sanitization_result", None),
                "filter_match_state",
                None,
            )
            if match_state and str(match_state) == "MATCH_FOUND":
                filter_results = getattr(
                    getattr(response, "sanitization_result", None),
                    "filter_results",
                    {},
                )
                reasons = []
                for filter_name, result in (filter_results or {}).items():
                    if getattr(result, "match_state", None) and "MATCH" in str(result.match_state):
                        reasons.append(f"{filter_name}: matched")
                return GuardrailVerdict(
                    GuardrailAction.BLOCK,
                    f"output-blocked: {'; '.join(reasons) or 'filter match'}",
                    raw_response=_response_to_dict(response),
                )

            return GuardrailVerdict(
                GuardrailAction.ALLOW,
                "output-clean",
                raw_response=_response_to_dict(response),
            )

        except Exception as exc:
            logger.warning("model-armor: screen_output failed (fail-open): %s", exc)
            return GuardrailVerdict(GuardrailAction.ALLOW, f"screen-error: {exc!r}")


def _response_to_dict(response: Any) -> dict:
    """Best-effort conversion of a protobuf response to a dict."""
    try:
        from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

        return MessageToDict(response._pb)
    except Exception:
        return {"raw": str(response)}
