"""Protocol Agent entrypoint (no LLM). Every ~4h: live DEX volume/fees per DEX, growth vs last run, new DEXes.
Output: data/protocols/latest.json (small summary that Agent 2 reads) + data/protocols.sqlite (history).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scout import onchain
from . import dex, flows

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "protocols.sqlite"
OUT = ROOT / "data" / "protocols"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_meta (pool TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS dex_seen (key TEXT PRIMARY KEY, first_seen TEXT, stage TEXT);
CREATE TABLE IF NOT EXISTS dex_hist (key TEXT, ts TEXT, volume REAL, fees REAL, pools INTEGER, PRIMARY KEY (key, ts));
CREATE TABLE IF NOT EXISTS vault_hist (key TEXT, ts TEXT, tvl REAL, net_flow REAL, depositors INTEGER, PRIMARY KEY (key, ts));
CREATE TABLE IF NOT EXISTS lend_hist (key TEXT, ts TEXT, borrows REAL, revenue REAL, net_borrow REAL, PRIMARY KEY (key, ts));
"""


def track(con, now, kind, rows, metric, hist_sql, hist_vals, new_min, mover_min):
    """growth vs previous run + first-seen detection (first run per chain = baseline). Returns (movers, new)."""
    movers, new = [], []
    for d in rows:
        key = f'{kind}:{d["chain"]}:{d["address"]}'
        d["key"] = key
        prev = con.execute(f"SELECT {metric[1]} FROM {metric[0]} WHERE key=? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        if prev and prev[0]:
            d["change_pct"] = round((d[metric[2]] / prev[0] - 1) * 100)
        if not con.execute("SELECT 1 FROM dex_seen WHERE key=?", (key,)).fetchone():
            baseline = con.execute("SELECT COUNT(*) FROM dex_seen WHERE key LIKE ?", (f'{kind}:{d["chain"]}:%',)).fetchone()[0] == 0
            con.execute("INSERT INTO dex_seen VALUES (?,?,?)", (key, now, "baseline" if baseline else "new"))
            if not baseline and d[metric[2]] >= new_min:
                new.append(d)
        con.execute(hist_sql, (key, now, *[d[f] for f in hist_vals]))
        if d.get("change_pct", 0) >= 30 and d[metric[2]] >= mover_min:
            movers.append(d)
    movers.sort(key=lambda d: -d["change_pct"])
    return movers[:15], new[:15]


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    cache = {p: json.loads(d) for p, d in con.execute("SELECT pool, data FROM pool_meta")}
    n_cache = len(cache)

    rows, coverage = [], {}
    for chain in onchain.CHAINS:
        try:
            r, cov = dex.scan_chain(chain, cache)
            rows += r
            coverage[chain] = cov
        except Exception as e:
            print(f"[warn] dex {chain}: {e}")
            coverage[chain] = 0
    con.executemany("INSERT OR REPLACE INTO pool_meta VALUES (?,?)",
                    [(p, json.dumps(m)) for p, m in list(cache.items())[n_cache:]])

    new_dexes, movers = [], []
    for d in rows:
        key = f'{d["chain"]}:{d["factory"]}'
        d["key"] = key
        prev = con.execute("SELECT fees, volume FROM dex_hist WHERE key=? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        if prev and prev[0] > 0:
            d["fees_change_pct"] = round((d["fees_4h"] / prev[0] - 1) * 100)
        if not con.execute("SELECT 1 FROM dex_seen WHERE key=?", (key,)).fetchone():
            baseline = con.execute("SELECT COUNT(*) FROM dex_seen WHERE key LIKE ?", (d["chain"] + ":%",)).fetchone()[0] == 0
            con.execute("INSERT INTO dex_seen VALUES (?,?,?)", (key, now, "baseline" if baseline else "new"))
            if not baseline and d["fees_4h"] >= 500:
                new_dexes.append(d)
        con.execute("INSERT OR REPLACE INTO dex_hist VALUES (?,?,?,?,?)", (key, now, d["volume_4h"], d["fees_4h"], d["pools"]))
        if d.get("fees_change_pct", 0) >= 100 and d["fees_4h"] >= 2000:
            movers.append(d)
    vaults, lending, fcov = [], [], {}
    for chain in onchain.CHAINS:
        try:
            v, l, cov = flows.scan_chain(chain)
            vaults += v
            lending += l
            fcov[chain] = cov
        except Exception as e:
            print(f"[warn] flows {chain}: {e}")
            fcov[chain] = 0
    v_movers, v_new = track(con, now, "vault", vaults, ("vault_hist", "tvl", "tvl"),
                            "INSERT OR REPLACE INTO vault_hist VALUES (?,?,?,?,?)", ("tvl", "net_flow_4h", "depositors_sample"),
                            100_000, 250_000)
    l_movers, l_new = track(con, now, "lend", lending, ("lend_hist", "borrows", "borrows"),
                            "INSERT OR REPLACE INTO lend_hist VALUES (?,?,?,?,?)", ("borrows", "revenue_4h", "net_borrow_4h"),
                            100_000, 250_000)
    con.commit()
    con.close()

    rows.sort(key=lambda d: -d["fees_4h"])
    movers.sort(key=lambda d: -d["fees_change_pct"])
    vaults.sort(key=lambda d: -d["tvl"])
    lending.sort(key=lambda d: -d["borrows"])
    inflow = sorted([v for v in vaults if v["net_flow_4h"] >= 50_000], key=lambda d: -d["net_flow_4h"])
    report = {"generated_at": now, "coverage_pct": coverage, "dexes_measured": len(rows),
              "top_fees": rows[:25], "fee_movers": movers[:15], "new_dexes": new_dexes[:15],
              "flows_coverage_pct": fcov, "vaults_measured": len(vaults), "lending_measured": len(lending),
              "top_vaults": vaults[:25], "vault_inflows": inflow[:15], "vault_movers": v_movers, "new_vaults": v_new,
              "top_lending": lending[:20], "lending_movers": l_movers, "new_lending": l_new}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({"dexes": len(rows), "movers": len(movers), "new": len(new_dexes), "coverage": coverage,
                      "vaults": len(vaults), "lending": len(lending), "flows_coverage": fcov}))


if __name__ == "__main__":
    main()
