import json
import os
import sqlite3
import threading

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "monitor.db")


class Database:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS assets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rules(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    value REAL NOT NULL,
                    cooldown_min INTEGER NOT NULL DEFAULT 30,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_triggered REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prices(
                    symbol TEXT NOT NULL,
                    ts REAL NOT NULL,
                    price REAL NOT NULL,
                    change_24h REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_prices_symbol_ts ON prices(symbol, ts);
                CREATE TABLE IF NOT EXISTS alerts_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    rule_id INTEGER,
                    symbol TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    price REAL NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(prices)")]
            if "volume" not in cols:
                self._conn.execute("ALTER TABLE prices ADD COLUMN volume REAL NOT NULL DEFAULT 0")
            self._conn.commit()


    def add_asset(self, symbol, name=""):
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO assets(symbol, name, created_at) VALUES(?, ?, ?)",
                (symbol.upper(), name, __import__("time").time()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def remove_asset(self, symbol):
        with self._lock:
            row = self._conn.execute("SELECT id FROM assets WHERE symbol=?", (symbol.upper(),)).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM rules WHERE asset_id=?", (row["id"],))
            self._conn.execute("DELETE FROM prices WHERE symbol=?", (symbol.upper(),))
            self._conn.execute("DELETE FROM assets WHERE id=?", (row["id"],))
            self._conn.commit()
            return True

    def assets(self):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM assets ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def asset_by_id(self, asset_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            return dict(row) if row else None

    def add_price(self, symbol, ts, price, change_24h, volume=0.0):
        with self._lock:
            self._conn.execute(
                "INSERT INTO prices(symbol, ts, price, change_24h, volume) VALUES(?, ?, ?, ?, ?)",
                (symbol, ts, price, change_24h, volume),
            )
            self._conn.commit()

    def hourly_closes(self, symbol, hours):
        import time

        since = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT bucket, price FROM (
                    SELECT CAST(ts/3600 AS INTEGER)*3600 AS bucket, ts, price,
                           ROW_NUMBER() OVER (PARTITION BY CAST(ts/3600 AS INTEGER)*3600 ORDER BY ts DESC) AS rn
                    FROM prices WHERE symbol=? AND ts>?
                ) WHERE rn=1 ORDER BY bucket ASC
                """,
                (symbol, since),
            ).fetchall()
            return [dict(r) for r in rows]

    def price_before(self, symbol, ts):
        with self._lock:
            row = self._conn.execute(
                "SELECT price FROM prices WHERE symbol=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                (symbol, ts),
            ).fetchone()
            return row["price"] if row else None

    def latest_price(self, symbol):
        with self._lock:
            row = self._conn.execute(
                "SELECT ts, price, change_24h FROM prices WHERE symbol=? ORDER BY ts DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            return dict(row) if row else None

    def history(self, symbol, since, limit=5000):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, price FROM prices WHERE symbol=? AND ts>? ORDER BY ts ASC LIMIT ?",
                (symbol, since, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_prices(self, days=3):
        import time

        cutoff = time.time() - days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM prices WHERE ts<?", (cutoff,))
            self._conn.commit()

    def rules(self, enabled_only=False):
        query = """
            SELECT r.*, a.symbol FROM rules r
            JOIN assets a ON a.id = r.asset_id
        """
        if enabled_only:
            query += " WHERE r.enabled=1"
        query += " ORDER BY r.id"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
            return [dict(r) for r in rows]

    def add_rule(self, asset_id, kind, value, cooldown_min=30):
        import time

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rules(asset_id, kind, value, cooldown_min, created_at) VALUES(?, ?, ?, ?, ?)",
                (asset_id, kind, value, cooldown_min, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_rule(self, rule_id, value=None, cooldown_min=None, enabled=None):
        with self._lock:
            if value is not None:
                self._conn.execute("UPDATE rules SET value=? WHERE id=?", (value, rule_id))
            if cooldown_min is not None:
                self._conn.execute("UPDATE rules SET cooldown_min=? WHERE id=?", (cooldown_min, rule_id))
            if enabled is not None:
                self._conn.execute("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))
            self._conn.commit()

    def delete_rule(self, rule_id):
        with self._lock:
            self._conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
            self._conn.commit()

    def set_rule_triggered(self, rule_id, ts):
        with self._lock:
            self._conn.execute("UPDATE rules SET last_triggered=? WHERE id=?", (ts, rule_id))
            self._conn.commit()

    def add_alert(self, rule_id, symbol, kind, price, message):
        import time

        with self._lock:
            self._conn.execute(
                "INSERT INTO alerts_log(ts, rule_id, symbol, kind, price, message) VALUES(?, ?, ?, ?, ?, ?)",
                (time.time(), rule_id, symbol, kind, price, message),
            )
            self._conn.commit()

    def recent_alerts(self, limit=50):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_log(self, level, message):
        import time

        with self._lock:
            self._conn.execute(
                "INSERT INTO logs(ts, level, message) VALUES(?, ?, ?)",
                (time.time(), level, message),
            )
            self._conn.commit()

    def recent_logs(self, since=0, limit=300):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM logs WHERE ts>? ORDER BY id DESC LIMIT ?", (since, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_setting(self, key, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except (ValueError, TypeError):
                return default

    def set_setting(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._conn.commit()
