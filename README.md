# PropEdge — Smarter Betting

An **AI brain** on top of **PrizePicks** and **Underdog** pick'em props. A quant
model prices a hit probability for every line — **Goblins 🟢 and Demons 😈
included** — and the brain then *reasons* over those prices to surface genuine
value, flag traps, and build one disciplined slip.

## What it does

- **🧠 Smart Board (the AI brain)** — reasons over the priced board and returns
  ranked plays each with a verdict (**SMART / LEAN / THIN / TRAP / FADE**), a
  slate strategy, and a recommended slip. Uses Claude when an `ANTHROPIC_API_KEY`
  is present, with a deterministic heuristic fallback so it always works
  (`model/brain.py`). Verdicts also badge every leg in Best Plays and Quick 6.
  Grounded only in the lines actually loaded — it never invents a prop.
- **Goblins & Demons board** — for every PrizePicks Goblin/Demon line, the
  projected probability the **Over** hits. Goblins are the safer (lower) lines;
  Demons are the juiced (higher) lines.
- **All Props table** — every line from both books, sortable, with our model
  probability and an edge column.
- **Cross-Book Edges** — same player + stat priced on both books, with the line
  difference highlighted (the cleanest real edges).

## How the projection works

The model needs an expected value (mean) for each player+stat, then applies a
distribution (Poisson for low-count counting stats, Normal for high-volume
continuous stats) to get P(Over). The mean comes from, in order of preference:

1. **Underdog de-vigged odds** — Underdog gives American odds per side; we strip
   the vig and invert through the stat distribution to a market-implied mean.
   Most reliable, because it reflects a priced market.
2. **PrizePicks standard line** — used when Underdog doesn't cover the player+stat.
3. **Our own model** — per-player season rates from the free MLB Stats API
   (`model/mlb_stats.py`), shrunk toward the league average so small samples
   don't blow up. Matchup-blind, so it sits last and only fills gaps the market
   leaves — but it lifts Goblin/Demon coverage from ~63% to ~99% on a live MLB
   pull (it prices lines like a Total Bases Demon that exists on neither book's
   standard menu).
4. If none exist, the line is left **unpriced** (shown as `—`) rather than
   guessed from a deliberately-skewed Goblin/Demon number.

The **Proj src** column in the UI shows which source priced each line.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Pick sports in the sidebar (start with one that has live games — MLB, NBA in
season; NFL is empty out of season). Hit **Refresh lines** to re-fetch.

## Data sources

- **PrizePicks** `api.prizepicks.com/projections` — behind PerimeterX bot
  protection; the header set in `config.py` (Origin/Referer/sec-fetch) gets past
  it. Throttled ~2.5s between leagues to avoid 429s.
- **Underdog** `api.underdogfantasy.com/beta/v5/over_under_lines` — one large
  open JSON payload with American odds per side.

> ⚠️ These are undocumented endpoints and gray-area on each book's ToS. They can
> change or break without notice. Built to run **locally** — PrizePicks blocks
> datacenter IPs harder, so cloud hosting needs a residential proxy (see Roadmap).

## Layout

```
app.py              Streamlit UI (3 tabs)
config.py           HTTP headers, league map, stat shapes + canonicalization
sources/
  base.py           normalized Prop schema + odds math
  prizepicks.py     fetch + join projections (goblin/demon/standard)
  underdog.py       fetch + join over/under lines (American odds)
model/projections.py  mean estimation + hit-probability model
storage.py          SQLite snapshots (history for future backtesting)
```

## Roadmap

- ✅ **Own projection model (MLB)** — season per-game rates with league-average
  shrinkage **and last-15-day recency weighting**, in `model/mlb_stats.py`.
  Next: matchup adjustment (opposing pitcher / park / handedness), plus an NBA
  model.
- ✅ **Backtesting** — `model/backtest.py` grades stored snapshots against real
  MLB box scores (the 📈 Backtest tab), reporting actual Goblin/Demon hit rates
  vs the model's predicted probability (calibration). Fills in as games finish.
- **Paid odds API** — plug a sportsbook consensus feed (The Odds API / OddsJam)
  as a third, higher-quality mean source and true no-vig edges.
- **Cloud hosting** — residential proxy for PrizePicks so it can run on
  Streamlit Cloud.
- **More sports** — NBA/NFL when in season; the pipeline is already sport-agnostic.
```
