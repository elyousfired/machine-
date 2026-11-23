"""On-chain protocol discovery (no DefiLlama delay).

Idea: a new protocol that matters starts pulling stablecoins. Every run we read USDC/USDT Transfer
logs of the last window straight from public RPCs, sum inflows per recipient, keep recipients that
are contracts, and flag the ones never seen before ("new money sinks"). Read-only JSON-RPC.
"""
import time
import requests

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
MIN_INFLOW_USD = 250_000
PER_CHAIN_BUDGET_S = 100

CHAINS = {
    "eth": {"rpc": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com"],
            "tokens": {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6, "0xdac17f958d2ee523a2206206994597c13d831ec7": 6}},
    "base": {"rpc": ["https://base-rpc.publicnode.com", "https://mainnet.base.org"],
             "tokens": {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6}},
    "arbitrum": {"rpc": ["https://arbitrum-one-rpc.publicnode.com", "https://arb1.arbitrum.io/rpc"],
                 "tokens": {"0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6, "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6}},
    "bsc": {"rpc": ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org"],
            "tokens": {"0x55d398326f99059ff775485246999027b3197955": 18, "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18}},
}


class RPC:
    def __init__(self, urls):
        self.urls, self.i = urls, 0

    def call(self, method, params):
        for _ in range(len(self.urls) * 2):
            url = self.urls[self.i % len(self.urls)]
            try:
                r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
                js = r.json()
                if "error" in js:
                    raise ValueError(str(js["error"])[:200])
                return js["result"]
            except ValueError:
                raise
            except Exception:
                self.i += 1
                time.sleep(1)
        raise RuntimeError(f"all RPCs failed for {method}")


def _ts(rpc, n):
    return int(rpc.call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def scan_chain(chain, hours=4):
    cfg = CHAINS[chain]
    rpc = RPC(cfg["rpc"])
    t0 = time.time()
    latest = int(rpc.call("eth_blockNumber", []), 16)
    probe = max(1, latest - 2000)
    secs_per_block = max(0.05, (_ts(rpc, latest) - _ts(rpc, probe)) / (latest - probe))
    start = max(1, latest - int(hours * 3600 / secs_per_block))

    inflow, covered_from = {}, latest + 1
    # walk backwards from the newest block so a time cut still covers the most recent part
    hi, step = latest, 2000
    while hi >= start and time.time() - t0 < PER_CHAIN_BUDGET_S:
        lo = max(start, hi - step + 1)
        try:
            logs = rpc.call("eth_getLogs", [{"fromBlock": hex(lo), "toBlock": hex(hi),
                                             "address": list(cfg["tokens"]), "topics": [TRANSFER]}])
        except (ValueError, RuntimeError):
            if step <= 50:
                break
            step //= 2
            continue
        for lg in logs:
            dec = cfg["tokens"].get(lg["address"].lower(), 6)
            to = "0x" + lg["topics"][2][-40:]
            inflow[to] = inflow.get(to, 0) + int(lg["data"], 16) / 10 ** dec
        covered_from, hi = lo, lo - 1
        if len(logs) < 3000 and step < 20000:
            step *= 2

    big = sorted(((a, v) for a, v in inflow.items() if v >= MIN_INFLOW_USD), key=lambda x: -x[1])[:150]
    out = []
    for addr, usd in big:
        if time.time() - t0 > PER_CHAIN_BUDGET_S + 40:
            break
        try:
            if rpc.call("eth_getCode", [addr, "latest"]) in ("0x", "0x0", None):
                continue    # EOA (wallet / CEX hot wallet), not a protocol
        except Exception:
            continue
        out.append({"chain": chain, "address": addr, "inflow_usd": round(usd)})
    hours_cov = round((latest - covered_from + 1) * secs_per_block / 3600, 2)
    return out, hours_cov


def money_sinks():
    """All chains; returns (sinks, coverage_hours_by_chain). Never raises."""
    sinks, cov = [], {}
    for chain in CHAINS:
        try:
            s, h = scan_chain(chain)
            sinks += s
            cov[chain] = h
        except Exception as e:
            print(f"[warn] onchain {chain}: {e}")
            cov[chain] = 0
    return sinks, cov
