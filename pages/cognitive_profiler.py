import streamlit as st
import json
import os
import io
from datetime import datetime, date
import plotly.graph_objects as go
from backend import get_supabase_client, transcribe_audio_bytes, ai_extract_signals

st.set_page_config(page_title="Cognitive Profiler", page_icon="🧠", layout="wide")

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
HAS_AI = bool(OPENAI_KEY)

supabase = get_supabase_client()

# ── Table bootstrap ────────────────────────────────────────────────────────────
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cognitive_profiles (
    id           BIGSERIAL PRIMARY KEY,
    trader_name  TEXT NOT NULL,
    session_date DATE,
    source       TEXT,
    instrument   TEXT,
    transcript   TEXT,
    signals      JSONB DEFAULT '{}',
    dimensions   JSONB DEFAULT '{}',
    notes        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
"""

def _ensure_table():
    try:
        supabase.table("cognitive_profiles").select("id").limit(1).execute()
    except Exception:
        try:
            supabase.rpc("exec_sql", {"sql": _CREATE_TABLE_SQL}).execute()
        except Exception:
            pass

_ensure_table()

# ── Constants ──────────────────────────────────────────────────────────────────
DIMENSIONS = [
    "Impulse Control",
    "Stress Tolerance",
    "Pattern Recognition",
    "Risk Calibration",
    "Process Discipline",
    "Adaptability",
]

SIGNALS = {
    "fomo_entry":          ("Chased / entered before planned level",           "negative"),
    "panic_exit":          ("Exited early under pressure / fear",              "negative"),
    "thesis_drift":        ("Changed plan or thesis mid-trade",                "negative"),
    "revenge_trade":       ("Entered immediately after a loss",                "negative"),
    "oversized":           ("Position size disproportionate to conviction",    "negative"),
    "high_stress_language":("Frustrated / emotional language during trade",    "negative"),
    "held_drawdown":       ("Held through drawdown with conviction intact",    "positive"),
    "followed_plan":       ("Executed exactly as pre-planned",                 "positive"),
    "scaled_exits":        ("Took partial profits systematically",             "positive"),
    "key_level_ref":       ("Referenced specific price / volume levels",       "positive"),
    "setup_named":         ("Named the pattern or structure before entry",     "positive"),
    "adapted_tape":        ("Adjusted approach when market conditions shifted","positive"),
}

# Dimension weights: {signal: (dimension_index, impact)}
_DIM_MAP = {
    "fomo_entry":           [(0, -25), (4, -20)],
    "panic_exit":           [(1, -25), (4, -15)],
    "thesis_drift":         [(4, -20), (5, -15)],
    "revenge_trade":        [(0, -30), (4, -25)],
    "oversized":            [(3, -30)],
    "high_stress_language": [(1, -20)],
    "held_drawdown":        [(1, +30)],
    "followed_plan":        [(4, +30), (0, +10)],
    "scaled_exits":         [(3, +25), (4, +15)],
    "key_level_ref":        [(2, +25)],
    "setup_named":          [(2, +30)],
    "adapted_tape":         [(5, +35)],
}


def score_dimensions(signals: dict) -> dict:
    scores = {d: 50 for d in DIMENSIONS}
    for sig, active in signals.items():
        if not active:
            continue
        for dim_idx, impact in _DIM_MAP.get(sig, []):
            dim = DIMENSIONS[dim_idx]
            scores[dim] = max(0, min(100, scores[dim] + impact))
    return scores


def render_radar(scores: dict, trader_name: str):
    cats = list(scores.keys())
    vals = [scores[c] for c in cats]
    vals_closed = vals + [vals[0]]
    cats_closed = cats + [cats[0]]
    fig = go.Figure(go.Scatterpolar(
        r=vals_closed,
        theta=cats_closed,
        fill="toself",
        fillcolor="rgba(0,200,150,0.15)",
        line=dict(color="#00c896", width=2),
        marker=dict(color="#00c896", size=6),
    ))
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(
                visible=True, range=[0, 100],
                tickfont=dict(color="#aaa", size=10),
                gridcolor="rgba(255,255,255,0.1)",
                linecolor="rgba(255,255,255,0.1)",
            ),
            angularaxis=dict(
                tickfont=dict(color="#ddd", size=11),
                gridcolor="rgba(255,255,255,0.08)",
            ),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title=dict(text=f"Cognitive Profile — {trader_name}", font=dict(color="#fff", size=14)),
        margin=dict(l=40, r=40, t=60, b=40),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)


def transcribe_audio(file_bytes: bytes, filename: str) -> str:
    """Transcribe audio using the shared Whisper helper from backend."""
    if not HAS_AI:
        return ""
    suffix = os.path.splitext(filename)[-1] or ".wav"
    result = transcribe_audio_bytes(file_bytes, suffix=suffix)
    if not result and HAS_AI:
        st.error("Transcription failed or returned empty — check OPENAI_API_KEY and file format.")
    return result


def save_profile(data: dict) -> bool:
    try:
        supabase.table("cognitive_profiles").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False


def load_profiles() -> list:
    try:
        res = supabase.table("cognitive_profiles").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception:
        return []


# ── UI ─────────────────────────────────────────────────────────────────────────
st.title("🧠 Trader Cognitive Profiler")
st.caption(
    "Upload live trading session recordings to extract behavioral signals and build a "
    "6-dimension cognitive profile. Early-stage data collection for the EdgeIQ profiling engine."
)

if not HAS_AI:
    st.info(
        "**AI transcription is off.** Add `OPENAI_API_KEY` to your secrets to enable automatic "
        "audio transcription (Whisper) and behavioral signal extraction (GPT-4). "
        "Until then, paste the transcript manually and tag signals by hand.",
        icon="ℹ️",
    )

tab_new, tab_library = st.tabs(["➕ New Profile", "📚 Profile Library"])

# ══════════════════════════════════════════════════════════════════════════════
with tab_new:
    st.markdown("### Step 1 — Trader Info")
    c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1.5])
    with c1:
        trader_name = st.text_input("Trader name / handle", placeholder="e.g. SmallCapKing")
    with c2:
        session_date = st.date_input("Session date", value=date.today())
    with c3:
        instrument = st.text_input("Instrument", placeholder="e.g. TSLA, SPY, NQ")
    with c4:
        source = st.selectbox("Source", ["YouTube", "Twitch", "Internal recording", "Client upload", "Other"])

    st.markdown("### Step 2 — Recording & Transcript")

    upload_col, _ = st.columns([2, 1])
    with upload_col:
        uploaded_file = st.file_uploader(
            "Upload audio or video recording",
            type=["mp3", "mp4", "wav", "m4a", "webm", "ogg", "mov"],
            help="Audio or video file of the live trading session. Max ~25 MB for Whisper API.",
        )

    transcript = ""
    auto_signals = {}

    if uploaded_file is not None:
        st.success(f"File loaded: **{uploaded_file.name}** ({uploaded_file.size / 1024:.0f} KB)")
        if HAS_AI:
            if st.button("🎙️ Transcribe & Analyse", type="primary"):
                with st.spinner("Transcribing audio with Whisper…"):
                    file_bytes = uploaded_file.read()
                    transcript = transcribe_audio(file_bytes, uploaded_file.name)
                if transcript:
                    st.session_state["cp_transcript"] = transcript
                    with st.spinner("Extracting behavioral signals with GPT-4…"):
                        auto_signals = ai_extract_signals(transcript)
                    st.session_state["cp_auto_signals"] = auto_signals
                    st.success("Transcription and analysis complete.")
        else:
            st.caption("Add OPENAI_API_KEY to enable one-click transcription.")

    transcript = st.text_area(
        "Transcript (paste manually or auto-populated above)",
        value=st.session_state.get("cp_transcript", ""),
        height=180,
        placeholder="Paste the trader's spoken commentary here, or transcribe above…",
        key="cp_transcript_input",
    )

    st.markdown("### Step 3 — Behavioral Signal Tagging")
    st.caption(
        "AI auto-tags these when a transcript is analysed. Review and adjust as needed — "
        "your manual override is always correct."
    )

    pre = st.session_state.get("cp_auto_signals", {})

    neg_signals = {k: v for k, v in SIGNALS.items() if v[1] == "negative"}
    pos_signals = {k: v for k, v in SIGNALS.items() if v[1] == "positive"}

    sig_col1, sig_col2 = st.columns(2)
    with sig_col1:
        st.markdown("**🔴 Risk signals**")
        neg_vals = {}
        for key, (label, _) in neg_signals.items():
            neg_vals[key] = st.checkbox(label, value=bool(pre.get(key, False)), key=f"cp_{key}")
    with sig_col2:
        st.markdown("**🟢 Strength signals**")
        pos_vals = {}
        for key, (label, _) in pos_signals.items():
            pos_vals[key] = st.checkbox(label, value=bool(pre.get(key, False)), key=f"cp_{key}")

    all_signals = {**neg_vals, **pos_vals}
    dimension_scores = score_dimensions(all_signals)

    st.markdown("### Step 4 — Cognitive Profile Preview")
    if trader_name.strip():
        render_radar(dimension_scores, trader_name)
    else:
        st.caption("Enter a trader name above to preview the radar chart.")

    # Score summary
    score_cols = st.columns(6)
    for i, (dim, score) in enumerate(dimension_scores.items()):
        color = "#00c896" if score >= 65 else ("#f4c542" if score >= 40 else "#e05c5c")
        score_cols[i].markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:11px;color:#aaa;margin-bottom:2px'>{dim}</div>"
            f"<div style='font-size:26px;font-weight:700;color:{color}'>{score}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("### Step 5 — Notes & Save")
    analyst_notes = st.text_area(
        "Analyst observations",
        height=80,
        placeholder="Any context about this session — market conditions, trader experience level, notable moments…",
    )

    save_ready = bool(trader_name.strip())
    if not save_ready:
        st.caption("Enter a trader name to enable save.")

    if st.button("💾 Save Profile", type="primary", disabled=not save_ready):
        payload = {
            "trader_name":  trader_name.strip(),
            "session_date": str(session_date),
            "source":       source,
            "instrument":   instrument.strip(),
            "transcript":   transcript.strip(),
            "signals":      json.dumps(all_signals),
            "dimensions":   json.dumps(dimension_scores),
            "notes":        analyst_notes.strip(),
        }
        if save_profile(payload):
            st.success(f"Profile saved for **{trader_name}**.")
            for k in ["cp_transcript", "cp_auto_signals"]:
                st.session_state.pop(k, None)
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
with tab_library:
    st.markdown("### Saved Profiles")
    profiles = load_profiles()

    if not profiles:
        st.info("No profiles saved yet. Create one in the New Profile tab.")
    else:
        st.caption(f"{len(profiles)} profile{'s' if len(profiles) != 1 else ''} on record.")

        for p in profiles:
            dims = json.loads(p.get("dimensions") or "{}")
            sigs = json.loads(p.get("signals") or "{}")
            avg_score = round(sum(dims.values()) / len(dims)) if dims else 0
            color = "#00c896" if avg_score >= 65 else ("#f4c542" if avg_score >= 40 else "#e05c5c")

            with st.expander(
                f"**{p['trader_name']}** · {p.get('session_date','?')} · "
                f"{p.get('instrument','?')} · Avg score: **{avg_score}**"
            ):
                lcol, rcol = st.columns([1, 1.4])
                with lcol:
                    st.markdown(f"**Source:** {p.get('source','—')}")
                    st.markdown(f"**Instrument:** {p.get('instrument','—')}")
                    st.markdown(f"**Date:** {p.get('session_date','—')}")
                    if p.get("notes"):
                        st.markdown(f"**Notes:** {p['notes']}")

                    st.markdown("**Behavioral signals detected:**")
                    active_pos = [SIGNALS[k][0] for k, v in sigs.items() if v and SIGNALS.get(k, ('',''))[1] == 'positive']
                    active_neg = [SIGNALS[k][0] for k, v in sigs.items() if v and SIGNALS.get(k, ('',''))[1] == 'negative']
                    if active_pos:
                        for s in active_pos:
                            st.markdown(f"🟢 {s}")
                    if active_neg:
                        for s in active_neg:
                            st.markdown(f"🔴 {s}")
                    if not active_pos and not active_neg:
                        st.caption("No signals tagged.")

                with rcol:
                    if dims:
                        render_radar(dims, p["trader_name"])

                    if p.get("transcript"):
                        with st.expander("View transcript"):
                            st.text(p["transcript"][:3000] + ("…" if len(p["transcript"]) > 3000 else ""))

        st.divider()
        st.markdown("#### Aggregate Dimension Averages")
        if len(profiles) >= 2:
            all_dims = [json.loads(p.get("dimensions") or "{}") for p in profiles if p.get("dimensions")]
            if all_dims:
                agg = {d: round(sum(p.get(d, 50) for p in all_dims) / len(all_dims)) for d in DIMENSIONS}
                render_radar(agg, f"All Traders (n={len(all_dims)})")
        else:
            st.caption("Add at least 2 profiles to see aggregate view.")
