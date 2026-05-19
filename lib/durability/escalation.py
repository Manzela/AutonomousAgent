"""24h Telegram silence watcher. Runs periodically (sidecar) — scans Hermes Kanban for
blocked cards with stale last_heartbeat_at and emits escalation alerts.

Consumes config/limits.yaml agent.telegram_escalation_timeout_h.
"""

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Default Kanban DB path. The container runs as user `hermes` (uid 1000)
# with HOME=/home/hermes after the α-5 security-hardening PR. Hermes
# resolves its DB via ``Path.home() / ".hermes" / "kanban.db"`` —
# verified live at ``/home/hermes/.hermes/kanban.db`` (no ``kanban/``
# subdir; see also ``lib/kanban/telegram_bridge._DEFAULT_DB_PATH``).
KANBAN_DB_PATH = os.environ.get("HERMES_KANBAN_DB", "/home/hermes/.hermes/kanban.db")


def find_stale_blocked_cards(
    threshold_h: int = 24, db_path: str = None
) -> List[Tuple[int, str, float]]:
    """Return [(card_id, title, last_heartbeat_age_h), ...] for cards stuck in blocked
    longer than threshold_h hours."""
    db_path = db_path or KANBAN_DB_PATH
    if not Path(db_path).exists():
        return []
    now = time.time()
    threshold_s = threshold_h * 3600
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, last_heartbeat_at FROM tasks "
            "WHERE status = 'blocked' AND (? - last_heartbeat_at) > ?",
            (now, threshold_s),
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], (now - r[2]) / 3600) for r in rows]


def emit_escalation(card_id: int, title: str, age_h: float) -> None:
    """Send an alert for a card stuck in ``blocked`` past the SLA.

    Primary channel is ``telegram_bridge.send_alert``; escalations share
    the same publish path (and the same fail-open guarantees) as Kanban
    status-transition alerts. When the primary channel is unreachable
    (no bot token, network failure, Telegram outage) the watcher falls
    back to ``github_fallback.open_incident_issue`` so the alert is at
    least durable in GitHub's issue tracker (with the ``incident/auto``
    label).

    Both channels are best-effort: any exception from either path is
    swallowed so the sidecar loop keeps ticking — losing a single alert
    cycle is preferable to crashing the watcher and missing every
    subsequent card. Closes audit P1-4.

    Local imports avoid forcing the kanban / github_fallback modules on
    the import path when this module is loaded by callers that don't
    care about side channels (e.g. ``find_stale_blocked_cards`` used
    standalone).
    """
    msg = (
        f"⚠️ Card {card_id} '{title}' blocked >24h ({age_h:.1f}h). "
        f"Use `/resume {card_id}` or `/cancel {card_id}`."
    )

    telegram_delivered = False
    try:
        from lib.kanban.telegram_bridge import send_alert

        telegram_delivered = bool(send_alert(card_id, msg))
    except Exception as exc:  # noqa: BLE001 — watcher must keep ticking
        logger.warning(
            "escalation: send_alert raised card=%s err=%s — original msg=%s",
            card_id,
            exc,
            msg,
        )

    if telegram_delivered:
        return

    # Telegram is silent. Open a GitHub issue so the operator still gets
    # paged through GitHub notifications. Dedupe is enforced inside
    # ``open_incident_issue`` so the every-10-min watcher cadence
    # doesn't generate one issue per tick for a multi-day outage.
    logger.warning(
        "escalation: Telegram unreachable for card=%s — falling back to GitHub issue",
        card_id,
    )
    try:
        from lib.durability.github_fallback import open_incident_issue

        open_incident_issue(
            card_id=card_id,
            title=f"[F32] Card {card_id} blocked >{int(age_h)}h ({title})",
            body=msg,
        )
    except Exception as exc:  # noqa: BLE001 — fallback must also fail-open
        logger.warning(
            "escalation: GitHub fallback raised card=%s err=%s — original msg=%s",
            card_id,
            exc,
            msg,
        )


def run_once(threshold_h: int = 24, db_path: str = None) -> int:
    stale = find_stale_blocked_cards(threshold_h=threshold_h, db_path=db_path)
    for card_id, title, age_h in stale:
        emit_escalation(card_id, title, age_h)
    return len(stale)


if __name__ == "__main__":
    n = run_once()
    print(f"escalated {n} card(s)")
