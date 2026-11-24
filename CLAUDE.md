# CLAUDE.md — machine- (crypto scouting system)

The owner speaks Moroccan Darija: reply in Darija written in **Arabic letters**. Keep replies short; the owner is
sensitive to token usage — prefer code over long explanations, and avoid polling loops.

## What this is
Three agents that find new crypto projects/protocols and ways to profit from them. Read-only: nothing here may
sign, trade, hold keys or send transactions. The repo is **public**: no secrets, no private verdicts/reports in it.

| Agent | Where | LLM? | What |
|---|---|---|---|
| Agent 1 `scout/` | GitHub Actions `agent1-scan` (hourly tick, 3h40 guard) | no | Scans 222 networks (GeckoTerminal) + on-chain: stablecoin inflows to new contracts (`onchain.py`), mass mints from blocks + contract reading/classification (`mints.py`), 24 testnets EVM + Sui/Aptos/Solana (`testnet.py`), DefiLlama (secondary). Writes `data/scout.sqlite`, `data/shortlist/latest.json`, `data/runs.json` |
| Protocol Agent `protocol_agent/` | GitHub Actions `protocol-agent` (hourly tick, 3h40 guard) | no | Live DEX volume & fees from Swap events (Uni V2/V3, PCS V3, Solidly) on eth/base/arbitrum/bsc, grouped by factory. Writes `data/protocols/latest.json` (`top_fees`, `fee_movers`, `new_dexes`) + `data/protocols.sqlite` |
| Agent 2 | claude.ai scheduled task "Agent 2 — crypto scanner analyst" (01:07, 05:07 … UTC) | yes | Follows `AGENT2.md`: analyzes only `python -m scout.pending` items, writes private reports to the claude.ai Project `claude/scanner/`, marks keys in `data/analyzed.json` |

Dashboard: `index.html` (GitHub Pages, reads `data/` from raw.githubusercontent).

## Data rules
- First scan of each chain/testnet/DEX is a silent **baseline**; only later appearances are "new".
- `history` table = one row per tracked item per scan (outcome learning). Don't break it.
- Noise filters (keep them): bot-like sinks (vanity `0x000000…`/`0xc0ffee…`, small code), batch-funded sinks (same amount to ≥5 contracts), NFT bot farms (same `code_hash` ≥3), testnet infra/faucets.
- Public RPCs only (owner's choice). Every chain call must fail soft (skip the chain, never crash the run). Keep total runtime under the workflow timeout (agent1 58 min, protocol-agent 30 min).

## Known issues / next steps
1. Protocol Agent phase 2: ERC-4626 vault `Deposit`/`Withdraw` flows (live TVL) and Aave/Compound-fork `Supply`/`Borrow`/`Repay` (borrow growth → revenue). Phase 3: Uniswap V4 (PoolManager Swap + Initialize), more chains.
2. Agent 2 verdicts are too binary: add a `FARM` verdict (real project, no token, farmable) with a checklist.
3. Some RPCs fail (arbitrum logs, scroll/blast/sonic/sui testnets, ~half of mint chains): try alternative public RPCs.
4. Dashboard: add Protocol Agent tables (top fees, movers, new DEXes).
5. GitHub cron is unreliable → hourly tick + guard; check `gh api repos/elyousfired/machine-/actions/runs` if data looks stale.

## Testing
No network to RPCs from some sandboxes: test parsing with mocked `_post`/`_gt` (see git history for examples), then
`gh workflow run` / dispatch and read the run logs.
