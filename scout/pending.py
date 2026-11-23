"""Agent 2 helper: list ONLY projects not analyzed yet, in compact form (saves LLM tokens).

usage:  python -m scout.pending            -> JSON of pending tokens + protocols
        python -m scout.pending --mark KEY [KEY ...]   -> record keys as analyzed
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import db

ANALYZED = Path(__file__).resolve().parent.parent / "data" / "analyzed.json"


def load():
    return json.loads(ANALYZED.read_text()) if ANALYZED.exists() else {}


def mark(keys):
    done = load()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for k in keys:
        done.setdefault(k, now)
    ANALYZED.write_text(json.dumps(done, indent=0, sort_keys=True))
    print(f"marked {len(keys)}; total analyzed {len(done)}")


def pending():
    done = load()
    con = db.connect()
    toks = []
    for key, score, warn, info, sec, mkt in con.execute(
            "SELECT key, score, warnings, info, security, market FROM tokens "
            "WHERE stage='shortlisted' ORDER BY score DESC"):
        if key in done:
            continue
        info, sec, m = json.loads(info), json.loads(sec), json.loads(mkt)
        sec.pop("raw", None)
        toks.append({
            "key": key, "score": score, "warnings": json.loads(warn),
            "name": m.get("name"), "dex": m.get("dex"), "pool": m.get("pool"), "created": m.get("created_at"),
            "liq": m.get("liquidity_usd"), "fdv": m.get("fdv_usd"), "vol24": m.get("vol_24h"),
            "buyers24": m.get("buyers_24h"), "chg24": m.get("price_chg_24h"),
            "site": info.get("websites"), "x": info.get("twitter"), "tg": info.get("telegram"),
            "top10": info.get("top10_pct"), "holders": info.get("holder_count"),
            "desc": (info.get("description") or "")[:400],
            "sec": {k: v for k, v in sec.items() if v not in (None, False, 0, "", [])},
        })
    prots = []
    for pid, data in con.execute("SELECT id, data FROM protocols"):
        key = f"protocol:{pid}"
        if key in done:
            continue
        d = json.loads(data)
        d["key"] = key
        d["description"] = (d.get("description") or "")[:400]
        prots.append(d)
    sinks = [{"key": k, "chain": c, "address": a, "inflow_usd_window": v, "first_seen": f}
             for k, c, a, v, f in con.execute(
                 "SELECT key, chain, address, inflow_usd, first_seen FROM sinks WHERE stage='new' ORDER BY inflow_usd DESC")
             if k not in done]
    print(json.dumps({"tokens": toks, "onchain_new_contracts": sinks, "protocols": prots}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--mark":
        mark(sys.argv[2:])
    else:
        pending()
