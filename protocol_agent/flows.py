"""Protocol Agent — phase 2: ERC-4626 vaults + lending (Aave V3 forks, Compound V2 forks), no LLM.

Same idea as dex.py: sample short block chunks across the 4h window, read the standard events, and group by
contract. Any fork that reuses the event is caught, even unlisted ones. Only assets we can price safely
(stables + wrapped native) are valued.
  vaults : Deposit/Withdraw  -> live TVL (totalAssets), net flow, unique depositors
  lending: Supply/Borrow/Repay (Aave), Mint/Borrow/RepayBorrow (Compound) -> flows, outstanding borrows,
           estimated interest & protocol revenue (borrows x borrow APR x reserve factor)
"""
import time

from scout import onchain
from . import dex

DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"   # ERC-4626
WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"
A_SUPPLY = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"  # Aave V3
A_BORROW = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
A_REPAY = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
C_MINT = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"    # Compound V2 cToken
C_BORROW = "0x13ed6866d4e1ee6da46f845c46d7e54120883d75c5ea9a2dacc1c4ca8984ab80"
C_REPAY = "0x1a2a22cb034d26d1854bdc6666a5b91fe25efbbb5dcad3b0355478d6f5c362a1"
TOPICS = [DEPOSIT, WITHDRAW, A_SUPPLY, A_BORROW, A_REPAY, C_MINT, C_BORROW, C_REPAY]

N_CHUNKS = 60            # these events are rarer than swaps: sample more
CHUNK_SECONDS = 30
PER_CHAIN_BUDGET_S = 120
MIN_TVL = 10_000

NAMES = {"0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2": "Aave V3", "0xc13e21b648a5ee794902342038ff3adab66be987": "Spark",
         "0xa238dd80c259a72e81d7e4664a9801593f98d1c5": "Aave V3", "0x794a61358d6845594f94dc1db02a252b5b4814ad": "Aave V3",
         "0x6807dc923806fe8fd134338eabca509979a7e0cb": "Aave V3", "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b": "Compound V2",
         "0xfd36e2c2a6789db23113685031d7f16329158384": "Venus Core"}


def _w(data):
    return [int(data[2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range((len(data) - 2) // 64)]


def _a(word_hex):
    return "0x" + word_hex[-40:].lower()


def _calls(urls, calls):
    """batched eth_call; returns hex result or None per call."""
    out = []
    for i in range(0, len(calls), 90):
        ch = calls[i:i + 90]
        try:
            res = dex._post(urls, [{"jsonrpc": "2.0", "id": k, "method": "eth_call",
                                    "params": [{"to": t, "data": d}, "latest"]} for k, (t, d) in enumerate(ch)])
        except RuntimeError:
            res = []
        r = {x.get("id"): x.get("result") for x in res} if isinstance(res, list) else {}
        out += [r.get(k) if r.get(k) and len(r.get(k)) > 2 else None for k in range(len(ch))]
    return out


def quotes(chain):
    q = {a: (1.0, d) for a, d in onchain.CHAINS[chain]["tokens"].items()}
    q.update({a: (1.0, d) for a, d in dex.EXTRA_STABLES.get(chain, {}).items()})
    px = dex.native_price(chain)
    if px:
        q[dex.NATIVE[chain][0]] = (px, dex.NATIVE[chain][1])
    return q


def sample(urls, topics, hours, budget_s, curls=None):
    t0 = time.time()
    curls = curls or urls
    latest = int(dex._one(curls, "eth_blockNumber", []), 16)
    probe = max(1, latest - 2000)
    b = dex._post(curls, [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber", "params": [hex(n), False]}
                         for i, n in enumerate((latest, probe))])
    ts = {x["id"]: int(x["result"]["timestamp"], 16) for x in b}
    spb = max(0.05, (ts[0] - ts[1]) / (latest - probe))
    window = int(hours * 3600 / spb)
    clen = max(1, min(dex.MAX_CHUNK_BLOCKS, int(CHUNK_SECONDS / spb)))
    starts = [latest - clen - int(i * (window - clen) / max(1, N_CHUNKS - 1)) for i in range(N_CHUNKS)]
    logs, sampled = [], 0
    for s in starts:
        if time.time() - t0 > budget_s:
            break
        got = dex.get_logs(urls, s, s + clen - 1, topics)
        if got is None:
            continue
        logs += got
        sampled += clen
    if not sampled:
        raise RuntimeError("no chunk could be read")
    return logs, window / sampled, spb, round(sampled / window * 100, 1)


def _vaults(chain, urls, logs, scale, q):
    v = {}
    for lg in logs:
        t, tp = lg["topics"][0], lg["topics"]
        if not ((t == DEPOSIT and len(tp) == 3) or (t == WITHDRAW and len(tp) == 4)):
            continue
        try:
            amt = _w(lg["data"])[0]
        except (IndexError, ValueError):
            continue
        d = v.setdefault(lg["address"].lower(), {"dep": 0, "wd": 0, "owners": set(), "n": 0})
        d["n"] += 1
        if t == DEPOSIT:
            d["dep"] += amt
            d["owners"].add(_a(tp[2]))
        else:
            d["wd"] += amt
    addrs = list(v)
    res = _calls(urls, [(a, s) for a in addrs for s in ("0x38d52e0f", "0x01e1d114")])   # asset(), totalAssets()
    out = []
    for i, a in enumerate(addrs):
        asset, ta = res[2 * i], res[2 * i + 1]
        if not asset or not ta or _a(asset) not in q:
            continue
        px, dec = q[_a(asset)]
        k = px / 10 ** dec
        tvl = int(ta[:66], 16) * k
        if tvl < MIN_TVL:
            continue
        d = v[a]
        out.append({"chain": chain, "address": a, "asset": _a(asset), "tvl": round(tvl),
                    "deposits_4h": round(d["dep"] * k * scale), "withdrawals_4h": round(d["wd"] * k * scale),
                    "net_flow_4h": round((d["dep"] - d["wd"]) * k * scale),
                    "depositors_sample": len(d["owners"]), "events_sample": d["n"]})
    return out


def _aave(chain, urls, logs, scale, q):
    pools = {}
    for lg in logs:
        t, tp = lg["topics"][0], lg["topics"]
        if t not in (A_SUPPLY, A_BORROW, A_REPAY) or len(tp) != 4:
            continue
        reserve = _a(tp[1])
        if reserve not in q:
            continue
        try:
            w = _w(lg["data"])
            amt = w[0] if t == A_REPAY else w[1]
        except (IndexError, ValueError):
            continue
        p = pools.setdefault(lg["address"].lower(), {"res": {}, "users": set()})
        r = p["res"].setdefault(reserve, [0, 0, 0])
        r[(A_SUPPLY, A_BORROW, A_REPAY).index(t)] += amt
        p["users"].add(_a(tp[2]))
    pairs = [(p, r) for p in pools for r in pools[p]["res"]]
    rd = _calls(urls, [(p, "0x35ea6a75" + r[2:].rjust(64, "0")) for p, r in pairs])     # getReserveData
    debt = {}
    for (p, r), x in zip(pairs, rd):
        if x and len(x) >= 2 + 64 * 11:
            w = [x[2 + 64 * i: 2 + 64 * (i + 1)] for i in range(11)]
            debt[(p, r)] = (_a(w[10]), int(w[4], 16) / 1e27, (int(w[0], 16) >> 64) & 0xFFFF)
    keys = list(debt)
    ts = _calls(urls, [(debt[k][0], "0x18160ddd") for k in keys])                       # totalSupply()
    out = []
    for p, d in pools.items():
        row = {"chain": chain, "address": p, "type": "aave", "name": NAMES.get(p, ""), "markets": len(d["res"]),
               "users_sample": len(d["users"]), "supply_4h": 0, "borrow_4h": 0, "repay_4h": 0,
               "borrows": 0, "interest_4h": 0, "revenue_4h": 0}
        for r, (s, b, rp) in d["res"].items():
            k = q[r][0] / 10 ** q[r][1]
            row["supply_4h"] += s * k * scale
            row["borrow_4h"] += b * k * scale
            row["repay_4h"] += rp * k * scale
            if (p, r) in debt:
                tot = ts[keys.index((p, r))]
                apr, rf = debt[(p, r)][1], debt[(p, r)][2]
                if tot and apr <= 2:
                    bor = int(tot[:66], 16) * k
                    row["borrows"] += bor
                    row["interest_4h"] += bor * apr * 4 / 8760
                    row["revenue_4h"] += bor * apr * 4 / 8760 * rf / 1e4
        out.append(row)
    return out


def _compound(chain, urls, logs, scale, q, spb):
    ct = {}
    for lg in logs:
        t = lg["topics"][0]
        if t not in (C_MINT, C_BORROW, C_REPAY) or len(lg["topics"]) != 1:      # Uni V2 Mint has an indexed sender
            continue
        try:
            w = _w(lg["data"])
            amt, user = (w[2], w[1]) if t == C_REPAY else (w[1], w[0])
        except (IndexError, ValueError):
            continue
        c = ct.setdefault(lg["address"].lower(), {"f": [0, 0, 0], "users": set()})
        c["f"][(C_MINT, C_BORROW, C_REPAY).index(t)] += amt
        c["users"].add(hex(user))
    addrs = list(ct)
    sels = ("0x6f307dc3", "0x5fe3b567", "0x47bd3718", "0xf8f9da28", "0x173b9904")
    # underlying, comptroller, totalBorrows, borrowRatePerBlock, reserveFactorMantissa
    res = _calls(urls, [(a, s) for a in addrs for s in sels])
    nat = dex.NATIVE[chain][0]
    per_year = 365 * 86400 / spb
    groups = {}
    for i, a in enumerate(addrs):
        und, comp, tb, rate, rf = res[5 * i: 5 * i + 5]
        if not comp:
            continue
        und = _a(und) if und else nat                       # cETH / vBNB style markets have no underlying()
        if und not in q:
            continue
        k = q[und][0] / 10 ** q[und][1]
        g = groups.setdefault(_a(comp), {"chain": chain, "address": _a(comp), "type": "compound", "name": NAMES.get(_a(comp), ""),
                                         "markets": 0, "users": set(), "supply_4h": 0, "borrow_4h": 0, "repay_4h": 0,
                                         "borrows": 0, "interest_4h": 0, "revenue_4h": 0})
        f = ct[a]["f"]
        g["markets"] += 1
        g["users"] |= ct[a]["users"]
        g["supply_4h"] += f[0] * k * scale
        g["borrow_4h"] += f[1] * k * scale
        g["repay_4h"] += f[2] * k * scale
        if tb and rate:
            apr = int(rate[:66], 16) / 1e18 * per_year
            bor = int(tb[:66], 16) * k
            g["borrows"] += bor
            if apr <= 2:
                g["interest_4h"] += bor * apr * 4 / 8760
                g["revenue_4h"] += bor * apr * 4 / 8760 * (int(rf[:66], 16) / 1e18 if rf else 0)
    out = []
    for g in groups.values():
        g["users_sample"] = len(g.pop("users"))
        out.append(g)
    return out


def scan_chain(chain, hours=4):
    urls, curls = dex.PA_RPC[chain], dex.call_urls(chain)
    q = quotes(chain)
    logs, scale, spb, cov = sample(urls, TOPICS, hours, PER_CHAIN_BUDGET_S, curls)
    vaults = _vaults(chain, curls, logs, scale, q)
    lending = _aave(chain, curls, logs, scale, q) + _compound(chain, curls, logs, scale, q, spb)
    for r in lending:
        for f in ("supply_4h", "borrow_4h", "repay_4h", "borrows", "interest_4h", "revenue_4h"):
            r[f] = round(r[f])
        r["net_borrow_4h"] = r["borrow_4h"] - r["repay_4h"]
    lending = [r for r in lending if r["borrows"] >= MIN_TVL or r["supply_4h"] + r["borrow_4h"] >= MIN_TVL]
    return vaults, lending, cov
