"""Hard rules (cheap, deterministic). Only survivors reach Agent 2."""

# Stage 1: market filters on raw pool data (no extra API calls)
MIN_LIQ_USD = 20_000
MIN_VOL_24H = 15_000
MIN_BUYERS_24H = 40
MAX_FDV_TO_LIQ = 60          # fdv / liquidity too high = thin, easy to dump

# Stage 2: security + project filters
MAX_TAX = 10                 # percent
MAX_TOP10_PCT = 60
MAX_CREATOR_PCT = 20
MIN_LP_LOCKED_EVM = 50       # percent of LP locked/burned
MIN_LIQ_EXOTIC = 40_000      # chains with no security API need more liquidity


def market_pass(p):
    reasons = []
    if (p["liquidity_usd"] or 0) < MIN_LIQ_USD:
        reasons.append("low_liquidity")
    if (p["vol_24h"] or 0) < MIN_VOL_24H:
        reasons.append("low_volume")
    if max(p["buyers_24h"] or 0, (p["buys_24h"] or 0) // 3) < MIN_BUYERS_24H:
        reasons.append("few_buyers")
    if p["fdv_usd"] and p["liquidity_usd"] and p["fdv_usd"] / p["liquidity_usd"] > MAX_FDV_TO_LIQ:
        reasons.append("fdv_liq_ratio")
    return not reasons, reasons


def security_pass(sec, info):
    """Returns (passed, hard_fail_reasons, warnings)."""
    hard, warn = [], []
    if not sec.get("ok"):
        warn.append("no_security_data")
    if sec.get("source") == "goplus-evm":
        if sec.get("honeypot"): hard.append("honeypot")
        if sec.get("cannot_sell_all"): hard.append("cannot_sell_all")
        if sec.get("hidden_owner"): hard.append("hidden_owner")
        if sec.get("take_back_ownership"): hard.append("take_back_ownership")
        if (sec.get("sell_tax") or 0) * 100 > MAX_TAX or (sec.get("buy_tax") or 0) * 100 > MAX_TAX:
            hard.append("high_tax")
        if sec.get("ok") and not sec.get("open_source"): hard.append("not_verified")
        if sec.get("mintable"): warn.append("mintable")
        if sec.get("proxy"): warn.append("proxy_upgradeable")
        if sec.get("pausable"): warn.append("pausable")
        if sec.get("blacklist"): warn.append("blacklist")
        if (sec.get("creator_pct") or 0) * 100 > MAX_CREATOR_PCT: hard.append("creator_holds_big")
        if sec.get("ok") and (sec.get("lp_locked_pct") or 0) * 100 < MIN_LP_LOCKED_EVM:
            warn.append("lp_not_locked")
    elif sec.get("source") == "goplus-sol":
        if sec.get("mintable"): hard.append("mint_authority")
        if sec.get("freezable"): hard.append("freeze_authority")
        if sec.get("balance_mutable"): hard.append("balance_mutable")
        if sec.get("non_transferable"): hard.append("non_transferable")
        if sec.get("closable"): warn.append("closable")

    elif sec.get("source") == "none":
        # small/exotic chain without automated security: only keep if it already has real liquidity
        warn.append("chain_without_security_scan")

    if (info.get("top10_pct") or 0) > MAX_TOP10_PCT:
        hard.append("top10_concentrated")
    if not (info.get("websites") or info.get("twitter")):
        hard.append("no_site_no_twitter")
    return not hard, hard, warn


def exotic_liq_ok(p, sec):
    return sec.get("source") != "none" or (p["liquidity_usd"] or 0) >= MIN_LIQ_EXOTIC


def score(p, sec, info, warn):
    """Rough 0-100 priority so Agent 2 reads the best first. Not a buy signal."""
    s = 50
    s += min(15, (p["liquidity_usd"] or 0) / 20_000)
    s += min(10, (p["buyers_24h"] or 0) / 50)
    s += 5 if info.get("websites") else 0
    s += 5 if info.get("twitter") else 0
    s += 3 if info.get("telegram") or info.get("discord") else 0
    s += 5 if len(info.get("description") or "") > 200 else 0
    s -= 6 * len(warn)
    return max(0, min(100, round(s)))
