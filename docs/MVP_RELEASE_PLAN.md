# ProEdge AI — Phased MVP Release Plan

**Owner:** Chattahoochee River Consulting LLC (CRC) · **Dev division:** Harvest Research Labs (HRL)
**Product:** ProEdge AI · **Orchestration/governance:** AngelSuiteOS
**Status:** Planning · **Last updated:** 2026-11-22

> This plan maps ProEdge from its **actual current state** to a commercial MVP. It is
> deliberately honest about what exists, what is prototype, and what is blocked on an
> external dependency (data licensing, legal/gaming compliance, payment providers). It
> does not claim any capability that is not built and tested. Director approval is
> required at every gate marked **[DIRECTOR GATE]**; legal/compliance sign-off is
> required at every gate marked **[LEGAL GATE]**.

---

## 0. Where ProEdge actually is today (baseline, no spin)

**Built and working:**
- Streamlit app (`app.py`, port 8502), 11 tabs led by the "Smart Board" AI brain.
- Two line sources: **PrizePicks** and **Underdog** — pulled from the books' *internal*
  endpoints (undocumented, gray-area ToS). PrizePicks `api.*` is now behind a DataDome
  captcha wall; the `fix/prizepicks-partner-host` branch routes to `partner-api.*` with
  429 backoff (pushed, **not yet merged to `main`**).
- A **statistical pricing model** (scipy Poisson/Normal by stat + shrinkage) that assigns
  a hit probability to each line. This is *not* trained ML — it's principled priors.
- **Own per-player projection models** for MLB, NBA, CFB, CBB (season rates shrunk toward
  prior season, blended with recent games). Matchup/injury/minutes-blind.
- The AI "brain" (`model/brain.py`) that reads the priced board into verdicts
  (SMART/LEAN/THIN/TRAP/FADE), a slate strategy, and a disciplined sample slip.
- A **FastAPI Command Center** gateway (`api/`) with MLB entity resolution + a
  participation gate; ~100+ passing tests (2 modules need `fastapi` installed locally).

**Not built yet (directive calls for these — treat as future, not current):**
- No trained ML models (XGBoost/LightGBM/PyTorch), no backtesting harness, no calibration
  reports, no model registry/MLflow.
- No accounts, auth, subscriptions, billing, or admin dashboard.
- No licensed data feed — everything is scraped from book internals.
- No contest-platform integration or entry handoff of any kind.
- No Next.js/TypeScript frontend (current UI is Streamlit).

**Hard external walls (cannot be engineered past — require authority/sign-off):**
- **Data licensing** — scraped book endpoints are fragile and against ToS. A commercial
  product needs a licensed feed. **[DIRECTOR GATE]** (paid contract) + **[LEGAL GATE]**.
- **Real-money gaming law** — any feature touching paid entries is regulated per-state,
  age-gated, and payment-provider-restricted. **[LEGAL GATE]** before *any* real-money path.
- **Public performance claims** — "X% accurate / beats the market" requires reproducible
  evidence before it can be shown to a customer. **[DIRECTOR GATE]**.

---

## 1. Release philosophy

- **Ship the defensible core first.** The honest, immediately legal product is a
  *research and explainability* tool. Entry-preparation and anything money-adjacent comes
  only after legal sign-off and licensed data.
- **Evidence over claims.** No phase is "done" until its acceptance criteria are
  demonstrated with test output or measured metrics — never screenshots or assertions.
- **Simulated funds only** until **[LEGAL GATE]** clears. Never customer money in tests.
- **User keeps financial authority.** ProEdge researches and prepares; the user submits.

---

## 2. Phase overview

| Phase | Theme | Ships | Gate to exit |
|------:|-------|-------|--------------|
| **0** | Stabilize | Merge the data-source fix; green CI; licensing research | Build is reliable & legally scoped |
| **1** | Research MVP (FREE) | Dashboard + Prediction Explorer + AI Analyst, explainable, licensed-or-clearly-flagged data | First usable product, no money features |
| **2** | Prediction rigor | Trained baseline model + backtest + calibration reports | Measured edge vs. baseline, documented |
| **3** | Entry Builder (SIMULATED) | Entry Builder + Stake Intelligence, simulated contests/funds | Correlation + budget controls proven in tests |
| **4** | Accounts & commerce | Auth, subscription tiers, billing, admin | Paying users can self-serve the research product |
| **5** | Authorized handoff | User-controlled entry handoff, compliance | **[LEGAL GATE]** + **[DIRECTOR GATE]** real-money |

The **commercial MVP line** is drawn at **end of Phase 4**: a paid *research/analytics*
subscription with no real-money handoff. Phase 5 is a separate, legally-gated product
decision, not a prerequisite for first revenue.

---

## 3. Phases in detail

### Phase 0 — Stabilize the foundation  *(≈ 1 week)*
**Goal:** a reliable, legally-scoped base to build on.
- Merge `fix/prizepicks-partner-host` to `main` after review (restores live data).
- Stand up CI (GitHub Actions) that runs the full suite incl. the FastAPI modules;
  pin `requirements.txt` and add a dev-requirements with `pytest`/`fastapi`.
- **Data-source decision memo:** cost/coverage/latency/ToS for ≥3 licensed providers
  (e.g. OpticOdds, OddsJam/OddsBlaze, The Odds API) that carry pick'em lines, vs. the
  current scrape. Recommend a path. **[DIRECTOR GATE]** on any paid contract.
- Replace silent scrape-failure with a visible "data source unavailable" state (started).

**Acceptance:** CI green on every push; data memo delivered; live board loads an in-season
sport out of the box.
**Exit gate:** Director picks the data path (licensed vs. continue scrape-with-risk).

### Phase 1 — Research MVP (FREE tier)  *(≈ 3–4 weeks)*
**Goal:** the first product a real user would open daily — pure research, zero money.
- **Dashboard:** today's games, current projections, injury/line freshness, model-perf
  summary (even if the summary is "insufficient resolved history yet" — stated honestly).
- **Prediction Explorer:** player search, probability distributions, historical trends,
  matchup context, with **plain-language "why"** for each projection (traceable to data).
- **AI Analyst:** natural-language research Q&A, RAG over the priced board + deterministic
  calc; **must refuse to invent** stats/injuries/lines (guardrail + test).
- Data freshness, lineage, dedup, and graceful degradation on source failure.

**Acceptance:** a user can research any in-season player and get an explained projection
with sources; AI Analyst passes a "no-fabrication" eval set; every number on screen traces
to a record.
**Exit gate:** Phase-1 acceptance tests pass; no fabrication regressions.

### Phase 2 — Prediction rigor  *(≈ 4–6 weeks)*
**Goal:** earn the right to make performance claims.
- Introduce a **trained baseline** (gradient boosting — XGBoost/LightGBM) alongside the
  existing statistical model; keep the statistical model as the benchmark.
- **Walk-forward backtesting**, time-ordered train/val, out-of-sample testing, explicit
  **data-leakage prevention**, and **probability calibration** (log loss, Brier, calibration
  error vs. a naive baseline).
- **Model registry / versioning** so every projection carries a reproducible model id.
- Reproducible **model-performance report** (notebook or script that regenerates figures).

**Acceptance:** backtest + calibration reports regenerate from a command; the model beats
the naive baseline on held-out data *with the numbers shown*; no leakage in an audit.
**Exit gate:** **[DIRECTOR GATE]** before any accuracy claim is shown to users publicly.

### Phase 3 — Entry Builder + Stake Intelligence (SIMULATED)  *(≈ 4–6 weeks)*
**Goal:** the signature feature — but on simulated contests and simulated funds only.
- **Entry Builder:** assemble multi-selection proposals, score individual legs, evaluate
  **correlations**, estimate EV where payout rules are known, flag concentrated exposure.
  States: `DRAFT → ANALYZED → READY FOR REVIEW` (no submission yet).
- **Stake Intelligence:** suggested entry amount strictly inside user-set entertainment
  budget + daily/weekly/monthly caps; shows **max loss = full entry**; conservative
  defaults; **never** escalates stake after a loss; no "investment" framing.
- Responsible-use controls: cooling-off / self-exclusion toggle; enforce user limits.

**Acceptance:** tests prove the suggested amount can never exceed configured caps; a loss
never raises the next suggestion; correlation warnings fire on correlated legs; all with
**simulated funds**.
**Exit gate:** responsible-gaming controls pass their test suite.

### Phase 4 — Accounts, subscriptions, admin  *(≈ 4–6 weeks)*  → **COMMERCIAL MVP**
**Goal:** turn the research product into a business.
- Secure auth (RBAC), FREE/PRO/ELITE tiers as *pricing hypotheses to validate*, billing
  via a vetted provider, account + responsible-use preferences, admin dashboard.
- **Pricing validation** (competitor scan, willingness-to-pay, infra-cost model) before
  locking numbers — the $0/$19.99/$49.99 tiers are starting points, not facts.
- Security pass: dependency scanning, audit logging, encryption in transit/at rest, API
  abuse protection; **no unresolved critical vulns** before launch.

**Acceptance:** a new user can sign up, pick a plan, pay (test provider), and use the
research product end-to-end; security assessment shows no open criticals.
**Exit gate:** **[DIRECTOR GATE]** production launch + **[LEGAL GATE]** terms/privacy/
responsible-gaming disclosures. **This is the first revenue-capable release.**

### Phase 5 — User-controlled entry handoff (REAL MONEY)  *(gated, no fixed date)*
**Goal:** close the loop only where it is legal and authorized.
- Only via **officially permitted** integration or a **manual handoff** (approved
  selections + amount in an easy-to-transfer format). User personally presses Submit on
  the destination platform. No credential storage, no CAPTCHA/anti-bot circumvention, no
  unauthorized form automation.
- Per-jurisdiction eligibility, age verification, geo-restriction, payment-provider terms.

**Acceptance:** real-money path is unreachable until **[LEGAL GATE]** + **[DIRECTOR GATE]**
are both recorded; entry is never marked "submitted" unless the user confirmed submission.
**Exit gate:** explicit written legal authorization per jurisdiction.

---

## 4. Cross-cutting (runs through all phases)
- **Testing (directive §15):** data ingestion, stat calcs, model eval, calibration, entry
  construction, correlations, stake caps, auth, state transitions, API security, UI flows,
  deploy readiness. Simulated data/funds for all entry tests.
- **Security (directive §11):** least-privilege, secrets management, dependency + supply-
  chain scanning, audit logging.
- **Governance (AngelSuiteOS, §12):** consequential actions follow authority boundaries;
  append-only evidence records; every completed task carries verification evidence.
  ProEdge stays independently deployable without AngelSuiteOS.

## 5. First demonstrable end-to-end (what to show the Director first)
Using **real historical/in-season data already available** (no new spend): the
**Phase-1 research path** — open ProEdge → today's NFL/NBA slate → an explained player
projection with sources → AI Analyst answers a question about it without fabricating.
That is a true, legal, evidence-backed demo achievable on the current codebase + Phase-0/1
work, and it does not depend on any blocked external gate.

## 6. External blockers to resolve early (so later phases aren't stranded)
1. **Licensed data feed** — decision + contract. **[DIRECTOR GATE]** / **[LEGAL GATE]**.
2. **Gaming-law review** per target state(s). **[LEGAL GATE]** — blocks Phase 5 entirely.
3. **Payment provider** acceptance for sports-adjacent SaaS. **[DIRECTOR GATE]**.
4. **Public performance claims** substantiation. **[DIRECTOR GATE]**.

---

### Sequencing summary
**Phase 0 → 1 → 2 → 3 → 4 = commercial research MVP (first revenue).**
**Phase 5 = separate, legally-gated real-money product decision.**
Nothing money-adjacent ships before legal sign-off; no claim ships without reproducible
evidence; the user always makes the final financial decision.
