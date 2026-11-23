# chain-scout — Agent 1

Read-only scanner for new crypto projects on **every chain GeckoTerminal indexes** (200+, fetched live).
No LLM, no API keys, no wallets, no transactions. Runs every 4h on GitHub Actions (free for public repos).

## Pipeline
1. `all_networks()` → list every network.
2. `new_pools()` per network → newest pools (busy chains get 2 pages).
3. **Market filter** (no extra calls): liquidity ≥ $20k, 24h volume ≥ $15k, ≥ 40 buyers, FDV/liquidity ≤ 60.
4. **Deep check** (top 80 by liquidity): socials/site/description (GeckoTerminal) + contract security (GoPlus: EVM chains in `GOPLUS_EVM` + Solana).
   Chains without a security API are kept only with liquidity ≥ $40k and flagged `chain_without_security_scan`.
5. New protocols listed on DefiLlama (last 3 days).
6. **Outcome tracking**: shortlisted tokens are re-snapshotted (~1 day, then weekly, up to 30 days) so we can learn what rugs and what works.

## Output for Agent 2
- `data/shortlist/latest.json` — `{generated_at, stats, tokens[], new_protocols[]}`, tokens sorted by `score`.
- `data/scout.sqlite` — tables `tokens`, `snapshots`, `protocols`.

`score` is only a reading priority, never a buy signal.

## Setup
1. Push this folder to a public GitHub repo.
2. Settings → Actions → General → Workflow permissions → **Read and write**.
3. Actions tab → `agent1-scan` → **Run workflow** for the first test.

Tune thresholds in `scout/filters.py`.
