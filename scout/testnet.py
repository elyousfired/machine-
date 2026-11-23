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
PER_NET_BUDGET_S = 60

DEFAULT = {
    "sepolia": {"rpc": ["https://ethereum-sepolia-rpc.publicnode.com"], "explorer": "https://sepolia.etherscan.io", "chainid": 11155111},
    "base-sepolia": {"rpc": ["https://base-sepolia-rpc.publicnode.com", "https://sepolia.base.org"], "explorer": "https://sepolia.basescan.org", "chainid": 84532},
    "arbitrum-sepolia": {"rpc": ["https://arbitrum-sepolia-rpc.publicnode.com", "https://sepolia-rollup.arbitrum.io/rpc"], "explorer": "https://sepolia.arbiscan.io", "chainid": 421614},
    "optimism-sepolia": {"rpc": ["https://optimism-sepolia-rpc.publicnode.com", "https://sepolia.optimism.io"], "explorer": "https://sepolia-optimism.etherscan.io", "chainid": 11155420},
    "bsc-testnet": {"rpc": ["https://bsc-testnet-rpc.publicnode.com"], "explorer": "https://testnet.bscscan.com", "chainid": 97},
    "monad-testnet": {"rpc": ["https://testnet-rpc.monad.xyz"], "explorer": "https://testnet.monadexplorer.com", "chainid": 10143},
    "linea-sepolia": {"rpc": ["https://rpc.sepolia.linea.build"], "explorer": "https://sepolia.lineascan.build", "chainid": 59141},
    "scroll-sepolia": {"rpc": ["https://sepolia-rpc.scroll.io"], "explorer": "https://sepolia.scrollscan.com", "chainid": 534351},
}


def load_cfg():
    if not CFG.exists():
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_text(json.dumps(DEFAULT, indent=1))
    return json.loads(CFG.read_text())


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
            c = scan_net(name, cfg)
            found += c
            status[name] = len(c)
        except Exception as e:
            print(f"[warn] testnet {name}: {e}")
            status[name] = "fail"
    return found, status
