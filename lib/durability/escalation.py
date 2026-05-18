"""24h Telegram silence watcher. Runs periodically (sidecar) — scans Hermes Kanban for
blocked cards with stale last_heartbeat_at and emits escalation alerts.

Consumes config/limits.yaml agent.telegram_escalation_timeout_h.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

KANBAN_DB_PATH = os.environ.get("HERMES_KANBAN_DB", "/root/.hermes/kanban/kanban.db")


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
    """Send a Telegram alert. Stubbed for now; P1-5 (session-e) will wire telegram_bridge."""
    # TODO(P1-5): replace with telegram_bridge.send_alert(...)
    print(f"[ESCALATION F32] card={card_id} title={title!r} blocked_age_h={age_h:.1f}")


def run_once(threshold_h: int = 24, db_path: str = None) -> int:
    stale = find_stale_blocked_cards(threshold_h=threshold_h, db_path=db_path)
    for card_id, title, age_h in stale:
        emit_escalation(card_id, title, age_h)
    return len(stale)


if __name__ == "__main__":
    n = run_once()
    print(f"escalated {n} card(s)")
