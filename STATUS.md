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

- **College football (CFB) and college basketball (CBB)** in the Sports picker,
  pulled from PrizePicks and Underdog. PrizePicks league ids are looked up live
  from `/leagues` (fallback ids in `config.py`). Priced off the market like
  NFL/NBA — no own college model yet.

- **NBA own-projection model (`model/nba_stats.py`)** from stats.nba.com: this
  season's per-game rates shrunk toward each player's last season (league
  average for rookies), blended with the last 10 games. Before opening night it
  projects from last season alone. Matchup-, minutes- and injury-blind, so it
  only prices lines the market doesn't anchor, plus the `own_prob` read.
  Needs a residential connection (stats.nba.com blocks datacenter IPs).

- **College football own-projection model (`model/cfb_stats.py`)** from the
  CollegeFootballData API: per-game passing/rushing/receiving rates (over team
  games played) shrunk toward the player's last season. Off unless
  `CFBD_API_KEY` is set (free key at collegefootballdata.com; env or
  `.streamlit/secrets.toml`). Same-name players on different teams are left
  unprojected.

- **College basketball own-projection model (`model/cbb_stats.py`)** from the
  CollegeBasketballData API: per-game box-score rates (same stat mapping as
  NBA) shrunk toward the player's last season; freshmen use their raw rate.
  Off unless `CBBD_API_KEY` (or `CFBD_API_KEY`) is set. Same-name players on
  different teams are left unprojected.

## Not done / next
- Extend coverage to the brain itself (verdicts, candidate ranking, edge gating are
  pure functions and still untested).
- **Runs local-only.** PrizePicks blocks datacenter IPs → won't work on Streamlit
  Cloud without a residential proxy. Data sources are undocumented, gray-area ToS.
- Roadmap: paid odds API, matchup-adjusted pitcher props, NBA minutes/injury
  and opponent adjustments.

## Run
```bash
pip install -r requirements.txt
streamlit run app.py           # → http://localhost:8502
```
`ANTHROPIC_API_KEY` (env or `.streamlit/secrets.toml`, gitignored) unlocks the AI
brain and screenshot features; `CFBD_API_KEY` / `CBBD_API_KEY` turn on the college
football / basketball models. Everything else runs without them.
