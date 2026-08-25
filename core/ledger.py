"""SQLite ledger: every earning, spend and lead the agent produces."""
import json
import os
import sqlite3
import threading

from .utils import DATA_DIR, now_iso, today_str

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    strategy TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_tx_day ON transactions(day);
"""

EARN_KINDS = ("earn",)          # real money in
PAPER_KINDS = ("paper_earn",)   # simulated money in


class Ledger:
    def __init__(self, db_path=None):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db_path = db_path or os.path.join(DATA_DIR, "earner.db")
        self.lock = threading.Lock()
        self.cx = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        with self.lock:
            self.cx.executescript(SCHEMA)
            self.cx.commit()

    def record(self, strategy, kind, amount=0.0, currency="USD", note=""):
        assert kind in EARN_KINDS + PAPER_KINDS + ("spend", "lead"), f"bad kind {kind}"
        with self.lock:
            cur = self.cx.execute(
                "INSERT INTO transactions(ts, day, strategy, kind, amount, currency, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (now_iso(), today_str(), strategy, kind, float(amount), currency, str(note)[:500]))
            self.cx.commit()
            return cur.lastrowid

    def day_total(self, day=None, kinds=EARN_KINDS):
        day = day or today_str()
        q = "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE day=? AND kind IN (%s)" % \
            ",".join("?" * len(kinds))
        row = self.cx.execute(q, (day, *kinds)).fetchone()
        return round(row[0], 2)

    def recent(self, limit=15):
        rows = self.cx.execute(
            "SELECT ts, strategy, kind, amount, currency, note FROM transactions"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def per_strategy_today(self):
        rows = self.cx.execute(
            "SELECT strategy, kind, SUM(amount) s FROM transactions WHERE day=? GROUP BY strategy, kind",
            (today_str(),)).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["strategy"], {})[r["kind"]] = round(r["s"], 2)
        return out

    def survival_streak(self, target):
        """Consecutive days (ending today) where REAL earnings >= target."""
        rows = self.cx.execute(
            "SELECT DISTINCT day FROM transactions ORDER BY day DESC LIMIT 400").fetchall()
        streak = 0
        for r in rows:
            if self.day_total(r["day"], EARN_KINDS) >= target:
                streak += 1
            else:
                break
        return streak

    # -- generic key/value state (paper wallet, dedupe caches...) -------
    def get_state(self, key, default=None):
        row = self.cx.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return default

    def set_state(self, key, value):
        with self.lock:
            self.cx.execute("INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
            self.cx.commit()

    def reset_paper_wallet(self, start_usd=100.0):
        self.set_state("paper_wallet", {"usd": start_usd, "position": None})

    def close(self):
        """Flush and release the SQLite handle (needed on Windows)."""
        with self.lock:
            try:
                self.cx.commit()
            finally:
                self.cx.close()
