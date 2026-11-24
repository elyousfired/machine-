"""Mint detection straight from blocks + smart-contract reading (EVM, mainnets and testnets).

Every ERC20/ERC721/ERC1155-style mint is a Transfer log whose `from` is 0x0. getLogs can filter on that
(topic1 = zero), so one call per block range returns every mint on the chain. We group by contract:
 - memes mint once to the deployer  -> ignored
 - protocols mint to MANY wallets   -> vault shares, staking receipts, LP, NFT/badge campaigns, points tokens
Then we read the contract over RPC (name/symbol/supply/proxy + function selectors in bytecode) and classify it.
Read-only JSON-RPC, never raises.
"""
import hashlib
import re
import time
import requests

from . import onchain, testnet

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64
MIN_RECIPIENTS = 20
PER_CHAIN_BUDGET_S = 20
EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

MAINNETS = {k: {"rpc": v["rpc"]} for k, v in onchain.CHAINS.items()}
MAINNETS.update({
    "optimism": {"rpc": ["https://optimism-rpc.publicnode.com", "https://mainnet.optimism.io"]},
    "polygon": {"rpc": ["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com"]},
    "avax": {"rpc": ["https://avalanche-c-chain-rpc.publicnode.com", "https://api.avax.network/ext/bc/C/rpc"]},
    "linea": {"rpc": ["https://rpc.linea.build"]},
    "scroll": {"rpc": ["https://rpc.scroll.io"]},
    "mantle": {"rpc": ["https://rpc.mantle.xyz"]},
    "blast": {"rpc": ["https://rpc.blast.io"]},
    "sonic": {"rpc": ["https://rpc.soniclabs.com"]},
    "unichain": {"rpc": ["https://mainnet.unichain.org"]},
    "berachain": {"rpc": ["https://rpc.berachain.com"]},
    "ink": {"rpc": ["https://rpc-gel.inkonchain.com"]},
    "hyperevm": {"rpc": ["https://rpc.hyperliquid.xyz/evm"]},
    "monad": {"rpc": ["https://rpc.monad.xyz"]},
    "abstract": {"rpc": ["https://api.mainnet.abs.xyz"]},
})

# function selectors searched as PUSH4 (0x63 + selector) in runtime bytecode
SELECTORS = {
    "erc20": ["a9059cbb", "70a08231", "18160ddd"],
    "erc721": ["6352211e", "42842e0e"],
    "erc1155": ["f242432a", "00fdd58e"],
    "vault_4626": ["6e553f65", "38d52e0f"],
    "public_mint": ["1249c58b", "a0712d68", "40c10f19"],
    "staking": ["a694fc3a", "3d18b912"],
    "lending": ["c5ebeaec", "a415bcad", "0e752702"],
    "dex_pair": ["0902f1ac"],
    "owner": ["8da5cb5b"],
    "pausable": ["8456cb59"],
    "blacklist": ["f9f92be4", "44337ea1"],
}


def _post(urls, payload):
    for url in urls:
        try:
            return requests.post(url, json=payload, timeout=30).json()
        except Exception:
            time.sleep(1)
    raise RuntimeError("rpc failed")


def _one(urls, method, params):
    js = _post(urls, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(js, dict) or "error" in js:
        raise ValueError(str(js)[:200])
    return js["result"]


def _str(hexdata):
    """Decode an ABI string (or bytes32) return value."""
    try:
        b = bytes.fromhex(hexdata[2:])
        if len(b) >= 64:
            n = int.from_bytes(b[32:64], "big")
            if n < 200:
                return b[64:64 + n].decode("utf-8", "ignore").strip()
        return b[:32].rstrip(b"\x00").decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def read_contract(urls, addr):
    """Read a contract on-chain: metadata + what kind of contract it is."""
    calls = [("name", "0x06fdde03"), ("symbol", "0x95d89b41"), ("totalSupply", "0x18160ddd"), ("decimals", "0x313ce567")]
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call", "params": [{"to": addr, "data": d}, "latest"]}
             for i, (_, d) in enumerate(calls)]
    batch += [{"jsonrpc": "2.0", "id": 10, "method": "eth_getCode", "params": [addr, "latest"]},
              {"jsonrpc": "2.0", "id": 11, "method": "eth_getStorageAt", "params": [addr, EIP1967_IMPL, "latest"]}]
    res = _post(urls, batch)
    r = {x.get("id"): x.get("result") for x in (res if isinstance(res, list) else [])}
    code = r.get(10) or "0x"
    impl_slot = r.get(11) or "0x0"
    info = {"name": _str(r[0]) if r.get(0) else "", "symbol": _str(r[1]) if r.get(1) else "", "code_bytes": (len(code) - 2) // 2}
    try:
        dec = int(r.get(3) or "0x0", 16)
        info["decimals"] = dec if dec < 80 else None
        info["total_supply"] = int(r.get(2) or "0x0", 16) / 10 ** (dec if dec < 80 else 0)
    except ValueError:
        pass
    impl = "0x" + impl_slot[-40:] if impl_slot and int(impl_slot, 16) else None
    clone = re.match(r"^0x363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3", code.lower())
    if clone and not impl:
        impl = "0x" + clone.group(1)          # EIP-1167 minimal proxy (clone)
        info["clone"] = True
    if impl:
        info["proxy_impl"] = impl
        try:
            code = _one(urls, "eth_getCode", [impl, "latest"]) or code   # classify the implementation
        except Exception:
            pass
    c = code.lower()
    info["code_hash"] = hashlib.sha256(c.encode()).hexdigest()[:16]   # same hash = same contract template (bot farms)
    info["features"] = [k for k, sels in SELECTORS.items() if any(("63" + s) in c for s in sels)]
    f = set(info["features"])
    info["kind"] = ("vault" if "vault_4626" in f else "lending" if "lending" in f else "staking" if "staking" in f
                    else "dex_lp" if "dex_pair" in f else "nft" if f & {"erc721", "erc1155"} else
                    "token_public_mint" if "public_mint" in f and "erc20" in f else "token" if "erc20" in f else "other")
    return info


def scan_chain(chain, urls, hours=4):
    t0 = time.time()
    latest = int(_one(urls, "eth_blockNumber", []), 16)
    probe = max(1, latest - 1000)
    b = _post(urls, [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber", "params": [hex(n), False]}
                     for i, n in enumerate((latest, probe))])
    ts = {x["id"]: int(x["result"]["timestamp"], 16) for x in b}
    spb = max(0.05, (ts[0] - ts[1]) / (latest - probe))
    start = max(1, latest - int(hours * 3600 / spb))

    agg, hi, step, covered = {}, latest, 5000, latest + 1
    while hi >= start and time.time() - t0 < PER_CHAIN_BUDGET_S:
        lo = max(start, hi - step + 1)
        try:
            logs = _one(urls, "eth_getLogs", [{"fromBlock": hex(lo), "toBlock": hex(hi), "topics": [TRANSFER, ZERO_TOPIC]}])
        except (ValueError, RuntimeError):
            if step <= 50:
                break
            step //= 2
            continue
        for lg in logs:
            if len(lg["topics"]) < 3:
                continue
            a = lg["address"].lower()
            e = agg.setdefault(a, {"mints": 0, "to": set(), "std": "erc721" if len(lg["topics"]) == 4 else "erc20"})
            e["mints"] += 1
            e["to"].add(lg["topics"][2][-40:])
        covered, hi = lo, lo - 1
        if len(logs) < 2000 and step < 50000:
            step *= 2

    hot = sorted(((a, e) for a, e in agg.items() if len(e["to"]) >= MIN_RECIPIENTS), key=lambda x: -len(x[1]["to"]))[:40]
    b7 = max(1, latest - int(7 * 86400 / spb))
    out = [{"chain": chain, "address": a, "mints": e["mints"], "recipients": len(e["to"]), "std": e["std"], "block_7d": b7}
           for a, e in hot]
    return out, round((latest - covered + 1) * spb / 3600, 2)


def younger_than_7d(urls, c):
    """True if the contract had no code 7 days ago (needs an archive-capable RPC; None if unknown)."""
    try:
        code = _one(urls, "eth_getCode", [c["address"], hex(c["block_7d"])])
        return code in ("0x", "0x0")
    except Exception:
        return None


def enrich(c, urls):
    try:
        c.update(read_contract(urls, c["address"]))
    except Exception as e:
        c["read_error"] = str(e)[:100]
    return c


def all_chains():
    nets = {k: v["rpc"] for k, v in MAINNETS.items()}
    for k, v in testnet.load_cfg().items():
        if v.get("type", "evm") == "evm":
            nets[k] = v["rpc"]
    return nets


def mass_mints():
    """Returns (contracts_minting_to_many_wallets, coverage_hours_by_chain, rpcs_by_chain)."""
    found, cov, nets = [], {}, all_chains()
    for chain, urls in nets.items():
        try:
            c, h = scan_chain(chain, urls)
            found += c
            cov[chain] = h
        except Exception as e:
            print(f"[warn] mints {chain}: {e}")
            cov[chain] = 0
    return found, cov, nets
