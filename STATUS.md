# STATUS — PropEdge (Smarter Betting)

**Updated:** 2026-09-18 · **Repo:** Harvest-research-Labs/prop_edge

## Ground truth
- **Command Center merged to main (2026-09-18):** FastAPI gateway over 7 service
  boundaries, real MLB entity resolution with a mandatory participation gate, and
  the Command Center UI. This was stranded on `feat/command-center`; verified at
  84/84 tests before merge.
- Working Streamlit app on **port 8502** (`.streamlit/config.toml`). 11 tabs led by
  **🧠 Smart Board** (the AI brain), plus Best Plays, +EV/Kelly, Slate, Goblins &
  Demons, All Props, Cross-Book Edges, Pick Evaluator, Post-Mortem, Backtest, Lottery.
- Pulls live prop lines from PrizePicks & Underdog; a quant model prices a hit
  probability for every line (Poisson/Normal by stat), with an own-MLB model +
  matchup/park adjustment.
- **Automated test suite: 84 tests, all passing** (`python3 -m pytest tests -q`) covering the API
  gateway, MLB entity resolver, backfill, participation gating and integration paths.

## Working / verified
- Board loads live (~7k lines / ~240 players on a live MLB slate).
- **AI brain (`model/brain.py`)** reasons over the priced board → verdicts
  (SMART/LEAN/THIN/TRAP/FADE), slate strategy, and a disciplined slip. Verified
  live with a real key (🤖 Claude); deterministic heuristic fallback when no key.
  Verdict badges also render in Best Plays + Quick 6.
- Real-market-only edge gating, side-relative edge, anchor-zone ranking.

## Not done / next
- Extend coverage to the brain itself (verdicts, candidate ranking, edge gating are
  pure functions and still untested).
- **Runs local-only.** PrizePicks blocks datacenter IPs → won't work on Streamlit
  Cloud without a residential proxy. Data sources are undocumented, gray-area ToS.
- Roadmap: NBA own-model, paid odds API, matchup-adjusted pitcher props.

## Run
```bash
pip install -r requirements.txt
streamlit run app.py           # → http://localhost:8502
```
`ANTHROPIC_API_KEY` (env or `.streamlit/secrets.toml`, gitignored) unlocks the AI
brain and screenshot features; everything else runs without it.
