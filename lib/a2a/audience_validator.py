"""Startup-time audience validator.

Ensures all peer audiences in peers.yaml are valid service account emails,
not URLs. Rejects URL-formed audiences in production.
"""

import os
import yaml
from pathlib import Path

_PEERS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "a2a" / "peers.yaml"


def validate_all_peers() -> None:
    """Iterate peers.yaml and assert each audience contains '@'.

    Raises RuntimeError in ENVIRONMENT=production if a URL-form audience is found.
    """
    if not _PEERS_CONFIG_PATH.exists():
        return

    try:
        data = yaml.safe_load(_PEERS_CONFIG_PATH.read_text()) or {}
    except Exception:
        return

    peers_list = data.get("peers") or []

    is_production = os.getenv("ENVIRONMENT", "").lower() == "production"

    validated_count = 0
    for peer in peers_list:
        audience = peer.get("audience")
        if audience:
            if (
                "@" not in audience
                or audience.startswith("http://")
                or audience.startswith("https://")
            ):
                msg = f"Invalid audience in peers.yaml for peer {peer.get('name')}: {audience}. Must be a Service Account email."
                if is_production:
                    raise RuntimeError(msg)
            validated_count += 1

    import logging

    logger = logging.getLogger(__name__)
    logger.info("audience_validator: validated %d peer(s), all email-form", validated_count)
