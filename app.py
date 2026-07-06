"""PropEdge — a DailyFantasyFuel-style board for PrizePicks & Underdog.

Pulls live prop lines from both books, projects a hit probability for every
line (Goblins and Demons included), and ranks the best plays.

Run:  streamlit run app.py
"""

import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from sources import prizepicks, underdog
from model.projections import annotate
from model import mlb_stats, backtest, evaluate, screenshot, recommend, matchup, lottery, brain
import storage
from config import SUPPORTED_SPORTS, canonical_stat


def _anthropic_key():
    """Anthropic key from Streamlit secrets or the environment (None if absent)."""
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001 - no secrets.toml is fine
        pass
    import os
    return os.environ.get("ANTHROPIC_API_KEY")


def _stat_options(frame):
    """Stat labels for a picker, most-common first, de-duped across the two
    books' spellings (PrizePicks 'Hits+Runs+RBIs' == Underdog 'Hits + Runs + RBIs')."""
    seen, opts = set(), []
    for s in frame["stat"].value_counts().index:
        c = canonical_stat(s)
        if c in seen:
            continue
        seen.add(c)
        opts.append(s)
    return opts


def _filter_stats(frame, picked):
    """Filter to rows whose stat matches any picked label (by canonical name)."""
    if not picked:
        return frame
    wanted = {canonical_stat(s) for s in picked}
    return frame[frame["stat"].map(lambda x: canonical_stat(x) in wanted)]

st.set_page_config(page_title="PropEdge", page_icon="🎯", layout="wide")

# --- look & feel --------------------------------------------------------
# Dark violet board with neon-green (Goblin) and hot-pink (Demon) accents.
# Evokes a pick'em app without copying any one brand's exact palette/marks.
GREEN = "#27E0A4"
PINK = "#FF4D8D"
VIOLET = "#8B5CF6"

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
    .block-container {{ padding-top: 1.6rem; }}

    /* hero header */
    .pe-hero {{
        background: radial-gradient(120% 140% at 0% 0%, #2A1457 0%, #17122A 55%, #0E0A18 100%);
        border: 1px solid rgba(139,92,246,.35);
        border-radius: 18px; padding: 20px 24px; margin-bottom: 14px;
        box-shadow: 0 8px 30px rgba(139,92,246,.18);
    }}
    .pe-title {{
        font-size: 2.1rem; font-weight: 800; letter-spacing:-.5px; margin:0;
        background: linear-gradient(90deg, {VIOLET}, {GREEN});
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .pe-sub {{ color:#B9B2D6; font-size:.95rem; margin-top:2px; }}
    .pe-pill {{
        display:inline-block; padding:3px 10px; border-radius:999px; font-size:.72rem;
        font-weight:600; margin-right:6px; background:rgba(139,92,246,.18);
        color:#D6CBFF; border:1px solid rgba(139,92,246,.35);
    }}

    /* tabs as pills */
    button[data-baseweb="tab"] {{
        background:#17122A; border-radius:12px 12px 0 0; padding:8px 16px;
        font-weight:600;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        background:linear-gradient(180deg, rgba(139,92,246,.30), rgba(139,92,246,.05));
        color:#fff; border-bottom:2px solid {VIOLET};
    }}

    /* metric cards */
    [data-testid="stMetric"] {{
        background:#17122A; border:1px solid rgba(139,92,246,.25);
        border-radius:14px; padding:14px 16px;
    }}
    /* dataframes */
    [data-testid="stDataFrame"] {{ border-radius:14px; overflow:hidden; }}
    /* buttons */
    .stButton>button, .stDownloadButton>button {{
        border-radius:10px; font-weight:600; border:1px solid rgba(139,92,246,.4);
    }}
    .stDownloadButton>button {{
        background:linear-gradient(90deg, {VIOLET}, #6D28D9); color:#fff; border:0;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

FLAVOR_LABEL = {
    "goblin": "🟢 Goblin",
    "demon": "😈 Demon",
    "standard": "Standard",
    "alternate": "Alt",
}

SOURCE_LABEL = {
    "ud_devig": "Market (UD)",
    "pp_standard": "PP standard",
    "own_model": "Our model",
    "none": "—",
}


@st.cache_resource(ttl=3600, show_spinner=False)
def get_projectors(sports):
    """Build our own per-player projection models for sports that have one."""
    projectors = {}
    if "MLB" in sports:
        try:
            base = mlb_stats.load_projector()
            try:
                adj = matchup.build_adjuster(datetime.date.today().isoformat())
            except Exception:  # noqa: BLE001 - matchup layer is optional
                adj = None

            def mlb_proj(name, stat, team=None, opp=None, _b=base, _a=adj):
                m = _b(name, stat, team, opp)
                return _a(stat, m, team, opp) if _a else m

            projectors["MLB"] = mlb_proj
        except Exception:  # noqa: BLE001 - own model is optional
            pass
    return projectors


@st.cache_data(ttl=300, show_spinner=False)
def load_props(sports, use_pp, use_ud):
    """Fetch both books, annotate with the model, persist a snapshot."""
    props, errors, logs = [], {}, []
    if use_pp:
        pp, e = prizepicks.fetch(sports, log=logs.append)
        props += pp
        errors.update(e)
    if use_ud:
        ud, e = underdog.fetch(sports, log=logs.append)
        props += ud
        errors.update(e)
    projectors = get_projectors(sports)
    rows = annotate(props, projectors=projectors)
    try:
        storage.save_snapshot(rows)
    except Exception as ex:  # noqa: BLE001 - persistence is best-effort
        logs.append(f"snapshot skipped: {ex}")
    return pd.DataFrame(rows), errors, logs


def _fmt_local(iso):
    """ISO-UTC → 'Sun 7:20 PM' local."""
    if not iso:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %-I:%M %p")
    except Exception:  # noqa: BLE001
        return iso


@st.cache_data(ttl=120, show_spinner=False)
def _ud_games(sports):
    return underdog.fetch_games(sports)


@st.cache_data(ttl=20, show_spinner=False)
def _mlb_schedule():
    return mlb_stats.fetch_schedule(datetime.date.today().isoformat())


def load_games(sports):
    """Full slate. MLB = every game (live scores, refreshed ~every 20s) with
    Underdog prop counts overlaid; other sports = Underdog games."""
    errs = {}
    ud_games, ud_err = _ud_games(sports)
    errs.update(ud_err)

    if "MLB" not in sports:
        return ud_games, errs

    try:
        sched = [dict(g) for g in _mlb_schedule()]   # copy — don't mutate the cache
    except Exception as e:  # noqa: BLE001
        errs["MLB schedule"] = str(e)
        return ud_games, errs

    # prop counts from Underdog MLB games, keyed by the team pair
    ud_props = {
        frozenset({(g.get("away") or "").upper(), (g.get("home") or "").upper()}): g.get("props")
        for g in ud_games if g["sport"] == "MLB"
    }
    for g in sched:
        g["props"] = ud_props.get(frozenset({(g.get("away") or "").upper(), (g.get("home") or "").upper()}))
        if g["status"] == "scheduled":
            g["progress"] = _fmt_local(g["progress"])

    games = sched + [g for g in ud_games if g["sport"] != "MLB"]
    order = {"live": 0, "scheduled": 1, "final": 2}
    games.sort(key=lambda g: (order.get(g.get("status"), 1), g.get("start_time") or ""))
    return games, errs


def pct(s):
    return (s * 100).round(1)


def to_excel(df):
    """Build a multi-sheet .xlsx (All Props + Goblins & Demons) in memory."""
    allp = df.copy()
    allp["hit_%"] = pct(allp["hit_prob"])
    allp["edge_%"] = pct(allp["edge"])
    allp = allp[[
        "book", "sport", "player", "team", "opponent", "stat", "line", "flavor",
        "over_odds", "under_odds", "mean", "mean_source", "hit_%", "edge_%",
    ]].rename(columns={"mean": "proj", "mean_source": "proj_src"})

    gd = df[(df["book"] == "PrizePicks") & (df["flavor"].isin(["goblin", "demon"]))].copy()
    gd = gd[gd["hit_prob"].notna()]
    gd["hit_%"] = pct(gd["hit_prob"])
    gd = gd[["flavor", "player", "team", "stat", "line", "mean", "mean_source", "hit_%"]].rename(
        columns={"mean": "proj", "mean_source": "proj_src"}).sort_values("hit_%", ascending=False)

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        allp.sort_values("edge_%", key=lambda s: s.abs(), ascending=False).to_excel(
            writer, sheet_name="All Props", index=False)
        gd.to_excel(writer, sheet_name="Goblins & Demons", index=False)
    return buf.getvalue()


# --- sidebar ------------------------------------------------------------

st.sidebar.title("🧠 Smarter Betting")
st.sidebar.caption("PropEdge · AI brain for PrizePicks & Underdog")

sports = st.sidebar.multiselect(
    "Sports", SUPPORTED_SPORTS, default=["MLB"],
    help="Sports with live games will return props; off-season ones come back empty.",
)
col_a, col_b = st.sidebar.columns(2)
use_pp = col_a.checkbox("PrizePicks", value=True)
use_ud = col_b.checkbox("Underdog", value=True)

if st.sidebar.button("🔄 Refresh lines", use_container_width=True):
    load_props.clear()

if not sports:
    st.info("Pick at least one sport in the sidebar to load props.")
    st.stop()

st.session_state["slate_sports"] = tuple(sports)

with st.spinner("Fetching live lines…"):
    df, errors, logs = load_props(tuple(sports), use_pp, use_ud)

for src, msg in errors.items():
    st.sidebar.error(f"{src}: {msg}")
if logs:
    st.sidebar.caption("  \n".join(logs))

if df.empty:
    st.warning("No props returned. Off-season sport, or the book blocked the request.")
    st.stop()

books = ", ".join(sorted(df["book"].unique()))
st.markdown(
    f"""
    <div class="pe-hero">
      <div class="pe-title">🧠 Smarter Betting</div>
      <div class="pe-sub">An AI brain on top of PrizePicks &amp; Underdog — every
      line priced by the model, then read for genuine value,
      <span style="color:{GREEN}">Goblins</span> &amp;
      <span style="color:{PINK}">Demons</span> included.</div>
      <div style="margin-top:10px">
        <span class="pe-pill">{len(df):,} lines</span>
        <span class="pe-pill">{df['player'].nunique():,} players</span>
        <span class="pe-pill">{books}</span>
        <span class="pe-pill">PropEdge</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Excel export (sidebar) — built from the full loaded board
st.sidebar.download_button(
    "⬇️ Export to Excel",
    data=to_excel(df),
    file_name=f"propedge_{datetime.date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

(tab_brain, tab_best, tab_ev, tab_slate, tab_gd, tab_all, tab_cross, tab_eval, tab_pm,
 tab_bt, tab_lotto) = st.tabs(
    ["🧠 Smart Board", "🔥 Best Plays", "💰 +EV / Kelly", "🏟️ Slate", "🟢😈 Goblins & Demons",
     "📋 All Props", "⚖️ Cross-Book Edges", "🧮 Pick Evaluator", "🔎 Post-Mortem",
     "📈 Backtest", "🎰 Lottery"])

# --- Smart Board: the AI brain ------------------------------------------

with tab_brain:
    st.subheader("The brain's read on today's board")
    st.caption(
        "The model prices every line; the brain reasons about those prices — "
        "which edges are real, which high-% legs are anchors, which juicy "
        "payouts are traps — and builds one disciplined slip. Grounded only in "
        "the lines actually loaded; nothing invented.")

    if st.button("🧠 Run the brain", type="primary", key="run_brain"):
        with st.spinner("Reading the board…"):
            st.session_state["brain_take"] = brain.get_take(
                df.to_dict("records"), api_key=_anthropic_key(), n_plays=10)

    take = st.session_state.get("brain_take")
    if not take:
        st.info("Hit **Run the brain** to get today's smart read.")
    else:
        badge = "🤖" if take["engine"] == "ai" else "⚙️"
        st.markdown(
            f"<span class='pe-pill'>{badge} {take['engine_label']}</span>",
            unsafe_allow_html=True)
        if take.get("note"):
            st.caption(f"ℹ️ {take['note']}")

        if take.get("headline"):
            st.markdown(f"#### {take['headline']}")
        if take.get("strategy"):
            st.markdown(f"> {take['strategy']}")

        st.markdown("##### Ranked plays")
        rows_out = []
        for p in take["plays"]:
            edge = p.get("edge")
            rows_out.append({
                "": brain.VERDICT_ICON.get(p["verdict"], ""),
                "Verdict": p["verdict"],
                "Player": p["player"],
                "Prop": f"{p['stat']} {p['line']} · {p.get('side_label','More')}",
                "Flavor": FLAVOR_LABEL.get(p.get("flavor"), p.get("flavor", "")),
                "Model %": round(p["model_prob"] * 100, 1),
                "Edge %": round(edge * 100, 1) if edge is not None else None,
                "Confidence": int(p.get("confidence", 0)),
                "Why": p.get("rationale", ""),
            })
        st.dataframe(
            pd.DataFrame(rows_out), use_container_width=True, hide_index=True,
            column_config={
                "Model %": st.column_config.ProgressColumn(
                    "Model %", min_value=0, max_value=100, format="%.1f%%",
                    help="Our quant model's hit probability — not the AI's."),
                "Confidence": st.column_config.ProgressColumn(
                    "Confidence", min_value=0, max_value=100, format="%d",
                    help="The brain's conviction in the verdict."),
                "Why": st.column_config.TextColumn("Why", width="large"),
            })
        with st.expander("What the verdicts mean"):
            for v in brain.VERDICTS:
                st.markdown(f"- {brain.VERDICT_ICON[v]} **{v}** — {brain.VERDICT_HELP[v]}")

        slip = take.get("slip") or {}
        if slip.get("legs"):
            st.markdown("##### The brain's slip")
            slip_rows = [{
                "Player": l["player"],
                "Prop": f"{l['stat']} {l['line']}",
                "Pick": brain._side_label(l.get("side", "more"), l.get("flavor", "standard")),
                "Model %": round(l["model_prob"] * 100, 1),
            } for l in slip["legs"]]
            st.dataframe(
                pd.DataFrame(slip_rows), use_container_width=True, hide_index=True,
                column_config={"Model %": st.column_config.ProgressColumn(
                    "Model %", min_value=0, max_value=100, format="%.1f%%")})
            cp = slip.get("combined_prob")
            if cp is not None:
                st.metric("All legs hit (independent)", f"{cp*100:.1f}%",
                          help="Assumes independent legs; correlated picks differ.")
            if slip.get("rationale"):
                st.caption(slip["rationale"])
        st.caption(
            "⚠️ Model probabilities are the quant layer's, shown verbatim — the "
            "brain reasons about them but never overwrites them. Bet responsibly.")

# --- Goblins & Demons ---------------------------------------------------

with tab_best:
    st.subheader("Recommended slips")
    st.caption(
        "Auto-built from the loaded board (one leg per player). Each pick shows its "
        "projected hit chance; the big number is the chance **all** legs hit."
    )
    rows = df.to_dict("records")

    # --- Quick 6: one-tap high-% slip mixing Goblins/Demons/Standard ----
    qc1, qc2 = st.columns([1, 3])
    if qc1.button("⚡ Quick 6", type="primary", help="Instant 6-leg slip: highest-% picks, "
                  "with Goblins, Demons, and Standard all in the mix."):
        st.session_state["quick6"] = recommend.quick_six(rows, n=6)
    q6 = st.session_state.get("quick6")
    if q6:
        with qc2:
            combined = recommend.combined_prob(q6)
            st.metric("⚡ Quick 6 — all hit", f"{combined*100:.1f}%" if combined >= 0.001
                      else f"{combined*100:.3f}%")
        q6_tbl = pd.DataFrame([{
            "🧠": f"{brain.VERDICT_ICON.get(brain.leg_verdict(l['prob'], l.get('flavor'))[0],'')}"
                  f" {brain.leg_verdict(l['prob'], l.get('flavor'))[0]}",
            "Flavor": FLAVOR_LABEL.get(l.get("flavor"), l.get("flavor")),
            "Pick": evaluate.side_label(l["side"], l.get("book")),
            "Player": l["player"],
            "Prop": f"{l['stat']} {l['line']}",
            "Book": l["book"],
            "%": round(l["prob"] * 100, 1),
        } for l in q6])
        st.dataframe(
            q6_tbl, use_container_width=True, hide_index=True,
            column_config={
                "🧠": st.column_config.TextColumn("🧠", help="The brain's read on this leg."),
                "%": st.column_config.ProgressColumn(
                    "Hit %", min_value=0, max_value=100, format="%.0f%%")},
        )
        flv = {l.get("flavor") for l in q6}
        missing = [FLAVOR_LABEL[f] for f in ("goblin", "demon", "standard")
                   if f not in flv and f in FLAVOR_LABEL]
        if missing:
            st.caption(f"⚠️ Board had no { ' or '.join(missing) } lines for the selected sports — "
                       "filled with the next-best picks instead.")
        st.divider()

    n_picks = st.slider("Picks per slip", 2, 6, 6)

    MODES = [
        ("🛡️ Safest", "safest", GREEN,
         "Highest individual hit probability — Goblins and strong leans."),
        ("💎 Best Value", "value", VIOLET,
         "Biggest gap between our projection and the market price (needs Underdog odds)."),
        ("🔥 Insane", "insane", PINK,
         "Swing for the fences — Demons with the best shot. Low combined odds, huge payout."),
    ]

    cols = st.columns(3)
    for col, (title, mode, color, blurb) in zip(cols, MODES):
        with col:
            legs = recommend.best_plays(rows, mode, n=n_picks)
            combined = recommend.combined_prob(legs)
            st.markdown(f"<h4 style='color:{color};margin-bottom:2px'>{title}</h4>", unsafe_allow_html=True)
            st.caption(blurb)
            if not legs:
                st.info("Not enough priced lines for this mode right now.")
                continue
            st.metric("All hit", f"{combined*100:.1f}%" if combined >= 0.001 else f"{combined*100:.3f}%")
            rowlist = []
            for l in legs:
                v, _conf, _why = brain.leg_verdict(l["prob"], l.get("flavor"))
                rowlist.append({
                    "🧠": f"{brain.VERDICT_ICON.get(v,'')} {v}",
                    "Pick": evaluate.side_label(l["side"], l.get("book")),
                    "Player": l["player"],
                    "Prop": f"{l['stat']} {l['line']}",
                    "%": round(l["prob"] * 100, 1),
                })
            tbl = pd.DataFrame(rowlist)
            st.dataframe(
                tbl, use_container_width=True, hide_index=True,
                column_config={
                    "🧠": st.column_config.TextColumn("🧠", help="The brain's read on this leg."),
                    "%": st.column_config.ProgressColumn(
                        "Hit %", min_value=0, max_value=100, format="%.0f%%")},
            )
    st.caption(
        "Combined odds assume independent legs (real slips correlate). Demons/Goblins "
        "carry adjusted payouts, so a lower-probability slip can still be +EV. Not betting advice."
    )

with tab_ev:
    st.subheader("+EV plays — our model vs the real price")
    st.caption(
        "Underdog props where our **independent** projection beats the book's de-vigged "
        "price. EV and Kelly use the actual offered American odds."
    )
    c1, c2, c3 = st.columns(3)
    min_ev = c1.slider("Min EV %", 0, 50, 5) / 100
    klabel = c2.selectbox("Kelly sizing", ["Quarter", "Half", "Full"], index=0)
    kmult = {"Quarter": 0.25, "Half": 0.5, "Full": 1.0}[klabel]
    bankroll = c3.number_input("Bankroll ($)", min_value=0, value=100, step=10)
    realistic = st.checkbox(
        "🎯 Confirmed edges only — hide edges > 20% (almost always model error, not real value)",
        value=True)
    ev_stat = st.multiselect("Prop / stat type", _stat_options(df[df["book"] == "Underdog"]),
                             placeholder="All stats", key="ev_stat_filter")

    rows_in = _filter_stats(df, ev_stat).to_dict("records")
    plays = recommend.ev_plays(rows_in, min_ev=min_ev, n=15,
                               max_edge=0.20 if realistic else None)
    if not plays:
        st.info("No +EV edges at this threshold — often means the market is efficient, which is normal and healthy.")
    else:
        from collections import Counter
        corr = [g for g, c in Counter(p["game"] for p in plays).items() if c > 1 and g != "?"]
        if corr:
            st.warning("⚠️ **Same-game legs found** (correlated — don't parlay these as if independent): "
                       + ", ".join(corr))
        tbl = pd.DataFrame([{
            "Pick": f"{p['side']} {p['player']} {p['line']} {p['stat']}",
            "Game": p["game"],
            "Odds": f"{'+' if p['odds'] > 0 else ''}{int(p['odds'])}",
            "Our %": round(p["our_p"] * 100, 1),
            "Mkt %": round(p["mkt_p"] * 100, 1) if p["mkt_p"] is not None else None,
            "Edge": round(p["edge"] * 100, 1) if p["edge"] is not None else None,
            "EV %": round(p["ev"] * 100, 1),
            f"{klabel} Kelly $": round(p["kelly"] * kmult * bankroll, 2),
            "⚑": "same-game" if p["correlated"] else "",
        } for p in plays])
        st.dataframe(
            tbl, use_container_width=True, hide_index=True,
            column_config={
                "Our %": st.column_config.ProgressColumn("Our %", min_value=0, max_value=100, format="%.0f%%"),
                "EV %": st.column_config.NumberColumn("EV %", format="%.1f"),
            },
        )
        st.caption(
            "⚠️ A big edge from a matchup-blind model usually means **the market knows something** "
            "(injury, matchup, weather) — treat these as hypotheses to verify, not locks. Props are "
            "−EV on average; size small and use fractional Kelly."
        )

@st.fragment(run_every=30)
def _slate_fragment():
    games, gerr = load_games(tuple(st.session_state.get("slate_sports", ("MLB",))))
    if gerr:
        st.warning(f"Couldn't load the slate: {gerr}")
    if not games:
        st.info("No games found for the selected sports.")
        return
    live = [g for g in games if (g.get("status") or "scheduled") != "scheduled"]
    st.caption(f"{len(games)} game(s) · {len(live)} live or final")
    per_row = 3
    for i in range(0, len(games), per_row):
        for col, g in zip(st.columns(per_row), games[i:i + per_row]):
            status = (g.get("status") or "scheduled")
            away, home = g.get("away") or "?", g.get("home") or "?"
            a_s, h_s = g.get("away_score"), g.get("home_score")
            prog = g.get("progress") or ""
            if status == "scheduled":
                badge = f"<span style='color:#B9B2D6'>🕒 {prog}</span>"
            elif status in ("completed", "final", "closed"):
                badge = f"<span style='color:#B9B2D6'>Final · {away} {a_s}–{h_s} {home}</span>"
            else:
                badge = (f"<span style='color:{PINK};font-weight:700'>● LIVE</span> "
                         f"<span style='color:#ECE9F7'>{away} {a_s}–{h_s} {home}</span>"
                         f"<span style='color:#B9B2D6'> · {prog}</span>")
            pl = g.get("props")
            props_txt = f" · {pl} props" if pl is not None else ""
            col.markdown(
                f"""
                <div style="background:#17122A;border:1px solid rgba(139,92,246,.25);
                            border-radius:14px;padding:12px 14px;margin-bottom:10px">
                  <div style="font-weight:700;font-size:1.02rem">{away} @ {home}</div>
                  <div style="font-size:.85rem;margin-top:5px">{badge}</div>
                  <div style="color:{VIOLET};font-size:.76rem;margin-top:7px">
                    {g.get('sport')}{props_txt}</div>
                </div>
                """, unsafe_allow_html=True)


with tab_slate:
    st.subheader("Today's slate")
    st.caption("Live scores refresh automatically (~30s) — or hit 🔄 Refresh lines anytime.")
    _slate_fragment()

with tab_gd:
    st.subheader("PrizePicks Goblin & Demon hit likelihood")
    st.caption(
        "Projected probability the **Over** hits, using PrizePicks' standard "
        "line as the expected value. Goblins 🟢 are the safer (lower) lines; "
        "Demons 😈 are the juiced (higher) lines."
    )
    gd = df[(df["book"] == "PrizePicks") & (df["flavor"].isin(["goblin", "demon"]))].copy()
    if gd.empty:
        st.info("No Goblin/Demon lines in the selected sports right now.")
    else:
        c1, c2 = st.columns(2)
        flav_pick = c1.radio("Flavor", ["Both", "🟢 Goblin", "😈 Demon"], horizontal=True)
        min_p = c2.slider("Min hit probability", 0, 100, 0, 5) / 100
        stat_pick = st.multiselect("Prop / stat type", _stat_options(gd), placeholder="All stats",
                                   key="gd_stat_filter")
        if flav_pick != "Both":
            want = "goblin" if "Goblin" in flav_pick else "demon"
            gd = gd[gd["flavor"] == want]
        gd = _filter_stats(gd, stat_pick)
        gd = gd[gd["hit_prob"].fillna(0) >= min_p]
        gd = gd[gd["hit_prob"].notna()]
        gd["Flavor"] = gd["flavor"].map(FLAVOR_LABEL)
        gd["Hit %"] = pct(gd["hit_prob"])
        gd["Proj src"] = gd["mean_source"].map(SOURCE_LABEL).fillna(gd["mean_source"])
        view = gd[["Flavor", "player", "team", "stat", "line", "mean", "Proj src", "Hit %"]].rename(
            columns={"player": "Player", "team": "Team", "stat": "Stat",
                     "line": "Line", "mean": "Proj"}
        ).sort_values("Hit %", ascending=False)
        st.dataframe(
            view, use_container_width=True, hide_index=True, height=560,
            column_config={
                "Hit %": st.column_config.ProgressColumn(
                    "Hit %", min_value=0, max_value=100, format="%.1f%%"),
                "Proj": st.column_config.NumberColumn("Proj", help="Projected expected value for this stat"),
            },
        )
        st.caption(
            "Proj = projected expected value · **Proj src**: where it came from — "
            "Market (Underdog de-vig), PP standard line, or Our model (season rates)."
        )

# --- All props ----------------------------------------------------------

with tab_all:
    st.subheader("All lines")
    f1, f2, f3, f4 = st.columns([1, 1, 1.6, 1.4])
    book_f = f1.multiselect("Book", sorted(df["book"].unique()), default=sorted(df["book"].unique()))
    flav_f = f2.multiselect("Flavor", sorted(df["flavor"].unique()), default=sorted(df["flavor"].unique()))
    # stat-type picker — options are whatever stats the loaded sports offer,
    # ordered most-common first (like PrizePicks' "Popular" bar)
    stat_f = f3.multiselect("Prop / stat type", _stat_options(df), placeholder="All stats",
                            key="all_stat_filter")
    search = f4.text_input("Search player")
    a = df[df["book"].isin(book_f) & df["flavor"].isin(flav_f)].copy()
    a = _filter_stats(a, stat_f)
    if search:
        s = search.lower()
        a = a[a["player"].str.lower().str.contains(s) | a["stat"].str.lower().str.contains(s)]
    a["Flavor"] = a["flavor"].map(FLAVOR_LABEL).fillna(a["flavor"])
    a["Hit %"] = pct(a["hit_prob"])
    a["Edge"] = pct(a["edge"])
    cols = ["book", "sport", "player", "team", "opponent", "stat", "line",
            "Flavor", "over_odds", "under_odds", "mean", "Hit %", "Edge"]
    view = a[cols].rename(columns={
        "book": "Book", "sport": "Sport", "player": "Player", "team": "Team",
        "opponent": "Opp", "stat": "Stat", "line": "Line",
        "over_odds": "Over", "under_odds": "Under", "mean": "Proj"})
    view = view.sort_values("Edge", ascending=False, key=lambda s: s.abs())
    st.dataframe(
        view, use_container_width=True, hide_index=True, height=600,
        column_config={
            "Hit %": st.column_config.ProgressColumn("Hit %", min_value=0, max_value=100, format="%.1f%%"),
            "Edge": st.column_config.NumberColumn("Edge", help="Model over% minus market/coin-flip", format="%.1f"),
        },
    )
    st.caption(
        "Edge: for Underdog (has odds) = model over% − de-vigged market over%. "
        "For PrizePicks = model over% − 50%."
    )

# --- Cross-book edges ---------------------------------------------------

with tab_cross:
    st.subheader("Same player + stat priced on both books")
    st.caption("Line and probability disagreements between PrizePicks and Underdog — the cleanest real edges.")
    if df["book"].nunique() < 2:
        st.info("Enable both books to see cross-book comparisons.")
    else:
        # one representative line per book per group (prefer standard flavor)
        df["_grp"] = df["sport"] + "|" + df["player"].str.lower() + "|" + df["stat"].str.lower()
        df["_pri"] = (df["flavor"] != "standard").astype(int)  # standard first
        rep = df.sort_values("_pri").groupby(["_grp", "book"], as_index=False).first()
        piv = rep.pivot_table(index=["_grp"], columns="book", values="line", aggfunc="first")
        piv = piv.dropna()
        if piv.empty or not {"PrizePicks", "Underdog"}.issubset(piv.columns):
            st.info("No overlapping player+stat lines between the two books right now.")
        else:
            meta = rep.groupby("_grp").first()
            out = piv.copy()
            out["Player"] = meta["player"]
            out["Stat"] = meta["stat"]
            out["Sport"] = meta["sport"]
            out["Line Δ"] = (out["PrizePicks"] - out["Underdog"]).round(2)
            out = out[out["Line Δ"].abs() > 0]
            out = out[["Sport", "Player", "Stat", "PrizePicks", "Underdog", "Line Δ"]]
            out = out.sort_values("Line Δ", ascending=False, key=lambda s: s.abs())
            if out.empty:
                st.info("Books agree on every overlapping line right now.")
            else:
                st.dataframe(out, use_container_width=True, hide_index=True, height=560)
                st.caption(f"{len(out)} player+stat lines differ between the books.")

# --- Pick Evaluator -----------------------------------------------------


def _render_slip(legs, lut):
    """Compute and display probabilities + EV for a list of legs."""
    for lg in legs:
        lg["prob"] = evaluate.leg_probability(lg["player"], lg["stat"], lg["line"], lg["side"], lut)
    table = pd.DataFrame([{
        "Player": lg["player"], "Stat": lg["stat"], "Line": lg["line"],
        "Pick": evaluate.side_label(lg["side"], lg.get("book")),
        "Hit %": round(lg["prob"] * 100, 1) if lg["prob"] is not None else None,
    } for lg in legs])
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={"Hit %": st.column_config.ProgressColumn(
            "Hit %", min_value=0, max_value=100, format="%.1f%%")},
    )
    unpriced = sum(1 for lg in legs if lg["prob"] is None)
    if unpriced:
        st.caption(f"⚠️ {unpriced} leg(s) couldn't be priced (player+stat not on the loaded board for the selected sports).")

    res = evaluate.evaluate(legs)
    n = res["n_priced"]
    if n < 1:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Legs priced", f"{n}/{res['n_total']}")
    c2.metric("All hit (parlay)", f"{res['all_hit']*100:.1f}%")
    ev, mult = evaluate.power_ev(res["all_hit"], n)
    if ev is not None:
        c3.metric(f"Power EV (×{mult})", f"{ev*100:+.1f}%", help="Per $1 stake, all-or-nothing. Standard multiplier.")

    if n >= 2:
        st.markdown("**Distribution — how many of your legs hit:**")
        dist_tbl = pd.DataFrame({
            "Legs hitting": list(range(n + 1)),
            "Exactly": [f"{p*100:.1f}%" for p in res["dist"]],
            "At least": [f"{p*100:.1f}%" for p in res["at_least"]],
        })
        st.dataframe(dist_tbl, use_container_width=True, hide_index=True)
        fev = evaluate.flex_ev(res["dist"], n)
        if fev is not None:
            st.caption(f"Flex EV (standard payouts): **{fev*100:+.1f}%** per $1. "
                       "Payouts vary by promo; Goblins/Demons change multipliers.")
    st.caption("Combined probability assumes independent legs — same-game or same-player picks correlate, so treat it as an estimate.")


with tab_eval:
    st.subheader("Evaluate a slip for probability")
    st.caption("Build an entry and see each leg's hit likelihood, the combined parlay probability, and expected value.")
    mode = st.radio("Add picks by", ["Select from board", "Upload screenshot"], horizontal=True)
    lut = evaluate.build_mean_lookup(df.to_dict("records"))

    if mode == "Select from board":
        opt = df.copy()
        opt["label"] = opt.apply(
            lambda r: f"{r['player']} — {r['stat']} {r['line']} · {FLAVOR_LABEL.get(r['flavor'], r['flavor'])} ({r['book']})",
            axis=1)
        chosen = st.multiselect("Add legs (2–6 for a typical entry)", opt["label"].tolist(), max_selections=6)
        sel = opt[opt["label"].isin(chosen)]
        if not sel.empty:
            editable = pd.DataFrame([{"Player": r["player"], "Stat": r["stat"], "Line": r["line"],
                                      "Book": r["book"], "Pick": "More / Over"} for _, r in sel.iterrows()])
            ed = st.data_editor(
                editable, hide_index=True, use_container_width=True,
                disabled=["Player", "Stat", "Line", "Book"],
                column_config={"Pick": st.column_config.SelectboxColumn(
                    "Pick", options=["More / Over", "Less / Under"],
                    help="More/Over (PrizePicks 'More', Underdog 'Higher') or Less/Under.")},
            )
            legs = [{"player": r.Player, "stat": r.Stat, "line": r.Line, "book": r.Book,
                     "side": evaluate.parse_side(r.Pick)} for r in ed.itertuples()]
            _render_slip(legs, lut)
        else:
            st.info("Pick some legs above to evaluate them.")

    else:  # Upload screenshot
        up = st.file_uploader("Upload a screenshot of your slip", type=["png", "jpg", "jpeg"])
        if up is not None:
            st.image(up, width=320)
            if st.button("🔍 Extract picks", type="primary"):
                key = _anthropic_key()
                if not key:
                    st.warning("Screenshot reading needs an Anthropic API key. Add `ANTHROPIC_API_KEY` to "
                               "`.streamlit/secrets.toml` or your environment, then try again.")
                else:
                    with st.spinner("Reading your slip…"):
                        try:
                            picks = screenshot.extract_picks(up.getvalue(), up.type or "image/png", api_key=key)
                            st.session_state["slip_picks"] = picks
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Couldn't read the screenshot: {e}")
        picks = st.session_state.get("slip_picks", [])
        if picks:
            legs = [{"player": p.get("player"), "stat": p.get("stat"),
                     "line": p.get("line"), "side": p.get("side", "more")} for p in picks]
            st.success(f"Found {len(legs)} pick(s) in the screenshot.")
            _render_slip(legs, lut)


# --- Post-Mortem: why didn't my slip hit? -------------------------------

with tab_pm:
    st.subheader("Why didn't it hit?")
    st.caption(
        "Upload a screenshot of a settled slip that lost. We read each leg's "
        "result, then compare it against our model to tell you whether it was a "
        "bad pick or just bad luck."
    )
    up_pm = st.file_uploader(
        "Upload a screenshot of the settled (graded) slip", type=["png", "jpg", "jpeg"],
        key="pm_upload")
    if up_pm is not None:
        st.image(up_pm, width=320)
        if st.button("🔎 Analyze why it didn't hit", type="primary"):
            key = _anthropic_key()
            if not key:
                st.warning("Screenshot reading needs an Anthropic API key. Add `ANTHROPIC_API_KEY` to "
                           "`.streamlit/secrets.toml` or your environment, then try again.")
            else:
                with st.spinner("Reading the result…"):
                    try:
                        st.session_state["pm_legs"] = screenshot.extract_results(
                            up_pm.getvalue(), up_pm.type or "image/png", api_key=key)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Couldn't read the screenshot: {e}")

    legs = st.session_state.get("pm_legs", [])
    if legs:
        lut = evaluate.build_mean_lookup(df.to_dict("records"))
        for lg in legs:
            lg["prob"] = evaluate.leg_probability(
                lg.get("player"), lg.get("stat"), lg.get("line"), lg.get("side"), lut)

        RES_ICON = {"hit": "✅", "miss": "❌", "push": "➖", "unknown": "❔"}
        table = pd.DataFrame([{
            "Player": lg.get("player"), "Stat": lg.get("stat"), "Line": lg.get("line"),
            "Pick": evaluate.side_label(lg.get("side")),
            "Actual": lg.get("actual"),
            "Result": RES_ICON.get(lg.get("result"), "❔"),
            "Model %": round(lg["prob"] * 100, 1) if lg.get("prob") is not None else None,
        } for lg in legs])
        st.dataframe(
            table, use_container_width=True, hide_index=True,
            column_config={"Model %": st.column_config.ProgressColumn(
                "Model %", min_value=0, max_value=100, format="%.1f%%",
                help="Our model's hit probability for this leg, before the games ran.")},
        )

        misses = [lg for lg in legs if lg.get("result") == "miss"]
        hits = sum(1 for lg in legs if lg.get("result") == "hit")
        st.markdown(f"**{hits}/{len(legs)} legs hit.** Your busted legs:")
        if not misses:
            st.info("No clearly-missed legs were detected in the image.")
        for lg in misses:
            p = lg.get("prob")
            actual = lg.get("actual")
            line_txt = f"needed {evaluate.side_label(lg.get('side'))} {lg.get('line')}"
            got_txt = f", got {actual}" if actual is not None else ""
            if p is None:
                verdict = "not on today's board, so we can't grade the pick itself."
            elif p >= 0.6:
                verdict = f"our model liked it ({p*100:.0f}%) — this one was **bad luck**."
            elif p >= 0.45:
                verdict = f"basically a coin flip ({p*100:.0f}%) — a **thin** leg to anchor a slip."
            else:
                verdict = f"our model was against it ({p*100:.0f}%) — this was a **bad pick**, not variance."
            st.markdown(f"- **{lg.get('player')} {lg.get('stat')}** ({line_txt}{got_txt}): {verdict}")

        priced_miss = [lg["prob"] for lg in misses if lg.get("prob") is not None]
        avg = sum(priced_miss) / len(priced_miss) if priced_miss else None
        verdict_estimated = False

        # Fallback: nothing on our board to price, so let the model estimate.
        if avg is None and misses:
            key = _anthropic_key()
            if key:
                with st.spinner("None of these legs are on today's board — estimating from sports knowledge…"):
                    try:
                        est = screenshot.estimate_loss(legs, api_key=key)
                    except Exception as e:  # noqa: BLE001
                        est = None
                        st.caption(f"Couldn't estimate a verdict: {e}")
                if est and est.get("avg_miss_prob") is not None:
                    avg = float(est["avg_miss_prob"])
                    verdict_estimated = True
                    if est.get("reasoning"):
                        st.caption(f"🤖 Estimated (no board data): {est['reasoning']}")

        verdict_label = None
        if avg is not None:
            if avg >= 0.58:
                verdict_label = "variance"
                st.success("Verdict: mostly **variance** — your missed legs were ones the model "
                           "rated highly. Keep the process, the results will follow.")
            elif avg >= 0.45:
                verdict_label = "thin edges"
                st.warning("Verdict: **thin edges**. Your busted legs were close to coin flips — "
                           "tighten up to higher-probability legs to lift your hit rate.")
            else:
                verdict_label = "pick quality"
                st.error("Verdict: **pick quality**, not luck. The model was already against the "
                         "legs that missed — lean on the Best Plays / +EV tabs next time.")
        st.caption("Model % is computed from today's loaded board, so legs from older slips may show blank.")

        if st.button("💾 Save this post-mortem", key="pm_save"):
            pm_id = storage.save_postmortem(legs, verdict_label, avg)
            st.success(f"Saved post-mortem #{pm_id} to your record.")

    # --- Record of past post-mortems ------------------------------------
    history = storage.list_postmortems()
    if history:
        st.divider()
        st.markdown("#### 📚 Your loss record")
        VERDICT_TAG = {"variance": "🍀 variance", "thin edges": "⚖️ thin edges",
                       "pick quality": "🎯 pick quality"}
        hist_tbl = pd.DataFrame([{
            "When": (h["created_at"] or "")[:16].replace("T", " "),
            "Legs hit": f"{h['n_hit']}/{h['n_legs']}",
            "Avg miss %": round(h["avg_miss_prob"] * 100, 1) if h["avg_miss_prob"] is not None else None,
            "Verdict": VERDICT_TAG.get(h["verdict"], h["verdict"] or "—"),
        } for h in history])
        st.dataframe(hist_tbl, use_container_width=True, hide_index=True)

        counts = {}
        for h in history:
            if h["verdict"]:
                counts[h["verdict"]] = counts.get(h["verdict"], 0) + 1
        if counts:
            top = max(counts, key=counts.get)
            st.caption(
                f"Across {len(history)} saved losses, the most common cause is "
                f"**{VERDICT_TAG.get(top, top)}** ({counts[top]}×). "
                + ("Mostly variance means your process is sound — stay the course."
                   if top == "variance" else
                   "Worth tightening leg selection on the Best Plays / +EV tabs.")
            )
            del_id = st.number_input("Delete a record by id", min_value=0, step=1, value=0, key="pm_del")
            if del_id and st.button("Delete record", key="pm_del_btn"):
                storage.delete_postmortem(int(del_id))
                st.rerun()


# --- Backtest -----------------------------------------------------------

with tab_bt:
    st.subheader("Did the Goblins & Demons actually hit?")
    st.caption(
        "Grades stored MLB snapshots against real box-score results. Every time "
        "you load lines they're saved, so this fills in as games complete."
    )
    dates = storage.game_dates_available()
    if not dates:
        st.info("No snapshots saved yet. Load some lines (any tab) and come back after the games finish.")
    else:
        date = st.selectbox("Game date", dates, help="Dates we have saved MLB lines for.")
        if st.button("Grade this date", type="primary"):
            with st.spinner("Fetching results & grading…"):
                g = backtest.grade_date(date)
            if not g["rows"]:
                st.warning(g.get("note") or "Nothing gradable (no completed MLB lines / results for this date).")
            else:
                s = g["summary"]
                cols = st.columns(3)
                for col, key, label in zip(cols, ["goblin", "demon", "overall"], ["🟢 Goblins", "😈 Demons", "All"]):
                    b = s.get(key)
                    if b:
                        delta = None
                        if b["model_avg_prob"] is not None:
                            delta = f"model said {b['model_avg_prob']*100:.0f}%"
                        col.metric(f"{label} hit rate", f"{b['actual_hit_rate']*100:.0f}%",
                                   delta=delta, delta_color="off", help=f"n = {b['n']}")
                tbl = pd.DataFrame(g["rows"])
                tbl["Hit?"] = tbl["over_hit"].map({True: "✅", False: "❌"})
                tbl["Model %"] = (tbl["predicted"].fillna(0) * 100).round(0)
                tbl["Flavor"] = tbl["flavor"].map(FLAVOR_LABEL).fillna(tbl["flavor"])
                show = tbl[["Flavor", "player", "stat", "line", "actual", "Hit?", "Model %"]].rename(
                    columns={"player": "Player", "stat": "Stat", "line": "Line", "actual": "Actual"})
                st.dataframe(show.sort_values("Model %", ascending=False),
                             use_container_width=True, hide_index=True, height=460)
                st.caption(
                    "Calibration check: a well-tuned model's **hit rate** should "
                    "track the **model said %** above it."
                )


# --- Lottery: Powerball & Mega Millions ---------------------------------

@st.cache_data(ttl=21600, show_spinner=False)
def _load_lottery(game_key, limit):
    return lottery.fetch_draws(game_key, limit)


def _ball_html(white, special, special_color):
    """Render a picked line as colored number-balls."""
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
        f'width:38px;height:38px;border-radius:50%;background:#F4F4F8;color:#17122A;'
        f'font-weight:800;margin:3px;border:2px solid #C9C7D6;">{n}</span>'
        for n in white)
    sp = (f'<span style="display:inline-flex;align-items:center;justify-content:center;'
          f'width:38px;height:38px;border-radius:50%;background:{special_color};color:#fff;'
          f'font-weight:800;margin:3px;">{special}</span>')
    return f'<div style="margin:6px 0;">{chips}<span style="margin:0 6px;color:#888;">+</span>{sp}</div>'


with tab_lotto:
    st.subheader("🎰 Powerball & Mega Millions")
    st.caption(
        "Number generators backed by real draw history. **Reality check:** every "
        "draw is independent and uniform — no method changes your odds of winning. "
        "The one true edge is *combinatorial*: picking uncommon numbers (above 31, "
        "no patterns) won't help you win, but it lowers the chance you'd **split** a "
        "jackpot with the crowd playing birthdays."
    )

    c1, c2 = st.columns([1, 1])
    game = c1.radio("Game", list(lottery.GAMES.keys()), horizontal=True, key="lotto_game")
    window = c2.selectbox("History window", ["Last 100", "Last 250", "Last 500", "Max (1000)"],
                          index=2, key="lotto_window")
    limit = {"Last 100": 100, "Last 250": 250, "Last 500": 500, "Max (1000)": 1000}[window]

    g = lottery.GAMES[game]
    try:
        draws = _load_lottery(game, limit)
    except Exception as e:  # noqa: BLE001
        draws = []
        st.error(f"Couldn't load draw history: {e}")

    if not draws:
        st.info("No draw history available right now.")
    else:
        st.caption(f"Loaded **{len(draws)}** draws · {draws[-1]['date']} → {draws[0]['date']} · "
                   f"{g['white_count']} white (1–{g['white_max']}) + "
                   f"{g['special_name']} (1–{g['special_max']}).")

        # --- Generator ---------------------------------------------------
        st.markdown("### 🎟️ Generate a line")
        gc1, gc2 = st.columns([2, 1])
        strat_label = gc1.radio("Strategy", list(lottery.PICKERS.keys()), key="lotto_strat")
        n_lines = gc2.slider("How many lines", 1, 10, 1, key="lotto_lines")
        if st.button("🎰 Generate numbers", type="primary", key="lotto_gen"):
            st.session_state["lotto_picks"] = lottery.generate(
                game, lottery.PICKERS[strat_label], draws, n_lines)

        picks = st.session_state.get("lotto_picks", [])
        if picks:
            html = "".join(_ball_html(w, s, g["special_color"]) for w, s in picks)
            st.markdown(html, unsafe_allow_html=True)
            txt = "\n".join(f"{' '.join(f'{n:02d}' for n in w)}  |  {g['special_name']}: {s:02d}"
                            for w, s in picks)
            st.code(txt, language=None)
        st.caption({
            "🎲 Pure random": "A fair uniform draw — the honest baseline.",
            "🔥 Hot (frequency-weighted)": "Each ball weighted by how often it has hit in this window.",
            "❄️ Overdue (cold)": "Each ball weighted by how long since it last appeared.",
            "⚖️ Balanced (math-optimized)": "Reject-sampled until the line matches real winners' "
                                            "sum range (10th–90th pct) and a mixed odd/even split.",
        }[strat_label])

        st.divider()

        # --- Check my ticket ---------------------------------------------
        st.markdown("### 🎫 Check my ticket")
        st.caption("Enter your numbers to see how this exact line would have done against every "
                   "draw in the window, plus its hot/cold and sum profile.")
        in_cols = st.columns(g["white_count"] + 1)
        my_white = []
        for i in range(g["white_count"]):
            v = in_cols[i].number_input(f"#{i+1}", min_value=1, max_value=g["white_max"],
                                        value=None, step=1, key=f"tk_w{i}_{game}")
            if v:
                my_white.append(int(v))
        my_sp = in_cols[-1].number_input(g["special_name"], min_value=1, max_value=g["special_max"],
                                         value=None, step=1, key=f"tk_s_{game}")
        if st.button("🎫 Check it", key="lotto_check"):
            st.session_state["lotto_ticket"] = lottery.check_ticket(
                game, my_white, int(my_sp) if my_sp else None, draws)

        res = st.session_state.get("lotto_ticket")
        if res and not res["valid"]:
            st.warning(res["error"])
        elif res:
            st.markdown(_ball_html(sorted(my_white), int(my_sp), g["special_color"]),
                        unsafe_allow_html=True)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Draws checked", res["draws_checked"])
            m2.metric("Winning draws", len(res["wins"]))
            best = res["best"]
            m3.metric("Best result", best["label"] if best else "—",
                      help=(f"{best['white_match']} white"
                            + (f" + {g['special_name']}" if best['special_match'] else "")
                            + f" on {best['date']}") if best else "No prize-tier match")
            m4.metric("Hypothetical winnings", f"${res['total_amount']:,.0f}"
                      + (f" + {res['jackpots']}× JP" if res["jackpots"] else ""),
                      help="If you'd played this line every draw in the window. Non-jackpot tiers "
                           "summed; jackpots counted separately.")
            if res["tier_counts"]:
                tier_tbl = pd.DataFrame(
                    [{"Prize tier": k, "Times won": v} for k, v in
                     sorted(res["tier_counts"].items(), key=lambda kv: -kv[1])])
                st.dataframe(tier_tbl, hide_index=True, use_container_width=True)
            else:
                st.info("This line wouldn't have hit any prize tier in the window — which is the "
                        "norm; the odds are astronomical.")

            prof = res["profile"]
            band_ok = "✅ in the typical winning band" if prof["in_band"] else "⚠️ outside the typical band"
            band_txt = f"{prof['sum_band'][0]}–{prof['sum_band'][1]}" if prof["sum_band"] else "?"
            st.markdown(
                f"**Profile** · sum **{prof['sum']}** ({band_ok}, band {band_txt}) · "
                f"odd/even **{prof['odds']}/{prof['evens']}** · "
                f"{g['special_name']} #{prof['special']['num']} is rank "
                f"**{prof['special']['rank']}/{prof['special']['of']}** by frequency."
            )
            prof_tbl = pd.DataFrame([
                {"Your number": b["num"], "Times hit": b["hits"],
                 "Hotness rank": f"{b['rank']}/{prof['white_of']}"} for b in prof["ball_freq"]])
            st.dataframe(prof_tbl, hide_index=True, use_container_width=True)
            st.caption("Purely hypothetical back-look — past draws don't predict future ones. "
                       "Each play is an independent shot at the same long odds.")

        st.divider()

        # --- Frequency ---------------------------------------------------
        st.markdown("### 📊 Most frequent numbers")
        wf = lottery.white_frequency(draws, game)
        sf = lottery.special_frequency(draws, game)
        gaps = lottery.overdue_gaps(draws, game, "white")
        hot = sorted(wf, key=wf.get, reverse=True)
        cold_by_gap = sorted(gaps, key=gaps.get, reverse=True)

        fc1, fc2 = st.columns(2)
        fc1.markdown("**🔥 Hottest white balls**")
        fc1.dataframe(pd.DataFrame({"Number": hot[:10], "Times hit": [wf[n] for n in hot[:10]]}),
                      hide_index=True, use_container_width=True)
        fc2.markdown("**❄️ Most overdue white balls**")
        fc2.dataframe(pd.DataFrame({"Number": cold_by_gap[:10],
                                    "Draws since seen": [gaps[n] for n in cold_by_gap[:10]]}),
                      hide_index=True, use_container_width=True)

        st.markdown(f"**White-ball frequency (1–{g['white_max']})**")
        st.bar_chart(pd.Series(wf, name="Times hit").sort_index())
        hot_sp = sorted(sf, key=sf.get, reverse=True)[:5]
        st.markdown(f"**Hottest {g['special_name']}s:** "
                    + " · ".join(f"`{n}` ({sf[n]}×)" for n in hot_sp))

        st.divider()

        # --- Best days ---------------------------------------------------
        st.markdown("### 📅 Best days to play certain numbers")
        st.caption(f"{game} draws on **{', '.join(g['draw_days'])}**. Below: the numbers that "
                   "have hit most on each draw day in this window. (Statistically this is noise — "
                   "the same machine runs every night — but here's the breakdown you asked for.)")
        bw = lottery.by_weekday(draws, game)
        day_cols = st.columns(len(bw) or 1)
        for col, (day, info) in zip(day_cols, bw.items()):
            col.markdown(f"**{day}** · {info['n']} draws")
            col.markdown("Hot white: " + " ".join(f"`{n}`" for n in info["white"]))
            if info["special"] is not None:
                col.markdown(f"Hot {g['special_name']}: `{info['special']}`")

        st.divider()

        # --- The math ----------------------------------------------------
        ss = lottery.sum_stats(draws)
        if ss:
            st.markdown("### 🧮 The math behind *Balanced*")
            st.markdown(
                f"Real winning lines aren't spread evenly — their **white-ball sum** clusters. "
                f"In this window the 5 white balls summed between **{ss['min']}** and **{ss['max']}**, "
                f"averaging **{ss['avg']}**, with 80% of draws landing in **{ss['p10']}–{ss['p90']}**. "
                f"The *Balanced* picker only keeps random lines whose sum falls in that band and "
                f"that avoid all-odd or all-even combos — so your numbers *look like* historical "
                f"winners. It does **not** improve your odds; it just filters out statistically "
                f"unusual-looking lines and crowd-favorite patterns."
            )
        st.caption("Data: official NY State open-data draw history. For entertainment — play responsibly.")
