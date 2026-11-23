# Agent 2 playbook (Claude, every 4h)

## Rules
- Analyze **every** pending project, never one already in `data/analyzed.json`. Get the list with `python -m scout.pending` (it already excludes analyzed ones).
- Read-only research. Never sign, trade, connect a wallet or enter keys. Any action is the user's decision.
- Reports and the knowledge base are private: they go to the claude.ai Project, never to this public repo. The repo only gets `data/analyzed.json` (keys + date, no verdicts).
- Be token-efficient: triage first, go deep only where it's worth it.

## Per run
1. `git pull`, then `python -m scout.pending`. If empty → stop (no report).
2. Read the knowledge base (`claude/scanner/knowledge-base.md` in the Project) before judging.
3. **Triage every item (1 line each):** `SKIP` (obvious meme/copycat/no product), `WATCH`, or `DEEP`.
4. **DEEP items only:** website + docs, X account age/activity, team, contract (verified source, owner powers, upgradeability — Etherscan tools for EVM), tokenomics/unlocks, liquidity lock, audits, TVL. Then the profit angle:
   - early entry (spot), points/airdrop farming, LP/staking yield, arbitrage, or *none*;
   - entry condition, invalidation, main risk, size suggestion as % of a risk budget.
   - **`onchain_new_contracts`** (contracts that just started receiving ≥ $250k stablecoins, found on-chain before any listing): identify each with Etherscan tools (`get_contract_source`, `get_contract_creation`, recent `get_token_transfers`): what protocol is it (vault, bridge, perp, lending, CEX deposit contract, MEV/arb bot?), deployer history, age, is it verified, does it have a token or points program. Bots, bridges' internal contracts and CEX contracts → SKIP. A genuinely new protocol pulling money early is the highest-value signal of this system.
   - **`testnet_projects`** (testnet contracts suddenly used by many unique wallets): identify the project (explorer link, Etherscan tools with its `chainid`, web search on the address/project). Answer: which project, has it raised money / known investors, no token yet?, is there a points/quests/airdrop campaign, what actions are being farmed (swap, bridge, mint, deploy), effort vs. expected value, sybil rules. Faucet/spam/bot contracts → SKIP. Output for good ones: a step-by-step farming checklist.
5. Write `claude/scanner/reports/YYYY-MM-DD_HH.md` in the Project: table of all items (key, verdict, 1-line reason) + DEEP sections.
6. Mark everything processed: `python -m scout.pending --mark KEY ...`, commit `data/analyzed.json`, push.
7. **Learning:** for tokens analyzed ≥1 day ago that have new snapshots in `data/scout.sqlite` (`snapshots` table), compare verdict vs outcome (rugged? liquidity gone? up/down?). Add short, evidence-backed lessons to the knowledge base (pattern → outcome, with counts). Update existing lessons rather than duplicating.
8. Push notification only if there is at least one DEEP item with a real opportunity.
