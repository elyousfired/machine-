"""Agent 1 entrypoint. Runs every 4h in GitHub Actions. Zero LLM tokens.

Output for Agent 2:  data/shortlist/latest.json  (+ a dated copy)
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import sources, filters, db

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "shortlist"
MAX_DEEP_CHECKS = 80      # tokens that get info+security calls per run (rate limits)
MAX_OUTCOME_CHECKS = 40
SCAN_BUDGET_S = 18 * 60    # stop scanning networks after 18 min so deep checks + commit always happen


def main():
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    con = db.connect()
    stats = {"pools": 0, "new": 0, "market_pass": 0, "shortlisted": 0}

    # 1) collect new pools, keep unseen tokens that pass market rules
    candidates = []
    networks = sources.all_networks()
    stats["networks"] = len(networks)
    big = {"solana", "eth", "bsc", "base", "arbitrum"}   # busy chains get 2 pages, always first
    import random
    rest = [n for n in networks if n not in big]
    random.shuffle(rest)          # if the time budget cuts the loop, other chains get covered next runs
    networks = [n for n in networks if n in big] + rest
    t0 = time.time()
    scanned = 0
    for net in networks:
        if time.time() - t0 > SCAN_BUDGET_S:
            break
        scanned += 1
        for p in sources.new_pools(net, pages=2 if net in big else 1):
            stats["pools"] += 1
            if not p["token"]:
                continue
            key = f'{net}:{p["token"]}'
            if db.seen(con, key) or any(c["token"] == p["token"] for c in candidates):
                continue
            stats["new"] += 1
            ok, _ = filters.market_pass(p)   # rejects aren't stored: a young pool may qualify next run
            if ok:
                candidates.append(p)
    stats["networks_scanned"] = scanned
    stats["market_pass"] = len(candidates)
    candidates.sort(key=lambda c: c["liquidity_usd"] or 0, reverse=True)

    # 2) deep checks: socials + security
    shortlist = []
    for p in candidates[:MAX_DEEP_CHECKS]:
        info = sources.token_info(p["network"], p["token"])
        sec = sources.security(p["network"], p["token"])
        ok, hard, warn = filters.security_pass(sec, info)
        if not filters.exotic_liq_ok(p, sec):
            ok, hard = False, hard + ["exotic_chain_low_liquidity"]
        if not ok:
            db.save_token(con, now, p, "rejected_security", hard, warn, info=info, sec=sec)
            continue
        sc = filters.score(p, sec, info, warn)
        db.save_token(con, now, p, "shortlisted", [], warn, sc, info, sec)
        db.snapshot(con, now, f'{p["network"]}:{p["token"]}', p)
        shortlist.append({"score": sc, "warnings": warn, "market": p, "info": info,
                          "security": {k: v for k, v in sec.items() if k != "raw"}})
    stats["shortlisted"] = len(shortlist)
    shortlist.sort(key=lambda s: s["score"], reverse=True)

    # 3) new real protocols (DefiLlama)
    protocols = [pr for pr in sources.new_llama_protocols() if db.save_protocol(con, now, pr)]

    # 4) outcome tracking for past shortlisted tokens (feeds the learning base)
    outcomes = 0
    rows = con.execute(
        "SELECT key, network, pool, first_seen FROM tokens WHERE stage='shortlisted' AND first_seen >= ? "
        "ORDER BY first_seen", ((now_dt - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchall()
    for key, net, pool, first in rows:
        if outcomes >= MAX_OUTCOME_CHECKS:
            break
        age = now_dt - datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        last = con.execute("SELECT MAX(ts) FROM snapshots WHERE key=?", (key,)).fetchone()[0]
        last_age = now_dt - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if last else age
        due = (age < timedelta(days=2) and last_age >= timedelta(hours=20)) or last_age >= timedelta(days=6)
        if not due:
            continue
        st = sources.pool_now(net, pool)
        if st:
            db.snapshot(con, now, key, st)
            outcomes += 1
    stats["outcome_snapshots"] = outcomes

    con.commit()
    con.close()

    report = {"generated_at": now, "stats": stats, "tokens": shortlist, "new_protocols": protocols}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    (OUT / f'{now_dt.strftime("%Y-%m-%d_%H")}.json').write_text(json.dumps(report, ensure_ascii=False))
    # run log for the dashboard (last 300 runs)
    log_path = ROOT / "data" / "runs.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    log.append({"at": now, **stats, "protocols": len(protocols)})
    log_path.write_text(json.dumps(log[-300:], indent=0))
    print(json.dumps(stats))


if __name__ == "__main__":
    sys.exit(main())
