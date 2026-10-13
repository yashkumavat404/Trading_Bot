"""SQLite persistence: settings, risk state, paper state, trades, events, equity."""
import json, sqlite3, threading, time
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT UNIQUE, symbol TEXT,
  side TEXT, qty REAL, entry REAL, exit REAL, sl REAL, tp REAL, open_ts INTEGER, close_ts INTEGER,
  gross REAL, fees REAL, net REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS equity(ts INTEGER, equity REAL, balance REAL);
"""


class Database:
    def __init__(self, path: str = "data/bot.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.lock = threading.Lock()
        with self.lock:
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA); self.conn.commit()

    def get(self, key, default=None):
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                              (key, json.dumps(value)))
            self.conn.commit()

    def insert_trade(self, t: dict):
        cols = ["client_id", "symbol", "side", "qty", "entry", "exit", "sl", "tp", "open_ts", "close_ts",
                "gross", "fees", "net", "reason"]
        with self.lock:
            self.conn.execute(f"INSERT OR IGNORE INTO trades({','.join(cols)}) VALUES({','.join('?'*len(cols))})",
                              [t[c] for c in cols])
            self.conn.commit()

    def trades(self) -> pd.DataFrame:
        with self.lock:
            return pd.read_sql_query("SELECT * FROM trades ORDER BY id", self.conn)

    def event(self, level, msg, ts=None):
        with self.lock:
            self.conn.execute("INSERT INTO events(ts,level,msg) VALUES(?,?,?)", (ts or int(time.time() * 1000), level, msg))
            self.conn.commit()

    def events(self, n=50) -> pd.DataFrame:
        with self.lock:
            return pd.read_sql_query("SELECT * FROM events ORDER BY id DESC LIMIT ?", self.conn, params=(n,))

    def add_equity(self, ts, equity, balance):
        with self.lock:
            self.conn.execute("INSERT INTO equity VALUES(?,?,?)", (ts, equity, balance)); self.conn.commit()

    def equity(self) -> pd.DataFrame:
        with self.lock:
            return pd.read_sql_query("SELECT * FROM equity ORDER BY ts", self.conn)
