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
CREATE TABLE IF NOT EXISTS young (       -- pools that failed the market filter while < 48h old: re-checked each run
  key TEXT PRIMARY KEY, network TEXT, pool TEXT, created_at TEXT, first_seen TEXT
);
CREATE TABLE IF NOT EXISTS sinks (       -- contracts receiving big stablecoin inflows (on-chain protocol discovery)
  key TEXT PRIMARY KEY, chain TEXT, address TEXT, first_seen TEXT, stage TEXT, inflow_usd REAL, last_inflow_usd REAL
);
CREATE TABLE IF NOT EXISTS testnet (     -- testnet contracts with many unique users (airdrop farming targets)
  key TEXT PRIMARY KEY, testnet TEXT, address TEXT, first_seen TEXT, stage TEXT, users REAL, last_users REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS mints (       -- contracts minting to many wallets (protocol deposits, NFT/badge campaigns)
  key TEXT PRIMARY KEY, chain TEXT, address TEXT, first_seen TEXT, stage TEXT, recipients REAL, last_recipients REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS history (     -- time series for every tracked item (tokens, mints, sinks, testnet) -> outcome learning
  key TEXT, ts TEXT, data TEXT, PRIMARY KEY (key, ts)
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


def add_young(con, now, p):
    con.execute("INSERT OR IGNORE INTO young VALUES (?,?,?,?,?)",
                (f'{p["network"]}:{p["token"]}', p["network"], p["pool"], p["created_at"], now))


def save_sink(con, now, s, baseline, old=False):
    """Returns True only for a sink never seen before (and not during the baseline run)."""
    key = f'sink:{s["chain"]}:{s["address"]}'
    row = con.execute("SELECT 1 FROM sinks WHERE key=?", (key,)).fetchone()
    if row:
        con.execute("UPDATE sinks SET last_inflow_usd=? WHERE key=?", (s["inflow_usd"], key))
        return False
    con.execute("INSERT INTO sinks VALUES (?,?,?,?,?,?,?)",
                (key, s["chain"], s["address"], now, "baseline" if baseline else "old" if old else "new",
                 s["inflow_usd"], s["inflow_usd"]))
    return not baseline and not old


def save_testnet(con, now, c):
    """New hot testnet contract -> True. The first scan of each testnet is a silent baseline."""
    key = f'testnet:{c["testnet"]}:{c["address"]}'
    if con.execute("SELECT 1 FROM testnet WHERE key=?", (key,)).fetchone():
        con.execute("UPDATE testnet SET last_users=? WHERE key=?", (c["users_sample"], key))
        return False
    baseline = con.execute("SELECT COUNT(*) FROM testnet WHERE testnet=?", (c["testnet"],)).fetchone()[0] == 0
    con.execute("INSERT INTO testnet VALUES (?,?,?,?,?,?,?,?)",
                (key, c["testnet"], c["address"], now, "baseline" if baseline else "new",
                 c["users_sample"], c["users_sample"], json.dumps(c)))
    return not baseline


def mint_status(con, now, c):
    """'new' for a never-seen contract (unless its chain is being baselined), else None. Updates counters."""
    key = f'mint:{c["chain"]}:{c["address"]}'
    if con.execute("SELECT 1 FROM mints WHERE key=?", (key,)).fetchone():
        con.execute("UPDATE mints SET last_recipients=? WHERE key=?", (c["recipients"], key))
        return None
    baseline = con.execute("SELECT COUNT(*) FROM mints WHERE chain=?", (c["chain"],)).fetchone()[0] == 0
    return "baseline" if baseline else "new"


def save_mint(con, now, c, stage):
    con.execute("INSERT OR IGNORE INTO mints VALUES (?,?,?,?,?,?,?,?)",
                (f'mint:{c["chain"]}:{c["address"]}', c["chain"], c["address"], now, stage,
                 c["recipients"], c["recipients"], json.dumps(c)))


def hist(con, now, key, data):
    con.execute("INSERT OR REPLACE INTO history VALUES (?,?,?)", (key, now, json.dumps(data)))
