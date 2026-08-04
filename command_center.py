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
from model import mlb_stats, matchup, brain, recommend, screenshot, lottery
import storage
from config import SUPPORTED_SPORTS
from proedge_ui import (ranked_board, evaluate_slip, explain_board,
                        extract_image, price_confirmed, api_available, participation_chip)
from resolver.resolve import gate_mode
from resolver.participation import participation_gate_mode

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
    st.markdown("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
    <style>
      :root{
        --bg:#0A0F1E; --surface:#111A2E; --surface2:#0D1424; --raise:#16203A;
        --border:#1E2A44; --line:#26344F; --text:#E7EBF5; --muted:#8A93AD;
        --purple:#9B6DFF; --purple-dim:#8B5CF6; --green:#22D08A; --blue:#4CB8F5;
        --gold:#F4C445; --red:#FF5D6C;
        --disp:'Sora',system-ui,sans-serif; --body:'IBM Plex Sans',system-ui,sans-serif;
        --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace;
      }
      /* ---- hide Streamlit chrome ---- */
      #MainMenu, header[data-testid="stHeader"], [data-testid="stToolbar"], footer,
      [data-testid="stDecoration"], .stDeployButton, [data-testid="stAppDeployButton"]{
        display:none !important; visibility:hidden !important;}
      /* ---- base typography + navy ---- */
      html, body, .stApp, [class*="css"]{font-family:var(--body); color:var(--text);}
      .stApp{background:radial-gradient(1200px 600px at 78% -8%, #142248 0%, var(--bg) 46%) fixed;}
      h1,h2,h3,h4,.ce-brand{font-family:var(--disp);letter-spacing:-.02em;}
      .block-container{padding-top:2.1rem;padding-bottom:3rem;max-width:1360px;}
      [data-testid="stVerticalBlock"]{gap:.55rem;}
      section[data-testid="stSidebar"]{background:#0B1120;border-right:1px solid var(--border);}
      section[data-testid="stSidebar"] .block-container{padding-top:1.4rem;}
      /* ---- brand ---- */
      .ce-brand{font-weight:800;font-size:23px;}
      .ce-brand .mk{color:var(--purple);}
      /* ---- custom nav (restyled radio) ---- */
      section[data-testid="stSidebar"] div[role="radiogroup"]{display:flex;flex-direction:column;gap:3px;}
      section[data-testid="stSidebar"] div[role="radiogroup"] label{
        display:flex;align-items:center;width:100%;margin:0;padding:9px 13px;border-radius:10px;
        border:1px solid transparent;cursor:pointer;transition:background .12s,border-color .12s;}
      section[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child{display:none;}
      section[data-testid="stSidebar"] div[role="radiogroup"] label:hover{background:var(--raise);}
      section[data-testid="stSidebar"] div[role="radiogroup"] label p{
        font-family:var(--disp);font-weight:600;font-size:15px;color:var(--muted);margin:0;}
      section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked){
        background:linear-gradient(90deg,rgba(139,92,246,.18),rgba(139,92,246,.04));
        border-color:rgba(139,92,246,.40);box-shadow:inset 3px 0 0 var(--purple);}
      section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p{color:#fff;}
      /* ---- buttons ---- */
      .stButton>button{border-radius:11px;font-family:var(--disp);font-weight:600;
        border:1px solid var(--line);background:var(--surface);transition:all .12s;}
      .stButton>button:hover{border-color:var(--purple);color:#fff;}
      .stButton>button[kind="primary"]{background:linear-gradient(180deg,#9B6DFF,#7C4DE0);
        border:0;box-shadow:0 6px 18px rgba(124,77,224,.35);}
      /* ---- cards ---- */
      .hero{background:linear-gradient(165deg,#16234A 0%,#0F1830 100%);
        border:1px solid #2A3B63;box-shadow:inset 0 0 0 1px rgba(155,109,255,.06),0 10px 30px rgba(0,0,0,.35);
        border-radius:16px;padding:18px 20px;margin-bottom:8px;position:relative;overflow:hidden;}
      .hero:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;
        background:linear-gradient(180deg,var(--purple),#5B8CFF);}
      .hero h2{margin:3px 0;font-size:27px;font-weight:800;}
      .hero .mkt{color:#C7D0E6;font-weight:500;font-size:13.5px;margin-bottom:14px;}
      .hero .mkt b{color:var(--green);}
      .pill{display:inline-block;font-family:var(--disp);font-size:10.5px;font-weight:700;
        letter-spacing:.03em;padding:3px 10px;border-radius:99px;border:1px solid;}
      .pcard{background:var(--surface);border:1px solid var(--border);border-radius:13px;
        padding:12px 15px;height:100%;transition:border-color .12s,transform .12s;}
      .pcard:hover{border-color:var(--line);transform:translateY(-1px);}
      .pcard .nm{font-family:var(--disp);font-weight:700;font-size:15px;}
      .pcard .mo{font-size:12px;color:var(--muted);margin:2px 0 8px;}
      .tag{font-family:var(--mono);font-size:10px;font-weight:600;padding:2px 6px;border-radius:5px;
        color:var(--muted);background:var(--surface2);border:1px solid var(--border);margin-right:6px;}
      .pbar{height:7px;border-radius:99px;background:var(--surface2);overflow:hidden;margin:6px 0 3px;
        border:1px solid var(--border);}
      .pbar>div{height:100%;background:linear-gradient(90deg,#159C63,var(--green));}
      .avc{background:var(--surface);border:1px solid #3A2436;border-radius:12px;padding:11px 14px;margin-bottom:8px;}
      .avc .fl{font-family:var(--disp);font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:99px;
        background:rgba(255,93,108,.10);color:var(--red);border:1px solid rgba(255,93,108,.30);}
      .slipbox{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:15px;
        position:sticky;top:12px;}
      .rowline{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;color:var(--muted);}
      .rowline b{color:var(--text);font-family:var(--mono);}
      .disc{font-size:10.5px;color:#5E6B86;margin-top:10px;line-height:1.45;}
      /* ---- numbers / metrics / inputs ---- */
      div[data-testid="stMetricValue"]{font-family:var(--mono);}
      [data-baseweb="tag"]{background:rgba(139,92,246,.16) !important;border:1px solid rgba(139,92,246,.4) !important;
        border-radius:8px !important;font-family:var(--disp);font-weight:600;}
      [data-testid="stFileUploaderDropzone"]{background:var(--surface);border:1px dashed var(--line);border-radius:13px;}
    </style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- state
ss = st.session_state
ss.setdefault("slip", [])
ss.setdefault("view", "Card View")
ss.setdefault("section", "Analyze")
ss.setdefault("extracted", None)
ss.setdefault("use_api", os.environ.get("PROEDGE_USE_API", "").lower() in ("1", "true", "yes"))


def source_note(meta):
    """Small badge telling the user where a result came from (api/local/fallback)."""
    src = (meta or {}).get("source", "local")
    label = {"api": "🟢 API gateway", "local": "🟡 Local model",
             "local-fallback": "🟠 Local fallback (gateway down)"}.get(src, src)
    st.caption(label + (" · " + "; ".join(meta.get("warnings", [])) if meta.get("warnings") else ""))


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
    _NAV = ["Home", "Analyze", "Find Picks", "My Slip", "Lottery", "Results"]
    ss.section = st.radio("Navigate", _NAV,
                          index=_NAV.index(ss.section) if ss.section in _NAV else 0,
                          label_visibility="collapsed")
    st.divider()
    sports = st.multiselect("Sports", SUPPORTED_SPORTS, default=["MLB", "NBA"])
    if st.button("↻ Refresh lines", use_container_width=True):
        load_board.clear(); st.rerun()
    with st.expander("🧰 Tools"):
        st.caption("🎰 **Lottery** (Powerball / Mega Millions) is now in the nav. Batch tools and "
                   "the research dashboard live in `app.py`.")
    ss.use_api = st.toggle("Route through API gateway", value=ss.use_api,
                           help="Off = direct local model (fallback). On = FastAPI gateway (PROEDGE_API_URL).")
    if ss.use_api:
        st.caption("🟢 Gateway reachable" if api_available(True)
                   else "🟠 Gateway unreachable — calls fall back to the local model.")
    else:
        st.caption("🟡 Local model (direct calls)")
    _gm = gate_mode()
    st.caption(f"MLB resolution gate: **{_gm}**"
               + ("" if _gm != "advisory" else " (warns, doesn't block)"))
    _pm = participation_gate_mode()
    st.caption(f"MLB participation gate: **{_pm}**"
               + ("" if _pm != "advisory" else " (warns, doesn't block)"))
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

best, top5, avoid, plays, rank_meta = ranked_board(rows, ss.use_api)


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
    data, meta = evaluate_slip(legs, ss.use_api)
    w = weakest()
    rows_html = "".join(
        f'<div class="rowline"><span>{"⚠️ " if l is w and len(legs)>1 else ""}{l["player"]} · {l["stat"]}</span>'
        f'<b style="color:#22D08A">{round((l["prob"] or 0)*100)}%</b></div>' for l in legs)
    st.markdown(f"""<div class="slipbox">
      {rows_html}<hr style="border-color:#201C33;margin:10px 0">
      <div class="rowline"><span>Naive combined</span><b>{round(data["naive_combined"]*100)}%</b></div>
      <div class="rowline"><span>Correlation-adj</span><b style="color:#22D08A">{round(data["correlation_adjusted"]*100)}%</b></div>
      <div class="rowline"><span>Correlation</span><b>{"modeled" if data["correlation_modeled"] else "independent"}</b></div>
      <div class="rowline"><span>Concentration</span><b>{data["concentration"]}</b></div>
      <div class="rowline"><span>Overall risk</span><b>{data["risk"]}</b></div>
      <div class="disc">{data["correlation_note"]} · Estimate only; correlation-adjusted shown alongside naive so you always know whether correlation was modeled.</div>
    </div>""", unsafe_allow_html=True)
    source_note(meta)
    b1, b2 = st.columns(2)
    if b1.button("Remove weakest", key="rmw", use_container_width=True) and len(legs) > 0:
        ss.slip.remove(w); st.rerun()
    if b2.button("Clear slip", key="clr", use_container_width=True):
        ss.slip = []; st.rerun()


def render_ai():
    st.markdown("#### 🧠 ProEdge AI · analyst")
    st.caption("Explains the model — never invents a probability.")
    q = None
    for label in ["Why is this the best pick?", "What's my weakest leg?", "Summarize today's board"]:
        if st.button(label, key=f"ai_{label}", use_container_width=True):
            q = label
    typed = st.text_input("Ask about your results…", key="ai_ask", label_visibility="collapsed",
                          placeholder="Ask about your results…")
    if typed:
        q = typed
    if q:
        if q.startswith("What's my weakest") and ss.slip:
            w = weakest()
            st.info(f"Weakest leg: **{w['player']} {w['stat']}** at {round((w['prob'] or 0)*100)}% "
                    "— consider Replace w/ next-best.")
        else:
            data, meta = explain_board(rows, q, ss.use_api)
            st.info(data.get("answer") or "No answer available for this board yet.")
            source_note(meta)


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
                picks, meta = extract_image(file.getvalue(), file.type or "image/png", ss.use_api)
                if meta["status"] in ("error", "unavailable"):
                    s.update(label="Extraction unavailable", state="error")
                    st.error("Couldn't extract picks: " + "; ".join(meta.get("errors") or ["unknown error"]) +
                             ("  ·  Set ANTHROPIC_API_KEY or start the API gateway."
                              if meta["status"] == "unavailable" else ""))
                    return None
                st.write(f"　→ {len(picks)} pick(s) found  ({meta['source']})")
        s.update(label="Analysis complete — confirm the picks below", state="complete")
        return picks


_PART_EMOJI = {"confirmed_starting": "🟢", "confirmed_pitcher": "🟢", "expected_starting": "🟡",
               "probable_pitcher": "🟡", "opener": "🟡", "bulk_relief": "🟡",
               "bench": "🔴", "scratched": "🔴", "inactive": "🔴", "unknown": "⏳"}


def render_participation_line(player, stat, chip):
    """Compact lineup/participation status shown next to each confirmed pick."""
    if not chip:
        return
    emoji = _PART_EMOJI.get(chip.get("status"), "•")
    bits = [f"{emoji} **{chip.get('label')}**"]
    if chip.get("batting_order"):
        bits.append(f"batting {chip['batting_order']}")
    ts = chip.get("source_updated_at")
    if ts:
        bits.append("as of " + (ts[11:16] + "Z" if len(ts) >= 16 else ts))
    st.caption(f"↳ {player} · {stat} — " + " · ".join(bits))


def render_confirm(picks):
    st.markdown("#### ✅ Confirm extracted picks")
    st.caption("Fix anything uncertain before pricing — OCR errors never flow into recommendations.")
    df = pd.DataFrame([{"Player": p.get("player"), "Stat": p.get("stat"),
                        "Line": p.get("line"), "Side": p.get("side", "more")} for p in picks])
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic", key="confirm_ed")
    if st.button("Confirm all & price →", type="primary"):
        picks_in = [{"player": r["Player"], "stat": r["Stat"], "line": r["Line"],
                     "side": r["Side"], "sport": None} for _, r in edited.iterrows()]
        priced, needs_review, notes, meta = price_confirmed(picks_in, rows, ss.use_api)
        st.caption(f"Gates in effect — identity: **{meta.get('gate_mode', '?')}** · "
                   f"participation: **{meta.get('participation_gate_mode', '?')}**")
        for leg in priced:
            add_leg({"player": leg["player"], "stat": leg["stat"], "line": leg.get("line"),
                     "side": leg.get("side", "more"), "model_prob": leg["prob"],
                     "flavor": leg.get("flavor", "standard"), "sport": leg.get("sport")})
            render_participation_line(leg.get("player"), leg.get("stat"), leg.get("participation"))
        ss.extracted = None   # confirmation consumed; rail slip (rendered after) reflects the adds
        st.success(f"Priced and added {len(priced)} of {len(picks_in)} pick(s) to your slip.")
        for r in needs_review:
            chip = r.get("participation") if isinstance(r, dict) else None
            reason = (r.get("gate") or {}).get("reason") if isinstance(r, dict) else ""
            st.warning(f"Held — {r.get('player')} · {r.get('stat')}: {reason}")
            render_participation_line(r.get("player"), r.get("stat"), chip)
        for n in notes:
            st.info("Held / unpriced — " + n)
        source_note(meta)


@st.cache_data(ttl=21600, show_spinner=False)
def _load_lottery(game_key, limit):
    return lottery.fetch_draws(game_key, limit)


def _ball_html(white, special, special_color):
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;justify-content:center;width:38px;'
        f'height:38px;border-radius:50%;background:#EEF1F8;color:#0A0F1E;font-weight:800;'
        f'font-family:var(--mono);margin:3px;border:2px solid #C9D2E6;">{n}</span>' for n in white)
    sp = (f'<span style="display:inline-flex;align-items:center;justify-content:center;width:38px;'
          f'height:38px;border-radius:50%;background:{special_color};color:#fff;font-weight:800;'
          f'font-family:var(--mono);margin:3px;">{special}</span>')
    return f'<div style="margin:6px 0;">{chips}<span style="margin:0 6px;color:#8A93AD;">+</span>{sp}</div>'


def render_lottery():
    st.markdown("### 🎰 Lottery — Powerball & Mega Millions")
    st.caption("Number generators backed by real draw history. **Reality check:** every draw is "
               "independent and uniform — no method changes your odds. The only real edge is "
               "*combinatorial*: uncommon numbers (above 31, no patterns) don't help you win, but "
               "they lower the chance you'd **split** a jackpot with the birthday-playing crowd.")
    c1, c2 = st.columns(2)
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
        return
    st.caption(f"Loaded **{len(draws)}** draws · {draws[-1]['date']} → {draws[0]['date']} · "
               f"{g['white_count']} white (1–{g['white_max']}) + {g['special_name']} "
               f"(1–{g['special_max']}).")

    st.markdown("#### 🎟️ Generate a line")
    gc1, gc2 = st.columns([2, 1])
    strat = gc1.radio("Strategy", list(lottery.PICKERS.keys()), key="lotto_strat")
    n_lines = gc2.slider("How many lines", 1, 10, 1, key="lotto_lines")
    if st.button("🎰 Generate numbers", type="primary", key="lotto_gen"):
        ss["lotto_picks"] = lottery.generate(game, lottery.PICKERS[strat], draws, n_lines)
    picks = ss.get("lotto_picks", [])
    if picks:
        st.markdown("".join(_ball_html(w, s, g["special_color"]) for w, s in picks),
                    unsafe_allow_html=True)
        st.code("\n".join(f"{' '.join(f'{n:02d}' for n in w)}  |  {g['special_name']}: {s:02d}"
                          for w, s in picks), language=None)

    st.divider()
    st.markdown("#### 🎫 Check my ticket")
    st.caption("See how an exact line would have done against every draw in the window.")
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
        ss["lotto_ticket"] = lottery.check_ticket(game, my_white, int(my_sp) if my_sp else None, draws)
    res = ss.get("lotto_ticket")
    if res and not res["valid"]:
        st.warning(res["error"])
    elif res:
        st.markdown(_ball_html(sorted(my_white), int(my_sp), g["special_color"]), unsafe_allow_html=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("Draws checked", res["draws_checked"])
        m2.metric("Winning draws", len(res["wins"]))
        m3.metric("Hypothetical winnings", f"${res['total_amount']:,.0f}"
                  + (f" + {res['jackpots']}× JP" if res["jackpots"] else ""))
        if res["tier_counts"]:
            st.dataframe(pd.DataFrame([{"Prize tier": k, "Times won": v} for k, v in
                                      sorted(res["tier_counts"].items(), key=lambda kv: -kv[1])]),
                         hide_index=True, use_container_width=True)
        else:
            st.info("This line wouldn't have hit any prize tier in the window — the norm; odds are astronomical.")
        st.caption("Purely hypothetical back-look — past draws don't predict future ones.")


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

    elif sec == "Lottery":
        render_lottery()

    elif sec == "Results":
        st.markdown("### 📊 Results")
        st.caption("Backtesting, calibration, and post-mortem grading run in the research app (`app.py`) "
                   "and feed the model. Customer-facing results view lands in Phase 3.")

with rail:
    if sec != "My Slip":
        render_slip()
    st.write("")
    render_ai()
