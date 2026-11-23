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
5. Write `claude/scanner/reports/YYYY-MM-DD_HH.md` in the Project: table of all items (key, verdict, 1-line reason) + DEEP sections.
6. Mark everything processed: `python -m scout.pending --mark KEY ...`, commit `data/analyzed.json`, push.
7. **Learning:** for tokens analyzed ≥1 day ago that have new snapshots in `data/scout.sqlite` (`snapshots` table), compare verdict vs outcome (rugged? liquidity gone? up/down?). Add short, evidence-backed lessons to the knowledge base (pattern → outcome, with counts). Update existing lessons rather than duplicating.
8. Push notification only if there is at least one DEEP item with a real opportunity.
