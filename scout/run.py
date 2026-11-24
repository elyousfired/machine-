"""Agent 1 entrypoint. Runs every 4h in GitHub Actions. Zero LLM tokens.

Output for Agent 2:  data/shortlist/latest.json  (+ a dated copy)
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import sources, filters, db, onchain, testnet, mints

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "shortlist"
MAX_DEEP_CHECKS = 80      # tokens that get info+security calls per run (rate limits)
MAX_OUTCOME_CHECKS = 120
SCAN_BUDGET_S = 8 * 60    # stop scanning networks after 8 min so deep checks + commit always happen


def main():
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    con = db.connect()
    stats = {"pools": 0, "new": 0, "market_pass": 0, "shortlisted": 0}

    candidates = []

    def consider(p):
        if not p["token"] or not p["pool"]:
            return
        # trending lists include old tokens: only projects whose pool is < 7 days old count as "new"
        if p["created_at"] and p["created_at"] < (now_dt - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"):
            return
        key = f'{p["network"]}:{p["token"]}'
        if db.seen(con, key) or any(c["token"] == p["token"] for c in candidates):
            return
        stats["new"] += 1
        ok, _ = filters.market_pass(p)
        if ok:
            candidates.append(p)
            con.execute("DELETE FROM young WHERE key=?", (key,))
        elif p["created_at"] and p["created_at"] >= (now_dt - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"):
            db.add_young(con, now, p)   # too early to judge: re-check next runs for up to 48h

    # 0) re-check young pools (< 48h) that failed earlier: 30 pools per call
    cutoff48 = (now_dt - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("DELETE FROM young WHERE created_at < ?", (cutoff48,))
    by_net = {}
    for net, pool in con.execute("SELECT network, pool FROM young"):
        by_net.setdefault(net, []).append(pool)
    rechecked, calls = 0, 0
    for net, pools in by_net.items():
        for i in range(0, len(pools), 30):
            if calls >= 90:
                break
            calls += 1
            for p in sources.pools_multi(net, pools[i:i + 30]):
                rechecked += 1
                consider(p)
    stats["young_rechecked"] = rechecked
    stats["young_promoted"] = len(candidates)

    # 1) collect new + trending pools
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
        pools = sources.new_pools(net, pages=2 if net in big else 1)
        if net in big:
            pools += sources.trending_pools(net)
        for p in pools:
            stats["pools"] += 1
            consider(p)
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

    # 3a) on-chain protocol discovery: new contracts pulling big stablecoin inflows
    baseline = con.execute("SELECT COUNT(*) FROM sinks").fetchone()[0] == 0
    raw_sinks, coverage = onchain.money_sinks()
    sinks = []
    for s in raw_sinks:
        known = con.execute("SELECT 1 FROM sinks WHERE key=?", (f'sink:{s["chain"]}:{s["address"]}',)).fetchone()
        # an established contract that simply wasn't in the top list before is not "new": require age < 7d
        old = (not known and not baseline and mints.younger_than_7d(onchain.CHAINS[s["chain"]]["rpc"], s) is False)
        if not known and not baseline and not old:
            try:
                s.update(mints.read_contract(onchain.CHAINS[s["chain"]]["rpc"], s["address"]))
            except Exception:
                pass
            a = s["address"].lower()
            bot_like = (a.startswith("0x000000") or a.startswith("0xc0ffee")
                        or (s.get("kind") == "other" and (s.get("code_bytes") or 0) < 8000))
            if bot_like or s.get("kind") in ("dex_lp", "nft", "token"):
                old = True     # stored, never reported
                stats["sinks_noise_dropped"] = stats.get("sinks_noise_dropped", 0) + 1
        if db.save_sink(con, now, s, baseline, old):
            sinks.append(s)
    for s in raw_sinks:
        db.hist(con, now, f'sink:{s["chain"]}:{s["address"]}', {"inflow_usd": s["inflow_usd"]})
    stats["sinks_checked"] = len(raw_sinks)
    stats["new_sinks"] = len(sinks)
    stats["onchain_hours"] = coverage
    stats["sinks_baseline_run"] = baseline

    # 3b) testnets: contracts suddenly used by many wallets (airdrop farming)
    tn_raw, tn_status = testnet.hot_contracts()
    # a testnet's first scan is a baseline: insert all its rows before judging "new"
    tn_new = []
    for c in tn_raw:
        if db.save_testnet(con, now, c):
            tn_new.append(c)
    for c in tn_raw:
        db.hist(con, now, f'testnet:{c["testnet"]}:{c["address"]}', {"users": c["users_sample"], "txs": c["txs_sample"]})
    stats["testnet_hot"] = len(tn_raw)
    stats["testnet_new"] = len(tn_new)
    stats["testnets"] = tn_status

    # 3c) mints read from blocks: contracts minting to many wallets, then read + classify the contract
    raw_m, m_cov, m_rpcs = mints.mass_mints()
    stages = [(c, db.mint_status(con, now, c)) for c in raw_m]
    new_mints = []
    for c, st in stages:
        if st == "baseline" and mints.younger_than_7d(m_rpcs[c["chain"]], c):
            st = "new"     # deployed this week: report it even during the chain's baseline run
            c["age"] = "<7d"
        if st == "baseline":
            db.save_mint(con, now, c, "baseline")
        elif st == "new":
            mints.enrich(c, m_rpcs[c["chain"]])
            if c.get("kind") == "dex_lp":
                db.save_mint(con, now, c, "skip_lp")
                continue
            new_mints.append(c)
    # bot farms: many new contracts sharing one code template -> keep one representative
    groups = {}
    for c in new_mints:
        groups.setdefault(c.get("code_hash") or c["address"], []).append(c)
    kept = []
    for h, g in groups.items():
        seen_farm = con.execute("SELECT 1 FROM mints WHERE stage='bot_farm' AND data LIKE ?", (f'%"{h}"%',)).fetchone()
        if len(g) >= 3 or seen_farm:
            for c in g:
                db.save_mint(con, now, c, "bot_farm")
            stats.setdefault("mint_farms_dropped", 0)
            stats["mint_farms_dropped"] += len(g)
        else:
            for c in g:
                db.save_mint(con, now, c, "new")
            kept += g
    new_mints = kept
    for c in raw_m:
        db.hist(con, now, f'mint:{c["chain"]}:{c["address"]}', {"recipients": c["recipients"], "mints": c["mints"]})
    stats["mints_hot"] = len(raw_m)
    stats["mints_new"] = len(new_mints)
    stats["mints_chains_ok"] = sum(1 for v in m_cov.values() if v)
    stats["mints_chains"] = len(m_cov)

    # 3d) DefiLlama kept as a secondary, delayed source
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
        due = age < timedelta(days=3) or last_age >= timedelta(hours=20)
        if not due:
            continue
        st = sources.pool_now(net, pool)
        if st:
            db.snapshot(con, now, key, st)
            db.hist(con, now, key, st)
            outcomes += 1
    stats["outcome_snapshots"] = outcomes

    con.commit()
    con.close()

    report = {"generated_at": now, "stats": stats, "tokens": shortlist, "new_sinks": sinks, "new_testnet": tn_new, "new_mints": new_mints, "new_protocols": protocols}
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
