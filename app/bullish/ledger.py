"""Persistent claims and order audit for bullish-flow entries."""

import json
import sqlite3
from contextlib import closing

from app.persistence.ledger import Ledger, utc_now_iso


class BullishLedger(Ledger):
    def initialize(self):
        super().initialize()
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS top_bullish_trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    total_premium REAL NOT NULL,
                    trade_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    order_request_json TEXT NOT NULL,
                    tracking_json TEXT,
                    order_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def claim(self, trade_id, payload, order_request):
        """Atomically reserve a trade and symbol before any broker call."""
        now = utc_now_iso()
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            cursor = conn.execute("""
                INSERT INTO top_bullish_trades
                    (trade_id, symbol, total_premium, trade_count, status,
                     payload_json, order_request_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, (trade_id, payload['symbol'], payload['total_premium'],
                  payload['trade_count'], json.dumps(payload), json.dumps(order_request), now, now))
            conn.commit()
            return cursor.rowcount == 1

    def contains(self, trade_id, symbol):
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            return conn.execute(
                'SELECT 1 FROM top_bullish_trades WHERE trade_id = ? OR symbol = ?',
                (trade_id, symbol),
            ).fetchone() is not None

    def update(self, trade_id, status, *, tracking=None, order=None, error=None):
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("""
                UPDATE top_bullish_trades SET status=?,
                    tracking_json=COALESCE(?, tracking_json),
                    order_json=COALESCE(?, order_json), error=?, updated_at=?
                WHERE trade_id=?
            """, (status, json.dumps(tracking) if tracking is not None else None,
                  json.dumps(order) if order is not None else None,
                  error, utc_now_iso(), trade_id))
            conn.commit()

    def mark_morning_sold(self, symbol):
        """Record a morning-sell liquidation so the symbol is not resold."""
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute(
                "UPDATE top_bullish_trades SET status='sold', updated_at=? "
                "WHERE symbol=? AND status='submitted'",
                (utc_now_iso(), str(symbol).upper()),
            )
            conn.commit()
