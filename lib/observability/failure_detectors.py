import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FailureDetectors:
    """Detects failure signatures in LLM outputs, e.g. text-only loops or sentinel patterns."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        # Maps session_id -> list of recent assistant responses
        self._history: Dict[str, List[str]] = {}

    def check_llm_output(self, session_id: str, text: str) -> Optional[str]:
        """Check LLM output text for loops or sentinel patterns.

        Returns the failure type (e.g., 'F-LOOP-TEXT' or 'F-SENTINEL') if detected,
        otherwise None.
        """
        if not session_id or not text:
            return None

        # 1. Sentinel output patterns detection
        sentinels = [
            "I apologize for the confusion",
            "As an AI language model",
            "I will now try to",
            "Let me try again",
        ]
        text_lower = text.lower()
        for sentinel in sentinels:
            if sentinel.lower() in text_lower:
                logger.warning(
                    "failure_detectors: Sentinel pattern '%s' detected in session %s",
                    sentinel,
                    session_id,
                )
                return "F-SENTINEL"

        # 2. Check for self-repetition within a single response
        # e.g. duplicate sentences repeated multiple times
        sentences = [s.strip() for s in text.split(".") if len(s.strip()) > 15]
        if len(sentences) >= 6:
            freq: dict[str, int] = {}
            for s in sentences:
                freq[s] = freq.get(s, 0) + 1
            for s, count in freq.items():
                if count >= 4:
                    logger.warning(
                        "failure_detectors: Internal sentence repetition loop detected in session %s",
                        session_id,
                    )
                    return "F-LOOP-TEXT"

        # 3. Text-only loops detection (consecutive identical/highly-similar outputs)
        history = self._history.setdefault(session_id, [])
        history.append(text.strip())
        if len(history) > self.threshold:
            history.pop(0)

        if len(history) >= self.threshold:
            # Check if all elements in history are identical
            if len(set(history)) == 1:
                logger.warning(
                    "failure_detectors: Text-only loop detected in session %s (repeated %d times)",
                    session_id,
                    self.threshold,
                )
                return "F-LOOP-TEXT"

        return None

    def reset(self, session_id: str) -> None:
        self._history.pop(session_id, None)
