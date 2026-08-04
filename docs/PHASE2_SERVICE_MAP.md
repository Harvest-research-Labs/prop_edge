# ProEdge — Phase 2 Service Map & Interfaces

Goal: split today's Streamlit-embedded backend into **7 stateless services** with clean
contracts, so both the **React/Next frontend** (Phase 3) and the **Streamlit research app**
consume the *same* services. FastAPI in front, existing Python models reused as the
implementation. Nothing here changes the models — it draws boundaries around them.

## Architecture
```
  React/Next (customer)  ─┐
                          ├─► FastAPI gateway ─► 7 services ─► existing model/ + sources/
  Streamlit (research)   ─┘        │
                                   ├─ Redis  (cache: boards, projections, jobs)
                                   ├─ Postgres (snapshots, results, provenance)
                                   └─ Job queue (RQ/Celery) for async screenshot processing
```
Screenshot processing is **async** (upload returns a `job_id`; frontend polls or subscribes
to stage updates). Everything else is synchronous request/response.

## Canonical data contracts (shared schemas)
```
RawPick        { text, player_raw, team_raw, opp_raw, stat_raw, line, side_raw,
                 platform, sport_guess, bbox, field_confidence{}, source_image_hash }
ResolvedPick   { player_id, player, team, opponent, event_id, event_time,
                 sport, league, canonical_stat, platform_stat, settlement_rule,
                 line, side, resolution_confidence }
PricedPick     { ...ResolvedPick, mean, distribution, mean_source, hit_prob,
                 fair_odds, edge, model_version, data_freshness, prob_confidence }
Leg            { player, stat, line, side, prob, flavor, sport, event_id }
```
`prob_confidence` (confidence in the *estimate*) is kept **separate** from `hit_prob`
(the estimate) — the UI shows both.

---

## The 7 services

### 1. Screenshot Extraction  ·  `POST /extract`
- **Does:** image → quality score → OCR/vision → `RawPick[]` with per-field confidence + bbox.
- **In:** `{ images: bytes[], hint_platform? }`  **Out:** `{ job_id }` → `{ quality, picks: RawPick[], stages[] }`
- **Today:** `model/screenshot.py::extract_picks` (Claude vision, PrizePicks/Underdog, fields player/stat/line/side).
- **Build:** image-quality validation; all-platform layout detection; bbox + per-field confidence; hybrid OCR + vision fallback; async job + stage stream (the 10 stages the UI already renders).

### 2. Entity Resolution  ·  `POST /resolve`
- **Does:** `RawPick[]` → `ResolvedPick[]` against authoritative rosters/schedules. Never trusts OCR text.
- **In:** `{ picks: RawPick[] }`  **Out:** `{ resolved: ResolvedPick[], needs_review: [] }`
- **Today:** partial — `config.py::canonical_stat` (stat normalization) only.
- **Build (mostly new):** player/team alias tables + canonical IDs, fuzzy match, duplicate-name disambiguation, event-time matching, roster/injury validation, `resolution_confidence`. **Gate:** don't emit a pick below threshold — it goes to `needs_review` (the confirmation screen).

### 3. Projection  ·  `POST /project`
- **Does:** `(player_id, canonical_stat, sport, event)` → expected mean + distribution + source.
- **In:** `{ resolved: ResolvedPick[] }`  **Out:** `{ means: [{mean, distribution, mean_source}] }`
- **Today:** `model/projections.py` (mean cascade), `model/mlb_stats.py` (own model), `model/matchup.py` (adjuster), `config.STAT_SHAPES` (distribution per stat).
- **Build:** per-sport projector registry (MLB exists; NBA/NFL/… phased); expose the cascade order as the response's `mean_source`.

### 4. Pricing  ·  `POST /price`
- **Does:** `(mean, distribution, line, side)` + market odds → `hit_prob`, `fair_odds`, `edge`.
- **In:** `{ means[], lines[], market_odds? }`  **Out:** `{ priced: PricedPick[] }`
- **Today:** `model/projections.py` (Poisson/Normal → P(over)), `sources/base.py::devig_two_way / implied_prob`.
- **Build:** widen the distribution set (negative-binomial / beta-binomial / log-normal / empirical per §3 of the improvement prompt); real-market-only edge gating (already the rule); `prob_confidence` from sample size + freshness.

### 5. Ranking  ·  `POST /rank`
- **Does:** `PricedPick[]` → ranked board + verdicts + **Best Pick** + **Avoid**.
- **In:** `{ priced: PricedPick[], sports_filter? }`  **Out:** `{ best, top: [], avoid: [] }`
- **Today:** `model/brain.py::candidates` (anchor-zone interest ranking) + `leg_verdict` (SMART/LEAN/THIN/TRAP/FADE) + `model/recommend.py`.
- **Build:** thresholds derived from backtesting (not hard-coded); this powers the hero + Top 5 + Avoid exactly as the Command Center renders them.

### 6. Slip Evaluation  ·  `POST /slip/eval`
- **Does:** `Leg[]` → naive combined **and** correlation-adjusted, weakest leg, concentration, EV, risk.
- **In:** `{ legs: Leg[] }`  **Out:** `{ naive, correlation_adjusted, correlation_modeled: bool, weakest, concentration, ev?, risk }`
- **Today:** `model/recommend.py::combined_prob` (poisson-binomial — proper, not naïve product).
- **Build:** an actual correlation model (same-game/same-player), so `correlation_modeled` is true; never return combined prob without that flag.

### 7. AI Explanation  ·  `POST /explain`
- **Does:** priced board + user question → grounded natural-language answer. **Never invents a probability.**
- **In:** `{ board: PricedPick[], question, context? }`  **Out:** `{ answer, cited_picks[] }`
- **Today:** `model/brain.py::ai_take / get_take / heuristic_take` (Claude with a strict grounding system prompt + deterministic fallback).
- **Build:** enforce structured output + schema validation; the analyst reads the numbers, it does not produce them (pricing is §4's job).

---

## Orchestration — the screenshot flow chains the services
`/extract` → `/resolve` → *(confirmation screen: user fixes `needs_review`)* → `/project` →
`/price` → `/rank` → render Best Pick + board → `/slip/eval` on selection → `/explain` on ask.
The **10 pipeline stages** the UI shows map 1:1 onto these calls (quality/extract = §1;
match = §2; verify lines/settlement = §2/§5; injuries + matchup data = §2/§3; probability = §4;
correlation = §6; ranking = §5).

## Migration order (low-risk)
1. Wrap **existing** modules behind the 7 FastAPI endpoints unchanged — pure interface extraction.
2. Point `command_center.py` at the API instead of importing models directly (validates the contracts).
3. Then improve each service internally (entity resolution + multi-sport projection are the big new builds).
4. Add Redis caching (board/projection TTL) + the async job queue for `/extract`.
5. Build the React/Next frontend against the now-stable API (Phase 3).

## Contract tests (first CI targets)
Golden-file tests per endpoint: `/extract` OCR accuracy, `/resolve` match accuracy,
`/price` odds/de-vig math, `/rank` verdict thresholds, `/slip/eval` correlation math — the
same pure-function targets named in the improvement prompt, now behind service boundaries.
