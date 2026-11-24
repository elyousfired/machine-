"""Protocol Agent — phase 1: live DEX volume & fees from Swap events (no LLM, no DefiLlama).

Every DEX that reuses the Uniswap V2 / Solidly / V3 / PancakeV3 Swap event is measured, including
brand-new forks nobody lists yet. We sample short block chunks spread over the window (public RPC
friendly) and scale up. Pools are grouped by their factory() = one DEX. A swap is valued only when one
side is a known quote token (stablecoin or wrapped native), so we never trust a meme's own price.
"""
import time

import requests

from scout import onchain, sources

V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"      # amount0In,1In,0Out,1Out
SOLIDLY = "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b"  # Aerodrome/Velodrome style
V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"      # amount0, amount1 (int256)
PCS_V3 = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"
KIND = {V2: "v2", SOLIDLY: "v2", V3: "v3", PCS_V3: "v3"}

N_CHUNKS = 24            # sampled chunks across the window
CHUNK_SECONDS = 30       # each chunk covers ~30s of blocks
PER_CHAIN_BUDGET_S = 150

# wrapped native per chain (priced once per run via GeckoTerminal)
NATIVE = {"eth": ("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", 18),
          "base": ("0x4200000000000000000000000000000000000006", 18),
          "arbitrum": ("0x82af49447d8a07e3bd95bd0d56f35241523fbab1", 18),
          "bsc": ("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", 18)}
EXTRA_STABLES = {"base": {"0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": 6},       # USDT on Base
                 "eth": {"0x6b175474e89094c44da98b954eedeac495271d0f": 18}}        # DAI

FACTORY_NAMES = {
    "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f": "Uniswap V2", "0x1f98431c8ad98523631ae4a59f267346ea31f984": "Uniswap V3",
    "0x8909dc15e40173ff4699343b6eb8132c65e18ec6": "Uniswap V2", "0x33128a8fc17869897dce68ed026d694621f6fdfd": "Uniswap V3",
    "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac": "SushiSwap V2", "0xbaceb8ec6b9355dfc0269c18bac9d6e2bdc29c4f": "SushiSwap V3",
    "0xca143ce32fe78f1f7019d7d551a6402fc5350c73": "PancakeSwap V2", "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865": "PancakeSwap V3",
    "0x420dd381b31aef6683db6b902084cb0ffece40da": "Aerodrome V2", "0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a": "Aerodrome Slipstream",
    "0xf1d7cc64fb4452f05c498126312ebe29f30fbcf9": "Uniswap V2", "0x02a84c1b3bbd7401a5f7fa98a384ebc70bb5749e": "PancakeSwap V3",
    "0xdb1d10011ad0ff90774d0c6bb92e5c5c8b4461f7": "Uniswap V3", "0x0227628f3f023bb0b980b67d528571c95c6dac1c": "Uniswap V3",
}


# publicnode refuses address-less getLogs, so the Protocol Agent uses its own RPC list (tested 2026-11-24)
PA_RPC = {"eth": ["https://eth.drpc.org", "https://rpc.mevblocker.io", "https://ethereum-rpc.publicnode.com"],
          "base": ["https://base.drpc.org", "https://1rpc.io/base", "https://mainnet.base.org"],
          "arbitrum": ["https://arbitrum.drpc.org", "https://arb1.arbitrum.io/rpc"],
          "bsc": ["https://rpc-bsc.48.club", "https://1rpc.io/bnb", "https://bsc.drpc.org"]}
MAX_CHUNK_BLOCKS = 45    # 1rpc caps getLogs at 50 blocks


def get_logs(urls, frm, to, topics):
    """try each RPC in turn (rate limits / address-less refusals differ per provider); None if all fail."""
    for u in urls:
        try:
            return _one([u], "eth_getLogs", [{"fromBlock": hex(frm), "toBlock": hex(to), "topics": [topics]}])
        except (ValueError, RuntimeError):
            continue
    return None


def call_urls(chain):
    """eth_call / blocks go to publicnode first (fine for calls), keeping drpc's public quota for getLogs."""
    return onchain.CHAINS[chain]["rpc"] + PA_RPC[chain]


def _post(urls, payload):
    """next RPC on exception OR on an error response (rate limits come back as JSON errors)."""
    for url in urls:
        try:
            js = requests.post(url, json=payload, timeout=40).json()
        except Exception:
            time.sleep(1)
            continue
        bad = (isinstance(js, dict) and "error" in js) if isinstance(payload, dict) else not isinstance(js, list)
        if isinstance(payload, list) and isinstance(js, list) and js and all("error" in x for x in js):
            bad = True
        if not bad:
            return js
    raise RuntimeError("rpc failed")


def _one(urls, method, params):
    try:
        js = _post(urls, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    except RuntimeError as e:
        raise ValueError(f"{method}: all rpcs failed") from e
    return js["result"]


def native_price(chain):
    addr = NATIVE[chain][0]
    js = sources._gt(f"/simple/networks/{chain}/token_price/{addr}")
    try:
        return float(js["data"]["attributes"]["token_prices"][addr])
    except Exception:
        return None


def pool_meta(urls, pools, cache):
    """token0/token1/factory/fee for unknown pools, batched 30 pools (120 calls) per request; cached in DB."""
    todo = [p for p in pools if not (cache.get(p) or {}).get("factory")]   # retry pools a rate-limited run left empty
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        batch = []
        for j, p in enumerate(chunk):
            for k, sel in enumerate(("0x0dfe1681", "0xd21220a7", "0xc45a0155", "0xddca3f43")):
                batch.append({"jsonrpc": "2.0", "id": j * 4 + k, "method": "eth_call",
                              "params": [{"to": p, "data": sel}, "latest"]})
        try:
            res = _post(urls, batch)
        except RuntimeError:
            break
        r = {x.get("id"): x.get("result") for x in (res if isinstance(res, list) else [])}
        for j, p in enumerate(chunk):
            a = lambda k: ("0x" + r[j * 4 + k][-40:]) if r.get(j * 4 + k) and len(r[j * 4 + k]) >= 42 else None
            fee = r.get(j * 4 + 3)
            cache[p] = {"t0": a(0), "t1": a(1), "factory": a(2),
                        "fee": int(fee, 16) / 1e6 if fee and len(fee) > 2 and int(fee, 16) < 1_000_000 else None}
    return cache


def _signed(h):
    v = int(h, 16)
    return v - (1 << 256) if v >= 1 << 255 else v


def scan_chain(chain, cache, hours=4):
    urls, curls = PA_RPC[chain], call_urls(chain)
    t0 = time.time()
    quotes = {a: (1.0, d) for a, d in onchain.CHAINS[chain]["tokens"].items()}
    quotes.update({a: (1.0, d) for a, d in EXTRA_STABLES.get(chain, {}).items()})
    px = native_price(chain)
    if px:
        quotes[NATIVE[chain][0]] = (px, NATIVE[chain][1])

    latest = int(_one(curls, "eth_blockNumber", []), 16)
    probe = max(1, latest - 2000)
    b = _post(curls, [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber", "params": [hex(n), False]}
                     for i, n in enumerate((latest, probe))])
    ts = {x["id"]: int(x["result"]["timestamp"], 16) for x in b}
    spb = max(0.05, (ts[0] - ts[1]) / (latest - probe))
    window = int(hours * 3600 / spb)
    clen = max(1, min(MAX_CHUNK_BLOCKS, int(CHUNK_SECONDS / spb)))
    starts = [latest - clen - int(i * (window - clen) / max(1, N_CHUNKS - 1)) for i in range(N_CHUNKS)]

    swaps, sampled_blocks = {}, 0     # pool -> list of (kind, data)
    for s in starts:
        if time.time() - t0 > PER_CHAIN_BUDGET_S:
            break
        logs = get_logs(urls, s, s + clen - 1, [V2, SOLIDLY, V3, PCS_V3])
        if logs is None:
            continue
        sampled_blocks += clen
        for lg in logs:
            swaps.setdefault(lg["address"].lower(), []).append((KIND[lg["topics"][0]], lg["data"]))
    if not sampled_blocks:
        raise RuntimeError("no chunk could be read")
    scale = window / sampled_blocks

    pool_meta(curls, list(swaps), cache)
    dexes = {}
    for pool, evs in swaps.items():
        m = cache.get(pool) or {}
        t0_, t1_ = m.get("t0"), m.get("t1")
        q = 0 if t0_ in quotes else 1 if t1_ in quotes else None
        if q is None or not m.get("factory"):
            continue
        price, dec = quotes[t0_ if q == 0 else t1_]
        vol = 0.0
        for kind, data in evs:
            w = [data[2 + 64 * i: 2 + 64 * (i + 1)] for i in range((len(data) - 2) // 64)]
            try:
                if kind == "v2":
                    raw = int(w[q], 16) + int(w[q + 2], 16)          # amountIn + amountOut on the quote side
                else:
                    raw = abs(_signed(w[q]))
            except (IndexError, ValueError):
                continue
            vol += raw / 10 ** dec * price
        fee_rate = m.get("fee") if m.get("fee") is not None else 0.003
        f = m["factory"]
        d = dexes.setdefault(f, {"chain": chain, "factory": f, "name": FACTORY_NAMES.get(f, ""),
                                 "volume_4h": 0.0, "fees_4h": 0.0, "pools": 0, "swaps_sampled": 0})
        d["volume_4h"] += vol * scale
        d["fees_4h"] += vol * scale * fee_rate
        d["pools"] += 1
        d["swaps_sampled"] += len(evs)
    for d in dexes.values():
        d["volume_4h"], d["fees_4h"] = round(d["volume_4h"]), round(d["fees_4h"])
    coverage = round(sampled_blocks / window * 100, 1)
    return list(dexes.values()), coverage
