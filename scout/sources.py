"""Data sources for Agent 1. Read-only HTTP calls to free public APIs. No LLM, no keys, no signing."""
import time
import requests

UA = {"User-Agent": "chain-scout/0.1", "Accept": "application/json"}
GT = "https://api.geckoterminal.com/api/v2"
GOPLUS = "https://api.gopluslabs.io/api/v1"
LLAMA = "https://api.llama.fi"

# GeckoTerminal network id -> GoPlus EVM chain id. Networks not listed here still get scanned,
# they just have no automatic contract-security check (Agent 2 must review those manually).
GOPLUS_EVM = {
    "eth": "1", "bsc": "56", "base": "8453", "arbitrum": "42161", "polygon_pos": "137",
    "avax": "43114", "optimism": "10", "ftm": "250", "cro": "25", "linea": "59144",
    "zksync": "324", "scroll": "534352", "mantle": "5000", "blast": "81457", "sonic": "146",
    "opbnb": "204", "manta-pacific": "169", "gnosis": "100", "celo": "42220", "metis": "1088",
    "unichain": "130", "berachain": "80094", "abstract": "2741", "ink": "57073",
}

_last_gt = 0.0


def _get(url, params=None, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"[warn] {url}: {e}")
            time.sleep(2 * (i + 1))
    return None


def _gt(path, params=None):
    """GeckoTerminal free tier: ~30 req/min -> keep >=2.2s between calls."""
    global _last_gt
    wait = 2.2 - (time.time() - _last_gt)
    if wait > 0:
        time.sleep(wait)
    _last_gt = time.time()
    return _get(f"{GT}{path}", params)


def all_networks(max_pages=10):
    """Every network GeckoTerminal indexes (200+), fetched live so new chains join automatically."""
    nets = []
    for page in range(1, max_pages + 1):
        js = _gt("/networks", {"page": page})
        data = (js or {}).get("data") or []
        if not data:
            break
        nets += [d["id"] for d in data if d.get("id")]
    return nets or ["solana", "eth", "bsc", "base", "arbitrum"]


def new_pools(network, pages=1):
    """Newest pools on a network, flattened."""
    out = []
    for page in range(1, pages + 1):
        js = _gt(f"/networks/{network}/new_pools", {"page": page})
        for p in (js or {}).get("data", []):
            a = p.get("attributes", {})
            rel = p.get("relationships", {})
            base_id = (rel.get("base_token", {}).get("data") or {}).get("id", "")
            tx = (a.get("transactions") or {}).get("h24") or {}
            out.append({
                "network": network,
                "pool": a.get("address"),
                "name": a.get("name"),
                "token": base_id.split("_", 1)[1] if "_" in base_id else base_id,
                "dex": (rel.get("dex", {}).get("data") or {}).get("id"),
                "created_at": a.get("pool_created_at"),
                "liquidity_usd": _f(a.get("reserve_in_usd")),
                "fdv_usd": _f(a.get("fdv_usd")),
                "mcap_usd": _f(a.get("market_cap_usd")),
                "vol_24h": _f((a.get("volume_usd") or {}).get("h24")),
                "buys_24h": tx.get("buys") or 0,
                "sells_24h": tx.get("sells") or 0,
                "buyers_24h": tx.get("buyers") or 0,
                "price_chg_24h": _f((a.get("price_change_percentage") or {}).get("h24")),
            })
    return out


def pool_now(network, pool):
    """Current state of one pool (for outcome tracking)."""
    js = _gt(f"/networks/{network}/pools/{pool}")
    a = ((js or {}).get("data") or {}).get("attributes")
    if not a:
        return None
    return {
        "liquidity_usd": _f(a.get("reserve_in_usd")),
        "fdv_usd": _f(a.get("fdv_usd")),
        "vol_24h": _f((a.get("volume_usd") or {}).get("h24")),
        "price_chg_24h": _f((a.get("price_change_percentage") or {}).get("h24")),
        "price_usd": _f(a.get("base_token_price_usd")),
    }


def token_info(network, token):
    """Website, socials, description, holders info from GeckoTerminal."""
    js = _gt(f"/networks/{network}/tokens/{token}/info")
    a = ((js or {}).get("data") or {}).get("attributes") or {}
    holders = a.get("holders") or {}
    return {
        "symbol": a.get("symbol"),
        "websites": a.get("websites") or [],
        "twitter": a.get("twitter_handle"),
        "telegram": a.get("telegram_handle"),
        "discord": a.get("discord_url"),
        "description": (a.get("description") or "")[:1500],
        "gt_score": a.get("gt_score"),
        "holder_count": holders.get("count"),
        "top10_pct": _f((holders.get("distribution_percentage") or {}).get("top_10")),
    }


def security(network, token):
    """GoPlus token security. Normalized to a common dict."""
    if network not in GOPLUS_EVM and network != "solana":
        return {"source": "none", "ok": False}
    chain = GOPLUS_EVM.get(network)
    if network == "solana":
        js = _get(f"{GOPLUS}/solana/token_security", {"contract_addresses": token})
        d = ((js or {}).get("result") or {}).get(token) or {}
        st = lambda k: str((d.get(k) or {}).get("status", "")) == "1"
        return {
            "source": "goplus-sol",
            "ok": bool(d),
            "mintable": st("mintable"),
            "freezable": st("freezable"),
            "closable": st("closable"),
            "balance_mutable": st("balance_mutable_authority"),
            "non_transferable": str(d.get("non_transferable", "0")) == "1",
            "raw": d,
        }
    js = _get(f"{GOPLUS}/token_security/{chain}", {"contract_addresses": token})
    res = (js or {}).get("result") or {}
    d = res.get(token.lower()) or res.get(token) or {}
    one = lambda k: str(d.get(k, "0")) == "1"
    lp = d.get("lp_holders") or []
    lp_locked = sum(_f(h.get("percent")) or 0 for h in lp if str(h.get("is_locked")) == "1")
    return {
        "source": "goplus-evm",
        "ok": bool(d),
        "honeypot": one("is_honeypot"),
        "open_source": one("is_open_source"),
        "proxy": one("is_proxy"),
        "mintable": one("is_mintable"),
        "hidden_owner": one("hidden_owner"),
        "take_back_ownership": one("can_take_back_ownership"),
        "pausable": one("transfer_pausable"),
        "cannot_sell_all": one("cannot_sell_all"),
        "blacklist": one("is_blacklisted"),
        "buy_tax": _f(d.get("buy_tax")),
        "sell_tax": _f(d.get("sell_tax")),
        "owner": d.get("owner_address"),
        "creator_pct": _f(d.get("creator_percent")),
        "lp_locked_pct": lp_locked,
        "holder_count": d.get("holder_count"),
        "raw": {k: d.get(k) for k in ("token_name", "token_symbol", "total_supply", "creator_address")},
    }


def new_llama_protocols(max_age_days=3):
    """Protocols newly listed on DefiLlama (real protocols, not just tokens)."""
    js = _get(f"{LLAMA}/protocols") or []
    cutoff = time.time() - max_age_days * 86400
    out = []
    for p in js:
        if (p.get("listedAt") or 0) >= cutoff:
            out.append({
                "id": p.get("slug") or p.get("name"),
                "name": p.get("name"),
                "url": p.get("url"),
                "twitter": p.get("twitter"),
                "category": p.get("category"),
                "chains": p.get("chains"),
                "tvl": p.get("tvl"),
                "audits": p.get("audits"),
                "audit_links": p.get("audit_links"),
                "token_address": p.get("address"),
                "gecko_id": p.get("gecko_id"),
                "description": (p.get("description") or "")[:1500],
                "listed_at": p.get("listedAt"),
            })
    return out


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
