from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Ledger:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    fingerprint TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    decision_json TEXT,
                    order_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS optionomics_trade_ideas (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    strategy TEXT,
                    pipeline_name TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_json TEXT,
                    order_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_trade_idea(self, trade_id: str, payload: dict[str, Any], *, status: str = "queued") -> None:
        now = utc_now_iso()
        payload_json = json.dumps(payload, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO optionomics_trade_ideas
                    (trade_id, symbol, direction, strategy, pipeline_name, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    str(payload.get("symbol") or ""),
                    str(payload.get("direction") or "neutral"),
                    str(payload.get("strategy") or ""),
                    str(payload.get("pipeline_name") or payload.get("pipeline_short_name") or ""),
                    payload_json,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()

    def is_trade_idea_seen(self, trade_id: str) -> bool:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            row = conn.execute(
                "SELECT 1 FROM optionomics_trade_ideas WHERE trade_id = ? LIMIT 1",
                (trade_id,),
            ).fetchone()
        return row is not None

    def has_trade_or_symbol_seen(self, *, trade_id: str | None = None, symbol: str | None = None) -> bool:
        if trade_id is None and symbol is None:
            return False

        if trade_id is None:
            return False

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            row = conn.execute(
                "SELECT 1 FROM optionomics_trade_ideas WHERE trade_id = ? LIMIT 1",
                (str(trade_id),),
            ).fetchone()
        return row is not None

    def has_ordered_trade(self, *, trace_id: str | None = None, symbol: str | None = None) -> bool:
        if trace_id is None and symbol is None:
            return False

        trace_id = str(trace_id).strip() if trace_id is not None else None
        symbol = str(symbol).strip().upper() if symbol is not None else None

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            if trace_id is not None and symbol is not None:
                row = conn.execute(
                    "SELECT 1 FROM optionomics_trade_ideas WHERE status = ? AND trade_id = ? AND symbol = ? LIMIT 1",
                    ("ordered", trace_id, symbol),
                ).fetchone()
            elif trace_id is not None:
                row = conn.execute(
                    "SELECT 1 FROM optionomics_trade_ideas WHERE status = ? AND trade_id = ? LIMIT 1",
                    ("ordered", trace_id),
                ).fetchone()
            elif symbol is not None:
                row = conn.execute(
                    "SELECT 1 FROM optionomics_trade_ideas WHERE status = ? AND symbol = ? LIMIT 1",
                    ("ordered", symbol),
                ).fetchone()
            else:
                return False
        return row is not None

    def mark_trade_idea_status(self, trade_id: str, *, status: str, decision: Any | None = None, order_payload: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        decision_json = json.dumps(decision or {}, sort_keys=True) if decision else None
        order_json = json.dumps(order_payload or {}, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                UPDATE optionomics_trade_ideas
                   SET status = ?, decision_json = ?, order_json = ?, updated_at = ?
                 WHERE trade_id = ?
                """,
                (status, decision_json, order_json, now, trade_id),
            )
            conn.commit()

    def reserve(self, fingerprint: str, payload: Any) -> bool:
        now = utc_now_iso()
        payload_json = json.dumps(payload, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events
                    (fingerprint, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fingerprint, "queued", payload_json, now, now),
            )
            conn.commit()

        return cursor.rowcount == 1

    def finish(self, fingerprint: str, status: str, decision: Any, order_payload: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        decision_json = json.dumps(decision or {}, sort_keys=True)
        order_json = json.dumps(order_payload or {}, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                                UPDATE events
                                     SET status = ?, decision_json = ?, order_json = ?, error = NULL, updated_at = ?
                                 WHERE fingerprint = ?
                """,
                (status, decision_json, order_json, now, fingerprint),
            )
            conn.commit()

    def fail(self, fingerprint: str, error: str) -> None:
        now = utc_now_iso()

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                                UPDATE events
                                     SET status = ?, error = ?, updated_at = ?
                                 WHERE fingerprint = ?
                """,
                ("failed", error[:1000], now, fingerprint),
            )
            conn.commit()
