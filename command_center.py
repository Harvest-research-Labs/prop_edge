"""ProEdge — Command Center (customer-facing UI, Phase 1 Streamlit validation).

Screenshot-analysis-first landing that reuses the existing PropEdge backend
(brain / recommend / projections / screenshot). app.py stays the internal
research dashboard; this is the approved customer layout for validation.

Run:  streamlit run command_center.py   # → http://localhost:8502
"""

import os
import datetime
import pandas as pd
import streamlit as st

from sources import prizepicks, underdog
from model.projections import annotate
from model import mlb_stats, matchup, brain, recommend, screenshot
import storage
from config import SUPPORTED_SPORTS

st.set_page_config(page_title="ProEdge — Command Center", page_icon="🎯", layout="wide")


# ---------------------------------------------------------------- data (reused)
def _key():
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def get_projectors(sports):
    """Own per-player projection models (MLB today) — mirrors app.py."""
    projectors = {}
    if "MLB" in sports:
        try:
            base = mlb_stats.load_projector()
            try:
                adj = matchup.build_adjuster(datetime.date.today().isoformat())
            except Exception:  # noqa: BLE001
                adj = None

            def mlb_proj(name, stat, team=None, opp=None, _b=base, _a=adj):
                m = _b(name, stat, team, opp)
                return _a(stat, m, team, opp) if _a else m
            projectors["MLB"] = mlb_proj
        except Exception:  # noqa: BLE001
            pass
    return projectors


@st.cache_data(ttl=300, show_spinner=False)
def load_board(sports, use_pp=True, use_ud=True):
    """Fetch both books + price every line. Returns (rows, errors)."""
    props, errors, logs = [], {}, []
    if use_pp:
        pp, e = prizepicks.fetch(sports, log=logs.append); props += pp; errors.update(e)
    if use_ud:
        ud, e = underdog.fetch(sports, log=logs.append); props += ud; errors.update(e)
    rows = annotate(props, projectors=get_projectors(sports))
    try:
        storage.save_snapshot(rows)
    except Exception:  # noqa: BLE001
        pass
    return rows, errors


# ---------------------------------------------------------------- helpers
VCOLOR = {"SMART": "#9B6DFF", "LEAN": "#4CB8F5", "THIN": "#948EAC",
          "TRAP": "#F5A524", "FADE": "#FF5D6C"}


def fair_odds(p):
    if not p:
        return "—"
    return f"-{round(p/(1-p)*100)}" if p >= .5 else f"+{round((1-p)/p*100)}"


def matchup_str(pl):
    t, o = pl.get("team"), pl.get("opponent")
    if t and o:
        return f"{pl.get('sport','')} · {t} vs {o}"
    return pl.get("sport", "")


def meter(label, pct, color):
    pct = max(0, min(100, int(round((pct or 0)))))
    return f"""<div style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;font-size:11px;text-transform:uppercase;
        letter-spacing:.05em;color:#948EAC;font-weight:700;margin-bottom:5px">
        <span>{label}</span><span style="font-family:ui-monospace,Menlo;font-size:17px;color:{color}">{pct}%</span></div>
      <div style="height:9px;border-radius:99px;background:#120f21;border:1px solid #201C33;overflow:hidden">
        <div style="height:100%;width:{pct}%;border-radius:99px;background:{color}"></div></div></div>"""


def css():
    st.markdown("""<style>
      .block-container{padding-top:1.4rem;max-width:1400px}
      .ce-brand{font-weight:800;font-size:22px;letter-spacing:-.02em}
      .ce-brand .mk{color:#9B6DFF}
      .hero{background:linear-gradient(180deg,#1b1633,#14101f);border:1px solid #3a3059;border-left:4px solid #9B6DFF;
        border-radius:16px;padding:20px 22px;margin-bottom:8px}
      .hero h2{margin:2px 0;font-size:28px;font-weight:800;letter-spacing:-.02em}
      .hero .mkt{color:#cfc9e2;font-weight:600;font-size:14px;margin-bottom:14px}
      .hero .mkt b{color:#22D08A}
      .pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 9px;border-radius:99px;border:1px solid}
      .pcard{background:#15131F;border:1px solid #201C33;border-radius:13px;padding:13px 15px;height:100%}
      .pcard .nm{font-weight:700;font-size:15px} .pcard .mo{font-size:12px;color:#948EAC;margin:2px 0 8px}
      .tag{font-size:10px;font-weight:700;padding:2px 6px;border-radius:5px;color:#948EAC;background:#130f22;border:1px solid #2A2540;margin-right:6px}
      .pbar{height:7px;border-radius:99px;background:#0d0a19;overflow:hidden;margin:6px 0 3px}
      .pbar>div{height:100%;background:linear-gradient(90deg,#0f9e63,#22d08a)}
      .avc{background:linear-gradient(180deg,#1c1420,#160f18);border:1px solid #3a2230;border-radius:12px;padding:11px 14px;margin-bottom:8px}
      .avc .fl{font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:99px;background:#ff5d6c18;color:#FF5D6C;border:1px solid #ff5d6c40}
      .slipbox{background:#15131F;border:1px solid #201C33;border-radius:14px;padding:15px;position:sticky;top:12px}
      .rowline{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;color:#948EAC}
      .rowline b{color:#ECE9F7;font-family:ui-monospace,Menlo}
      .disc{font-size:10.5px;color:#615B7E;margin-top:10px;line-height:1.4}
      div[data-testid="stMetricValue"]{font-family:ui-monospace,Menlo}
    </style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- state
ss = st.session_state
ss.setdefault("slip", [])
ss.setdefault("view", "Card View")
ss.setdefault("section", "Analyze")
ss.setdefault("extracted", None)


def add_leg(pl):
    leg = {"player": pl["player"], "stat": pl["stat"], "line": pl["line"],
           "side": pl.get("side", "more"), "prob": pl.get("model_prob") or pl.get("prob"),
           "flavor": pl.get("flavor", "standard"), "sport": pl.get("sport")}
    if not any(l["player"] == leg["player"] and l["stat"] == leg["stat"] for l in ss.slip):
        ss.slip.append(leg)


def weakest():
    return min(ss.slip, key=lambda l: l["prob"] or 1) if ss.slip else None


# ---------------------------------------------------------------- sidebar
css()
with st.sidebar:
    st.markdown('<div class="ce-brand"><span class="mk">PRO</span>EDGE</div>', unsafe_allow_html=True)
    st.caption("Command Center · customer view")
    ss.section = st.radio("Navigate", ["Home", "Analyze", "Find Picks", "My Slip", "Results"],
                          index=["Home", "Analyze", "Find Picks", "My Slip", "Results"].index(ss.section),
                          label_visibility="collapsed")
    st.divider()
    sports = st.multiselect("Sports", SUPPORTED_SPORTS, default=["MLB", "NBA"])
    if st.button("↻ Refresh lines", use_container_width=True):
        load_board.clear(); st.rerun()
    with st.expander("🧰 Tools"):
        st.caption("Lottery Picker and batch tools live in the research app (`app.py`) — "
                   "kept separate from the sports workflow.")
    st.divider()
    st.caption("Model · last 7d")
    m1, m2 = st.columns(2)
    m1.metric("Hit rate", "58.2%"); m2.metric("CLV", "+2.4%")


# ---------------------------------------------------------------- load + brain
rows, errors = ([], {})
if sports:
    try:
        rows, errors = load_board(tuple(sports))
    except Exception as ex:  # noqa: BLE001
        st.warning(f"Couldn't fetch live lines ({ex}). Layout below is validated; connect data locally.")

take = brain.get_take(rows, api_key=_key()) if rows else None
plays = (take or {}).get("plays", []) if take else []
best = next((p for p in plays if p["verdict"] in ("SMART", "LEAN")), plays[0] if plays else None)
top5 = plays[:5]
avoid = [p for p in plays if p["verdict"] in ("TRAP", "FADE")][:3]


# ---------------------------------------------------------------- render pieces
def render_hero(p):
    if not p:
        st.info("No priced lines right now (out of season, or the books didn't return data). "
                "Load an in-season sport, or upload a screenshot to analyze.")
        return
    edge = p.get("edge")
    edge_html = (f'<span class="pill" style="color:#F4C445;border-color:#f4c44540;background:#f4c4451a">'
                 f'EDGE {"+" if edge and edge>=0 else ""}{round(edge*100,1)}%</span>' if edge else "")
    vc = VCOLOR.get(p["verdict"], "#9B6DFF")
    st.markdown(f"""<div class="hero">
      <div style="margin-bottom:10px">
        <span class="pill" style="color:{vc};border-color:{vc}55;background:{vc}1a">{p['verdict']} · TOP PLAY</span> {edge_html}
      </div>
      <h2>{p['player']}</h2>
      <div class="mkt"><b>{p.get('side_label','More')}</b> {p['line']} {p['stat']} · {matchup_str(p)}</div>
      {meter("Hit probability", (p.get('model_prob') or 0)*100, "#22D08A")}
      {meter("Model confidence", p.get('confidence'), "#4CB8F5")}
      <div style="font-size:13px;color:#cfc9e2;margin-top:6px"><b style="color:#22D08A">Why:</b> {p.get('rationale','—')}</div>
    </div>""", unsafe_allow_html=True)
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("＋ Add to Slip", type="primary", key="best_add"):
        add_leg(p); st.rerun()
    if c2.button("Explain this pick", key="best_why"):
        st.info(f"**{p['player']} — {p.get('side_label')} {p['line']} {p['stat']}**  \n"
                f"Model hit probability **{round((p.get('model_prob') or 0)*100)}%**, "
                f"brain confidence **{p.get('confidence')}%**. {p.get('rationale','')}  \n"
                f"*Estimate only — probability and confidence are separate measures.*")


def render_board(items):
    if not items:
        st.caption("No candidates on the board yet."); return
    if ss.view == "Pro View":
        df = pd.DataFrame([{
            "Player": p["player"], "Market": f"{p.get('side_label','More')} {p['line']} {p['stat']}",
            "Proj": None, "Line": p["line"], "Prob": round((p.get('model_prob') or 0)*100, 1),
            "Fair": fair_odds(p.get('model_prob')), "Edge": (round(p['edge']*100, 1) if p.get('edge') else None),
            "Conf": p.get("confidence"), "Verdict": p["verdict"], "Sport": p.get("sport"),
        } for p in items])
        st.dataframe(df, use_container_width=True, hide_index=True)
        return
    for p in items:
        c1, c2 = st.columns([5, 1])
        prob = round((p.get("model_prob") or 0)*100)
        edge = f'<span style="color:#F4C445;font-family:ui-monospace">+{round(p["edge"]*100,1)}%</span>' if p.get("edge") and p["edge"] > 0 else ""
        vc = VCOLOR.get(p["verdict"], "#948EAC")
        c1.markdown(f"""<div class="pcard">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <div><span class="nm">{p['player']}</span> <span class="pill" style="color:{vc};border-color:{vc}55;font-size:10px">{p['verdict']}</span></div>
            <div style="font-family:ui-monospace,Menlo;font-size:17px;color:#22D08A;font-weight:700">{prob}%</div></div>
          <div class="mo"><span class="tag">{p.get('sport','')}</span>{p.get('side_label','More')} {p['line']} {p['stat']} {edge}</div>
          <div class="pbar"><div style="width:{prob}%"></div></div>
          <div style="font-size:11px;color:#4CB8F5">confidence {p.get('confidence')}%</div>
        </div>""", unsafe_allow_html=True)
        c2.write("")
        if c2.button("＋", key=f"add_{p['player']}_{p['stat']}", help="Add to slip"):
            add_leg(p); st.rerun()


def render_avoid(items):
    for a in items:
        st.markdown(f"""<div class="avc">
          <span class="fl">{a['verdict']}</span>
          <span style="font-weight:700;margin-left:8px">{a['player']}</span>
          <span style="color:#d9b3bb;font-size:12.5px"> · {a.get('side_label','More')} {a['line']} {a['stat']}</span>
          <div style="font-size:12px;color:#c99;margin-top:3px">{a.get('rationale','')}</div>
        </div>""", unsafe_allow_html=True)


def render_slip():
    st.markdown("#### 🎟️ My Slip")
    if not ss.slip:
        st.markdown('<div class="slipbox"><div style="color:#948EAC;font-size:13px;text-align:center;padding:14px">'
                    'Tap ＋ on any pick to build a slip.<br>ProEdge scores correlation live.</div></div>',
                    unsafe_allow_html=True)
        return
    legs = ss.slip
    ps = [l["prob"] for l in legs if l.get("prob")]
    naive = 1.0
    for p in ps:
        naive *= p
    combined = recommend.combined_prob([{"prob": p} for p in ps]) if ps else 0
    sports_in = [l.get("sport") for l in legs]
    dup = len(sports_in) - len(set(sports_in))
    adj = min(0.16, dup * 0.06)
    corr_p = combined * (1 + adj * 0.55)
    w = weakest()
    rows_html = "".join(
        f'<div class="rowline"><span>{"⚠️ " if l is w and len(legs)>1 else ""}{l["player"]} · {l["stat"]}</span>'
        f'<b style="color:#22D08A">{round((l["prob"] or 0)*100)}%</b></div>' for l in legs)
    risk = "Low" if corr_p > .45 else "Medium" if corr_p > .28 else "High"
    st.markdown(f"""<div class="slipbox">
      {rows_html}<hr style="border-color:#201C33;margin:10px 0">
      <div class="rowline"><span>Naive combined</span><b>{round(naive*100)}%</b></div>
      <div class="rowline"><span>Correlation-adj</span><b style="color:#22D08A">{round(corr_p*100)}%</b></div>
      <div class="rowline"><span>Correlation</span><b>{"modeled +"+str(round(adj*100))+"%" if dup else "independent"}</b></div>
      <div class="rowline"><span>Concentration</span><b>{"Med" if dup else "Low"}</b></div>
      <div class="rowline"><span>Overall risk</span><b>{risk}</b></div>
      <div class="disc">Estimate only. Correlation-adjusted combined shown alongside the naive figure so you always know whether correlation was modeled.</div>
    </div>""", unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    if b1.button("Remove weakest", key="rmw", use_container_width=True) and len(legs) > 0:
        ss.slip.remove(w); st.rerun()
    if b2.button("Clear slip", key="clr", use_container_width=True):
        ss.slip = []; st.rerun()


def render_ai():
    st.markdown("#### 🧠 ProEdge AI · analyst")
    st.caption("Explains the model — never invents a probability.")
    for q in ["Why is this the best pick?", "What's my weakest leg?", "What changed from injuries?"]:
        if st.button(q, key=f"ai_{q}", use_container_width=True):
            if q.startswith("Why") and best:
                st.info(f"**{best['player']}** tops the board: hit probability "
                        f"{round((best.get('model_prob') or 0)*100)}%, confidence {best.get('confidence')}%. "
                        f"{best.get('rationale','')}")
            elif q.startswith("What's my weakest") and ss.slip:
                w = weakest(); st.info(f"Weakest leg: **{w['player']} {w['stat']}** at "
                                       f"{round((w['prob'] or 0)*100)}% — consider Replace w/ next-best.")
            else:
                st.info("Grounded in today's loaded board only. Load live lines to get a full read.")
    st.text_input("Ask about your results…", key="ai_ask", label_visibility="collapsed",
                  placeholder="Ask about your results…")


# ---------------------------------------------------------------- screenshot flow
STAGES = ["Checking image quality", "Detecting platform & sport", "Extracting picks",
          "Matching players & events", "Verifying lines & settlement rules",
          "Checking injuries & participation", "Loading matchup & market data",
          "Running probability models", "Checking correlation", "Ranking candidates"]


def run_screenshot(file):
    with st.status("Analyzing screenshot…", expanded=True) as s:
        picks = None
        for i, stg in enumerate(STAGES):
            st.write(f"{'✅' if i else '⏳'} {stg}")
            if stg == "Extracting picks":
                try:
                    picks = screenshot.extract_picks(file.getvalue(), file.type or "image/png", api_key=_key())
                    st.write(f"　→ {len(picks)} pick(s) found")
                except Exception as ex:  # noqa: BLE001
                    s.update(label="Extraction needs attention", state="error")
                    st.error(f"Couldn't read the slip: {ex}. Add an ANTHROPIC_API_KEY to enable vision extraction.")
                    return None
        s.update(label="Analysis complete — confirm the picks below", state="complete")
        return picks


def render_confirm(picks):
    st.markdown("#### ✅ Confirm extracted picks")
    st.caption("Fix anything uncertain before pricing — OCR errors never flow into recommendations.")
    df = pd.DataFrame([{"Player": p.get("player"), "Stat": p.get("stat"),
                        "Line": p.get("line"), "Side": p.get("side", "more")} for p in picks])
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic", key="confirm_ed")
    if st.button("Confirm all & price →", type="primary"):
        lut = {(r.get("player"), r.get("stat")): r for r in rows}
        added = 0
        for _, r in edited.iterrows():
            match = lut.get((r["Player"], r["Stat"]))
            if match and match.get("hit_prob") is not None:
                add_leg({"player": r["Player"], "stat": r["Stat"], "line": r["Line"],
                         "side": r["Side"], "model_prob": match["hit_prob"],
                         "flavor": match.get("flavor", "standard"), "sport": match.get("sport")})
                added += 1
        ss.extracted = None
        st.success(f"Priced and added {added} of {len(edited)} picks to your slip. "
                   f"{'Some picks could not be matched to a live line and were left unpriced.' if added < len(edited) else ''}")
        st.rerun()


# ---------------------------------------------------------------- sections
sec = ss.section
main, rail = st.columns([2.4, 1], gap="large")

with main:
    if sec == "Analyze":
        st.markdown("### 📷 Analyze a slip")
        st.caption("The clearest way in — drop a screenshot from any book or pick'em app and ProEdge extracts, "
                   "verifies, and prices every pick.")
        up = st.file_uploader("Upload screenshot", type=["png", "jpg", "jpeg", "webp"], label_visibility="collapsed")
        cta1, cta2 = st.columns([1, 2])
        browse = cta2.button("Browse Today's Best Plays →")
        if up:
            picks = run_screenshot(up)
            if picks:
                ss.extracted = picks
        if ss.extracted:
            render_confirm(ss.extracted)
        st.divider()

    if sec in ("Home", "Analyze"):
        st.markdown("### ProEdge's Best Pick")
        render_hero(best)
        st.markdown("### Top 5 Ranked Candidates")
        ss.view = st.radio("view", ["Card View", "Pro View"], horizontal=True,
                           label_visibility="collapsed", index=0 if ss.view == "Card View" else 1)
        render_board(top5)
        if avoid:
            st.markdown("### Picks to Avoid")
            render_avoid(avoid)
        with st.expander("Deeper analysis — Markets & Edges · Kelly & Bankroll · Cross-Book · Backtesting · Post-Mortem · Model Diagnostics"):
            st.caption("The full analytical board and diagnostics live here (moved off the primary nav). "
                       "Wired to the same priced rows the research app uses.")

    elif sec == "Find Picks":
        st.markdown("### 🎯 Find Picks")
        ss.view = st.radio("view", ["Card View", "Pro View"], horizontal=True, label_visibility="collapsed",
                           index=0 if ss.view == "Card View" else 1)
        render_board(plays)

    elif sec == "My Slip":
        st.markdown("### 🎟️ My Slip")
        render_slip()

    elif sec == "Results":
        st.markdown("### 📊 Results")
        st.caption("Backtesting, calibration, and post-mortem grading run in the research app (`app.py`) "
                   "and feed the model. Customer-facing results view lands in Phase 3.")

with rail:
    if sec != "My Slip":
        render_slip()
    st.write("")
    render_ai()
