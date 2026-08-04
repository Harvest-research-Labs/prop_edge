# ChatGPT Improvement Prompt — PropEdge (Expanded: + Screenshot Analyzer)
Paste the block below to ChatGPT (or any strong model) for prioritized, concrete
improvements. Reframes the screenshot analyzer as **Product Area B** inside the existing
architecture — improve `model/screenshot.py` into a production-grade, all-sports system,
NOT treat screenshot analysis as something new. Update when features change.

> Current-state fact (verified in-repo, for accuracy): a BASIC screenshot analyzer already
> exists — `model/screenshot.py` uses Claude vision (`claude-opus-4-8`) with a constrained
> JSON schema (`extract_picks`, `extract_results`, `estimate_loss`) to pull picks from
> **PrizePicks/Underdog** slips, but only fields player/stat/line/side (more/less), those
> two platforms, and no entity resolution or probability scoring. The task is to LEVEL IT UP.

---

```text
You are a senior software engineer, quantitative sports modeler, computer-vision engineer,
product architect, and risk/compliance reviewer evaluating a betting and number-analysis
application called PropEdge ("Smarter Betting").

PropEdge already exists and works. Do not redesign from scratch or merely summarize. Propose
prioritized, concrete improvements that make the current product more accurate, useful,
reliable, deployable, and commercially viable. For each recommendation give: What should
change · Why it matters · Rough effort (S/M/L) · Expected impact · Technical implementation
approach · Concrete first step · Dependencies and risks · How success should be measured.
Flag the five highest-leverage improvements overall.

WHAT PROPEDGE IS — a local Streamlit app with THREE product areas:
A. LIVE SPORTS PROP ANALYSIS — pulls live pick'em prop lines from PrizePicks and Underdog,
   estimates each line's hit probability, identifies value, flags traps, builds ONE
   disciplined slip. Standard props + PrizePicks Goblins and Demons. Decision-support only;
   it does not place bets.
B. SCREENSHOT PROP ANALYZER — EXISTING BASIC MODULE (model/screenshot.py: Claude-vision
   extract_picks/extract_results/estimate_loss, PrizePicks/Underdog only, fields
   player/stat/line/side) TO LEVEL UP into a production-grade, ALL-SPORTS, ALL-PLATFORM
   system. A user uploads a screenshot from a sportsbook, pick'em platform, fantasy app,
   social post, or bet slip; the system inspects it, extracts every visible pick, computes
   an estimated hit probability for each, and ranks which players/teams are strongest to
   hit. Must NOT just read text and assign arbitrary confidence — it must connect each
   extracted pick to real player/team/event/market/statistical data before pricing.
   Target sports: NFL, College Football, NBA, WNBA, College Basketball, MLB, NHL, Soccer,
   Tennis, Golf, UFC, Boxing, NASCAR, F1, Esports, and other pro/college sports.
   Target platforms: PrizePicks, Underdog, Sleeper, FanDuel, DraftKings, BetMGM, Caesars,
   ESPN BET, Hard Rock Bet, Betr, Fliff, and other sportsbooks/pick'em apps.
C. LOTTERY NUMBER PICKER — Powerball & Mega Millions generators backed by real draw history
   (NY State open-data / Socrata), current-matrix filtered. Strategies: pure random,
   hot/frequency, overdue/cold, balanced/optimized (reject-sample to match winner shape:
   sum range, odd/even, range spread), plus historical stats. HONEST STANCE (keep it):
   draws are independent & uniform — NO frequency/hot/cold/overdue/pattern/optimized method
   changes win probability. The only defensible edge is COMBINATORIAL split-avoidance:
   choosing combinations others are less likely to pick may reduce the chance of sharing a
   jackpot if the ticket wins.

TECH STACK: Python, Streamlit (local, port 8502), pandas, scipy, requests, Anthropic SDK,
SQLite snapshots.
STRUCTURE:
- app.py — UI tabs: Smart Board, Best Plays, +EV/Kelly, Slate, Goblins & Demons, All Props,
  Cross-Book Edges, Pick Evaluator, Post-Mortem, Backtest, Lottery
- config.py — HTTP headers, league map, stat canonicalization, source config
- sources/ — prizepicks.py, underdog.py, base.py (normalized Prop schema, odds de-vig)
- model/ — projections.py, mlb_stats.py, brain.py, backtest.py, evaluate.py, matchup.py,
  recommend.py, lottery.py, screenshot.py
- storage.py — SQLite snapshots

SPORTS PRICING MODEL: each line needs an expected mean + a distribution → P(Over/Under/
Higher/Lower). Mean cascade: (1) Underdog de-vigged American odds (preferred — priced
market), (2) PrizePicks standard line, (3) internal MLB model (season per-game rates, free
MLB Stats API, league-avg shrinkage, last-15-day recency, currently matchup-blind), (4) else
unpriced (blank, never invented). UI shows the projection source. Distribution: Poisson for
low-count counting stats, Normal for high-volume/continuous.

AI BRAIN (model/brain.py): reasons over the loaded, priced board → ranked plays, verdicts
(SMART/LEAN/THIN/TRAP/FADE), slate strategy, one recommended slip. Uses Claude when
ANTHROPIC_API_KEY is set, deterministic heuristic fallback otherwise. Grounded ONLY in the
loaded board; must never invent a player, opponent, line, stat, or event.

REQUIRED SCREENSHOT ANALYZER WORKFLOW (design/recommend an implementation for each step):
1. UPLOAD — one or more images (PNG, JPEG, HEIC if practical, WEBP); support a single prop,
   a full board, a multi-pick entry, a parlay slip, multiple games, multiple sports in one
   image, and multiple screenshots from one entry.
2. IMAGE-QUALITY VALIDATION — resolution, blur, cropping, glare, compression, obstructed
   text, dark-mode readability, duplicate detection, cut-off info → return a quality score.
   When extraction confidence is low, name the specific uncertain field; don't pretend.
3. OCR + VISUAL EXTRACTION (when visible): platform, sport, league, player, team, opponent,
   event date/time, stat category, listed line, pick direction (Over/Under/Higher/Lower/
   More/Less/Yes/No), spread, moneyline, payout/multiplier, alternate-line type,
   Goblin/Demon designation, slip/entry structure. Structured schema; each field carries
   value, confidence, bounding box, source screenshot, and visually-confirmed vs inferred.
4. ENTITY RESOLUTION — don't trust OCR; resolve names/markets to authoritative data.
   ("J. Williams" → correct athlete; disambiguate duplicates; normalize team abbrevs;
   match soccer players to competition; college athletes to school; esports handles to
   game/team/tournament; line to correct event date). Compute an entity-resolution
   confidence; do NOT price when player/event/market can't be resolved confidently.
5. MARKET NORMALIZATION — map platform wording to a canonical Prop schema (More=Over,
   Less=Under, Higher=Over, Lower=Under; fantasy scoring; shots vs shots-on-target; points
   variants; tackles definitions; MMA sig-strikes settlement variance). Store canonical
   sport/league/stat, platform-specific stat, settlement rules, line, direction, event,
   athlete/team, platform. Flag markets whose settlement rules differ across platforms.
6. DATA RETRIEVAL — universal factors (season baseline, recent form, expected playing time,
   starting status, injuries, rest, travel, venue, opponent, matchup, role changes, line
   movement, market consensus, weather, game/event script, variance, sample size, data
   freshness) plus sport-specific factors for football/basketball/baseball/hockey/soccer/
   tennis/golf/UFC-boxing/motorsports/esports.
7. PROBABILITY CALCULATION — per resolved pick: expected mean/event rate, distribution,
   estimated hit probability, fair odds, confidence, risk class, projection source, data
   freshness, model version, primary supporting + risk factors. Combine (where available)
   market-implied prob, internal projection, historical baseline, recent form, matchup,
   opportunity/playing-time, injury, venue/weather, line movement, model uncertainty.
   SEPARATE (1) estimated hit probability from (2) confidence in the estimate. Don't emit a
   high-confidence probability on a weak projection.
8. RANK THE SCREENSHOT PICKS — strongest→weakest, answering "which players/teams shown are
   the best candidates to hit their listed picks?" Columns: Rank, Sport, League,
   Player/Team, Opponent, Market, Line, Direction, Est. hit prob, Model confidence,
   Projection source, Risk, Verdict, Why it can hit, Why it can miss. Verdict bands: Elite
   Candidate / Strong / Playable / Lean Only / Avoid / Unpriced / Insufficient Data.
   Thresholds must be derived from backtesting, not hard-coded — recommend how.
9. BEST CANDIDATES — best overall, safest, best value, best Over/Higher, best Under/Lower,
   most dangerous, most likely trap, avoid, needs-more-info, strongest 2-pick, strongest
   3-pick. Don't force a multi-pick when there aren't enough defensible plays.
10. ENTRY/PARLAY ANALYSIS — detect legs, price each, find the weakest, detect +/- correlation,
    estimate combined probability, compare naive-independent vs correlation-adjusted, flag
    game/team/event concentration + duplicated exposure, recommend removing/replacing weak
    legs. Never multiply probabilities blindly when legs are correlated.
11. USER CORRECTIONS — edit player/team/opponent/stat/line/direction/event-date/platform;
    rerun entity resolution + pricing without a new screenshot.
12. AUDITABILITY — reproducible: store original screenshot hash, extracted structured data,
    OCR confidence, user corrections, data sources + timestamps, model version, probability
    result, AI commentary, final ranking.

LOTTERY DETAILS — as above; improve ONLY the defensible edge: expected payout conditional on
winning, adjusted for the estimated chance others picked the same combination. Crowd-
popularity factors: birthday bias, 1–31 preference, sequences, repeated digits, playslip
visual patterns, lucky numbers, previous winners, symmetry, memorable combos, common
multiples, pop-culture refs. Keep stating clearly this does NOT change win probability.

CURRENT STATE — Working: live sports board (~7k MLB lines / ~240 players), Claude brain +
heuristic fallback, verdict badges, real-market-only edge gating, side-relative edge,
anchor-zone ranking, MLB backtesting vs box scores + calibration, Powerball/Mega Millions
generators off official history (hot/cold/overdue/random/balanced). Known gaps: NO automated
tests (0 files); PrizePicks behind PerimeterX + datacenter-IP blocks → local-only;
PrizePicks/Underdog endpoints undocumented + ToS exposure; internal model MLB-only and
matchup-blind (no opposing-pitcher/handedness/park); no native NBA/NFL/all-sports model; no
paid consensus-odds feed; screenshot module basic (2 platforms, limited fields, no entity
resolution/pricing) and must become production-grade all-sports.

REQUIRED IMPROVEMENT DIMENSIONS — give recommendations across all 12:
1. SCREENSHOT INGESTION & COMPUTER VISION — recommend an ARCHITECTURE (not just a tool list)
   for the all-sports upload feature: OCR-engine selection, multimodal vision-model use,
   local vs cloud OCR, bounding-box extraction, dark-mode, platform-layout detection,
   multi-image entries, preprocessing, duplicate detection, quality scoring, confidence
   thresholds, human-correction workflow, cost controls, privacy, retention, an evaluation
   dataset, and OCR-accuracy metrics. Compare Tesseract, EasyOCR, PaddleOCR, Apple Vision,
   Google Cloud Vision, AWS Textract, Azure AI Vision, Claude vision, OpenAI vision, and a
   hybrid OCR + vision-model-validation approach.
2. ALL-SPORTS ENTITY RESOLUTION — fuzzy matching, alias tables, abbreviations, duplicate
   names, college athletes, international leagues, soccer competitions, esports handles,
   event-time matching, roster validation, injury/transaction changes, confidence scoring,
   manual correction, canonical IDs.
3. SPORTS MODEL — mean estimation, market blending, opponent/park/handedness/playing-time/
   opportunity/injury/weather/pace/role/game-script adjustments, distribution choice by
   sport & stat (zero-inflated, negative binomial, beta-binomial, log-normal, empirical,
   quantile), simulation, Bayesian updating, correlated outcomes, model uncertainty.
   Explain WHICH model for WHICH stat category.
4. MULTI-SPORT MODEL EXPANSION — a PHASED roadmap for NBA, NFL, NHL, Soccer, Tennis, Golf,
   UFC, College, Motorsports, Esports. Per phase: required data, data sources, projection
   features, settlement-rule complications, model type, backtesting method, engineering
   effort, licensing/cost. Don't recommend launching every sport at once without a
   defensible architecture.
5. EDGE & BANKROLL — no-vig, multi-book consensus, fair-price estimation, Kelly/fractional
   Kelly, max exposure, daily risk limits, correlation penalties, model-uncertainty
   penalties, overfitting guards, alternate-line pricing, Goblin/Demon evaluation, pick'em
   payout structures. Distinguish sportsbook EV from pick'em-platform EV.
6. AI BRAIN — prompt design, strict grounding, structured output, schema validation, anti-
   hallucination, refusal to price unresolved picks, evidence citations, tool-based
   retrieval, confidence language, verdict criteria, heuristic fallback, cost control,
   prompt caching, model routing, retries, deterministic reproducibility, screenshot-
   specific summaries. The brain EXPLAINS model outputs; it must not manufacture
   probabilities the quant model didn't produce.
7. LOTTERY PICKER — target only split-avoidance: crowd-popularity model, birthday-bias
   estimation, pattern-popularity scoring, jackpot split-probability, expected payout
   conditional on winning, rank lines by likely uniqueness, historical winner-count
   analysis, matrix-change handling, correct hot/cold/overdue math, more games/regions,
   data validation, honest messaging. Explicitly state which lottery improvements do NOT
   change win probability.
8. BACKTESTING & VALIDATION — calibration curves, Brier, log loss, reliability diagrams,
   prediction intervals, CLV/closing-line comparison, walk-forward validation, time splits,
   look-ahead-bias + leakage prevention, honest sample sizes, sport/stat-specific eval,
   model-version comparison, screenshot-extraction accuracy, entity-resolution accuracy,
   end-to-end recommendation accuracy, lottery split-EV analysis. SEPARATELY evaluate:
   (1) OCR accuracy (2) entity resolution (3) projection accuracy (4) probability
   calibration (5) recommendation performance.
9. ENGINEERING — automated tests (unit for pure funcs, integration, screenshot regression,
   OCR golden datasets, contract tests for data sources), schema-drift detection, rate-
   limit handling, retry/backoff, circuit breakers, caching, config + secret management,
   logging/observability, error reporting, data provenance, DB migrations, model
   versioning, feature flags, background jobs/queueing, API separation, type-checking,
   linting, CI/CD. First test targets: odds conversion, de-vigging, side-relative edge,
   verdict thresholds, ranking, edge gating, distributions, screenshot parsing, entity
   resolution, lottery strategies (hot/cold/overdue/random/optimized), matrix-change.
10. DEPLOYMENT — solve the residential-proxy/endpoint-access problem. Evaluate: local
    desktop app, Streamlit Cloud, traditional cloud, user-side data retrieval, browser
    extension, local agent + hosted analysis, mobile app, desktop wrapper, bring-your-own-
    session, licensed data feeds, paid consensus-odds providers, residential-proxy risks,
    data-source replacement. Recommend a cloud-viable architecture that reduces dependence
    on undocumented endpoints.
11. USER EXPERIENCE — screenshot upload flow, drag-and-drop, camera-roll upload, mobile
    layout, extraction review, fast correction, probability display, estimate-confidence
    display, data-freshness display, projection-source display, best-candidate ranking,
    avoid warnings, slip repair, correlation visualization, explainability, one-screen
    decision workflow, accessibility, dark mode, loading states, partial results, error
    recovery. Make immediately clear that: hit probability is an estimate; confidence in
    the estimate may differ; some picks can't be priced reliably; a recommended pick is not
    guaranteed to win.
12. RISK, LEGAL & COMPLIANCE — scraping, undocumented APIs, PrizePicks/Underdog ToS,
    residential proxies, circumvention of access controls, sports-data licensing, athlete
    name/logo use, screenshot storage, privacy, gambling marketing, state-by-state
    restrictions, age gating, responsible gambling, self-exclusion awareness, deposit/loss
    limits, "not financial advice" language, avoiding guaranteed-win claims, lottery
    marketing, misleading probability language. Distinguish technical feasibility vs
    contractual vs regulatory vs data-licensing vs product-policy risk. Do NOT give a vague
    "consult an attorney" — name the specific practices creating the greatest exposure and
    propose safer alternatives.

OUTPUT FORMAT
- Executive Assessment — direct take on the current product, strongest assets, biggest
  weaknesses, and whether the screenshot analyzer should be a core feature or a separate
  product module.
- Top Five Highest-Leverage Improvements — each: Rank · Improvement · Why now · Effort ·
  Expected impact · First action.
- Remaining recommendations organized by the 12 dimensions. For each use: Improvement Name /
  What / Why / Effort / Expected impact / Technical approach / First step / Dependencies /
  Risks / Success metric.
- Recommended 30-Day Plan — realistic weekly sequence.
- Recommended 90-Day Roadmap — separate Foundation / Screenshot MVP / Model Expansion /
  Validation / Deployment / Compliance.
- What Will Not Move the Needle — impressive-sounding ideas unlikely to materially improve
  prediction accuracy, calibration, decision quality, EV, reliability, or lottery win
  probability. Be especially clear that NO lottery number-selection method changes the
  underlying odds of drawing a winning combination.
```
