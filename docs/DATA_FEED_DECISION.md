# ProEdge AI — Data-Feed Decision Memo (Phase 0)

**Prepared:** 2026-11-22 · **Decision owner:** Director (CRC) · **Status:** Recommendation, pending Director + legal sign-off
**Question:** How should ProEdge source PrizePicks and Underdog lines for a *commercial* product, instead of scraping the books' internal endpoints?

> All prices/limits below are as advertised on vendor pages on 2026-11-22 and must be
> re-confirmed before any contract. Figures marked *(unverified)* come from third-party
> comparisons, not the vendor. This memo makes no purchase — it recommends one for approval
> **[DIRECTOR GATE]** and flags the legal review **[LEGAL GATE]** that must precede it.

---

## 0. The finding that changes the framing

**No vendor sells a "licensed" PrizePicks feed, because PrizePicks has no public developer
API.** Every source of PrizePicks data — including ProEdge today and every paid API below —
is ultimately *aggregating* data PrizePicks does not officially publish. Underdog is the same.

So the real decision is **not** "scrape vs. licensed." It is:

> **Who absorbs the aggregation/ToS risk and the maintenance burden — us, or a vendor with
> terms, uptime, and support?**

That reframes "licensed data feed" in the MVP plan: for sportsbook odds a truly licensed
feed exists; for **DFS pick'em lines (PrizePicks/Underdog) it does not**, from anyone.

---

## 1. Options compared

| Option | Carries PrizePicks? | Carries Underdog? | Price (2026-11-22) | Freshness | Nature of data | Fit |
|---|---|---|---|---|---|---|
| **Keep scraping ourselves** (current) | Yes (partner-api) | Yes | $0 + our maintenance | ~live | Our own aggregation; gray-area ToS; fragile (DataDome walls, 429s) | Works now, not commercial-grade |
| **SportsGameOdds** | **Yes — named at Pro tier** | Claimed on bookmaker/DFS pages; **not named on pricing tiers — confirm** | Free / **$99 Rookie** / **$299 Pro** / custom | **<60s** (Pro) | Vendor aggregation, documented API, terms + support | **Best fit** |
| **OpticOdds / OddsJam** | "Partner" (delivery unclear) | Not confirmed | Sales-gated; *~$5,000/mo per sport (unverified)* | sub-second | Enterprise sportsbook feed | Overkill, costly |
| **The Odds API** | No (sportsbooks only) | No | $30–$249/mo *(unverified)*; props are a paid add-on | varies | Licensed sportsbook odds | Wrong product |
| **Apify DFS scrapers** | Yes | Yes | ~$0.05–0.15 / 1,000 lines | ~live | Managed scraping — **same ToS risk**, offloaded infra | Cheap stopgap, not a moat |

Sources at the end.

---

## 2. Recommendation

**Primary: adopt SportsGameOdds, Pro tier ($299/mo), for PrizePicks lines — pending a sales
confirmation on Underdog and a legal review.**

Why it wins:
- **It's the only mainstream provider that packages DFS pick'em lines behind a documented
  API with public, affordable pricing.** PrizePicks is explicitly named at the Pro tier.
- **<60s freshness** and league coverage that matches ProEdge's sports (NBA, CBB, NFL, CFB,
  MLB, NHL, soccer).
- **Its quirk is a non-issue for us:** PrizePicks entries carry a placeholder `+100` odds
  (DFS books don't publish per-side prices); the real value is the `overUnder` line.
  **ProEdge already prices the line itself** and ignores book odds, so this fits our model
  with zero loss.
- **Cost is commercial-reasonable:** $299/mo vs. ~$5k/mo/sport for the enterprise feeds.
- **It moves the ToS/maintenance risk to a vendor** with terms and support, which is exactly
  the upgrade a commercial product needs over our own scraper.

Two things to settle **before** committing:
1. **Underdog at the Pro tier** — the pricing page names PrizePicks but not Underdog; their
   bookmaker/DFS pages claim Underdog. **Confirm in writing with SGO sales** before relying on it.
2. **Commercial-use terms** — the product pages state no explicit commercial/redistribution
   terms. **[LEGAL GATE]**: have counsel read SGO's Terms for our use (re-displaying their
   aggregated DFS data to paying subscribers) *and* assess downstream PrizePicks/Underdog ToS
   exposure, since SGO aggregates data those books don't license.

## 3. Interim position (no spend, no new risk)
Until the Director approves a contract, **keep the current scraper as-is** — the
`fix/prizepicks-partner-host` work (now on `main`) makes it reliable again. Treat it as a
**bridge, not the commercial data layer.** Do not build new paid features on top of a
scraped source.

## 4. Suggested integration path (low effort, reversible)
- Add a `sources/sportsgameodds.py` adapter that maps SGO's schema (`overUnder`, `playerID`,
  `statID`, `marketName`, `lastUpdatedAt`, …) → the existing `Prop` object. The rest of the
  pipeline (pricing, brain, UI) is source-agnostic and needs no change.
- Run SGO and the scraper **side by side** behind the existing source toggle for a week;
  compare coverage, freshness, and line agreement before switching the default. This is a
  drop-in, not a rewrite.
- **Start on the free tier** to validate the adapter and schema at $0 before paying for Pro.

## 5. Cost summary (for the approval package)
- **Validation:** $0 (SGO free tier) — build + test the adapter.
- **Production primary feed:** **$299/mo** (SGO Pro), *if* Underdog is confirmed and legal clears.
- **Fallback:** keep the $0 scraper as a secondary source.
- **Not recommended now:** enterprise feeds (~$5k/mo/sport) — revisit only if ProEdge needs
  true sub-second sportsbook odds at scale, which the current product does not.

## 6. Open decisions for the Director
1. Approve **$0 validation** of the SportsGameOdds adapter on the free tier? (no risk)
2. Approve **$299/mo SGO Pro** as the production feed, *conditional on* (a) written Underdog
   confirmation and (b) legal sign-off? **[DIRECTOR GATE]** + **[LEGAL GATE]**
3. Authorize **legal review** of SGO terms + DFS-book ToS exposure?

---

### Sources (retrieved 2026-11-22)
- SportsGameOdds pricing — https://sportsgameodds.com/pricing
- SportsGameOdds PrizePicks coverage — https://sportsgameodds.com/bookmakers/prizepicks-odds-api
- SportsGameOdds Underdog coverage — https://sportsgameodds.com/bookmakers/underdog-odds-api
- SportsGameOdds DFS use case — https://sportsgameodds.com/use-cases/dfs-data-api
- Odds API pricing comparison *(third-party, unverified)* — https://oddspapi.io/blog/?p=2498
- OddsJam/OpticOdds pricing notes *(third-party, unverified)* — https://oddspapi.io/blog/?p=2860
- The Rundown API pricing — https://therundown.io/pricing/api
- Apify DFS props scrapers (PrizePicks/Underdog) — https://apify.com/lergassy/dfs-props-scraper
