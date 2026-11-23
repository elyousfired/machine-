"""Testnet project discovery (airdrop / early-user farming).

For each testnet in data/testnets.json we sample blocks of the last window over public RPC, count unique
senders per destination contract, and report contracts with many users that we never saw before.
A contract on a testnet that suddenly gets hundreds of different wallets = a project people are farming.
Add/remove testnets by editing data/testnets.json (no code change). Read-only JSON-RPC.
"""
import json
import time
from pathlib import Path

import requests

CFG = Path(__file__).resolve().parent.parent / "data" / "testnets.json"
SAMPLE_BLOCKS = 160
MIN_USERS = 25            # unique senders inside the sample
PER_NET_BUDGET_S = 20

DEFAULT = {
    "sepolia": {"rpc": ["https://ethereum-sepolia-rpc.publicnode.com"], "explorer": "https://sepolia.etherscan.io", "chainid": 11155111},
    "base-sepolia": {"rpc": ["https://base-sepolia-rpc.publicnode.com", "https://sepolia.base.org"], "explorer": "https://sepolia.basescan.org", "chainid": 84532},
    "arbitrum-sepolia": {"rpc": ["https://arbitrum-sepolia-rpc.publicnode.com", "https://sepolia-rollup.arbitrum.io/rpc"], "explorer": "https://sepolia.arbiscan.io", "chainid": 421614},
    "optimism-sepolia": {"rpc": ["https://optimism-sepolia-rpc.publicnode.com", "https://sepolia.optimism.io"], "explorer": "https://sepolia-optimism.etherscan.io", "chainid": 11155420},
    "bsc-testnet": {"rpc": ["https://bsc-testnet-rpc.publicnode.com"], "explorer": "https://testnet.bscscan.com", "chainid": 97},
    "monad-testnet": {"rpc": ["https://testnet-rpc.monad.xyz"], "explorer": "https://testnet.monadexplorer.com", "chainid": 10143},
    "linea-sepolia": {"rpc": ["https://rpc.sepolia.linea.build"], "explorer": "https://sepolia.lineascan.build", "chainid": 59141},
    "scroll-sepolia": {"rpc": ["https://sepolia-rpc.scroll.io"], "explorer": "https://sepolia.scrollscan.com", "chainid": 534351},
    "eth-hoodi": {"rpc": ["https://ethereum-hoodi-rpc.publicnode.com"], "explorer": "https://hoodi.etherscan.io", "chainid": 560048},
    "unichain-sepolia": {"rpc": ["https://sepolia.unichain.org"], "explorer": "https://sepolia.uniscan.xyz", "chainid": 1301},
    "zksync-sepolia": {"rpc": ["https://sepolia.era.zksync.dev"], "explorer": "https://sepolia.explorer.zksync.io", "chainid": 300},
    "mantle-sepolia": {"rpc": ["https://rpc.sepolia.mantle.xyz"], "explorer": "https://sepolia.mantlescan.xyz", "chainid": 5003},
    "blast-sepolia": {"rpc": ["https://sepolia.blast.io"], "explorer": "https://sepolia.blastscan.io", "chainid": 168587773},
    "ink-sepolia": {"rpc": ["https://rpc-gel-sepolia.inkonchain.com"], "explorer": "https://explorer-sepolia.inkonchain.com", "chainid": 763373},
    "berachain-bepolia": {"rpc": ["https://bepolia.rpc.berachain.com"], "explorer": "https://testnet.berascan.com", "chainid": 80069},
    "abstract-testnet": {"rpc": ["https://api.testnet.abs.xyz"], "explorer": "https://sepolia.abscan.org", "chainid": 11124},
    "sonic-testnet": {"rpc": ["https://rpc.blaze.soniclabs.com"], "explorer": "https://testnet.sonicscan.org", "chainid": 57054},
    "megaeth-testnet": {"rpc": ["https://carrot.megaeth.com/rpc"], "explorer": "https://www.megaexplorer.xyz", "chainid": 6342},
    "polygon-amoy": {"rpc": ["https://polygon-amoy-bor-rpc.publicnode.com"], "explorer": "https://amoy.polygonscan.com", "chainid": 80002},
    "avalanche-fuji": {"rpc": ["https://avalanche-fuji-c-chain-rpc.publicnode.com"], "explorer": "https://testnet.snowtrace.io", "chainid": 43113},
    # non-EVM testnets
    "sui-testnet": {"type": "sui", "rpc": ["https://fullnode.testnet.sui.io:443"], "explorer": "https://suiscan.xyz/testnet/object"},
    "aptos-testnet": {"type": "aptos", "rpc": ["https://api.testnet.aptoslabs.com/v1"], "explorer": "https://explorer.aptoslabs.com/account"},
    "solana-devnet": {"type": "solana", "rpc": ["https://api.devnet.solana.com"], "explorer": "https://explorer.solana.com/address", "suffix": "?cluster=devnet"},
    "solana-testnet": {"type": "solana", "rpc": ["https://api.testnet.solana.com"], "explorer": "https://explorer.solana.com/address", "suffix": "?cluster=testnet"},
}

# programs/infra that every tx touches: never a "project"
SOL_SKIP = {"11111111111111111111111111111111", "ComputeBudget111111111111111111111111111111", "Vote111111111111111111111111111111111111111",
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "AddressLookupTab1e1111111111111111111111111", "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
            "BPFLoaderUpgradeab1e11111111111111111111111", "Config1111111111111111111111111111111111111", "Stake11111111111111111111111111111111111111"}


def load_cfg():
    """data/testnets.json overrides/extends DEFAULT. Set "disabled": true on an entry to skip it."""
    user = json.loads(CFG.read_text()) if CFG.exists() else {}
    merged = {**DEFAULT, **user}
    if not CFG.exists():
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_text(json.dumps({}, indent=1))
    return {k: v for k, v in merged.items() if not v.get("disabled")}


def _rpc1(urls, method, params):
    js = _post(urls, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in js:
        raise RuntimeError(str(js["error"])[:200])
    return js["result"]


def _rank(name, cfg, users, txs, t0):
    hot = sorted(((a, len(s)) for a, s in users.items() if len(s) >= MIN_USERS), key=lambda x: -x[1])[:60]
    return [{"testnet": name, "address": a, "users_sample": n, "txs_sample": txs[a],
             "explorer": f'{cfg.get("explorer", "")}/{"address/" if not cfg.get("type") else ""}{a}{cfg.get("suffix", "")}', "chainid": cfg.get("chainid")}
            for a, n in hot]


def scan_sui(name, cfg):
    """Unique senders per Move package over the latest transactions."""
    t0, users, txs, cursor = time.time(), {}, {}, None
    for _ in range(12):
        if time.time() - t0 > PER_NET_BUDGET_S:
            break
        res = _rpc1(cfg["rpc"], "suix_queryTransactionBlocks",
                    [{"options": {"showInput": True}}, cursor, 50, True])
        for tx in res.get("data", []):
            d = ((tx.get("transaction") or {}).get("data") or {})
            sender = d.get("sender")
            for cmd in ((d.get("transaction") or {}).get("transactions") or []):
                mc = cmd.get("MoveCall") if isinstance(cmd, dict) else None
                if mc and mc.get("package") and not mc["package"].startswith("0x0000000000000000000000000000000000000000000000000000000000000"):
                    users.setdefault(mc["package"], set()).add(sender)
                    txs[mc["package"]] = txs.get(mc["package"], 0) + 1
        cursor = res.get("nextCursor")
        if not res.get("hasNextPage"):
            break
    return _rank(name, cfg, users, txs, t0)


def scan_aptos(name, cfg):
    """Unique senders per module account over the latest ~1200 transactions."""
    t0, users, txs = time.time(), {}, {}
    base = cfg["rpc"][0]
    latest = int(requests.get(base, timeout=20).json()["ledger_version"])
    for k in range(12):
        if time.time() - t0 > PER_NET_BUDGET_S:
            break
        start = max(0, latest - (k + 1) * 100)
        for tx in requests.get(f"{base}/transactions", params={"start": start, "limit": 100}, timeout=30).json():
            if tx.get("type") != "user_transaction":
                continue
            fn = ((tx.get("payload") or {}).get("function") or "")
            mod = fn.split("::")[0]
            if not mod or mod in ("0x1", "0x3", "0x4") or len(mod) < 6:
                continue
            users.setdefault(mod, set()).add(tx.get("sender"))
            txs[mod] = txs.get(mod, 0) + 1
    return _rank(name, cfg, users, txs, t0)


def scan_solana(name, cfg):
    """Unique fee payers per program over recent sampled slots."""
    t0, users, txs = time.time(), {}, {}
    slot = _rpc1(cfg["rpc"], "getSlot", [])
    for i in range(40):
        if time.time() - t0 > PER_NET_BUDGET_S:
            break
        try:
            blk = _rpc1(cfg["rpc"], "getBlock", [slot - i * 25, {"encoding": "json", "transactionDetails": "full",
                                                                 "rewards": False, "maxSupportedTransactionVersion": 0}])
        except RuntimeError:
            continue   # skipped slot
        for tx in (blk or {}).get("transactions", []):
            msg = tx["transaction"]["message"]
            keys = msg.get("accountKeys", [])
            if not keys:
                continue
            payer = keys[0]
            for ix in msg.get("instructions", []):
                idx = ix.get("programIdIndex")
                if idx is None or idx >= len(keys) or keys[idx] in SOL_SKIP:
                    continue
                users.setdefault(keys[idx], set()).add(payer)
                txs[keys[idx]] = txs.get(keys[idx], 0) + 1
    return _rank(name, cfg, users, txs, t0)


def _post(urls, payload):
    for url in urls:
        try:
            r = requests.post(url, json=payload, timeout=30)
            js = r.json()
            return js
        except Exception:
            time.sleep(1)
    raise RuntimeError("all RPCs failed")


def scan_net(name, cfg, hours=4):
    t0 = time.time()
    urls = cfg["rpc"]
    latest = int(_post(urls, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})["result"], 16)
    probe = max(1, latest - 1000)
    b = _post(urls, [{"jsonrpc": "2.0", "id": i, "method": "eth_getBlockByNumber", "params": [hex(n), False]}
                     for i, n in enumerate((latest, probe))])
    ts = {x["id"]: int(x["result"]["timestamp"], 16) for x in b}
    spb = max(0.05, (ts[0] - ts[1]) / (latest - probe))
    window = int(hours * 3600 / spb)
    stepb = max(1, window // SAMPLE_BLOCKS)
    nums = [latest - i * stepb for i in range(min(SAMPLE_BLOCKS, window))]

    users, txs = {}, {}
    for i in range(0, len(nums), 20):
        if time.time() - t0 > PER_NET_BUDGET_S:
            break
        batch = [{"jsonrpc": "2.0", "id": n, "method": "eth_getBlockByNumber", "params": [hex(n), True]} for n in nums[i:i + 20]]
        try:
            res = _post(urls, batch)
        except RuntimeError:
            break
        for blk in res if isinstance(res, list) else []:
            for tx in ((blk.get("result") or {}).get("transactions") or []):
                to = (tx.get("to") or "").lower()
                if not to:
                    continue
                users.setdefault(to, set()).add(tx.get("from", "").lower())
                txs[to] = txs.get(to, 0) + 1

    hot = sorted(((a, len(s)) for a, s in users.items() if len(s) >= MIN_USERS), key=lambda x: -x[1])[:60]
    out = []
    if hot:
        codes = _post(urls, [{"jsonrpc": "2.0", "id": k, "method": "eth_getCode", "params": [a, "latest"]}
                             for k, (a, _) in enumerate(hot)])
        is_contract = {c["id"]: c.get("result") not in (None, "0x", "0x0") for c in (codes if isinstance(codes, list) else [])}
        for k, (a, n) in enumerate(hot):
            if is_contract.get(k):
                out.append({"testnet": name, "address": a, "users_sample": n, "txs_sample": txs[a],
                            "explorer": f'{cfg.get("explorer", "")}/address/{a}', "chainid": cfg.get("chainid")})
    return out


def hot_contracts():
    """All configured testnets; never raises. Returns (contracts, per-testnet status)."""
    found, status = [], {}
    for name, cfg in load_cfg().items():
        try:
            kind = cfg.get("type", "evm")
            c = {"sui": scan_sui, "aptos": scan_aptos, "solana": scan_solana}.get(kind, scan_net)(name, cfg)
            found += c
            status[name] = len(c)
        except Exception as e:
            print(f"[warn] testnet {name}: {e}")
            status[name] = "fail"
    return found, status
