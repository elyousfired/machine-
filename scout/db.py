"""SQLite store committed to the repo. Tracks every project seen + price snapshots for outcome learning."""
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "scout.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  key TEXT PRIMARY KEY,          -- network:token
  network TEXT, token TEXT, pool TEXT, name TEXT, dex TEXT,
  first_seen TEXT, created_at TEXT,
  stage TEXT,                    -- rejected_market | rejected_security | shortlisted
  reasons TEXT, warnings TEXT, score INTEGER,
  info TEXT, security TEXT, market TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (   -- outcome tracking: 1d / 7d / 30d
  key TEXT, ts TEXT, liquidity_usd REAL, fdv_usd REAL, vol_24h REAL, price_chg_24h REAL,
  PRIMARY KEY (key, ts)
);
CREATE TABLE IF NOT EXISTS protocols (
  id TEXT PRIMARY KEY, name TEXT, first_seen TEXT, category TEXT, chains TEXT,
  tvl REAL, url TEXT, twitter TEXT, audits TEXT, data TEXT
);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def seen(con, key):
    return con.execute("SELECT 1 FROM tokens WHERE key=?", (key,)).fetchone() is not None


def save_token(con, now, p, stage, reasons=(), warnings=(), score=None, info=None, sec=None):
    key = f'{p["network"]}:{p["token"]}'
    con.execute(
        "INSERT OR REPLACE INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, p["network"], p["token"], p["pool"], p["name"], p["dex"], now, p["created_at"],
         stage, json.dumps(list(reasons)), json.dumps(list(warnings)), score,
         json.dumps(info or {}), json.dumps(sec or {}), json.dumps(p)),
    )


def snapshot(con, now, key, p):
    con.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)",
                (key, now, p.get("liquidity_usd"), p.get("fdv_usd"), p.get("vol_24h"), p.get("price_chg_24h")))


def save_protocol(con, now, pr):
    if con.execute("SELECT 1 FROM protocols WHERE id=?", (pr["id"],)).fetchone():
        return False
    con.execute("INSERT INTO protocols VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pr["id"], pr["name"], now, pr["category"], json.dumps(pr["chains"]), pr["tvl"],
                 pr["url"], pr["twitter"], json.dumps(pr.get("audit_links")), json.dumps(pr)))
    return True
