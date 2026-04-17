import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta, time as dtime
import time
import pytz
import os
import html as _html
import threading
import subprocess
import sys

from backend import *
from backend import (
    _compute_value_area, _strip_emoji,
    _parse_batch_pairs, _RECALIBRATE_EVERY, _BRAIN_WEIGHT_KEYS,
    _JOURNAL_COLS, _find_peaks, _is_strong_hvn, _detect_double_distribution,
    _label_to_weight_key, _save_brain_weights, _stream_worker, _GRADE_COLORS, _GRADE_SCORE,
    _compress_image_b64,
    _edge_band, _rvol_band,
    WK_DISPLAY,
    save_signal_conditions, log_signal_outcome, get_predictive_context,
    compute_win_rates, monte_carlo_equity_curves,
    compute_order_flow_signals,
    load_watchlist,
    get_next_trading_day,
    detect_chart_patterns,
    parse_webull_csv,
    compute_journal_model_crossref,
    compute_pretrade_quality,
    scan_ticker_patterns,
    enrich_trade_context,
    enrich_eod_from_journal,
    compute_setup_brief,
    scan_journal_patterns,
    classify_macro_regime,
    save_breadth_regime,
    get_breadth_regime,
    get_breadth_regime_history,
    ensure_macro_breadth_log_table,
    _MACRO_BREADTH_SQL,
    ensure_paper_trades_regime_column,
    _PAPER_TRADES_REGIME_MIGRATION,
    ensure_kalshi_tables,
    _KALSHI_PREDICTIONS_SQL,
    get_kalshi_performance_summary,
    compute_portfolio_metrics,
    run_pending_migrations,
    _ALL_PENDING_MIGRATIONS,
    _startup_errors,
    _SECRET_CATALOG,
    _secret_statuses,
    recheck_secret_statuses,
    _alpaca_mismatch_status,
    load_tcs_alert_structures,
    save_tcs_alert_structures,
    get_runtime_last_healthy_ts,
)

# ── Cached DB-loader wrappers (ttl=300 s ≈ 5 min) ────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_journal(user_id: str = ""):
    return load_journal(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_accuracy_tracker(user_id: str = ""):
    return load_accuracy_tracker(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_paper_trades(user_id: str = "", days: int = 365):
    return load_paper_trades(user_id=user_id, days=days)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_user_prefs(user_id: str = ""):
    return load_user_prefs(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_watchlist(user_id: str = ""):
    return load_watchlist(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_watchlist_predictions(user_id: str = "", pred_date=None):
    return load_watchlist_predictions(user_id=user_id, pred_date=pred_date)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_eod_notes(user_id: str = "", limit: int = 60):
    return load_eod_notes(user_id=user_id, limit=limit)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_tcs_alert_structures():
    return load_tcs_alert_structures()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_tcs_threshold_history(days: int = 14):
    return load_tcs_threshold_history(days=days)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_tcs_thresholds(default: int = 50):
    return load_tcs_thresholds(default=default)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_high_conviction_log():
    return load_high_conviction_log()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_brain_weights(user_id: str = ""):
    return load_brain_weights(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_sa_journal():
    return load_sa_journal()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_ranking_accuracy(user_id: str = ""):
    return load_ranking_accuracy(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_ticker_rankings(user_id: str = "", rating_date=None):
    return load_ticker_rankings(user_id=user_id, rating_date=rating_date)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_cognitive_delta_today(user_id: str = "", trade_date=None):
    return load_cognitive_delta_today(user_id=user_id, trade_date=trade_date)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_cognitive_delta_analysis(user_id: str = ""):
    return load_cognitive_delta_analysis(user_id=user_id)

# ── Auto-regenerate build notes HTML on startup ───────────────────────────────
def _regenerate_notes_html():
    import json as _json
    _notes = os.path.join(os.path.dirname(__file__), ".local", "build_notes.md")
    _out   = os.path.join(os.path.dirname(__file__), "static", "notes.html")
    _key   = "a5e1fcab-8369-42c4-8550-a8a19734510c"
    try:
        if not os.path.exists(_notes):
            return
        _md_mtime = os.path.getmtime(_notes)
        _html_mtime = os.path.getmtime(_out) if os.path.exists(_out) else 0
        if _html_mtime >= _md_mtime:
            return
        with open(_notes) as _f:
            _content = _f.read()
        _html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>EdgeIQ Build Notes</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#0e0e1a;color:#d0d0e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;line-height:1.75;padding-bottom:80px}}
header{{background:#12122a;border-bottom:1px solid #2a2a4a;padding:18px 20px;position:sticky;top:0;z-index:10}}
header h1{{font-size:19px;font-weight:800;color:#7986cb;margin:0}}header p{{font-size:11px;color:#555;margin:3px 0 0 0}}
#content{{max-width:840px;margin:0 auto;padding:28px 20px}}
h1,h2{{color:#7986cb;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #2a2a4a}}
h3{{color:#9fa8da;margin:22px 0 8px}}h4{{color:#b0bec5;margin:14px 0 5px}}p{{margin:0 0 11px}}
ul,ol{{margin:0 0 11px 20px}}li{{margin:3px 0}}strong{{color:#e0e0ff}}em{{color:#b0bec5}}
code{{background:#1a1a30;border:1px solid #2a2a4a;border-radius:4px;padding:1px 5px;font-family:'SF Mono','Fira Code',monospace;font-size:13px;color:#80cbc4}}
pre{{background:#1a1a30;border:1px solid #2a2a4a;border-radius:6px;padding:14px;overflow-x:auto;margin:0 0 14px}}
pre code{{background:none;border:none;padding:0;color:#80cbc4}}
table{{width:100%;border-collapse:collapse;margin:0 0 14px;font-size:14px}}
th{{background:#1a1a30;color:#7986cb;padding:7px 11px;text-align:left;border:1px solid #2a2a4a}}
td{{padding:6px 11px;border:1px solid #2a2a4a;vertical-align:top}}
tr:nth-child(even) td{{background:#12122a}}
hr{{border:none;border-top:1px solid #2a2a4a;margin:26px 0}}
blockquote{{border-left:3px solid #7986cb;padding:7px 14px;margin:0 0 14px;background:#12122a;color:#9fa8da}}
a{{color:#7986cb}}
#gate{{position:fixed;inset:0;background:#0e0e1a;z-index:99;display:flex;align-items:center;justify-content:center}}
#gate p{{color:#555;font-size:14px}}
</style></head>
<body>
<div id="gate"><p>Access denied.</p></div>
<header style="display:none" id="hdr"><h1>&#x1F4CB; EdgeIQ Build Notes</h1><p>Live document &mdash; always current</p></header>
<div id="content" style="display:none"></div>
<script>
const KEY={_json.dumps(_key)};const raw={_json.dumps(_content)};
const p=new URLSearchParams(window.location.search);
if(p.get('key')===KEY){{document.getElementById('gate').style.display='none';document.getElementById('hdr').style.display='block';document.getElementById('content').style.display='block';document.getElementById('content').innerHTML=marked.parse(raw);}}
</script></body></html>"""
        os.makedirs(os.path.dirname(_out), exist_ok=True)
        with open(_out, "w") as _f:
            _f.write(_html)
    except Exception:
        pass

_regenerate_notes_html()

# ── Close-price backfill pipeline (background thread) ─────────────────────────
_BACKFILL_LOG    = "/tmp/backfill_pipeline.log"
_BACKFILL_STATUS = "/tmp/backfill_pipeline.status"

# Module-level lock ensures only one pipeline thread runs at a time.
# The lock is held for the entire duration of the run and released in the
# finally block, so even a UI "clear" during a run cannot start a second run.
_BACKFILL_LOCK: threading.Lock = threading.Lock()


def _backfill_pipeline_thread():
    """Run backfill_close_prices.py then run_sim_backfill.py sequentially.
    Progress is written to _BACKFILL_LOG; final status to _BACKFILL_STATUS.
    The module-level _BACKFILL_LOCK is held for the full duration and released
    in the finally block, preventing concurrent runs."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(_BACKFILL_LOG, "w", buffering=1) as lf:
            lf.write("=== Step 1 of 2: Fetching EOD close prices from Alpaca ===\n\n")
            lf.flush()
            r1 = subprocess.run(
                [sys.executable, os.path.join(script_dir, "backfill_close_prices.py")],
                stdout=lf, stderr=subprocess.STDOUT,
                cwd=script_dir, timeout=7200,
            )
            lf.write(f"\n[Step 1 complete — exit code {r1.returncode}]\n\n")
            if r1.returncode != 0:
                lf.write("[Pipeline halted: step 1 exited with an error. See output above.]\n")
                with open(_BACKFILL_STATUS, "w") as sf:
                    sf.write("error")
                return
            lf.write("=== Step 2 of 2: Recomputing eod_pnl_r ===\n\n")
            lf.flush()
            r2 = subprocess.run(
                [sys.executable, os.path.join(script_dir, "run_sim_backfill.py")],
                stdout=lf, stderr=subprocess.STDOUT,
                cwd=script_dir, timeout=7200,
            )
            lf.write(f"\n[Step 2 complete — exit code {r2.returncode}]\n\n")
            if r2.returncode != 0:
                lf.write("[Pipeline halted: step 2 exited with an error. See output above.]\n")
                with open(_BACKFILL_STATUS, "w") as sf:
                    sf.write("error")
                return
            lf.write("=== Backfill pipeline finished successfully ===\n")
        with open(_BACKFILL_STATUS, "w") as sf:
            sf.write("done")
    except Exception as _exc:
        try:
            with open(_BACKFILL_LOG, "a") as lf:
                lf.write(f"\n[FATAL ERROR: {_exc}]\n")
        except Exception:
            pass
        with open(_BACKFILL_STATUS, "w") as sf:
            sf.write("error")
    finally:
        _BACKFILL_LOCK.release()


st.set_page_config(page_title="Volume Profile Dashboard", page_icon="📊", layout="wide")

# ── Startup / configuration error banner ──────────────────────────────────────
# _startup_errors is populated at import time in backend.py and surfaced here
# so operators see misconfigured secrets immediately instead of silent failures.
def _render_setup_checklist() -> None:
    """Render the 🔐 Setup Checklist content (sidebar expander body)."""
    import streamlit as _st
    _checklist_has_errors = bool(_startup_errors)
    with _st.sidebar.expander("🔐 Setup Checklist", expanded=_checklist_has_errors):
        if not _checklist_has_errors:
            _st.success("All required secrets are configured.", icon="✅")
        else:
            _n_missing   = sum(1 for s in _secret_statuses.values() if s == "missing")
            _n_malformed = sum(1 for s in _secret_statuses.values() if s == "malformed")
            _summary_parts: list[str] = []
            if _n_missing:
                _summary_parts.append(f"{_n_missing} missing")
            if _n_malformed:
                _summary_parts.append(f"{_n_malformed} malformed")
            _st.error(f"{', '.join(_summary_parts).capitalize()} — see details below.", icon="⚠️")

        _col_caption, _col_btn = _st.columns([3, 1])
        with _col_caption:
            _st.caption(
                "Set secrets in Replit → **Secrets** (lock icon), then click Re-check. "
                "Re-check refreshes this checklist immediately; a full app restart is still "
                "needed for backend services to reconnect with new credentials."
            )
        with _col_btn:
            if _st.button("🔄 Re-check", key="_recheck_secrets_btn", use_container_width=True):
                recheck_secret_statuses()
                _st.rerun()
        _st.markdown("---")

        if "_runtime_recheck_requested" not in _st.session_state:
            _st.session_state["_runtime_recheck_requested"] = False
        if _st.session_state.get("_runtime_recheck_requested"):
            _st.toast("Credential re-check requested — results will appear momentarily.", icon="🔑")
            _st.session_state["_runtime_recheck_requested"] = False

        _col_rt_caption, _col_rt_btn = _st.columns([3, 1])
        with _col_rt_caption:
            _st.caption(
                "**Force a live credential validation** against Alpaca & Supabase right now, "
                "without waiting for the 5-minute background cycle."
            )
        with _col_rt_btn:
            if _st.button(
                "🔑 Re-check now",
                key="_recheck_runtime_creds_btn",
                use_container_width=True,
                help="Immediately re-validate Alpaca and Supabase credentials",
            ):
                check_credentials_runtime(force=True)
                _st.session_state["_runtime_recheck_requested"] = True
                _st.rerun()
        _st.markdown("---")

        for _sc_item in _SECRET_CATALOG:
            _sc_name   = _sc_item["name"]
            _sc_status = _secret_statuses.get(_sc_name, "missing")
            if _sc_status == "set":
                _st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
                    f'<span style="font-size:16px;">✅</span>'
                    f'<span style="font-weight:600;font-size:13px;">{_sc_item["label"]}</span>'
                    f'<code style="font-size:11px;color:#888;">{_sc_name}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif _sc_status == "malformed":
                _st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">'
                    f'<span style="font-size:16px;">⚠️</span>'
                    f'<span style="font-weight:600;font-size:13px;color:#ffb74d;">{_sc_item["label"]}</span>'
                    f'<code style="font-size:11px;color:#888;">{_sc_name}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _st.caption(f"Value is malformed. {_sc_item['description']}")
                _st.markdown(
                    f'[📎 {_sc_item["obtain_label"]}]({_sc_item["obtain_url"]})',
                )
                _st.markdown("")
            else:
                _st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">'
                    f'<span style="font-size:16px;">❌</span>'
                    f'<span style="font-weight:600;font-size:13px;color:#ef5350;">{_sc_item["label"]}</span>'
                    f'<code style="font-size:11px;color:#888;">{_sc_name}</code>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _st.caption(_sc_item["description"])
                _st.markdown(
                    f'[📎 {_sc_item["obtain_label"]}]({_sc_item["obtain_url"]})',
                )
                _st.markdown("")


if _startup_errors:
    _REQUIRED_SECRETS = {"SUPABASE_URL", "SUPABASE_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"}
    _missing_names = {_n for _n, _ in _startup_errors if _secret_statuses.get(_n) == "missing"}
    _all_missing   = _REQUIRED_SECRETS == _missing_names

    if _all_missing:
        # ── Welcome / onboarding screen for brand-new (unconfigured) instances ──
        st.markdown(
            """
            <div style="
                max-width:680px;
                margin:3rem auto;
                padding:2.5rem 2rem;
                border:1px solid #334155;
                border-radius:12px;
                background:#0f172a;
                color:#e2e8f0;
                font-family:sans-serif;
            ">
            <h1 style="margin-top:0;font-size:1.8rem;color:#f8fafc;">
                👋 Welcome to EdgeIQ
            </h1>
            <p style="color:#94a3b8;font-size:1rem;margin-bottom:1.5rem;">
                This looks like a fresh instance — no secrets have been configured yet.
                Follow the steps below to get up and running in a few minutes.
            </p>

            <hr style="border-color:#1e293b;margin-bottom:1.5rem;" />

            <h3 style="color:#f8fafc;font-size:1.05rem;margin-bottom:0.3rem;">
                Step 1 — Fork this project
            </h3>
            <p style="color:#94a3b8;font-size:0.92rem;margin-bottom:1.4rem;">
                In Replit, click <strong style="color:#e2e8f0;">Fork</strong> to create your own
                private copy. All secrets are stored per-fork, so never share your fork URL
                with anyone who shouldn't have access.
            </p>

            <h3 style="color:#f8fafc;font-size:1.05rem;margin-bottom:0.3rem;">
                Step 2 — Add your secrets
            </h3>
            <p style="color:#94a3b8;font-size:0.92rem;margin-bottom:0.6rem;">
                Open <strong style="color:#e2e8f0;">Secrets</strong> (🔒 lock icon in the
                left sidebar) and add each of the four keys below.
            </p>
            <table style="
                width:100%;
                border-collapse:collapse;
                font-size:0.88rem;
                margin-bottom:1.4rem;
            ">
              <thead>
                <tr style="border-bottom:1px solid #1e293b;">
                  <th style="text-align:left;padding:6px 8px;color:#64748b;">Secret name</th>
                  <th style="text-align:left;padding:6px 8px;color:#64748b;">Where to find it</th>
                </tr>
              </thead>
              <tbody>
                <tr style="border-bottom:1px solid #1e293b;">
                  <td style="padding:7px 8px;"><code style="color:#7dd3fc;">SUPABASE_URL</code></td>
                  <td style="padding:7px 8px;">
                    <a href="https://supabase.com/dashboard/project/_/settings/api"
                       target="_blank" style="color:#38bdf8;">
                      Supabase → Settings → API → Project URL
                    </a>
                  </td>
                </tr>
                <tr style="border-bottom:1px solid #1e293b;">
                  <td style="padding:7px 8px;"><code style="color:#7dd3fc;">SUPABASE_KEY</code></td>
                  <td style="padding:7px 8px;">
                    <a href="https://supabase.com/dashboard/project/_/settings/api"
                       target="_blank" style="color:#38bdf8;">
                      Supabase → Settings → API → anon / public key
                    </a>
                  </td>
                </tr>
                <tr style="border-bottom:1px solid #1e293b;">
                  <td style="padding:7px 8px;"><code style="color:#7dd3fc;">ALPACA_API_KEY</code></td>
                  <td style="padding:7px 8px;">
                    <a href="https://app.alpaca.markets/paper/dashboard/overview"
                       target="_blank" style="color:#38bdf8;">
                      Alpaca → Paper Dashboard → API Keys → Generate
                    </a>
                  </td>
                </tr>
                <tr>
                  <td style="padding:7px 8px;"><code style="color:#7dd3fc;">ALPACA_SECRET_KEY</code></td>
                  <td style="padding:7px 8px;">
                    <a href="https://app.alpaca.markets/paper/dashboard/overview"
                       target="_blank" style="color:#38bdf8;">
                      Alpaca → Paper Dashboard → API Keys → Generate
                    </a>
                  </td>
                </tr>
              </tbody>
            </table>

            <h3 style="color:#f8fafc;font-size:1.05rem;margin-bottom:0.3rem;">
                Step 3 — Restart the app
            </h3>
            <p style="color:#94a3b8;font-size:0.92rem;margin-bottom:0;">
                After saving all four secrets, click the <strong style="color:#e2e8f0;">Stop ▶ Run</strong>
                button (or use the <strong style="color:#e2e8f0;">Restart</strong> option) to reboot
                the app. This screen will be replaced by the full dashboard once the secrets are
                detected.
            </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    else:
        # ── Partial-configuration error banner (existing behaviour) ───────────
        _err_lines = "\n".join(f"• {_msg}" for _, _msg in _startup_errors)
        st.error(
            f"**⚠️ Configuration problem detected — {len(_startup_errors)} secret(s) need attention:**\n\n"
            f"{_err_lines}\n\n"
            "Fix these in your environment **Secrets**, then use the **🔄 Re-check** button "
            "in the **🔐 Setup Checklist** sidebar panel to refresh without a full restart."
        )
        if any(_name in ("SUPABASE_URL", "SUPABASE_KEY") for _name, _ in _startup_errors):
            st.warning(
                "Database credentials are missing. Most features will show empty data until "
                "SUPABASE_URL and SUPABASE_KEY are correctly set."
            )
            # Render the checklist in the sidebar BEFORE stopping so operators can see
            # which secrets are missing and where to get them even in the halted state.
            _render_setup_checklist()
            st.stop()

# ── Runtime credential failure banner ─────────────────────────────────────────
# check_credentials_runtime() re-validates Alpaca and Supabase credentials in a
# background thread (at most every 5 minutes) and returns any mid-session
# failures.  Unlike _startup_errors above (never configured), these banners mean
# the credentials were valid at launch but have since been revoked or expired.
_runtime_errors = check_credentials_runtime()
if _runtime_errors:
    _rt_lines = "\n".join(f"• {_msg}" for _, _msg in _runtime_errors)
    st.error(
        f"**🔑 Credential failure detected mid-session — "
        f"{len(_runtime_errors)} secret(s) stopped working after startup:**\n\n"
        f"{_rt_lines}\n\n"
        "These credentials were valid when the app launched but are no longer "
        "accepted. Update the secrets and restart the app."
    )
    if any(_name in ("SUPABASE_KEY",) for _name, _ in _runtime_errors):
        st.warning(
            "Supabase credentials are no longer valid. Database queries may fail "
            "until SUPABASE_URL and SUPABASE_KEY are updated and the app is restarted."
        )

# ── Alpaca credential mismatch banner ─────────────────────────────────────────
# _alpaca_mismatch_status is mutated in-place by the background account-type
# check thread in backend.py.  Streamlit re-runs this script on every
# interaction, so the banner appears as soon as the thread has finished (usually
# within a few seconds of first page load).
#
# Auto-clear the dismiss flag once the mismatch resolves so the banner will
# reappear if a new mismatch is introduced later in the same session.
if not _alpaca_mismatch_status["mismatch"]:
    st.session_state.pop("alpaca_mismatch_dismissed", None)
elif not st.session_state.get("alpaca_mismatch_dismissed", False):
    _col_msg, _col_btn = st.columns([10, 1])
    with _col_msg:
        st.markdown(
            '<div style="background:#1c1000; border:1px solid #b45309; border-radius:6px; '
            'padding:12px 16px; margin-bottom:12px; line-height:1.6;">'
            '<span style="font-size:14px; font-weight:700; color:#fbbf24;">⚠️ Alpaca credential mismatch detected</span><br>'
            f'<span style="font-size:13px; color:#fde68a;">{_alpaca_mismatch_status["message"]}</span><br>'
            '<span style="font-size:13px; color:#fcd34d;">Use the '
            '<a href="#trading-mode" style="color:#fbbf24; font-weight:700; '
            'text-decoration:underline;">🔀 Trading Mode</a>'
            ' toggle in the sidebar to switch modes without restarting.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with _col_btn:
        if st.button("Dismiss", key="dismiss_alpaca_mismatch"):
            st.session_state["alpaca_mismatch_dismissed"] = True
            st.rerun()

# ── Session state ──────────────────────────────────────────────────────────────
_DEFAULTS = {
    "live_active": False,
    "live_bars": [],
    "live_current_bar": None,
    "live_trades": deque(maxlen=3000),
    "live_thread": None,
    "live_stop_event": None,
    "live_queue": None,
    "live_ticker": "",
    "live_error": None,
    # Alert state
    "tcs_fired_high": False,   # True once ≥ 80% crossed this session
    "tcs_was_high": False,     # True while TCS was ≥ 60% (for chop-drop detection)
    "sound_trigger": 0,        # Incremented to force fresh audio iframe
    # RVOL / Sector cache — pre-fetched once per analysis session
    "rvol_avg_vol": None,          # Average daily volume (5-day lookback)
    "sector_pct_chg": 0.0,         # Sector ETF % change for current date
    "rvol_intraday_curve": None,   # Per-minute cumulative vol curve (390 elements)
    # Ticker widget state (explicit key so scanner can override)
    "ticker_input": "AAPL",
    # Auto-run flag: scanner sets this to True to trigger historical analysis on next rerun
    "auto_run": False,
    # Gap Scanner results cache
    "scanner_results": [],         # [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    "scanner_last_run": None,      # datetime of last successful scan
    # Last analysis snapshot — used by Live Pulse header + Log Entry
    "last_analysis_state": None,
    # Auth
    "auth_user":    None,   # supabase User object when logged in
    "auth_user_id": "",     # user UUID string
    "auth_email":   "",     # user email string
    # Trade journal active tab state
    "active_tab": 0,
    # Position tracking
    "position_in":          False,
    "position_avg_entry":   0.0,
    "position_peak_price":  0.0,
    "position_ticker":      "",
    "position_shares":      0,
    "position_structure":   "",
    # MarketBrain — stores the live predicted structure between reruns
    "brain_session_correct":  0,    # correct predictions this session
    "brain_session_total":    0,    # total comparisons this session
    "brain_last_compared":    "",   # "TICKER_YYYY-MM-DD" — dedup key
    "brain_predicted":        None,
    "brain_ib_high":        0.0,
    "brain_ib_low":         float("inf"),
    "brain_ib_set":         False,
    "brain_high_touched":   False,
    "brain_low_touched":    False,
    # Replay mode
    "replay_bars":          None,   # full-day DataFrame (all bars)
    "replay_bar_idx":       0,      # index of current visible bar (0-based)
    "replay_playing":       False,  # auto-advance in progress
    "replay_speed":         1,      # bars advanced per step
    "replay_ticker":        "",
    "replay_date":          None,
    "replay_avg_vol":       None,
    "replay_intraday_curve": None,
    "replay_sector_bonus":  0.0,
    # Small Account Challenge tab
    "sa_account_bal":       5000.0,
    "sa_risk_pct":          2.0,
    "sa_pdt_used":          0,
    "sa_news_confirmed":    False,
    "sa_float_est":         0.0,
    "last_bars":            None,   # Real bars from last analysis run
    "sa_bin_centers":       None,   # VP bin centers for SA tab
    # Macro breadth regime (Stockbee inputs)
    "breadth_4pct_count":   0,
    "breadth_13_34_ratio":  0.0,
    "breadth_q_up":         0,
    "breadth_q_down":       0,
    "breadth_regime":       None,   # cached classify_macro_regime() result
    "sa_vap":               None,   # VP volumes for SA tab
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Restore today's brain accuracy counters from Supabase on first load ────────
if st.session_state.brain_session_total == 0:
    try:
        _restore_df = _cached_load_accuracy_tracker()
        if "timestamp" in _restore_df.columns and not _restore_df.empty:
            _today_str  = datetime.now(EASTERN).strftime("%Y-%m-%d")
            _today_rows = _restore_df[
                _restore_df["timestamp"].astype(str).str.startswith(_today_str)
            ]
            if not _today_rows.empty and "correct" in _today_rows.columns:
                st.session_state.brain_session_total   = int(len(_today_rows))
                st.session_state.brain_session_correct = int(
                    (_today_rows["correct"] == "✅").sum())
                if "compare_key" in _today_rows.columns:
                    _non_empty = _today_rows["compare_key"].dropna()
                    _non_empty = _non_empty[_non_empty.astype(str).str.strip() != ""]
                    if not _non_empty.empty:
                        st.session_state.brain_last_compared = str(_non_empty.iloc[-1])
    except Exception:
        pass  # safe fallback — counters stay at 0

# ── Load macro breadth regime from Supabase on first load ─────────────────────
if st.session_state.breadth_regime is None:
    try:
        _uid_startup = st.session_state.get("auth_user_id", "")
        st.session_state.breadth_regime = get_breadth_regime(user_id=_uid_startup)
    except Exception:
        pass

# ── Audio JS (Web Audio API, synthesised tones — no external files) ────────────
_CHIME_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[523.25,0],[659.25,0.18],[783.99,0.36],[1046.50,0.54]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='sine'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.26,t+0.04);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.42);
      o.start(t); o.stop(t+0.43);
    });
  }catch(e){}
})();"""

_LOW_TONE_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[240,0],[200,0.26],[155,0.52]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.32,t+0.05);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.40);
      o.start(t); o.stop(t+0.41);
    });
  }catch(e){}
})();"""

_TARGET_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[1174.66,0],[1318.51,0.14],[1568.0,0.28],[1318.51,0.42]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.30,t+0.03);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.55);
      o.start(t); o.stop(t+0.56);
    });
  }catch(e){}
})();"""

EASTERN = pytz.timezone("America/New_York")

# ══════════════════════════════════════════════════════════════════════════════
# CORE MATH
# ══════════════════════════════════════════════════════════════════════════════

def check_tcs_alerts(tcs: float, audio_enabled: bool):
    """Fire visual toast + audio when TCS crosses key thresholds."""
    import streamlit.components.v1 as components

    # ── HIGH CONVICTION: TCS ≥ 80%, fires only once per session ───────────────
    if tcs >= 80 and not st.session_state.tcs_fired_high:
        st.session_state.tcs_fired_high = True
        st.session_state.tcs_was_high = True
        st.toast("🚀 HIGH CONVICTION TREND DETECTED", icon="🚀")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* hc:{n} */{_CHIME_JS}</script>',
                height=0, scrolling=False
            )

    # Track whether TCS has been "high" (≥ 60%) so we can detect a drop
    elif tcs >= 60:
        st.session_state.tcs_was_high = True

    # ── CHOP RISK: TCS drops below 30 after being high ────────────────────────
    if tcs < 30 and st.session_state.tcs_was_high:
        st.session_state.tcs_was_high = False
        st.toast("⚠️ CHOP RISK INCREASED", icon="⚠️")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* cr:{n} */{_LOW_TONE_JS}</script>',
                height=0, scrolling=False
            )


# ══════════════════════════════════════════════════════════════════════════════
# TARGET ZONES
# ══════════════════════════════════════════════════════════════════════════════

def check_target_alerts(price, targets, audio_enabled):
    """Fire a unique 'Target Reached' sound when price touches a target zone (0.5% tol)."""
    import streamlit.components.v1 as components
    if not audio_enabled or not targets or price is None:
        return
    tol = price * 0.005
    for tz in targets:
        key = f"target_fired_{tz['type']}_{tz['price']:.2f}"
        if abs(price - tz["price"]) <= tol and not st.session_state.get(key, False):
            st.session_state[key] = True
            st.toast(f"🎯 Target Reached — {tz['label']} at ${tz['price']:.2f}", icon="🎯")
            n = st.session_state.get("sound_trigger", 0) + 1
            st.session_state["sound_trigger"] = n
            components.html(
                f'<script>/* tr:{n} */{_TARGET_JS}</script>',
                height=0, scrolling=False
            )


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

def render_login_page():
    """Full-page login / sign-up screen shown when no user is authenticated."""
    st.markdown("""
    <style>
    .auth-card {
        max-width: 440px; margin: 60px auto 0 auto;
        background: #12122288; border: 1px solid #2a2a4a;
        border-radius: 16px; padding: 40px 36px;
    }
    .auth-logo {
        text-align: center; font-size: 48px; margin-bottom: 4px;
    }
    .auth-title {
        text-align: center; font-size: 22px; font-weight: 800;
        color: #e0e0e0; letter-spacing: -0.5px; margin-bottom: 4px;
    }
    .auth-sub {
        text-align: center; font-size: 12px; color: #5c6bc0;
        text-transform: uppercase; letter-spacing: 1px; margin-bottom: 28px;
    }
    </style>
    <div class="auth-card">
      <div class="auth-logo">📊</div>
      <div class="auth-title">Volume Profile Terminal</div>
      <div class="auth-sub">Professional Trading Analytics</div>
    </div>
    """, unsafe_allow_html=True)

    # Center the form using columns
    _, col, _ = st.columns([1, 2, 1])
    with col:
        _auth_tab = st.radio(
            "Action", ["🔐 Log In", "📝 Sign Up"],
            horizontal=True, key="auth_tab_select", label_visibility="collapsed",
        )
        st.markdown("")

        _email = st.text_input(
            "Email", placeholder="you@example.com",
            key="auth_email_input", label_visibility="collapsed",
        )
        _password = st.text_input(
            "Password", type="password", placeholder="Password",
            key="auth_password_input", label_visibility="collapsed",
        )

        if _auth_tab == "🔐 Log In":
            if st.button("Log In", use_container_width=True, type="primary",
                         key="auth_login_btn"):
                if not _email or not _password:
                    st.error("Please enter your email and password.")
                else:
                    with st.spinner("Logging in…"):
                        _res = auth_login(_email.strip(), _password)
                    if _res["error"]:
                        st.error(_res["error"])
                    else:
                        _u  = _res["user"]
                        _s  = _res.get("session")
                        st.session_state["auth_user"]    = _u
                        st.session_state["auth_user_id"] = str(_u.id) if _u else ""
                        st.session_state["auth_email"]   = str(_u.email) if _u else _email
                        _at = getattr(_s, "access_token",  None) or ""
                        _rt = getattr(_s, "refresh_token", None) or ""
                        st.session_state["auth_access_token"]  = _at
                        st.session_state["auth_refresh_token"] = _rt
                        if _u and _rt:
                            save_session_cache(str(_u.id), str(_u.email), _rt)
                        set_user_session(_at, _rt)
                        st.success("Logged in! Loading dashboard…")
                        st.rerun()
        else:
            st.caption("Password must be at least 6 characters.")
            if st.button("Create Account", use_container_width=True, type="primary",
                         key="auth_signup_btn"):
                if not _email or not _password:
                    st.error("Please enter your email and password.")
                elif len(_password) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    with st.spinner("Creating account…"):
                        _res = auth_signup(_email.strip(), _password)
                    if _res["error"]:
                        st.error(_res["error"])
                    else:
                        _u = _res["user"]
                        if _u and _u.id:
                            st.session_state["auth_user"]    = _u
                            st.session_state["auth_user_id"] = str(_u.id)
                            st.session_state["auth_email"]   = str(_u.email)
                            _s2  = _res.get("session")
                            _at2 = getattr(_s2, "access_token",  None) or ""
                            _rt2 = getattr(_s2, "refresh_token", None) or ""
                            st.session_state["auth_access_token"]  = _at2
                            st.session_state["auth_refresh_token"] = _rt2
                            if _rt2:
                                save_session_cache(str(_u.id), str(_u.email), _rt2)
                            set_user_session(_at2, _rt2)
                            st.success("Account created! Loading dashboard…")
                            st.rerun()
                        else:
                            st.success(
                                "Account created! Check your email to confirm, "
                                "then log in below."
                            )

        st.markdown(
            '<div style="text-align:center; font-size:10px; color:#333; margin-top:24px;">'
            'Powered by Supabase Auth · Data isolated per account'
            '</div>', unsafe_allow_html=True,
        )


def render_beta_portal(beta_user_id: str):
    """Private portal for beta testers. Accessible via /?beta=USER_ID.
    Shows only: CSV upload + Telegram logging instructions. Nothing else."""

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="stHeader"]  { display: none !important; }
    .beta-wrap { max-width: 560px; margin: 60px auto 0 auto; }
    .beta-logo { text-align: center; font-size: 42px; margin-bottom: 4px; }
    .beta-title { text-align: center; font-size: 22px; font-weight: 800;
                  color: #e0e0e0; letter-spacing: -0.5px; margin-bottom: 2px; }
    .beta-sub   { text-align: center; font-size: 11px; color: #5c6bc0;
                  text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 36px; }
    .beta-section { background: #12122288; border: 1px solid #2a2a4a;
                    border-radius: 12px; padding: 24px 28px; margin-bottom: 20px; }
    .beta-section-title { font-size: 13px; font-weight: 700; color: #7986cb;
                          text-transform: uppercase; letter-spacing: 1px; margin-bottom: 14px; }
    .tg-cmd { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px;
              padding: 10px 14px; font-family: monospace; font-size: 13px;
              color: #80cbc4; margin: 6px 0; }
    </style>
    <div class="beta-wrap">
      <div class="beta-logo">📊</div>
      <div class="beta-title">EdgeIQ Beta</div>
      <div class="beta-sub">Scanner Testing Program</div>
    </div>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 4, 1])
    with col:

        # ── Step 1: CSV Upload ─────────────────────────────────────────────
        st.markdown('<div class="beta-section">'
                    '<div class="beta-section-title">Step 1 — Weekly Trade Upload</div>',
                    unsafe_allow_html=True)
        st.caption("In Webull: Orders → History → Export CSV. Drop it below once a week.")

        _b_file = st.file_uploader(
            "Drop your Webull CSV here",
            type=["csv"],
            key="beta_csv_uploader",
            label_visibility="collapsed",
        )

        if _b_file is not None:
            try:
                _b_df     = pd.read_csv(_b_file)
                _b_trades = parse_webull_csv(_b_df)
                if not _b_trades:
                    st.warning("No closed trades found. Make sure you exported Order History (not Account History).")
                else:
                    # Dedup against existing journal
                    _b_existing = _cached_load_journal(user_id=beta_user_id)
                    _b_existing_keys: set = set()
                    if not _b_existing.empty:
                        for _, _br in _b_existing.iterrows():
                            _bk = f"{_br.get('ticker','')}_{str(_br.get('timestamp',''))[:10]}"
                            _b_existing_keys.add(_bk)

                    _b_new, _b_skipped = [], 0
                    for _bt in _b_trades:
                        _bk = f"{_bt.get('ticker','')}_{str(_bt.get('timestamp',''))[:10]}"
                        if _bk in _b_existing_keys:
                            _b_skipped += 1
                        else:
                            _b_new.append(_bt)

                    if not _b_new:
                        st.success(f"All {len(_b_trades)} trades already uploaded. Nothing new to add.")
                    else:
                        st.success(f"Found **{len(_b_new)} new trades** to import"
                                   + (f" ({_b_skipped} already uploaded)" if _b_skipped else "") + ".")

                        if st.button(f"💾 Upload {len(_b_new)} Trades",
                                     type="primary", use_container_width=True,
                                     key="beta_import_btn"):
                            _b_saved = 0
                            for _bt in _b_new:
                                try:
                                    save_journal_entry(_bt, user_id=beta_user_id)
                                    _b_saved += 1
                                except Exception:
                                    pass
                            st.success(f"✅ {_b_saved} trades uploaded successfully.")
                            st.balloons()

            except Exception as _be:
                st.error(f"Could not read file: {_be}")

        st.markdown('</div>', unsafe_allow_html=True)

        # ── Step 2: Daily Trade Log ───────────────────────────────────────
        st.markdown('<div class="beta-section">'
                    '<div class="beta-section-title">Step 2 — Daily Trade Log</div>',
                    unsafe_allow_html=True)
        st.caption("Log your most important trade of the day — win or loss. Both matter equally.")

        _b_col1, _b_col2 = st.columns([1, 1])
        with _b_col1:
            _b_ticker = st.text_input("Ticker", placeholder="ARAI", key="beta_ticker",
                                      label_visibility="visible").upper().strip()
        with _b_col2:
            _b_wl = st.selectbox("Result", ["Win", "Loss"], key="beta_wl")

        _b_col3, _b_col4 = st.columns([1, 1])
        with _b_col3:
            _b_entry = st.number_input("Entry Price", min_value=0.0, step=0.01,
                                       format="%.4f", key="beta_entry")
        with _b_col4:
            _b_exit = st.number_input("Exit Price", min_value=0.0, step=0.01,
                                      format="%.4f", key="beta_exit")

        _b_note = st.text_input("Note (optional)", placeholder="what you saw, why you took it",
                                key="beta_note", label_visibility="visible")

        if st.button("📝 Log Trade", type="primary", use_container_width=True, key="beta_log_btn"):
            if not _b_ticker:
                st.warning("Enter a ticker.")
            elif _b_entry <= 0 or _b_exit <= 0:
                st.warning("Enter valid entry and exit prices.")
            else:
                _b_result = save_telegram_trade(
                    ticker=_b_ticker,
                    win_loss=_b_wl,
                    entry_price=_b_entry,
                    exit_price=_b_exit,
                    notes=_b_note,
                    user_id=beta_user_id,
                )
                if _b_result.get("duplicate"):
                    st.warning(f"Already logged: {_b_ticker} {_b_entry}→{_b_exit}. Nothing added.")
                elif _b_result.get("error"):
                    st.error(f"Save failed: {_b_result['error']}")
                else:
                    _b_pnl  = _b_result["pnl_pct"]
                    _b_sign = "+" if _b_pnl >= 0 else ""
                    _b_emoji = "🟢" if _b_wl == "Win" else "🔴"
                    st.success(
                        f"{_b_emoji} Logged: **{_b_ticker}** | {_b_wl.upper()} | "
                        f"${_b_entry:.2f} → ${_b_exit:.2f} | {_b_sign}{_b_pnl:.1f}%"
                    )

        st.markdown('</div>', unsafe_allow_html=True)

        # ── Step 3: Get Alerts ────────────────────────────────────────────
        st.markdown('<div class="beta-section">'
                    '<div class="beta-section-title">Step 3 — Get Alerts</div>',
                    unsafe_allow_html=True)
        st.caption("Connect Telegram to receive morning scanner setups and end-of-day results each trading day.")

        _b_prefs = _cached_load_user_prefs(user_id=beta_user_id)
        _b_chat_id = _b_prefs.get("tg_chat_id")

        if _b_chat_id:
            st.success("✅ Connected — you'll receive morning alerts and EOD results via Telegram.")
        else:
            _tg_bot = os.getenv("TELEGRAM_BOT_USERNAME", "edgeiq_alerts_bot").lstrip("@")
            _deep_link = f"https://t.me/{_tg_bot}?start={beta_user_id}"
            st.link_button(
                "📲 Connect Telegram for Alerts →",
                url=_deep_link,
                use_container_width=True,
            )
            st.caption("Tap the button → opens Telegram → you're connected. Takes 5 seconds.")

        st.markdown('</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# BUILD NOTES VIEWER  — accessible via /?notes=<USER_ID>
# ══════════════════════════════════════════════════════════════════════════════

def render_build_notes():
    """Render build notes as a live hosted page. Accessible via /?notes=USER_ID."""
    _NOTES_PASSCODE = os.environ.get("NOTES_PASSCODE", "")

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none !important; }
    .notes-header { text-align: center; padding: 32px 0 8px 0; }
    .notes-header h1 { font-size: 28px; font-weight: 800; color: #7986cb; margin: 0; }
    .notes-header p  { font-size: 12px; color: #666; margin: 4px 0 0 0; }
    </style>
    """, unsafe_allow_html=True)

    if not st.session_state.get("notes_unlocked"):
        st.markdown("""
        <div class="notes-header">
          <h1>🔒 EdgeIQ Build Notes</h1>
          <p>Enter passcode to view</p>
        </div>
        """, unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            code_input = st.text_input("Passcode", type="password", placeholder="Enter passcode")
            if st.button("Unlock", use_container_width=True):
                if code_input == _NOTES_PASSCODE:
                    st.session_state["notes_unlocked"] = True
                    st.rerun()
                else:
                    st.error("Incorrect passcode.")
        return

    st.markdown("""
    <div class="notes-header">
      <h1>📋 EdgeIQ Build Notes</h1>
      <p>Live document — updates automatically</p>
    </div>
    """, unsafe_allow_html=True)

    notes_path = os.path.join(os.path.dirname(__file__), ".local", "build_notes.md")
    if os.path.exists(notes_path):
        with open(notes_path, "r") as f:
            content = f.read()
        st.markdown(content)
    else:
        st.error("Build notes file not found.")


def render_private_build_notes():
    """Render private build notes. Accessible via /?private=<KEY>."""
    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none !important; }
    .pnotes-header { text-align: center; padding: 32px 0 8px 0; }
    .pnotes-header h1 { font-size: 28px; font-weight: 800; color: #ef5350; margin: 0; }
    .pnotes-header p  { font-size: 12px; color: #666; margin: 4px 0 0 0; }
    </style>
    <div class="pnotes-header">
      <h1>🔒 EdgeIQ Private Notes</h1>
      <p>Internal only — strategy, architecture, roadmap</p>
    </div>
    """, unsafe_allow_html=True)

    notes_path = os.path.join(os.path.dirname(__file__), ".local", "build_notes_private.md")
    if os.path.exists(notes_path):
        with open(notes_path, "r") as f:
            content = f.read()
        st.markdown(content)
    else:
        st.error("Private build notes file not found.")


def render_trade_journal_page():
    """Trade Journal Logger page — accessible via /?journal=<USER_ID>.
    Single-user trade journal for quick logging outside the main app tabs."""
    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none !important; }
    .tj-header { text-align: center; padding: 32px 0 8px 0; }
    .tj-header h1 { font-size: 28px; font-weight: 800; color: #66bb6a; margin: 0; }
    .tj-header p  { font-size: 12px; color: #5c6bc0; text-transform: uppercase;
                    letter-spacing: 1.5px; margin: 4px 0 0 0; }
    .tj-section { background: #12122288; border: 1px solid #2a2a4a;
                  border-radius: 12px; padding: 24px 28px; margin-bottom: 20px; }
    .tj-section-title { font-size: 13px; font-weight: 700; color: #7986cb;
                        text-transform: uppercase; letter-spacing: 1px; margin-bottom: 14px; }
    .tj-stat { text-align: center; padding: 12px; background: #1a1a2e;
               border-radius: 8px; border: 1px solid #2a2a4a; }
    </style>
    <div class="tj-header">
      <h1>📓 EdgeIQ Trade Journal</h1>
      <p>Quick-Log Your Trades</p>
    </div>
    """, unsafe_allow_html=True)

    _tj_uid = "a5e1fcab-8369-42c4-8550-a8a19734510c"

    _, col, _ = st.columns([1, 4, 1])
    with col:

        _tj_journal = _cached_load_journal(user_id=_tj_uid)
        _tj_count = len(_tj_journal) if not _tj_journal.empty else 0

        _tj_acc_rows = []
        try:
            if supabase:
                _tj_acc = supabase.table("accuracy_tracker").select("predicted,correct").range(0, 9999).execute()
                _tj_acc_rows = _tj_acc.data or []
        except Exception:
            pass
        _tj_total_preds = len(_tj_acc_rows)
        _tj_correct = sum(1 for r in _tj_acc_rows if "✅" in str(r.get("correct", "")))
        _tj_acc_pct = round(_tj_correct / _tj_total_preds * 100, 1) if _tj_total_preds > 0 else 0

        _tj_pt_rows = []
        try:
            if supabase:
                _tj_pt = supabase.table("paper_trades").select("win_loss").execute()
                _tj_pt_rows = _tj_pt.data or []
        except Exception:
            pass
        _tj_pt_total = len(_tj_pt_rows)
        _tj_pt_wins = sum(1 for r in _tj_pt_rows if str(r.get("win_loss", "")).strip().lower() == "win")

        st.markdown('<div class="tj-section"><div class="tj-section-title">Your Stats</div>', unsafe_allow_html=True)
        _s1, _s2, _s3, _s4 = st.columns(4)
        with _s1:
            st.markdown(f"<div class='tj-stat'><div style='font-size:11px;color:#777'>Journal Entries</div>"
                        f"<div style='font-size:22px;font-weight:800;color:#90caf9'>{_tj_count}</div></div>",
                        unsafe_allow_html=True)
        with _s2:
            st.markdown(f"<div class='tj-stat'><div style='font-size:11px;color:#777'>Predictions</div>"
                        f"<div style='font-size:22px;font-weight:800;color:#ce93d8'>{_tj_total_preds}</div></div>",
                        unsafe_allow_html=True)
        with _s3:
            _acc_color = "#66bb6a" if _tj_acc_pct >= 65 else "#ffa726" if _tj_acc_pct >= 50 else "#ef5350"
            st.markdown(f"<div class='tj-stat'><div style='font-size:11px;color:#777'>Prediction Acc</div>"
                        f"<div style='font-size:22px;font-weight:800;color:{_acc_color}'>{_tj_acc_pct}%</div></div>",
                        unsafe_allow_html=True)
        with _s4:
            _pt_wr = round(_tj_pt_wins / _tj_pt_total * 100, 1) if _tj_pt_total > 0 else 0
            _pt_color = "#66bb6a" if _pt_wr >= 65 else "#ffa726" if _pt_wr >= 50 else "#ef5350"
            st.markdown(f"<div class='tj-stat'><div style='font-size:11px;color:#777'>Paper Win Rate</div>"
                        f"<div style='font-size:22px;font-weight:800;color:{_pt_color}'>{_pt_wr}% ({_tj_pt_total})</div></div>",
                        unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="tj-section"><div class="tj-section-title">Quick Log — CSV Import</div>', unsafe_allow_html=True)
        st.caption("Export your Webull order history as CSV and drop it here.")

        _tj_file = st.file_uploader("Drop Webull CSV", type=["csv"], key="tj_csv_uploader", label_visibility="collapsed")

        if _tj_file is not None:
            try:
                _tj_df = pd.read_csv(_tj_file)
                _tj_trades = parse_webull_csv(_tj_df)
                if not _tj_trades:
                    st.warning("No closed trades found. Make sure you exported Order History.")
                else:
                    _tj_existing = _cached_load_journal(user_id=_tj_uid)
                    _tj_existing_keys: set = set()
                    if not _tj_existing.empty:
                        for _, _r in _tj_existing.iterrows():
                            _k = f"{_r.get('ticker', '')}_{str(_r.get('timestamp', ''))[:10]}"
                            _tj_existing_keys.add(_k)

                    _tj_new, _tj_skipped = [], 0
                    for _t in _tj_trades:
                        _k = f"{_t.get('ticker', '')}_{str(_t.get('timestamp', ''))[:10]}"
                        if _k in _tj_existing_keys:
                            _tj_skipped += 1
                        else:
                            _tj_new.append(_t)

                    if _tj_new:
                        for _t in _tj_new:
                            _t["user_id"] = _tj_uid
                            save_journal_entry(_t)
                        st.success(f"Logged {len(_tj_new)} new trades! ({_tj_skipped} duplicates skipped)")
                    else:
                        st.info(f"All {_tj_skipped} trades already logged — nothing new to add.")
            except Exception as e:
                st.error(f"Error parsing CSV: {e}")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="tj-section"><div class="tj-section-title">Per-Structure TCS Thresholds</div>', unsafe_allow_html=True)
        st.caption("Based on your live accuracy — updated every time you open this page.")
        _tj_tcs = compute_structure_tcs_thresholds()
        if _tj_tcs:
            for _t in _tj_tcs:
                _hr = _t["hit_rate"]
                if _hr is not None:
                    _hr_c = "#66bb6a" if _hr >= 70 else "#ffa726" if _hr >= 55 else "#ff7043" if _hr >= 40 else "#ef5350"
                    _tcs_c = "#66bb6a" if _t["recommended_tcs"] <= 55 else "#ffa726" if _t["recommended_tcs"] <= 70 else "#ef5350"
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;align-items:center;padding:8px 12px;"
                        f"background:#1a1a2e;border-radius:6px;margin-bottom:4px;border:1px solid #2a2a4a'>"
                        f"<span style='color:#e0e0e0;font-weight:600'>{_t['status']} {_t['structure']}</span>"
                        f"<span style='display:flex;gap:16px'>"
                        f"<span style='color:{_hr_c};font-weight:700'>{_hr:.1f}%</span>"
                        f"<span style='color:{_tcs_c};font-weight:700'>TCS ≥ {_t['recommended_tcs']}</span>"
                        f"<span style='color:#777;font-size:11px'>{_t['sample_count']}n</span>"
                        f"</span></div>",
                        unsafe_allow_html=True)
                else:
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;align-items:center;padding:8px 12px;"
                        f"background:#1a1a2e;border-radius:6px;margin-bottom:4px;border:1px solid #2a2a4a;opacity:0.4'>"
                        f"<span style='color:#555'>{_t['status']} {_t['structure']}</span>"
                        f"<span style='color:#555'>No data</span></div>",
                        unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # ── TCS Threshold History (last 14 days) ───────────────────────────────
        _tcs_hist = _cached_load_tcs_threshold_history(days=14)
        if _tcs_hist:
            import pandas as _pd
            st.markdown(
                '<div class="tj-section"><div class="tj-section-title">TCS Threshold History (last 14 days)</div>',
                unsafe_allow_html=True,
            )
            st.caption("Each row is one nightly recalibration — shows how thresholds have drifted per structure.")

            # Build a wide DataFrame: rows = timestamps, cols = structure keys
            _hist_rows = []
            for _rec in _tcs_hist:
                _ts = _rec.get("timestamp", "")
                _row = {"Date": _ts[:10], "Time (UTC)": _ts[11:16]}
                _row.update(_rec.get("thresholds", {}))
                _hist_rows.append(_row)
            _hist_df = _pd.DataFrame(_hist_rows)

            # Show the table (most recent first)
            st.dataframe(_hist_df.iloc[::-1].reset_index(drop=True), use_container_width=True, hide_index=True)

            # Sparklines per structure using st.line_chart (if ≥ 2 data points)
            _struct_cols = [c for c in _hist_df.columns if c not in ("Date", "Time (UTC)", "Timestamp")]
            if len(_hist_df) >= 2 and _struct_cols:
                _chart_df = _hist_df[_struct_cols].copy()
                _chart_df.index = _hist_df["Date"] + " " + _hist_df["Time (UTC)"]
                st.caption("Sparklines — higher = stricter bar, lower = more trades allowed")
                st.line_chart(_chart_df, use_container_width=True, height=200)

            st.markdown('</div>', unsafe_allow_html=True)

        if not _tj_journal.empty:
            st.markdown('<div class="tj-section"><div class="tj-section-title">Recent Journal Entries</div>', unsafe_allow_html=True)
            _display_cols = [c for c in ["timestamp", "ticker", "side", "price", "quantity", "pnl", "pnl_pct", "notes"] if c in _tj_journal.columns]
            if _display_cols:
                _tj_show = _tj_journal[_display_cols].head(20)
                st.dataframe(_tj_show, use_container_width=True, hide_index=True)
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(
            '<div style="text-align:center;font-size:10px;color:#333;margin-top:24px">'
            'EdgeIQ Trade Journal · Single-user mode · Data stored in Supabase'
            '</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# LIVE STREAM
# ══════════════════════════════════════════════════════════════════════════════

def render_log_entry_ui():
    """Show the Notes box + LOG ENTRY button below the chart."""
    state = st.session_state.get("last_analysis_state")
    if not state:
        return
    with st.expander("💾 Log This Trade Entry", expanded=True):
        ticker  = state.get("ticker", "?")
        price   = state.get("price", 0.0)
        tcs     = state.get("tcs", 0.0)
        rvol    = state.get("rvol") or 0.0
        struct  = state.get("structure", "?")
        st.markdown(
            f"**Ready to log:** `{ticker}` @ `${price:.2f}` — "
            f"TCS `{tcs:.0f}` · RVOL `{rvol:.2f}x` · Structure `{struct}`"
        )

        # ── Live Quote Fetch ──────────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
            'letter-spacing:1px; margin:8px 0 4px 0;">🔍 Override / Verify Live Price</div>',
            unsafe_allow_html=True,
        )
        _fc1, _fc2 = st.columns([3, 1])
        with _fc1:
            _fetch_ticker = st.text_input(
                "Ticker to fetch", value=ticker,
                placeholder="e.g. TSLA", label_visibility="collapsed",
                key="live_fetch_ticker_input",
            )
        with _fc2:
            _fetch_clicked = st.button("Fetch Price", use_container_width=True,
                                       key="live_fetch_btn")
        if _fetch_clicked:
            with st.spinner(f"Fetching live quote for {_fetch_ticker}…"):
                _q = fetch_live_quote(_fetch_ticker)
            if _q["error"]:
                st.error(f"Could not fetch `{_fetch_ticker}`: {_q['error']}")
            else:
                st.session_state["_fetched_price"]  = _q["price"]
                st.session_state["_fetched_volume"] = _q["volume"]
                st.session_state["_fetched_symbol"] = _fetch_ticker.upper()

        if st.session_state.get("_fetched_price") is not None:
            _fp  = st.session_state["_fetched_price"]
            _fv  = st.session_state["_fetched_volume"]
            _fsym = st.session_state.get("_fetched_symbol", "")
            _vol_str = f"{_fv:,}" if _fv else "—"
            st.markdown(
                f'<div style="background:#0d2b1a; border:1px solid #2e7d32; border-radius:6px; '
                f'padding:6px 12px; margin:4px 0; display:flex; gap:24px; align-items:center;">'
                f'<span style="color:#81c784; font-weight:700; font-size:13px;">{_fsym}</span>'
                f'<span style="color:#a5d6a7;">Price: <b style="color:#e8f5e9;">${_fp:.2f}</b></span>'
                f'<span style="color:#a5d6a7;">Vol today: <b style="color:#e8f5e9;">{_vol_str}</b></span>'
                f'<span style="font-size:10px; color:#555;">via yfinance</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Use fetched price in the log entry
            price = _fp

        # ── Entry time override ───────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
            'letter-spacing:1px; margin:8px 0 4px 0;">⏱ Entry Time (ET)</div>',
            unsafe_allow_html=True,
        )
        _tc1, _tc2 = st.columns([1, 1])
        _now_et = datetime.now(EASTERN)
        _entry_date = _tc1.date_input(
            "Entry date", value=_now_et.date(),
            key="journal_entry_date", label_visibility="collapsed",
        )
        _entry_time = _tc2.time_input(
            "Entry time (ET)", value=_now_et.time().replace(second=0, microsecond=0),
            key="journal_entry_time", label_visibility="collapsed",
            step=60,
        )

        notes = st.text_input(
            "Mental State / Notes",
            placeholder="e.g. Calm, FOMO, Greed, Hesitated...",
            key="journal_notes_input",
        )
        if st.button("💾 LOG ENTRY", use_container_width=True, key="journal_log_btn"):
            grade, reason = compute_trade_grade(
                state.get("rvol"), state.get("tcs"), state.get("price"),
                state.get("ib_high"), state.get("ib_low"), state.get("structure"),
            )
            _log_ticker = st.session_state.get("_fetched_symbol") or state.get("ticker", "")
            _entry_dt = datetime.combine(_entry_date, _entry_time)
            entry = {
                "timestamp": _entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ticker":    _log_ticker,
                "price":     round(float(price), 4),
                "structure": state.get("structure", ""),
                "tcs":       round(state.get("tcs", 0.0), 1),
                "rvol":      round(state.get("rvol") or 0.0, 2),
                "ib_high":   round(state.get("ib_high") or 0.0, 4),
                "ib_low":    round(state.get("ib_low") or 0.0, 4),
                "notes":     notes,
                "grade":     grade,
                "grade_reason": reason,
                "social_bull_pct":  state.get("social_bull_pct"),
                "social_bear_pct":  state.get("social_bear_pct"),
                "social_msg_count": state.get("social_msg_count"),
            }
            save_journal_entry(entry, user_id=st.session_state.get("auth_user_id", ""))
            # Clear fetched price state so next log starts fresh
            for _ck in ("_fetched_price", "_fetched_volume", "_fetched_symbol"):
                st.session_state.pop(_ck, None)
            gc = _GRADE_COLORS.get(grade, "#aaa")
            st.success(f"Logged! **Grade {grade}** — {reason}")
            st.markdown(
                f'<div style="display:inline-block; background:{gc}22; border:2px solid {gc}; '
                f'border-radius:50%; width:52px; height:52px; line-height:52px; '
                f'text-align:center; font-size:24px; font-weight:900; color:{gc};">'
                f'{grade}</div>',
                unsafe_allow_html=True,
            )

    # ── Recent Trades preview (last 10 from Supabase) ─────────────────────────
    _recent_df = _cached_load_journal(user_id=st.session_state.get("auth_user_id", ""))
    if not _recent_df.empty:
        _cols = [c for c in ["timestamp", "ticker", "price", "structure", "grade"]
                 if c in _recent_df.columns]
        _show = _recent_df[_cols].head(10).copy()

        # Format price column
        if "price" in _show.columns:
            _show["price"] = _show["price"].apply(
                lambda x: f"${float(x):.2f}" if x not in (None, "", "nan") else "—"
            )

        # Grade → colored badge via styled HTML
        _grade_row_colors = {"A": "#4caf50", "B": "#26a69a", "C": "#ffa726", "F": "#ef5350"}
        rows_html = ""
        for _, row in _show.iterrows():
            g  = str(row.get("grade", ""))
            gc = _grade_row_colors.get(g, "#555")
            ts = str(row.get("timestamp", ""))[:16]   # trim seconds
            rows_html += (
                f'<tr>'
                f'<td style="color:#888; font-size:11px; padding:5px 8px; white-space:nowrap;">{ts}</td>'
                f'<td style="color:#e0e0e0; font-weight:700; padding:5px 8px;">{row.get("ticker","")}</td>'
                f'<td style="color:#90caf9; padding:5px 8px;">{row.get("price","")}</td>'
                f'<td style="color:#aaa; font-size:11px; padding:5px 8px;">{row.get("structure","")}</td>'
                f'<td style="padding:5px 8px; text-align:center;">'
                f'<span style="background:{gc}22; border:1px solid {gc}; color:{gc}; '
                f'border-radius:4px; padding:2px 8px; font-weight:700; font-size:12px;">{g}</span>'
                f'</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="margin-top:12px;">'
            f'<div style="font-size:12px; color:#5c6bc0; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:6px;">📋 Recent Trades</div>'
            f'<table style="width:100%; border-collapse:collapse; background:#12122a; '
            f'border-radius:6px; overflow:hidden;">'
            f'<thead><tr style="background:#1a1a3e; border-bottom:1px solid #2a2a4a;">'
            f'<th style="text-align:left; color:#5c6bc0; font-size:10px; padding:6px 8px; '
            f'text-transform:uppercase; letter-spacing:1px;">Time</th>'
            f'<th style="text-align:left; color:#5c6bc0; font-size:10px; padding:6px 8px; '
            f'text-transform:uppercase; letter-spacing:1px;">Ticker</th>'
            f'<th style="text-align:left; color:#5c6bc0; font-size:10px; padding:6px 8px; '
            f'text-transform:uppercase; letter-spacing:1px;">Price</th>'
            f'<th style="text-align:left; color:#5c6bc0; font-size:10px; padding:6px 8px; '
            f'text-transform:uppercase; letter-spacing:1px;">Structure</th>'
            f'<th style="text-align:center; color:#5c6bc0; font-size:10px; padding:6px 8px; '
            f'text-transform:uppercase; letter-spacing:1px;">Grade</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )


def render_journal_tab(api_key: str = "", secret_key: str = ""):
    """Render the 📖 My Journal tab."""
    _uid = st.session_state.get("auth_user_id", "")
    df = _cached_load_journal(user_id=_uid)

    with st.expander("📋 How to use this tab — read this first", expanded=df.empty):
        st.markdown(
            """
**This is your personal trade journal. It is the source of truth for your analytics.**

---

**STEP 1 — Import your trades from Webull**
- In Webull: go to **Orders → History → Export CSV**
- Upload that file using the **"Import Trades from Webull CSV"** section below
- The system auto-pairs your Buy → Sell orders and grades each trade by P&L
- Open positions (no matching sell) are skipped automatically

**STEP 2 — Fix any "Unknown" structures** *(if the button appears)*
- After importing, if you see a **"🔄 Fix X Unknown Structures"** button, click it
- This pulls historical bar data from Alpaca for each trade and fills in the correct structure label (Trend Day, Normal, etc.)
- Requires your Alpaca credentials to be entered in the sidebar

**STEP 3 — Sync to Analytics** *(only needed once after first import)*
- Go to the **📊 Analytics** tab
- If you see a **"🔄 Sync Journal → Analytics"** button, click it to backfill your accuracy data
- After that, new imports sync automatically

**STEP 4 — Review your journal cards below**
- Each card shows: Ticker · Entry price → Exit price · P&L · Shares · Hold time badge
- Grades: **A/B = winner**, **C = breakeven/scratch**, **D/F = loss**
- You can manually add notes or change the grade on any entry

---

⚠️ **What this tab is NOT for:**
- Do not enter your watchlist or scan tickers here — that's the sidebar / Scanner tab
- Do not manually type fabricated trades — the analytics are only useful if the data is real
            """,
        )

    cola, colb, colc = st.columns([2, 1, 1])
    with cola:
        st.subheader("📖 My Trade Journal")
    with colb:
        if not df.empty:
            _unknown_count = (
                df["structure"].isin(["Unknown", "", None]) |
                df["structure"].isna()
            ).sum() if "structure" in df.columns else 0
            _bf_label = (
                f"🔄 Fix {_unknown_count} Unknown Structures"
                if _unknown_count > 0 else "✅ All Structures Enriched"
            )
            _bf_disabled = _unknown_count == 0
            if st.button(
                _bf_label,
                disabled=_bf_disabled,
                use_container_width=True,
                key="backfill_structures_btn",
                help="Re-fetches bar data from Alpaca for any journal entry showing 'Unknown' structure and fills in the correct Trend Day / Normal / Neutral etc. label.",
            ):
                _bf_api  = st.session_state.get("_sb_api_key", "")
                _bf_sec  = st.session_state.get("_sb_secret_key", "")
                _bf_uid  = st.session_state.get("auth_user_id", "")
                _bf_feed = st.session_state.get("data_feed", "sip")
                if not _bf_api or not _bf_sec:
                    st.error("Enter your Alpaca credentials in the sidebar first.")
                else:
                    with st.spinner(
                        f"Fetching bar data for {_unknown_count} entries… "
                        "This may take a minute."
                    ):
                        _bf_result = backfill_unknown_structures(
                            _bf_api, _bf_sec, _bf_uid, feed=_bf_feed
                        )
                    if _bf_result["updated"] > 0:
                        st.success(
                            f"✅ Fixed **{_bf_result['updated']}** entries. "
                            f"{_bf_result['failed']} failed. Refresh to see updates."
                        )
                        if _bf_result["errors"]:
                            with st.expander("Details"):
                                for _e in _bf_result["errors"]:
                                    st.caption(_e)
                    elif _bf_result["failed"] > 0:
                        st.warning(
                            f"Could not enrich {_bf_result['failed']} entries. "
                            "Check that your Alpaca key has market data access."
                        )
                        for _e in _bf_result["errors"][:5]:
                            st.caption(_e)
                    else:
                        st.info("No Unknown structures found — journal is fully enriched.")
    with colc:
        if not df.empty:
            _journal_export = df.copy()
            if "structure" in _journal_export.columns:
                _journal_export["structure"] = _journal_export["structure"].apply(_clean_structure_label)
            csv_bytes = _journal_export.to_csv(index=False).encode()
            st.download_button(
                "⬇️ Download Journal (CSV)",
                data=csv_bytes,
                file_name=f"trade_journal_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    # ── Webull CSV Import ──────────────────────────────────────────────────────
    with st.expander("📥 Import Trades from Webull CSV", expanded=df.empty):
        st.markdown(
            '<div style="font-size:12px; color:#546e7a; margin-bottom:10px;">'
            'In Webull: <b>Orders → History → Export CSV</b>. '
            'Upload your file below — all closed round-trips are auto-detected, '
            'paired buy→sell, graded by P&L, and saved to your journal instantly. '
            'Open positions are skipped automatically.'
            '</div>',
            unsafe_allow_html=True,
        )
        _wb_file = st.file_uploader(
            "Drop your Webull order history CSV here",
            type=["csv"],
            key="webull_csv_uploader",
            label_visibility="collapsed",
        )
        if _wb_file is not None:
            try:
                _wb_df = pd.read_csv(_wb_file)
                _wb_trades = parse_webull_csv(_wb_df)
                if not _wb_trades:
                    st.warning(
                        "Could not detect any closed round-trip trades in this file. "
                        "Make sure you exported **Order History** (not Account History) "
                        "and that the file contains both Buy and Sell filled orders."
                    )
                    st.caption(f"Columns found: {', '.join(_wb_df.columns.tolist())}")
                else:
                    _wb_preview = pd.DataFrame([{
                        "Ticker":      t["ticker"],
                        "Entry Date":  str(t["timestamp"])[:10],
                        "Entry Price": f"${t['price']:.4f}",
                        "Grade":       t["grade"],
                        "Notes":       t["notes"],
                    } for t in _wb_trades])
                    st.success(f"Found **{len(_wb_trades)} closed trades** ready to import:")
                    st.dataframe(_wb_preview, use_container_width=True, height=220)

                    _wb_col1, _wb_col2 = st.columns([1, 1])
                    with _wb_col1:
                        _wb_skip_dups = st.checkbox(
                            "Skip duplicates (same ticker + date already in journal)",
                            value=True, key="wb_skip_dups",
                        )
                    with _wb_col2:
                        _wb_import_btn = st.button(
                            f"💾 IMPORT {len(_wb_trades)} TRADES",
                            type="primary", use_container_width=True,
                            key="wb_import_btn",
                        )

                    if _wb_import_btn:
                        _existing_keys: set = set()
                        if _wb_skip_dups and not df.empty:
                            for _, _erow in df.iterrows():
                                _ekey = (
                                    str(_erow.get("ticker", "")).upper(),
                                    str(_erow.get("timestamp", ""))[:10],
                                )
                                _existing_keys.add(_ekey)

                        # Filter to only trades that need saving
                        _to_save = []
                        _skipped = 0
                        for _t in _wb_trades:
                            _key = (_t["ticker"].upper(), str(_t["timestamp"])[:10])
                            if _wb_skip_dups and _key in _existing_keys:
                                _skipped += 1
                            else:
                                _to_save.append(_t)

                        if _to_save:
                            _enrich_status = st.empty()
                            _enrich_status.info(
                                f"Enriching {len(_to_save)} trade(s) with TCS, RVOL, IB levels "
                                f"and structure — this may take a moment..."
                            )

                            _ak  = st.session_state.get("_sb_api_key", "")
                            _ask = st.session_state.get("_sb_secret_key", "")

                            def _enrich_one(trade):
                                ctx = enrich_trade_context(
                                    _ak, _ask,
                                    trade["ticker"],
                                    trade.get("timestamp", ""),
                                    feed="sip",
                                )
                                enriched = dict(trade)
                                for field in ("tcs", "rvol", "ib_high", "ib_low", "structure"):
                                    if ctx.get(field) is not None and not enriched.get(field):
                                        enriched[field] = ctx[field]
                                _extra_parts = []
                                if ctx.get("gap_pct") is not None:
                                    _extra_parts.append(f"Gap: {ctx['gap_pct']:+.1f}%")
                                if ctx.get("poc_price") is not None:
                                    _extra_parts.append(f"POC: ${ctx['poc_price']:.4f}")
                                if ctx.get("top_pattern"):
                                    _pdir = ctx.get("top_pattern_direction", "")
                                    _pscore = ctx.get("top_pattern_score", 0)
                                    _extra_parts.append(
                                        f"Pattern: {ctx['top_pattern']} ({_pdir}, {_pscore:.0%})"
                                    )
                                if _extra_parts:
                                    enriched["notes"] = (
                                        enriched.get("notes", "") + " | " + " | ".join(_extra_parts)
                                    )
                                return enriched

                            from concurrent.futures import ThreadPoolExecutor, as_completed
                            _enriched_trades = [None] * len(_to_save)
                            with ThreadPoolExecutor(max_workers=min(6, len(_to_save))) as _ex:
                                _futs = {_ex.submit(_enrich_one, t): i for i, t in enumerate(_to_save)}
                                for _f in as_completed(_futs):
                                    _enriched_trades[_futs[_f]] = _f.result()

                            _enrich_status.empty()

                            _imported = 0
                            for _t in _enriched_trades:
                                if _t is not None:
                                    save_journal_entry(_t, user_id=_uid)
                                    # Also write to accuracy_tracker so Analytics tab
                                    # shows real P&L stats from your Webull history
                                    _entry_p = float(_t.get("price", 0) or 0)
                                    _exit_p  = float(_t.get("exit_price", 0) or 0)
                                    _mfe_v   = float(_t.get("mfe", 0) or 0)
                                    if _entry_p > 0 and _exit_p > 0:
                                        # Use P&L direction to determine correct/incorrect.
                                        # Win (exit > entry) → structure prediction was useful → ✅
                                        # Loss (exit ≤ entry) → prediction didn't play out → ❌
                                        # Previously both were set to the same structure string
                                        # which always logged ✅ regardless of trade outcome.
                                        _was_win = _exit_p > _entry_p
                                        _predicted_struct = _t.get("structure", "Unknown")
                                        _actual_struct = _predicted_struct if _was_win else "Loss"
                                        log_accuracy_entry(
                                            symbol=_t.get("ticker", ""),
                                            predicted=_predicted_struct,
                                            actual=_actual_struct,
                                            compare_key="webull_import",
                                            entry_price=_entry_p,
                                            exit_price=_exit_p,
                                            mfe=_mfe_v,
                                            user_id=_uid,
                                        )
                                    _imported += 1
                        else:
                            _imported = 0

                        st.success(
                            f"Imported **{_imported} trades** into your journal"
                            + (f" ({_skipped} skipped as duplicates)" if _skipped else "")
                            + " — each entry includes TCS, RVOL, IB levels, and structure. "
                            "Refresh the page to see them below."
                        )
                        if _imported > 0:
                            st.balloons()
            except Exception as _wb_err:
                st.error(f"Could not parse CSV: {_wb_err}. Make sure you uploaded an unmodified Webull export.")

    # ── Voice Memo Logger ──────────────────────────────────────────────────────
    with st.expander("🎙️ Log a Voice Memo / Trade Note", expanded=False):
        st.markdown(
            '<div style="font-size:12px; color:#546e7a; margin-bottom:12px;">'
            'Paste the transcript of your voice memo below. The system will automatically '
            'extract behavioral tags (FOMO, thesis drift, volume conviction, etc.) '
            'and log it to your cognitive profile.'
            '</div>',
            unsafe_allow_html=True,
        )
        _vm_c1, _vm_c2 = st.columns([2, 1])
        with _vm_c1:
            _vm_ticker = st.text_input(
                "Ticker", placeholder="e.g. SOPA",
                key="vm_ticker",
            ).strip().upper()
            _vm_date = st.date_input(
                "Trade Date", value=date.today(), key="vm_date",
            )
        with _vm_c2:
            _vm_entry  = st.number_input("Entry Price ($)", min_value=0.0, step=0.01, format="%.4f", key="vm_entry")
            _vm_exit   = st.number_input("Exit Price ($)",  min_value=0.0, step=0.01, format="%.4f", key="vm_exit")
            _vm_pnl    = st.number_input("P&L (%)",         step=0.01,                format="%.2f", key="vm_pnl")
            _vm_wl     = st.selectbox("Outcome", ["Win", "Loss", "Breakeven"], key="vm_wl")

        _vm_transcript = st.text_area(
            "Paste transcript here",
            height=180,
            placeholder="OK it is April 15 at 12:38 PM east. I'm looking at SOPA...",
            key="vm_transcript",
            label_visibility="collapsed",
        )
        _vm_btn = st.button("🧠 Analyze & Log", type="primary", use_container_width=True, key="vm_log_btn")

        if _vm_btn:
            if not _vm_ticker:
                st.error("Enter a ticker.")
            elif not _vm_transcript.strip():
                st.error("Paste the transcript first.")
            elif _vm_entry <= 0 or _vm_exit <= 0:
                st.error("Enter entry and exit prices.")
            else:
                _vm_uid = st.session_state.get("auth_user_id", "")
                with st.spinner("Extracting behavioral tags…"):
                    _vm_result = log_voice_memo(
                        transcript=_vm_transcript.strip(),
                        ticker=_vm_ticker,
                        trade_date=str(_vm_date),
                        entry_price=_vm_entry,
                        exit_price=_vm_exit,
                        pnl_pct=_vm_pnl,
                        win_loss=_vm_wl,
                        user_id=_vm_uid,
                    )
                if _vm_result.get("saved"):
                    st.success(f"✅ Logged {_vm_ticker} — {len(_vm_result['tags'].get('flags', []))} behavioral tags detected")
                    _vm_tags = _vm_result["tags"].get("flags", [])
                    if _vm_tags:
                        _tag_cols = st.columns(min(len(_vm_tags), 4))
                        for _i, _tag in enumerate(_vm_tags):
                            _tag_cols[_i % 4].markdown(
                                f'<span style="background:#1e3a2f;color:#4caf50;padding:3px 8px;'
                                f'border-radius:4px;font-size:11px;">{_tag.replace("_"," ")}</span>',
                                unsafe_allow_html=True,
                            )
                    st.caption(_vm_result.get("behavioral_summary", ""))
                elif "Duplicate" in str(_vm_result.get("error", "")):
                    st.warning("Already logged — this ticker/date combo is already in your journal.")
                else:
                    st.error(f"Error: {_vm_result.get('error')}")

    st.markdown("---")

    if df.empty:
        st.info("No entries yet. Run an analysis and click **💾 LOG ENTRY** under the chart.")

    # ── Journal entries (only when not empty) ─────────────────────────────────
    if not df.empty:
        import re as _re_j
        # Grade badges + table
        st.markdown("---")
        for _, row in df.iterrows():
            grade = str(row.get("grade", "?"))
            gc = _GRADE_COLORS.get(grade, "#aaaaaa")
            reason = row.get("grade_reason", "")
            ts = row.get("timestamp", "")
            sym = row.get("ticker", "")
            price = row.get("price", "")
            struct = row.get("structure", "")
            tcs_v = row.get("tcs", "")
            rvol_v = row.get("rvol", "")
            notes_v = str(row.get("notes", "") or "")

            # Parse exit price, P&L, shares, and exit timestamp from notes
            _ex_m   = _re_j.search(r"Exit:\s*\$([0-9.]+)", notes_v)
            _pnl_m  = _re_j.search(r"P&L:\s*\$([+-]?[0-9.]+)\s*\(([+-]?[0-9.]+)%\)", notes_v)
            _sh_m   = _re_j.search(r"Shares:\s*([0-9]+)", notes_v)
            # ExitTS: new format; also handle old "Exit: YYYY-MM-DD HH:MM"
            _ets_m  = _re_j.search(r"ExitTS:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[T\s][0-9:]+)", notes_v)
            if not _ets_m:
                _ets_m = _re_j.search(r"Exit:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s[0-9:]+)", notes_v)

            _exit_str   = f"${float(_ex_m.group(1)):.4f}" if _ex_m else ""
            _pnl_val    = float(_pnl_m.group(1)) if _pnl_m else 0.0
            _pnl_pct    = _pnl_m.group(2) if _pnl_m else ""
            _pnl_str    = f"${_pnl_val:+.2f} ({_pnl_pct}%)" if _pnl_m else ""
            _pnl_color  = "#4caf50" if _pnl_val >= 0 else "#ef5350"
            _shares_str = _sh_m.group(1) if _sh_m else ""
            _entry_fmt  = f"${float(price):.4f}" if price not in (None, "", "nan") else "—"

            # Compute hold time from entry → exit timestamps
            _hold_badge = ""
            if _ets_m and ts:
                try:
                    import pandas as _pd_j
                    _entry_dt = _pd_j.to_datetime(str(ts), errors="coerce")
                    _exit_dt  = _pd_j.to_datetime(_ets_m.group(1).strip(), errors="coerce")
                    if _pd_j.notna(_entry_dt) and _pd_j.notna(_exit_dt):
                        _hold_days = (_exit_dt.date() - _entry_dt.date()).days
                        if _hold_days == 0:
                            _hold_mins = max(0, int((_exit_dt - _entry_dt).total_seconds() / 60))
                            _hold_label = f"Intraday · {_hold_mins}m" if _hold_mins > 0 else "Intraday"
                            _hold_color = "#29b6f6"
                        elif _hold_days == 1:
                            _hold_label = "Overnight"
                            _hold_color = "#ffa726"
                        else:
                            _hold_label = f"Multi-day · {_hold_days}d"
                            _hold_color = "#ce93d8"
                        _hold_badge = (
                            f'<span style="background:{_hold_color}22;border:1px solid {_hold_color}66;'
                            f'color:{_hold_color};font-size:10px;font-weight:600;padding:2px 7px;'
                            f'border-radius:10px;margin-left:6px;white-space:nowrap;">'
                            f'{_hold_label}</span>'
                        )
                except Exception:
                    pass

            if _exit_str:
                _price_line = (
                    f'<span style="color:#888;font-size:11px;">Entry</span> '
                    f'<span style="color:#90caf9;">{_entry_fmt}</span>'
                    f'<span style="color:#555;margin:0 5px;">→</span>'
                    f'<span style="color:#888;font-size:11px;">Exit</span> '
                    f'<span style="color:#90caf9;">{_exit_str}</span>'
                )
                if _pnl_str:
                    _price_line += (
                        f'<span style="color:#555;margin:0 6px;">·</span>'
                        f'<span style="color:#888;font-size:11px;">P&amp;L</span> '
                        f'<span style="color:{_pnl_color};font-weight:700;">{_pnl_str}</span>'
                    )
                if _shares_str:
                    _price_line += (
                        f'<span style="color:#666;font-size:11px;margin-left:6px;">{_shares_str} sh</span>'
                    )
            else:
                _price_line = (
                    f'<span style="color:#888;font-size:11px;">Entry</span> '
                    f'<span style="color:#90caf9;">{_entry_fmt}</span>'
                )

            # Strip Webull boilerplate from display notes
            _clean_notes = _re_j.sub(
                r"Webull import\s*\|?\s*|Exit:\s*\$[0-9.]+\s*\|?\s*"
                r"|P&L:\s*\$[+-]?[0-9.]+\s*\([+-]?[0-9.]+%\)\s*\|?\s*"
                r"|Shares:\s*[0-9]+\s*\|?\s*"
                r"|ExitTS:\s*[0-9]{4}-[0-9]{2}-[0-9]{2}[T\s][0-9:]+\s*\|?\s*"
                r"|Exit:\s*[0-9]{4}-[0-9]{2}-[0-9]{2}[^|]*\|?\s*",
                "", notes_v
            ).strip(" |·").strip()

            _j_cols = st.columns([8, 1])
            with _j_cols[0]:
                st.markdown(
                    f'<div style="display:flex;gap:16px;align-items:center;background:#12122288;'
                    f'border:1px solid #2a2a4a;border-radius:10px;padding:12px 18px;margin:8px 0;">'
                    f'<div style="flex-shrink:0;width:52px;height:52px;border-radius:50%;'
                    f'background:{gc}22;border:2.5px solid {gc};display:flex;'
                    f'align-items:center;justify-content:center;'
                    f'font-size:24px;font-weight:900;color:{gc};">{grade}</div>'
                    f'<div style="flex:1;min-width:0;">'
                    f'<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">'
                    f'<span style="font-size:20px;font-weight:800;color:#e0e0e0;">{sym}</span>'
                    f'{_hold_badge}'
                    f'<span style="font-size:11px;color:#666;">{ts}</span>'
                    f'</div>'
                    f'<div style="font-size:13px;margin:3px 0;">{_price_line}</div>'
                    f'<div style="font-size:12px;color:#90caf9;margin:2px 0;">{struct}</div>'
                    f'<div style="font-size:11px;color:#888;">'
                    f'TCS {tcs_v}%  ·  RVOL {rvol_v}x'
                    f'{("  ·  <em>" + _clean_notes + "</em>") if _clean_notes else ""}'
                    f'</div>'
                    f'<div style="font-size:12px;color:{gc};margin-top:4px;">{reason}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            with _j_cols[1]:
                st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
                _ts_str = str(ts)[:10] if ts else ""
                _replay_key = f"journal_replay_{_}_{sym}"
                if st.button("📈 Replay", key=_replay_key, use_container_width=True,
                             help=f"Load {sym} on {_ts_str} in the Main Chart tab"):
                    try:
                        from datetime import date as _date_cls
                        _replay_dt = _date_cls.fromisoformat(_ts_str) if _ts_str else None
                    except Exception:
                        _replay_dt = None
                    st.session_state["_load_ticker"]   = str(sym).upper().strip()
                    if _replay_dt:
                        st.session_state["_replay_date"] = _replay_dt
                    st.success(f"✅ {sym} loaded — switch to Main Chart tab and click Fetch & Analyze")

    if not df.empty:
        # Equity curve — grade average over entries
        st.markdown("---")
        st.markdown("**Grade Discipline Curve**")
        df2 = df.copy()
        df2["grade_score"] = df2["grade"].map(_GRADE_SCORE).fillna(1)
        df2["entry_num"]   = range(1, len(df2) + 1)
        df2["rolling_avg"] = df2["grade_score"].expanding().mean()
    
        import plotly.graph_objects as _go
        fig = _go.Figure()
        fig.add_trace(_go.Scatter(
            x=df2["entry_num"], y=df2["rolling_avg"],
            mode="lines+markers",
            line=dict(color="#00bcd4", width=2.5),
            marker=dict(size=7, color=df2["grade_score"].map(
                {4: "#4caf50", 3: "#26a69a", 2: "#ffa726", 1: "#ef5350"}
            ).fillna("#aaa")),
            name="Grade Average",
            hovertemplate="Entry %{x} — Avg %{y:.2f}<extra></extra>",
        ))
        fig.add_hline(y=3.0, line=dict(color="rgba(76,175,80,0.4)", dash="dot"),
                      annotation_text="B threshold", annotation_font_color="#4caf50")
        fig.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"), height=220,
            xaxis=dict(title="Entry #", gridcolor="#2a2a4a"),
            yaxis=dict(title="Avg Grade (F=1 \u2013 A=4)", gridcolor="#2a2a4a",
                       tickvals=[1, 2, 3, 4], ticktext=["F", "C", "B", "A"],
                       range=[0.5, 4.5]),
            margin=dict(l=10, r=10, t=20, b=40),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    
        # ── Review Trades ────────────────────────────────────────────────────────
        st.markdown("---")
        with st.expander("🔍 Review Trades — Log Actual Outcome", expanded=False):
            st.markdown(
                '<div style="font-size:12px; color:#90caf9; margin-bottom:8px;">'
                'Pick a logged trade, enter your actual exit price and what structure '
                'the day turned out to be, and save the result to the Accuracy Tracker.'
                '</div>', unsafe_allow_html=True,
            )
    
            _STRUCTURES = [
                "Trend Day", "Non-Trend", "Normal", "Normal Variation",
                "Neutral", "Neutral Extreme", "Double Distribution",
            ]
    
            # Build display labels for the selectbox
            _trade_labels = []
            for _, _r in df.iterrows():
                _ts  = str(_r.get("timestamp", ""))[:16]
                _sym = str(_r.get("ticker", "?"))
                _px  = _r.get("price", 0.0)
                _st  = str(_r.get("structure", ""))[:20]
                _trade_labels.append(f"{_ts}  ·  {_sym}  @${_px:.2f}  [{_st}]")
    
            _sel_idx = st.selectbox(
                "Select a trade to review",
                options=range(len(_trade_labels)),
                format_func=lambda i: _trade_labels[i],
                key="review_trade_select",
            )
    
            _sel_row = df.iloc[_sel_idx].to_dict()
            _entry_px = float(_sel_row.get("price", 0.0))
            _pred_struct = str(_sel_row.get("structure", ""))
    
            # Summary card
            _gc = _GRADE_COLORS.get(str(_sel_row.get("grade", "?")), "#aaa")
            st.markdown(
                f'<div style="background:#12122288; border:1px solid #2a2a4a; border-radius:8px; '
                f'padding:10px 16px; margin:8px 0; display:flex; gap:20px; align-items:center;">'
                f'<div style="color:{_gc}; font-weight:900; font-size:20px;">'
                f'{_sel_row.get("grade","?")} Grade</div>'
                f'<div><span style="color:#e0e0e0; font-weight:700;">{_sel_row.get("ticker","")}</span>'
                f'&nbsp;<span style="color:#90caf9;">Entry @ ${_entry_px:.2f}</span></div>'
                f'<div style="color:#888; font-size:11px;">Predicted: '
                f'<span style="color:#ffcc80;">{_pred_struct}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    
            _rc1, _rc2, _rc3 = st.columns([2, 2, 1])
            with _rc1:
                _exit_price = st.number_input(
                    "Actual Exit Price ($)", min_value=0.01, value=float(_entry_px),
                    step=0.01, format="%.4f", key="review_exit_price",
                )
            with _rc2:
                # Pre-select predicted structure if it exists in list
                _def_idx = 0
                for _si, _sl in enumerate(_STRUCTURES):
                    if _strip_emoji(_pred_struct.lower()) in _strip_emoji(_sl.lower()):
                        _def_idx = _si; break
                _actual_struct = st.selectbox(
                    "Actual Market Structure",
                    options=_STRUCTURES,
                    index=_def_idx,
                    key="review_actual_structure",
                )
            with _rc3:
                _direction = st.radio(
                    "Direction", options=["Long", "Short"],
                    key="review_direction", horizontal=False,
                )
    
            _submit_review = st.button(
                "💾 Save Review to Accuracy Tracker",
                use_container_width=True, key="review_submit_btn",
            )
    
            if _submit_review:
                if _exit_price <= 0:
                    st.error("Exit price must be greater than zero.")
                else:
                    with st.spinner("Saving review…"):
                        _res = save_trade_review(
                            journal_row=_sel_row,
                            exit_price=_exit_price,
                            actual_structure=_actual_struct,
                            direction=_direction,
                            user_id=_uid,
                        )
                    if _res.get("error"):
                        st.error(f"Error: {_res['error']}")
                    else:
                        _wl       = _res["win_loss"]
                        _pnl_d    = _res["pnl_dollars"]
                        _pnl_p    = _res["pnl_pct"]
                        _corr_s   = _res["correct_structure"]
                        _wl_color = "#4caf50" if _wl == "Win" else ("#ef5350" if _wl == "Loss" else "#ffa726")
                        _wl_icon  = "✅" if _wl == "Win" else ("❌" if _wl == "Loss" else "➖")
                        _pnl_sign = "+" if _pnl_d >= 0 else ""
                        _struct_badge = (
                            '<span style="color:#4caf50;">✅ Structure Correct</span>'
                            if _corr_s else
                            '<span style="color:#ef5350;">❌ Structure Wrong</span>'
                        )
                        st.markdown(
                            f'<div style="background:#0a0a1a; border:2px solid {_wl_color}; '
                            f'border-radius:10px; padding:16px 22px; margin:10px 0;">'
                            f'<div style="font-size:22px; font-weight:900; color:{_wl_color};">'
                            f'{_wl_icon} {_wl}</div>'
                            f'<div style="font-size:14px; color:#e0e0e0; margin-top:6px;">'
                            f'P&L: <b style="color:{_wl_color};">'
                            f'{_pnl_sign}${_pnl_d:.4f} ({_pnl_sign}{_pnl_p:.2f}%)</b>'
                            f'&nbsp;&nbsp;·&nbsp;&nbsp;{_struct_badge}</div>'
                            f'<div style="font-size:11px; color:#666; margin-top:4px;">'
                            f'Saved to Accuracy Tracker · '
                            f'{_direction} {_sel_row.get("ticker","")} '
                            f'${_entry_px:.4f} → ${_exit_price:.4f}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
    
        # ── Sync from Alpaca ─────────────────────────────────────────────────────
        st.markdown("---")
        with st.expander("📥 Sync from Alpaca — Auto-Import Closed Trades", expanded=False):
            st.markdown(
                '<div style="font-size:12px; color:#90caf9; margin-bottom:10px;">'
                'Pulls your actual filled orders from Alpaca, matches buy+sell pairs '
                'into round-trip trades, calculates real P&amp;L, and saves directly '
                'to the Accuracy Tracker — no manual entry needed.'
                '</div>', unsafe_allow_html=True,
            )
            if not api_key or not secret_key:
                st.warning("Enter your Alpaca API Key and Secret Key in the sidebar first.")
            else:
                _sc1, _sc2, _sc3 = st.columns([2, 1, 1])
                with _sc1:
                    _sync_date = st.date_input(
                        "Trade Date", value=date.today(), key="sync_alpaca_date",
                    )
                with _sc2:
                    _sync_default_idx = 0 if st.session_state.get("_trading_mode", "paper") == "paper" else 1
                    _is_paper = st.radio(
                        "Account", ["Paper", "Live"],
                        index=_sync_default_idx,
                        key="sync_alpaca_mode", horizontal=True,
                    ) == "Paper"
                with _sc3:
                    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    _sync_btn = st.button("🔄 Fetch Orders", use_container_width=True,
                                          key="sync_alpaca_btn")
    
                if _sync_btn:
                    with st.spinner("Connecting to Alpaca…"):
                        _fills, _err = fetch_alpaca_fills(
                            api_key, secret_key,
                            is_paper=_is_paper,
                            trade_date=str(_sync_date),
                        )
                    if _err:
                        st.error(f"Alpaca error: {_err}")
                    elif not _fills:
                        st.info(f"No filled orders found for {_sync_date} "
                                f"({'paper' if _is_paper else 'live'} account).")
                    else:
                        _trips = match_fills_to_roundtrips(_fills)
                        st.session_state["_alpaca_roundtrips"] = _trips
                        st.session_state["_alpaca_date"]       = str(_sync_date)
                        if not _trips:
                            st.info(f"Found {len(_fills)} fills but no complete round-trips "
                                    "(need both a buy and a sell for the same ticker).")
    
                _trips = st.session_state.get("_alpaca_roundtrips", [])
                if _trips:
                    st.markdown(
                        f'<div style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
                        f'letter-spacing:1px; margin:8px 0 4px 0;">'
                        f'Found {len(_trips)} round-trip trade(s) — '
                        f'{st.session_state.get("_alpaca_date","")}</div>',
                        unsafe_allow_html=True,
                    )
                    for _ti, _t in enumerate(_trips):
                        _tw_color = ("#4caf50" if _t["win_loss"] == "Win"
                                     else "#ef5350" if _t["win_loss"] == "Loss"
                                     else "#ffa726")
                        _pnl_sign = "+" if _t["pnl_dollars"] >= 0 else ""
                        st.markdown(
                            f'<div style="background:#0a0a1a; border:1px solid {_tw_color}44; '
                            f'border-radius:8px; padding:10px 16px; margin:6px 0; '
                            f'display:flex; gap:20px; align-items:center; flex-wrap:wrap;">'
                            f'<span style="font-weight:800; color:#e0e0e0; font-size:16px;">'
                            f'{_t["symbol"]}</span>'
                            f'<span style="color:#888; font-size:11px;">Entry '
                            f'<b style="color:#90caf9;">${_t["avg_entry"]:.4f}</b></span>'
                            f'<span style="color:#888; font-size:11px;">Exit '
                            f'<b style="color:#90caf9;">${_t["avg_exit"]:.4f}</b></span>'
                            f'<span style="color:#888; font-size:11px;">Qty '
                            f'<b style="color:#e0e0e0;">{int(_t["qty"])}</b></span>'
                            f'<span style="font-weight:700; color:{_tw_color};">'
                            f'{_t["win_loss"]} &nbsp;'
                            f'{_pnl_sign}${_t["pnl_dollars"]:.2f} '
                            f'({_pnl_sign}{_t["pnl_pct"]:.2f}%)</span>'
                            f'<span style="color:#555; font-size:10px;">{_t["fill_time"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
    
                        # Per-trade save button
                        _imp_key = f"import_alpaca_{_ti}"
                        if st.session_state.get(f"_imported_{_imp_key}"):
                            st.markdown(
                                '<span style="color:#4caf50; font-size:11px;">'
                                '✅ Saved to Accuracy Tracker</span>',
                                unsafe_allow_html=True,
                            )
                        else:
                            _imp_col1, _imp_col2 = st.columns([3, 1])
                            with _imp_col2:
                                if st.button(f"💾 Save", key=_imp_key,
                                             use_container_width=True):
                                    # Find matching journal row for predicted structure
                                    _jrow = {"ticker": _t["symbol"],
                                             "price":  _t["avg_entry"],
                                             "structure": ""}
                                    if not df.empty:
                                        _mask = df["ticker"].str.upper() == _t["symbol"]
                                        _match = df[_mask]
                                        if not _match.empty:
                                            _jrow = _match.iloc[-1].to_dict()
                                    log_accuracy_entry(
                                        symbol      = _t["symbol"],
                                        predicted   = str(_jrow.get("structure", "")),
                                        actual      = "",
                                        compare_key = "alpaca_sync",
                                        entry_price = _t["avg_entry"],
                                        exit_price  = _t["avg_exit"],
                                        mfe         = _t["pnl_dollars"],
                                        user_id     = _uid,
                                    )
                                    st.session_state[f"_imported_{_imp_key}"] = True
                                    st.rerun()
    
                    if st.button("💾 Save ALL to Accuracy Tracker",
                                 use_container_width=True, key="sync_alpaca_save_all"):
                        _saved = 0
                        for _ti2, _t2 in enumerate(_trips):
                            _ak2 = f"_imported_import_alpaca_{_ti2}"
                            if not st.session_state.get(_ak2):
                                _jrow2 = {"ticker": _t2["symbol"], "price": _t2["avg_entry"],
                                          "structure": ""}
                                if not df.empty:
                                    _mask2 = df["ticker"].str.upper() == _t2["symbol"]
                                    _match2 = df[_mask2]
                                    if not _match2.empty:
                                        _jrow2 = _match2.iloc[-1].to_dict()
                                log_accuracy_entry(
                                    symbol      = _t2["symbol"],
                                    predicted   = str(_jrow2.get("structure", "")),
                                    actual      = "",
                                    compare_key = "alpaca_sync",
                                    entry_price = _t2["avg_entry"],
                                    exit_price  = _t2["avg_exit"],
                                    mfe         = _t2["pnl_dollars"],
                                    user_id     = _uid,
                                )
                                st.session_state[f"_imported_import_alpaca_{_ti2}"] = True
                                _saved += 1
                        if _saved > 0:
                            st.success(f"Saved {_saved} trade(s) to Accuracy Tracker.")
                            st.session_state["_alpaca_roundtrips"] = []
                            st.rerun()
                        else:
                            st.info("All trades already saved.")

    # ── End-of-Day Review ─────────────────────────────────────────────────────
    st.markdown("---")
    st.header("📸 End-of-Day Review")
    st.caption("Chart screenshots, trendline notes, and watchlist for tomorrow — all saved per day.")

    # ── Pre-market watchlist prediction panel ─────────────────────────────────
    _pm_preds_df = _cached_load_watchlist_predictions(user_id=_uid, pred_date=date.today())
    if not _pm_preds_df.empty:
        _pm_tickers = _pm_preds_df["ticker"].tolist()
        _pm_verified_count = int(_pm_preds_df["verified"].sum()) if "verified" in _pm_preds_df.columns else 0
        _pm_pending  = len(_pm_tickers) - _pm_verified_count
        _pm_color    = "#29b6f6"

        st.markdown(
            f'<div style="background:#071b2e;border:1px solid #1e3a5f;border-left:4px solid {_pm_color};'
            f'border-radius:8px;padding:12px 18px;margin-bottom:14px;">'
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">'
            f'<span style="font-size:13px;font-weight:700;color:{_pm_color};">📋 PRE-MARKET WATCHLIST</span>'
            f'<span style="background:#0d2a42;border:1px solid {_pm_color};border-radius:10px;'
            f'padding:2px 10px;font-size:11px;color:{_pm_color};">'
            f'{len(_pm_tickers)} ticker{"s" if len(_pm_tickers)!=1 else ""} on watch</span>'
            f'<span style="font-size:11px;color:#555;margin-left:auto;">'
            f'These predictions were saved pre-market and will be auto-verified against '
            f'today\'s EOD structure — they feed brain recalibration.</span></div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;">',
            unsafe_allow_html=True,
        )

        _struct_colors = {
            "Trending Up":       ("#4caf50", "▲"),
            "Trending Down":     ("#ef5350", "▼"),
            "At IB High":        ("#ffa726", "◆"),
            "At IB Low":         ("#29b6f6", "◆"),
            "Inside IB":         ("#9e9e9e", "◼"),
            "Extended Above IB": ("#7c4dff", "▲"),
            "Extended Below IB": ("#ff5252", "▼"),
        }
        _conf_colors = {
            "HIGH":     ("#4caf50", "🟢"),
            "MODERATE": ("#ffa726", "🟡"),
            "LOW":      ("#9e9e9e", "⚪"),
        }
        _has_brief = (
            "entry_zone_low" in _pm_preds_df.columns and
            _pm_preds_df["entry_zone_low"].notna().any()
        )

        if _has_brief:
            # Full setup brief cards — one per row
            st.markdown("</div></div>", unsafe_allow_html=True)
            for _, _pr in _pm_preds_df.iterrows():
                _ptk  = _pr.get("ticker", "")
                _pstr = _pr.get("predicted_structure", "—")
                _pver = bool(_pr.get("verified", False))
                _pcor = str(_pr.get("correct", ""))
                _pclr, _pico = _struct_colors.get(_pstr, ("#888", "●"))
                _conf = str(_pr.get("confidence_label") or "LOW")
                _cclr, _cico = _conf_colors.get(_conf, ("#9e9e9e", "⚪"))
                _elo  = _pr.get("entry_zone_low")
                _ehi  = _pr.get("entry_zone_high")
                _stop = _pr.get("stop_level")
                _tgts = _pr.get("targets") or []
                _trig = str(_pr.get("entry_trigger") or "—")
                _pat  = str(_pr.get("pattern") or "")
                _nl   = _pr.get("pattern_neckline")
                _wrc  = str(_pr.get("win_rate_context") or "No win rate data yet.")
                _wrp  = _pr.get("win_rate_pct")
                _vstamp = ("✅ Correct" if _pver and _pcor == "✅"
                           else "❌ Incorrect" if _pver
                           else "⏳ Unverified")
                _vstamp_clr = "#4caf50" if "Correct" in _vstamp else "#ef5350" if "Incorrect" in _vstamp else "#555"
                _entry_str = (f"${_elo:.4f} – ${_ehi:.4f}"
                              if _elo is not None and _ehi is not None else "—")
                _stop_str  = f"${_stop:.4f}" if _stop is not None else "—"
                _tgt_str   = "  ·  ".join(f"R{i+1} ${t:.4f}" for i, t in enumerate(_tgts[:3]))
                _pat_str   = f"{_pat}  neckline ${_nl:.4f}" if _pat and _nl else (_pat or "")
                _wr_display = f"  ·  {_cico} {_conf} confidence ({_wrp:.0f}%)" if _wrp else f"  ·  {_cico} {_conf} confidence"
                st.markdown(
                    f'<div style="background:#071b2e;border:1px solid #1e3a5f;border-left:4px solid {_pclr};'
                    f'border-radius:8px;padding:12px 16px;margin-bottom:10px;">'
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                    f'<span style="font-size:15px;font-weight:800;color:#fff;">{_ptk}</span>'
                    f'<span style="background:{_pclr}22;border:1px solid {_pclr}66;border-radius:5px;'
                    f'padding:2px 8px;font-size:11px;color:{_pclr};">{_pico} {_pstr}</span>'
                    + (f'<span style="background:#1a1a1a;border:1px solid #333;border-radius:5px;'
                       f'padding:2px 8px;font-size:10px;color:#aaa;">{_pat_str}</span>' if _pat_str else "")
                    + f'<span style="margin-left:auto;font-size:10px;color:{_vstamp_clr};">{_vstamp}</span>'
                    f'</div>'
                    f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Entry Zone</div>'
                    f'<div style="font-size:12px;color:#29b6f6;font-weight:600;">{_entry_str}</div></div>'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Stop</div>'
                    f'<div style="font-size:12px;color:#ef5350;font-weight:600;">{_stop_str}</div></div>'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Targets</div>'
                    f'<div style="font-size:11px;color:#4caf50;font-weight:600;">{_tgt_str or "—"}</div></div></div>'
                    f'<div style="font-size:10px;color:#aaa;margin-bottom:4px;line-height:1.4;">'
                    f'<span style="color:#ffa726;font-weight:600;">Trigger:</span> {_trig}</div>'
                    f'<div style="font-size:10px;color:#7986cb;line-height:1.4;">'
                    f'<span style="font-weight:600;">Win Rate:</span> {_wrc}{_wr_display}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            # Legacy: simple chips (schema not yet migrated)
            _chips_html = ""
            for _, _pr in _pm_preds_df.iterrows():
                _ptk  = _pr.get("ticker", "")
                _pstr = _pr.get("predicted_structure", "—")
                _pver = bool(_pr.get("verified", False))
                _pcor = str(_pr.get("correct", ""))
                _pclr, _pico = _struct_colors.get(_pstr, ("#888", "●"))
                _vstamp = (f' <span style="color:#4caf50;font-size:10px;">✅</span>' if _pver and _pcor == "✅"
                           else f' <span style="color:#ef5350;font-size:10px;">❌</span>' if _pver
                           else "")
                _chips_html += (
                    f'<div style="background:#0d2a42;border:1px solid {_pclr}44;border-radius:6px;'
                    f'padding:5px 12px;font-size:12px;">'
                    f'<span style="font-weight:700;color:#fff;">{_ptk}</span>'
                    f'<span style="color:{_pclr};margin-left:6px;">{_pico} {_pstr}</span>'
                    f'{_vstamp}</div>'
                )
            st.markdown(_chips_html + "</div></div>", unsafe_allow_html=True)

        _pm_col1, _pm_col2 = st.columns([1, 1])
        with _pm_col1:
            if _pm_col1.button(
                f"🔍 Verify EOD Outcomes ({_pm_pending} pending)",
                use_container_width=True,
                key="eod_verify_premarket_btn",
                disabled=(_pm_pending == 0),
            ):
                _vak  = st.session_state.get("api_key", "")
                _vask = st.session_state.get("secret_key", "")
                if not _vak or not _vask:
                    st.warning("Enter your Alpaca API keys in Settings first.")
                else:
                    with st.spinner("Verifying predictions against today's EOD bar data..."):
                        _vr = verify_watchlist_predictions(
                            api_key=_vak, secret_key=_vask,
                            user_id=_uid, pred_date=date.today(),
                        )
                    _vc = _vr.get("correct", 0)
                    _vt = _vr.get("verified", 0)
                    _va = _vr.get("accuracy", 0)
                    if _vt > 0:
                        _vcol = "#4caf50" if _va >= 60 else "#ffa726" if _va >= 40 else "#ef5350"
                        st.success(f"Verified {_vt} predictions — {_vc}/{_vt} correct ({_va:.0f}% accuracy). Brain recalibration queued.")
                    else:
                        st.info(_vr.get("error", "No predictions to verify for today."))
                    st.rerun()
        with _pm_col2:
            if _pm_col2.button(
                "📋 Use These Tickers in EOD Note",
                use_container_width=True,
                key="eod_use_pm_tickers_btn",
            ):
                st.session_state["eod_watch_tickers"] = ", ".join(_pm_tickers)
                st.rerun()
    else:
        st.info(
            "📋 **No pre-market watchlist predictions on file for today.** "
            "Run the Gap Scanner, expand any ticker, and save a prediction — "
            "it will appear here at end of day for one-click verification.",
            icon=None,
        )

    # ── One-time Supabase schema helper ──────────────────────────────────────
    with st.expander("⚙️ First-time Supabase setup (run once in your Supabase SQL editor)", expanded=False):
        st.markdown(
            "If images or multi-ticker entries aren't saving to the cloud, "
            "paste this SQL into your **Supabase → SQL Editor** and click **Run**. "
            "You only need to do this once."
        )
        st.warning(
            "**Run each block separately** in the SQL editor — paste one, click Run, then paste the next."
        )
        st.caption("**Step 1 — Drop old constraint** (run this first on its own)")
        st.code(
            "ALTER TABLE eod_notes DROP CONSTRAINT IF EXISTS eod_notes_user_id_note_date_key;",
            language="sql",
        )
        st.caption("**Step 2 — Add correct constraint** (run after Step 1 succeeds)")
        st.code(
            "ALTER TABLE eod_notes ADD CONSTRAINT eod_notes_unique_ticker\n"
            "    UNIQUE (user_id, note_date, watch_tickers);",
            language="sql",
        )
        st.caption("**Step 3 — Add outcome column**")
        st.code(
            "ALTER TABLE eod_notes ADD COLUMN IF NOT EXISTS outcome JSONB DEFAULT '{}';",
            language="sql",
        )
        st.caption("**Step 4 — User preferences table**")
        st.code(
            "CREATE TABLE IF NOT EXISTS user_preferences (\n"
            "    user_id TEXT PRIMARY KEY,\n"
            "    prefs   JSONB DEFAULT '{}',\n"
            "    updated_at TIMESTAMPTZ DEFAULT NOW()\n"
            ");",
            language="sql",
        )

    # ── Edit-mode prefill: copy pending values into widget keys before render ──
    if st.session_state.get("_eod_prefill_pending"):
        _pf = st.session_state.pop("_eod_prefill_pending")
        st.session_state["eod_note_date"]         = _pf["date"]
        st.session_state["eod_watch_tickers"]     = _pf["ticker"]
        st.session_state["eod_notes_text"]        = _pf["notes"]
        st.session_state["_eod_edit_images"]      = _pf["images"]
        st.session_state["_eod_edit_orig_date"]   = str(_pf["date"])
        st.session_state["_eod_edit_orig_ticker"] = _pf["ticker"]
        # Reset the uploader so stale files from a previous edit don't carry over
        st.session_state["_eod_upload_gen"] = st.session_state.get("_eod_upload_gen", 0) + 1

    # ── Edit-mode banner ──────────────────────────────────────────────────────
    _edit_imgs = st.session_state.get("_eod_edit_images", [])
    if _edit_imgs or st.session_state.get("_eod_edit_active"):
        st.info("✏️ **Edit mode** — modify the fields below and hit Save Review to update this entry. "
                "Existing images are kept unless you clear them.")

    _eod_col1, _eod_col2 = st.columns([1, 2])
    with _eod_col1:
        _eod_date = st.date_input("Review Date", value=date.today(), key="eod_note_date")
    with _eod_col2:
        _eod_watch = st.text_input(
            "👀 Watch Tomorrow",
            placeholder="NVDA, GME, AMC — tickers to monitor at open",
            key="eod_watch_tickers",
        )

    _eod_notes = st.text_area(
        "📝 Notes — what happened today / key levels / thesis for tomorrow",
        height=130,
        placeholder="e.g. NVDA rejected VWAP twice, watching $118.50 reclaim. "
                    "GME broke out of balance — watch for continuation above $22.",
        key="eod_notes_text",
    )

    _upload_gen = st.session_state.get("_eod_upload_gen", 0)
    _eod_uploads = st.file_uploader(
        "📷 Chart Images (up to 5, PNG/JPG) — leave blank to keep existing images",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"eod_image_uploader_{_upload_gen}",
    )

    # Show existing images that will be kept (edit mode)
    if _edit_imgs:
        st.caption(f"📎 {len(_edit_imgs)} existing image(s) attached — uploading new ones replaces them:")
        _prev_cols = st.columns(min(len(_edit_imgs), 3))
        for _pi, _pimg in enumerate(_edit_imgs):
            if _pimg.get("data"):
                _prev_cols[_pi % 3].image(
                    f"data:image/jpeg;base64,{_pimg['data']}",
                    caption=_pimg.get("filename", ""),
                    use_container_width=True,
                )
        if st.button("🗑 Clear existing images", key="eod_clear_imgs"):
            st.session_state["_eod_edit_images"] = []
            st.rerun()

    _eod_save_col, _eod_load_col = st.columns(2)
    if _eod_save_col.button("💾 Save Review", use_container_width=True, key="eod_save_btn"):
        # New uploads override existing; if no new uploads, keep existing
        if _eod_uploads:
            _imgs_payload = []
            for _f in _eod_uploads[:5]:
                try:
                    _b64 = _compress_image_b64(_f.read())
                    _imgs_payload.append({"filename": _f.name, "data": _b64, "caption": ""})
                except Exception as _img_err:
                    import traceback as _tb
                    print(f"Image compress error for {_f.name}: {_img_err}\n{_tb.format_exc()}")
            print(f"EOD save: {len(_eod_uploads)} uploads → {len(_imgs_payload)} compressed")
        else:
            _imgs_payload = st.session_state.get("_eod_edit_images", [])
            print(f"EOD save: no new uploads, using {len(_imgs_payload)} existing images from session")

        # If ticker or date changed during edit, delete the old entry first
        _orig_date   = st.session_state.get("_eod_edit_orig_date", "")
        _orig_ticker = st.session_state.get("_eod_edit_orig_ticker", "")
        if _orig_date:
            _new_ticker_str = _eod_watch.strip() if _eod_watch else ""
            if (_orig_ticker.strip() != _new_ticker_str
                    or _orig_date != str(_eod_date)):
                delete_eod_note(_orig_date, _orig_ticker, user_id=_uid)

        _ok, _src = save_eod_note(
            note_date     = _eod_date,
            notes         = _eod_notes,
            watch_tickers = _eod_watch,
            images_b64    = _imgs_payload,
            user_id       = _uid,
        )
        if _ok:
            for _k in ("_eod_edit_images", "_eod_edit_active",
                       "_eod_edit_orig_date", "_eod_edit_orig_ticker"):
                st.session_state.pop(_k, None)
            # Bump upload gen so the file uploader resets completely
            st.session_state["_eod_upload_gen"] = st.session_state.get("_eod_upload_gen", 0) + 1
            if _src == "supabase":
                st.success(f"✅ Review saved for {_eod_date}")
            else:
                st.warning("⚠️ Saved locally — will sync to cloud on next load when Supabase is available.")
            # Auto-reload so changes appear immediately without manual button click
            _raw_notes = _cached_load_eod_notes(user_id=_uid, limit=100)
            st.session_state["_eod_notes_loaded"] = enrich_eod_from_journal(_raw_notes, df)
            st.rerun()
        else:
            st.error("❌ Save failed completely. Contact support.")

    if _eod_load_col.button("📂 Load Past Reviews", use_container_width=True, key="eod_load_btn"):
        _raw_notes = _cached_load_eod_notes(user_id=_uid, limit=100)
        st.session_state["_eod_notes_loaded"] = enrich_eod_from_journal(_raw_notes, df)

    # ── Display loaded notes ──────────────────────────────────────────────────
    import re as _re
    _loaded_notes = st.session_state.get("_eod_notes_loaded")
    if _loaded_notes is not None:
        if not _loaded_notes:
            st.info("No reviews found. Any reviews saved before today's database setup weren't persisted — re-enter them above and hit Save Review.")
        else:
            # ── Accuracy summary ──────────────────────────────────────────────
            _verified_notes = [_n for _n in _loaded_notes if _n.get("outcome")]
            if _verified_notes:
                _total_hits = 0
                _total_checks = 0
                for _vn in _verified_notes:
                    import json as _j
                    _oc = _vn["outcome"]
                    if isinstance(_oc, str):
                        try: _oc = _j.loads(_oc)
                        except: _oc = {}
                    for _tk, _tr in _oc.items():
                        if isinstance(_tr, dict) and "above_hit" in _tr:
                            if _tr.get("above_hit") is not None:
                                _total_checks += 1
                                if _tr["above_hit"]: _total_hits += 1
                        if isinstance(_tr, dict) and "below_hit" in _tr:
                            if _tr.get("below_hit") is not None:
                                _total_checks += 1
                                if _tr["below_hit"]: _total_hits += 1
                _hit_pct = (_total_hits / _total_checks * 100) if _total_checks else 0
                _hit_color = "#4caf50" if _hit_pct >= 60 else "#ffa726" if _hit_pct >= 40 else "#ef5350"
                st.markdown(
                    f'<div style="background:#12122299;border:1px solid #2a2a4a;border-radius:8px;'
                    f'padding:12px 18px;margin-bottom:14px;display:flex;gap:32px;align-items:center;">'
                    f'<div><span style="font-size:11px;color:#888;text-transform:uppercase;'
                    f'letter-spacing:1px;">Level Hit Rate</span><br>'
                    f'<span style="font-size:26px;font-weight:800;color:{_hit_color};">'
                    f'{_hit_pct:.0f}%</span>'
                    f'<span style="font-size:12px;color:#666;margin-left:6px;">'
                    f'{_total_hits}/{_total_checks} levels touched</span></div>'
                    f'<div><span style="font-size:11px;color:#888;text-transform:uppercase;'
                    f'letter-spacing:1px;">Reviews Verified</span><br>'
                    f'<span style="font-size:22px;font-weight:700;color:#90caf9;">'
                    f'{len(_verified_notes)}</span>'
                    f'<span style="font-size:12px;color:#666;"> / {len(_loaded_notes)}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            for _ni_idx, _n in enumerate(_loaded_notes):
                _nd  = _n.get("note_date", "")
                _nw  = _n.get("watch_tickers", "")
                _nt  = _n.get("notes", "")
                _nim = _n.get("images", [])
                _noc = _n.get("outcome", {})
                if isinstance(_noc, str):
                    try:
                        import json as _j2; _noc = _j2.loads(_noc)
                    except: _noc = {}
                _has_outcome = bool(_noc)

                # Build header status badge
                _outcome_badge = ""
                if _has_outcome:
                    _hits = sum(1 for _r in _noc.values()
                                if isinstance(_r, dict) and (_r.get("above_hit") or _r.get("below_hit")))
                    _outcome_badge = f"  ✅ Verified ({_hits} hit)" if _hits else "  🔍 Verified"

                with st.expander(
                    f"📅 {_nd}" + (f"  ·  👀 {_nw}" if _nw else "") + _outcome_badge,
                    expanded=True
                ):
                    # ── Journal merge badge (shown when journal data found) ────
                    _jctx = _n.get("_journal_ctx", {})
                    if _jctx:
                        _jbadges = []
                        for _jtk, _jd in _jctx.items():
                            _parts = []
                            if _jd.get("tcs") is not None:
                                _parts.append(f"TCS {float(_jd['tcs']):.0f}")
                            if _jd.get("rvol") is not None:
                                _parts.append(f"RVOL {float(_jd['rvol']):.1f}x")
                            if _jd.get("structure"):
                                _parts.append(str(_jd["structure"]))
                            if _jd.get("grade"):
                                _gc = {"A":"#4caf50","B":"#26a69a","C":"#ffa726","F":"#ef5350"}.get(str(_jd["grade"]),"#888")
                                _parts.append(f'<span style="color:{_gc};font-weight:800;">Grade {_jd["grade"]}</span>')
                            if _parts:
                                _jbadges.append(
                                    f'<span style="font-weight:700;color:#90caf9;">{_jtk}</span>'
                                    f' &nbsp;{"  ·  ".join(_parts)}'
                                )
                        if _jbadges:
                            st.markdown(
                                f'<div style="background:#0d1b2a;border:1px solid #1e3a5f;'
                                f'border-left:3px solid #29b6f6;border-radius:6px;'
                                f'padding:7px 14px;margin-bottom:10px;font-size:12px;color:#aaa;">'
                                f'<span style="font-size:10px;text-transform:uppercase;'
                                f'letter-spacing:1px;color:#555;">📊 Journal data auto-merged</span><br>'
                                + "<br>".join(_jbadges)
                                + "</div>",
                                unsafe_allow_html=True,
                            )
                    if _nw:
                        _above = _re.findall(r'[Pp]rice\s+[Aa]bove\s+([\$]?[\d\.]+)', _nt)
                        _below = _re.findall(r'[Pp]rice\s+[Bb]elow\s+([\$]?[\d\.]+)', _nt)
                        _price_chips = "".join([
                            f'<span style="display:inline-block;background:#1e1e3a;border:1px solid #5c6bc0;'
                            f'color:#9fa8da;font-size:11px;font-weight:600;padding:2px 8px;'
                            f'border-radius:10px;margin-left:6px;">Above {v}</span>'
                            for v in _above
                        ] + [
                            f'<span style="display:inline-block;background:#1e1e3a;border:1px solid #5c6bc0;'
                            f'color:#9fa8da;font-size:11px;font-weight:600;padding:2px 8px;'
                            f'border-radius:10px;margin-left:6px;">Below {v}</span>'
                            for v in _below
                        ])
                        st.markdown(
                            f'<div style="background:#12122299;border-left:3px solid #90caf9;'
                            f'padding:8px 14px;border-radius:4px;margin-bottom:10px;">'
                            f'<span style="font-size:11px;color:#888;text-transform:uppercase;'
                            f'letter-spacing:1px;">Watch Tomorrow</span><br>'
                            f'<span style="color:#90caf9;font-weight:700;">{_nw}</span>'
                            f'{_price_chips}</div>',
                            unsafe_allow_html=True,
                        )
                    if _nt:
                        st.markdown(
                            f'<div style="white-space:pre-wrap;font-size:13px;'
                            f'color:#e0e0e0;line-height:1.6;">{_nt}</div>',
                            unsafe_allow_html=True,
                        )
                    if _nim:
                        _img_cols = st.columns(min(len(_nim), 3))
                        for _ic, _img in enumerate(_nim):
                            _img_data = _img.get("data", "")
                            if _img_data:
                                _col = _img_cols[_ic % 3]
                                _thumb, _ = _col.columns([1, 1])
                                _thumb.image(
                                    f"data:image/jpeg;base64,{_img_data}",
                                    caption=_img.get("filename", ""),
                                    use_container_width=True,
                                )
                                import base64 as _b64mod
                                _thumb.download_button(
                                    label="⬇ Download",
                                    data=_b64mod.b64decode(_img_data),
                                    file_name=_img.get("filename", f"chart_{_ic+1}.jpg"),
                                    mime="image/jpeg",
                                    key=f"dl_img_{_ni_idx}_{_ic}",
                                    use_container_width=True,
                                )

                    # ── Outcome verification panel ────────────────────────────
                    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
                    _vkey = f"eod_verify_{_nd}_{_nw.replace(' ','_')}_{_ni_idx}"
                    # Parse individual tickers for the selector
                    _ticker_list = [t.strip().upper() for t in
                                    _re.split(r"[,\s]+", _nw) if t.strip()] if _nw else []
                    _sel_tickers = st.multiselect(
                        "Select tickers to verify",
                        options=_ticker_list,
                        default=_ticker_list,
                        key=f"sel_{_vkey}",
                        label_visibility="collapsed",
                        placeholder="Choose tickers…",
                    ) if _ticker_list else []
                    _vcol1, _vcol2 = st.columns([1, 1])
                    if _vcol1.button("📊 Verify Next Day", key=f"btn_{_vkey}",
                                     use_container_width=True,
                                     help="Fetch next trading day's actual price action"):
                        if not _sel_tickers:
                            st.warning("Select at least one ticker above.")
                        else:
                            _sel_str = ", ".join(_sel_tickers)
                            with st.spinner(f"Fetching next-day data for {_sel_str}…"):
                                _vresult = verify_eod_predictions(
                                    _nd, _sel_str, _nt,
                                    st.session_state.get("alpaca_key", ""),
                                    st.session_state.get("alpaca_secret", ""),
                                )
                            st.session_state[_vkey] = _vresult

                    # Show stored outcome OR freshly fetched result
                    _show_outcome = st.session_state.get(_vkey) or (_noc if _noc else None)
                    if _show_outcome and isinstance(_show_outcome, dict):
                        for _vticker, _vr in _show_outcome.items():
                            if not isinstance(_vr, dict): continue
                            if _vr.get("no_data") or _vr.get("error"):
                                st.warning(f"**{_vticker}**: No data for {_vr.get('next_date','?')} — market closed or pre-market.")
                                continue
                            _v_nd   = _vr.get("next_date", "")
                            _v_h    = _vr.get("high", 0)
                            _v_l    = _vr.get("low", 0)
                            _v_o    = _vr.get("open", 0)
                            _v_c    = _vr.get("close", 0)
                            _v_la   = _vr.get("levels_above", [])
                            _v_lb   = _vr.get("levels_below", [])
                            _v_ah   = _vr.get("above_hit")
                            _v_bh   = _vr.get("below_hit")
                            # Build level result chips
                            _lchips = ""
                            for _lv in _v_la:
                                _hit = _v_h >= _lv
                                _c = "#4caf50" if _hit else "#ef5350"
                                _lchips += (f'<span style="display:inline-block;background:{_c}22;'
                                            f'border:1px solid {_c};color:{_c};font-size:11px;'
                                            f'font-weight:600;padding:2px 8px;border-radius:10px;'
                                            f'margin:2px 4px;">Above {_lv} {"✓" if _hit else "✗"}</span>')
                            for _lv in _v_lb:
                                _hit = _v_l <= _lv
                                _c = "#4caf50" if _hit else "#ef5350"
                                _lchips += (f'<span style="display:inline-block;background:{_c}22;'
                                            f'border:1px solid {_c};color:{_c};font-size:11px;'
                                            f'font-weight:600;padding:2px 8px;border-radius:10px;'
                                            f'margin:2px 4px;">Below {_lv} {"✓" if _hit else "✗"}</span>')
                            st.markdown(
                                f'<div style="background:#0d1117;border:1px solid #2a2a4a;'
                                f'border-radius:8px;padding:10px 14px;margin:6px 0;">'
                                f'<span style="font-size:14px;font-weight:700;color:#e0e0e0;">'
                                f'{_vticker}</span>'
                                f'<span style="font-size:11px;color:#666;margin-left:8px;">'
                                f'{_v_nd}</span><br>'
                                f'<span style="font-size:12px;color:#aaa;">'
                                f'O {_v_o}  H <b style="color:#4caf50">{_v_h}</b>  '
                                f'L <b style="color:#ef5350">{_v_l}</b>  C {_v_c}</span><br>'
                                f'<div style="margin-top:6px;">{_lchips}</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        # Save outcome button
                        if _vcol2.button("💾 Save Outcome", key=f"save_{_vkey}",
                                         use_container_width=True):
                            _saved_oc = save_eod_outcome(_nd, _show_outcome, user_id=_uid)
                            if _saved_oc:
                                # ── Feed predictive probability engine ────────────
                                try:
                                    _oc_hits = sum(
                                        1 for _rr in _show_outcome.values()
                                        if isinstance(_rr, dict)
                                        and (_rr.get("above_hit") or _rr.get("below_hit"))
                                    )
                                    _oc_win = _oc_hits > 0
                                    # Pick first ticker from the watch list
                                    _first_tk = (str(_nw or "").split(",")[0].strip().upper()
                                                 if _nw else "")
                                    if _first_tk and _uid:
                                        log_signal_outcome(
                                            user_id=_uid,
                                            ticker=_first_tk,
                                            trade_date=_nd,
                                            outcome_win=_oc_win,
                                            outcome_pct=0.0,
                                        )
                                except Exception:
                                    pass
                                st.success("Outcome saved — contributes to your hit rate!")
                                st.session_state["_eod_notes_loaded"] = None
                                st.rerun()
                            else:
                                st.warning("Save failed — check Supabase connection.")

                    # ── Edit this entry ────────────────────────────────────────
                    st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)
                    if st.button("✏️ Edit this entry", key=f"edit_entry_{_ni_idx}",
                                 use_container_width=False,
                                 help="Load this review into the form above to edit it"):
                        import datetime as _dtmod
                        try:
                            _edit_date = _dtmod.date.fromisoformat(str(_nd))
                        except Exception:
                            _edit_date = date.today()
                        st.session_state["_eod_prefill_pending"] = {
                            "date":   _edit_date,
                            "ticker": _nw or "",
                            "notes":  _nt or "",
                            "images": _nim or [],
                        }
                        st.session_state["_eod_edit_active"] = True
                        st.rerun()

    # ── Supabase setup SQL ────────────────────────────────────────────────────
    with st.expander("⚙️ First-time setup — create eod_notes table", expanded=False):
        st.code(
            "CREATE TABLE IF NOT EXISTS eod_notes (\n"
            "  id           BIGSERIAL PRIMARY KEY,\n"
            "  user_id      TEXT,\n"
            "  note_date    DATE,\n"
            "  notes        TEXT DEFAULT '',\n"
            "  watch_tickers TEXT DEFAULT '',\n"
            "  images       JSONB DEFAULT '[]',\n"
            "  updated_at   TIMESTAMPTZ DEFAULT NOW(),\n"
            "  UNIQUE(user_id, note_date)\n"
            ");",
            language="sql",
        )
        st.caption("Run this once in your Supabase SQL editor.")


# ══════════════════════════════════════════════════════════════════════════════
# CHART & RENDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, title,
                target_zones=None, position=None):
    # ── Reindex to a full 9:30–16:00 minute grid so the chart ALWAYS
    #    anchors at 9:30 ET, even for thinly-traded small-caps whose first
    #    real trade doesn't occur until 9:31 or later.
    def _et_label(ts):
        try:
            return pd.Timestamp(ts).tz_convert(EASTERN).strftime("%H:%M ET")
        except Exception:
            return str(ts)[-8:]

    # Keep a handle to the original (real) bars before we expand the index
    _real_df = df.copy()
    _current_price = float(_real_df["close"].dropna().iloc[-1]) if not _real_df.empty else None

    try:
        _first = df.index[0]
        _last_real = df.index[-1]
        _d = _first.date()
        _open_et  = EASTERN.localize(datetime(_d.year, _d.month, _d.day,  9, 30))
        _close_et = EASTERN.localize(datetime(_d.year, _d.month, _d.day, 16,  0))
        _grid_end = min(_last_real, _close_et)
        _full_idx = pd.date_range(_open_et, _grid_end, freq="1min")
        df = df.reindex(_full_idx)
    except Exception:
        pass

    x_labels = [_et_label(ts) for ts in df.index]
    x0, x1   = x_labels[0], x_labels[-1]

    # ── Value Area (70 % of session volume) ──────────────────────────────────
    val_price, vah_price = _compute_value_area(bin_centers, vap)

    fig = make_subplots(rows=1, cols=2, column_widths=[0.75, 0.25],
                        shared_yaxes=True, horizontal_spacing=0.01)

    # ── Candlestick ───────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x_labels, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
        showlegend=False,
    ), row=1, col=1)

    # ── Value Area shaded band on price chart (very subtle) ───────────────────
    if val_price is not None and vah_price is not None:
        fig.add_hrect(
            y0=val_price, y1=vah_price,
            fillcolor="rgba(92, 107, 192, 0.06)",
            line_width=0,
            row=1, col=1,
        )

    # ── Key Level lines — all with right-edge price labels ────────────────────
    # POC — gold solid, thickest
    fig.add_trace(go.Scatter(
        x=[x0, x1], y=[poc_price, poc_price],
        mode="lines+text",
        name="POC",
        line=dict(color="rgba(255,215,0,1)", width=2.5),
        text=["", f"  POC  ${poc_price:.2f}"],
        textposition="middle right",
        textfont=dict(color="rgba(255,215,0,0.95)", size=11, family="monospace"),
        legendrank=10,
    ), row=1, col=1)

    # IB High — solid green
    if ib_high is not None and ib_low is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[ib_high, ib_high],
            mode="lines+text",
            name="IB High",
            line=dict(color="#00e676", width=2.0),
            text=["", f"  IB Hi ${ib_high:.2f}"],
            textposition="middle right",
            textfont=dict(color="#00e676", size=11, family="monospace"),
            legendrank=20,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[ib_low, ib_low],
            mode="lines+text",
            name="IB Low",
            line=dict(color="#ff5252", width=2.0),
            text=["", f"  IB Lo ${ib_low:.2f}"],
            textposition="middle right",
            textfont=dict(color="#ff5252", size=11, family="monospace"),
            legendrank=30,
        ), row=1, col=1)

    # VAH / VAL — thin blue dashed, clearly labelled
    if vah_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[vah_price, vah_price],
            mode="lines+text",
            name="VAH",
            line=dict(color="#64b5f6", width=1.2, dash="dot"),
            text=["", f"  VAH  ${vah_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#64b5f6", size=10, family="monospace"),
            legendrank=40,
        ), row=1, col=1)
    if val_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[val_price, val_price],
            mode="lines+text",
            name="VAL",
            line=dict(color="#64b5f6", width=1.2, dash="dot"),
            text=["", f"  VAL  ${val_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#64b5f6", size=10, family="monospace"),
            legendrank=50,
        ), row=1, col=1)

    # Current Price — white dotted
    if _current_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[_current_price, _current_price],
            mode="lines+text",
            name="Last Price",
            line=dict(color="rgba(220,220,220,0.8)", width=1.0, dash="dot"),
            text=["", f"  ▶ ${_current_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#e0e0e0", size=11, family="monospace"),
            legendrank=60,
        ), row=1, col=1)

    # ── VWAP line — bright orange dashed, with right-edge label ──────────────
    if "vwap" in _real_df.columns:
        try:
            # Reindex VWAP onto the full minute grid (NaN gaps stay NaN — no fill)
            _vwap_series = _real_df["vwap"].reindex(df.index)
            _vwap_vals   = _vwap_series.tolist()
            _vwap_last   = _vwap_series.dropna().iloc[-1] if not _vwap_series.dropna().empty else None
            # Label only at the last real bar; blanks elsewhere keep the line clean
            _vwap_text   = [""] * len(x_labels)
            if _vwap_last is not None:
                _vwap_text[-1] = f"  VWAP ${_vwap_last:.2f}"
            fig.add_trace(go.Scatter(
                x=x_labels,
                y=_vwap_vals,
                mode="lines+text",
                name="VWAP",
                line=dict(color="#ce93d8", width=1.6),
                text=_vwap_text,
                textposition="middle right",
                textfont=dict(color="#ce93d8", size=11, family="monospace"),
                connectgaps=False,
                legendrank=55,
            ), row=1, col=1)
        except Exception:
            pass

    # ── Dynamic Target Zone overlay ───────────────────────────────────────────
    lvn_idx_to_highlight = None
    if target_zones:
        for tz in target_zones:
            tp = tz["price"]
            tc = tz["color"]
            tl = tz["label"]
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[tp, tp], mode="lines+text",
                name=tl,
                line=dict(color=tc, width=1.4, dash="dot"),
                text=["", f"  {tl}"],
                textposition="top right",
                textfont=dict(color=tc, size=10),
                showlegend=False,
            ), row=1, col=1)
            if tz["type"] == "trend_extension":
                band = tp * 0.005
                fig.add_shape(
                    type="rect", xref="paper", x0=0, x1=0.75,
                    y0=tp - band, y1=tp + band,
                    fillcolor=tc + "28",
                    line=dict(width=0),
                    row=1, col=1,
                )
            if tz["type"] == "gap_fill" and "lvn_idx" in tz:
                lvn_idx_to_highlight = tz["lvn_idx"]

    # ── Volume Profile — intensity-based gradient coloring ────────────────────
    bw = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0.01
    max_vap = float(np.max(vap)) if len(vap) > 0 and np.max(vap) > 0 else 1.0
    colors = []
    for i, (p, v) in enumerate(zip(bin_centers, vap)):
        norm = v / max_vap
        is_poc = abs(p - poc_price) < bw * 0.5
        is_lvn = (lvn_idx_to_highlight is not None and i == lvn_idx_to_highlight)
        in_va  = (val_price is not None and vah_price is not None
                  and val_price <= p <= vah_price)

        if is_poc:
            colors.append("rgba(255,215,0,1.0)")         # bright gold
        elif is_lvn:
            colors.append("rgba(255,235,59,0.88)")       # yellow LVN marker
        elif norm > 0.55:
            # High Volume Node — amber → deep orange
            g = int(165 - (norm - 0.55) / 0.45 * 90)
            a = min(1.0, 0.80 + norm * 0.20)
            colors.append(f"rgba(255,{max(75,g)},10,{a:.2f})")
        elif in_va:
            # Inside Value Area — blue-indigo, brighter with more volume
            r = int(80 + norm * 80)
            g = int(120 + norm * 50)
            b = int(210 - norm * 30)
            a = 0.60 + norm * 0.30
            colors.append(f"rgba({r},{g},{b},{a:.2f})")
        else:
            # Outside Value Area — muted slate, barely visible
            b_ch = int(115 + norm * 30)
            a = 0.28 + norm * 0.30
            colors.append(f"rgba(55,70,{b_ch},{a:.2f})")

    fig.add_trace(go.Bar(
        x=vap, y=bin_centers, orientation="h",
        name="Vol Profile", marker_color=colors, opacity=0.95,
        showlegend=False,
    ), row=1, col=2)

    # ── POC tick on the volume profile panel ──────────────────────────────────
    fig.add_trace(go.Scatter(
        x=[max_vap * 1.02], y=[poc_price],
        mode="text",
        text=["◀ POC"],
        textfont=dict(color="rgba(255,215,0,0.85)", size=10, family="monospace"),
        showlegend=False,
    ), row=1, col=2)

    # ── Position overlay ──────────────────────────────────────────────────────
    if position and position.get("in"):
        avg_entry  = position["avg_entry"]
        peak_price = position["peak_price"]
        price_now  = float(df["close"].iloc[-1])
        shares     = position.get("shares", 0)
        pnl_pct    = (price_now - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0
        pnl_dol    = (price_now - avg_entry) * shares if shares > 0 else 0
        pnl_color  = "#4caf50" if pnl_pct >= 0 else "#ef5350"

        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[avg_entry, avg_entry], mode="lines+text",
            name=f"Entry ${avg_entry:.2f}",
            line=dict(color="#ffffff", width=2.0, dash="solid"),
            text=["", f"  📍 ENTRY ${avg_entry:.2f}"],
            textposition="top right",
            textfont=dict(color="#ffffff", size=12),
            showlegend=False,
        ), row=1, col=1)

        if peak_price > avg_entry:
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[peak_price, peak_price], mode="lines+text",
                name=f"MFE ${peak_price:.2f}",
                line=dict(color="#00bcd4", width=1.5, dash="dash"),
                text=["", f"  ⬆ MFE ${peak_price:.2f}"],
                textposition="top right",
                textfont=dict(color="#00bcd4", size=11),
                showlegend=False,
            ), row=1, col=1)

        pnl_txt = (f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%"
                   f"  (${pnl_dol:+.0f})" if shares > 0 else
                   f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%")
        fig.add_annotation(
            xref="paper", yref="y",
            x=0.74, y=avg_entry,
            text=f"<b>{pnl_txt}</b>",
            showarrow=False,
            font=dict(color=pnl_color, size=13),
            bgcolor=pnl_color + "22",
            bordercolor=pnl_color,
            borderwidth=1,
            borderpad=4,
        )

    # ── Chart layout ──────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color="#e0e0e0")),
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
        font=dict(color="#c0c0d8"),
        height=660,
        xaxis=dict(
            rangeslider=dict(visible=False),
            gridcolor="#1c2040",
            showgrid=True,
            type="category",
        ),
        yaxis=dict(
            gridcolor="#1c2040",
            showgrid=True,
            tickformat=".2f",
            tickfont=dict(family="monospace", size=11, color="#8888aa"),
        ),
        xaxis2=dict(
            gridcolor="#1c2040",
            showgrid=False,
            title="",
            showticklabels=False,
        ),
        # ── Clean legend box — key levels only ────────────────────────────────
        legend=dict(
            bgcolor="rgba(8, 12, 40, 0.88)",
            bordercolor="rgba(80, 90, 160, 0.6)",
            borderwidth=1,
            font=dict(color="#c8c8e0", size=11, family="monospace"),
            x=0.01, y=0.99,
            xanchor="left", yanchor="top",
            title=dict(
                text="KEY LEVELS",
                font=dict(size=9, color="#5c6bc0"),
            ),
            tracegroupgap=1,
            itemsizing="constant",
        ),
        margin=dict(l=10, r=130, t=55, b=40),
    )
    fig.update_xaxes(nticks=16, tickangle=-45, row=1, col=1)
    return fig


def render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=False, sector_bonus=0.0, insight=None):
    top3 = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
    prob_pills = "".join(
        f'<span style="display:inline-block; background:{color}33; border:1px solid {color}66; '
        f'border-radius:4px; padding:2px 8px; margin:2px 4px; font-size:13px; color:#eee;">'
        f'<b>{n}</b> {p}%</span>'
        for n, p in top3
    )

    # ── TCS gauge colour logic ─────────────────────────────────────────────────
    if is_runner:
        # Gold → Electric Blue gradient for MULTI-DAY RUNNER / STOCK IN PLAY
        gauge_fill = "linear-gradient(90deg,#FFD700,#00BFFF)"
        gauge_color = "#FFD700"
        badge = ('<span style="background:#FFD70033; border:1px solid #FFD700; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#FFD700; '
                 'text-transform:uppercase; letter-spacing:1px; '
                 'box-shadow:0 0 8px #FFD70066;">⚡ RUNNER MODE</span>')
    elif tcs >= 70:
        gauge_fill = f"linear-gradient(90deg,#4caf5099,#4caf50)"
        gauge_color = "#4caf50"
        badge = ('<span style="background:#4caf5033; border:1px solid #4caf50; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#4caf50; '
                 'text-transform:uppercase; letter-spacing:1px;">🔥 HIGH CONVICTION</span>')
    elif tcs <= 30:
        gauge_fill = f"linear-gradient(90deg,#ef535099,#ef5350)"
        gauge_color = "#ef5350"
        badge = ('<span style="background:#ef535033; border:1px solid #ef5350; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#ef5350; '
                 'text-transform:uppercase; letter-spacing:1px;">⚠ CHOP RISK</span>')
    else:
        gauge_fill = f"linear-gradient(90deg,#ffa72699,#ffa726)"
        gauge_color = "#ffa726"
        badge = ""

    # Sector tailwind badge
    sector_badge = ""
    if sector_bonus > 0:
        sector_badge = ('<span style="background:#26a69a33; border:1px solid #26a69a; '
                        'border-radius:4px; padding:2px 10px; font-size:12px; font-weight:700; '
                        'color:#26a69a; text-transform:uppercase; letter-spacing:1px; '
                        'margin-left:6px;">🌊 SECTOR TAILWIND +10</span>')

    tcs_label = f"{tcs:.0f}%"
    if sector_bonus > 0:
        tcs_label += f" (+{sector_bonus:.0f} sector)"

    tcs_bar = f"""
    <div style="margin-top:10px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; white-space:nowrap;">
            Trend Confidence
        </span>
        <div style="flex:1; min-width:120px; max-width:220px; background:#2a2a4a;
                    border-radius:6px; height:12px; overflow:hidden;">
            <div style="width:{min(tcs,100)}%; background:{gauge_fill};
                        height:100%; border-radius:6px; transition:width 0.4s;"></div>
        </div>
        <span style="font-size:18px; font-weight:800; color:{gauge_color}; min-width:44px;">{tcs_label}</span>
        {badge}{sector_badge}
    </div>
    """

    # Runner glow border
    glow = f"box-shadow:0 0 18px {gauge_color}55;" if is_runner else ""

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{color}22,{color}0a);
                border-left:5px solid {color}; border-radius:8px;
                padding:14px 22px; margin:10px 0 4px 0; {glow}">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
            <div>
                <div style="font-size:26px; font-weight:800; color:{color}; letter-spacing:0.5px;">{label}</div>
                <div style="font-size:13px; color:#cccccc; margin-top:4px;">{detail}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:11px; color:#888; text-transform:uppercase;
                            letter-spacing:1px; margin-bottom:4px;">Structure Probability</div>
                <div>{prob_pills}</div>
            </div>
        </div>
        {tcs_bar}
    </div>
    """, unsafe_allow_html=True)

    # Key Insights box — separate call so Streamlit's markdown parser doesn't escape inner HTML
    if insight:
        st.markdown(
            f'<div style="margin-top:6px; background:#1a2744; border-left:3px solid {color}99;'
            f' border-radius:5px; padding:9px 14px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase;'
            f' letter-spacing:1px; font-weight:600;">KEY INSIGHTS</span><br>'
            f'<span style="font-size:13px; color:#d0d8f0; line-height:1.55;">{insight}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_rvol_widget(rvol_val, label_str, label_color, is_runner):
    """Show the RVOL reading with appropriate colour and optional glow for runner stocks."""
    if rvol_val is None:
        return
    display_label = label_str if label_str else f"RVOL {rvol_val:.1f}×  — Normal Activity"
    if label_str is None:
        label_color = "#aaaaaa"

    runner_style = ""
    if is_runner:
        runner_style = ("box-shadow:0 0 16px #FFD70077; "
                        "border-color:#FFD700 !important; "
                        "animation:rvol-pulse 1.8s ease-in-out infinite;")

    st.markdown(f"""
    <style>
    @keyframes rvol-pulse {{
        0%   {{ box-shadow: 0 0 8px #FFD70044; }}
        50%  {{ box-shadow: 0 0 22px #FFD700cc; }}
        100% {{ box-shadow: 0 0 8px #FFD70044; }}
    }}
    </style>
    <div style="display:inline-flex; align-items:center; background:{label_color}11;
                border:2px solid {label_color}77; border-radius:8px;
                padding:8px 20px; margin:4px 0 6px 0; gap:16px; {runner_style}">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">RVOL</span>
        <span style="font-size:24px; font-weight:900; color:{label_color};">{rvol_val:.1f}×</span>
        <span style="font-size:14px; font-weight:700; color:{label_color};">{display_label}</span>
    </div>
    """, unsafe_allow_html=True)


def render_buy_sell_widget(bsp, rvol_val=None):
    """Buy/Sell Volume Pressure oscillator — 0-100 gauge with momentum arrow.

    Uses the blended CLV+Tick formula (same as ThinkScript Blended split).
    Fires a HIGH CONVICTION alert when RVOL > 3 AND buy pressure is ramping.
    """
    if bsp is None:
        return
    buy_pct    = bsp["buy_pct"]
    trend_now  = bsp["trend_now"]
    trend_prev = bsp["trend_prev"]
    delta      = trend_now - trend_prev

    if buy_pct >= 65:
        bar_color, label = "#4caf50", "🔼 BUY DOMINANT"
    elif buy_pct >= 57:
        bar_color, label = "#8bc34a", "↑ Buy Leaning"
    elif buy_pct >= 43:
        bar_color, label = "#ffa726", "⇔ Balanced"
    elif buy_pct >= 35:
        bar_color, label = "#ef9a9a", "↓ Sell Leaning"
    else:
        bar_color, label = "#ef5350", "🔽 SELL DOMINANT"

    if delta > 3:
        momentum, mom_color = f"▲ Ramping +{delta:.0f}%", "#4caf50"
    elif delta < -3:
        momentum, mom_color = f"▼ Cooling {delta:.0f}%", "#ef5350"
    else:
        momentum, mom_color = "→ Flat", "#aaaaaa"

    # ── High-conviction signal: RVOL ≥ 5 + buy/sell pressure ramping ─────────
    rvol_gt5     = rvol_val is not None and rvol_val >= 5.0
    buy_ramping  = delta > 3 and buy_pct >= 55
    sell_ramping = delta < -3 and buy_pct <= 45
    hc_alert     = ""
    if rvol_gt5 and buy_ramping:
        hc_alert = (
            f'<div style="background:#4caf5022; border:1px solid #4caf5088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#4caf50; text-align:center;">'
            f'🚀 HIGH CONVICTION BUY — RVOL {rvol_val:.1f}× + Buy Ramping'
            f'</div>'
        )
        st.session_state["_hc_alert_state"] = {
            "ticker": st.session_state.get("ticker_input", ""),
            "rvol": rvol_val, "direction": "BUY",
        }
    elif rvol_gt5 and sell_ramping:
        hc_alert = (
            f'<div style="background:#ef535022; border:1px solid #ef535088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#ef5350; text-align:center;">'
            f'🔻 HIGH CONVICTION SELL — RVOL {rvol_val:.1f}× + Sell Ramping'
            f'</div>'
        )
        st.session_state["_hc_alert_state"] = {
            "ticker": st.session_state.get("ticker_input", ""),
            "rvol": rvol_val, "direction": "SELL",
        }
    else:
        st.session_state.pop("_hc_alert_state", None)

    st.markdown(f"""
    <div style="background:#1a1a2e; border:1px solid {bar_color}55; border-radius:8px;
                padding:10px 16px; margin:4px 0 6px 0;">
      {hc_alert}
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">
          Buy / Sell Pressure &nbsp;<span style="color:#555;">(CLV+Tick Blended)</span>
        </span>
        <span style="font-size:12px; font-weight:700; color:{bar_color};">{label}</span>
        <span style="font-size:11px; color:{mom_color};">{momentum}</span>
      </div>
      <div style="background:#333; border-radius:4px; height:14px; width:100%;
                  position:relative; overflow:hidden;">
        <div style="position:absolute; left:0; top:0; height:100%; width:{buy_pct:.1f}%;
                    background:{bar_color}; border-radius:4px;"></div>
        <div style="position:absolute; left:50%; top:0; height:100%;
                    width:2px; background:#ffffff44;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:5px;">
        <span style="font-size:11px; color:#ef5350;">🔴 Sell {bsp["sell_pct"]:.0f}%</span>
        <span style="font-size:11px; color:{bar_color}; font-weight:700;">🟢 Buy {buy_pct:.0f}%</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_social_sentiment_widget(sentiment: dict, rvol_val=None, buy_pct: float = 50.0):
    """Social sentiment widget — StockTwits bull/bear/neutral bars with HERD/TRAP alerts."""
    if not sentiment or sentiment.get("error"):
        return

    bull  = sentiment.get("bull_pct") or 0
    bear  = sentiment.get("bear_pct") or 0
    neut  = sentiment.get("neutral_pct") or 0
    count = sentiment.get("msg_count", 0)
    vel   = sentiment.get("msg_velocity", 0.0)
    trend = sentiment.get("trending", False)

    # Velocity arrow
    if vel > 1:
        vel_arrow, vel_color = f"▲ +{vel:.0f}/hr", "#4caf50"
    elif vel < -1:
        vel_arrow, vel_color = f"▼ {vel:.0f}/hr", "#ef5350"
    else:
        vel_arrow, vel_color = "→ Steady", "#aaaaaa"

    # HERD PILING IN alert: rising msgs + RVOL ≥ 3 + buy pressure ramping
    alert_html = ""
    rvol_hot = rvol_val is not None and rvol_val >= 3.0
    if trend and rvol_hot and buy_pct >= 55:
        alert_html = (
            '<div style="background:#4caf5022; border:1px solid #4caf5088; '
            'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            'font-size:12px; font-weight:700; color:#4caf50; text-align:center;">'
            '🐂 HERD PILING IN — Messages spiking + RVOL hot + Buy pressure'
            '</div>'
        )
    elif trend and rvol_hot and buy_pct <= 45:
        alert_html = (
            '<div style="background:#ffa72622; border:1px solid #ffa72688; '
            'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            'font-size:12px; font-weight:700; color:#ffa726; text-align:center;">'
            '⚠️ CROWD TRAP — Retail buying but sell pressure dominant'
            '</div>'
        )

    st.markdown(f"""
    <div style="background:#1a1a2e; border:1px solid #33336655; border-radius:8px;
                padding:10px 16px; margin:4px 0 6px 0;">
      {alert_html}
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">
          Social Sentiment &nbsp;<span style="color:#555;">(StockTwits · {count} msgs)</span>
        </span>
        <span style="font-size:11px; color:{vel_color};">{vel_arrow}</span>
      </div>
      <div style="display:flex; gap:3px; height:8px; border-radius:4px; overflow:hidden; margin-bottom:5px;">
        <div style="width:{bull:.1f}%; background:#4caf50;"></div>
        <div style="width:{neut:.1f}%; background:#666;"></div>
        <div style="width:{bear:.1f}%; background:#ef5350;"></div>
      </div>
      <div style="display:flex; justify-content:space-between;">
        <span style="font-size:11px; color:#4caf50;">🟢 Bull {bull:.0f}%</span>
        <span style="font-size:11px; color:#888;">Neutral {neut:.0f}%</span>
        <span style="font-size:11px; color:#ef5350;">🔴 Bear {bear:.0f}%</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_order_flow_widget(ofs):
    """Tier 2 order flow panel — pressure acceleration, bar quality, vol surge, streak."""
    if ofs is None:
        return

    # ── Color helpers ─────────────────────────────────────────────────────────
    _sig = ofs["composite_signal"]
    _score = ofs["composite_score"]
    if _sig == "Strong Buy Flow":
        _sig_color, _sig_icon = "#4caf50", "🟢"
    elif _sig == "Moderate Buy Flow":
        _sig_color, _sig_icon = "#8bc34a", "↑"
    elif _sig == "Strong Sell Flow":
        _sig_color, _sig_icon = "#ef5350", "🔴"
    elif _sig == "Moderate Sell Flow":
        _sig_color, _sig_icon = "#ef9a9a", "↓"
    else:
        _sig_color, _sig_icon = "#ffa726", "⇔"

    _accel_color = (
        "#4caf50" if ofs["pressure_accel"] == "Accelerating"
        else "#ef5350" if ofs["pressure_accel"] == "Decelerating"
        else "#aaaaaa"
    )
    _accel_arrow = (
        "▲" if ofs["pressure_accel"] == "Accelerating"
        else "▼" if ofs["pressure_accel"] == "Decelerating"
        else "→"
    )

    _bq = ofs["bar_quality"]
    _bq_color = (
        "#4caf50" if _bq >= 65
        else "#ef5350" if _bq <= 35
        else "#ffa726"
    )

    _vsr = ofs["vol_surge_ratio"]
    _vs_color = (
        "#4caf50" if _vsr >= 2.0
        else "#8bc34a" if _vsr >= 1.3
        else "#aaaaaa" if _vsr >= 0.7
        else "#ef5350"
    )

    _streak = ofs["streak"]
    _stk_color = "#4caf50" if _streak > 0 else "#ef5350" if _streak < 0 else "#aaaaaa"
    _stk_sign  = "+" if _streak > 0 else ""

    # ── IB proximity badge ────────────────────────────────────────────────────
    _ib_html = ""
    if ofs["ib_proximity"] and ofs["ib_proximity"] != "Mid-Range":
        _ib_c  = "#FFD700" if ofs["ib_vol_confirm"] else "#ffa72666"
        _ib_bd = "#FFD70088" if ofs["ib_vol_confirm"] else "#ffa72644"
        _ib_confirm_txt = " ✓ Vol Confirmed" if ofs["ib_vol_confirm"] else " (low vol)"
        _ib_html = (
            f'<div style="background:{_ib_c}18; border:1px solid {_ib_bd}; '
            f'border-radius:5px; padding:3px 10px; display:inline-block; '
            f'font-size:11px; font-weight:700; color:{_ib_c}; margin-bottom:6px;">'
            f'⚡ {ofs["ib_proximity"]}{_ib_confirm_txt}'
            f'</div>'
        )

    # ── Composite score bar (centered at 0) ───────────────────────────────────
    _abs_score  = abs(_score)
    _bar_left   = 50.0 if _score >= 0 else 50.0 - _abs_score / 2.0
    _bar_width  = _abs_score / 2.0
    _bar_left   = max(0.0, min(50.0, _bar_left))
    _bar_width  = max(0.0, min(50.0, _bar_width))

    _streak_short = (
        ofs["streak_label"]
        .replace(" Tape", "")
        .replace(" Upward", " \u25b2")
        .replace(" Downward", " \u25bc")
    )
    _of_html = (
        f'<div style="background:#1a1a2e; border:1px solid {_sig_color}44;'
        f' border-radius:8px; padding:10px 16px; margin:4px 0 6px 0;">'
        f'{_ib_html}'
        f'<div style="display:flex; justify-content:space-between; align-items:center;'
        f' margin-bottom:8px;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">'
        f'Order Flow Signals <span style="color:#555;">(Tier 2)</span></span>'
        f'<span style="font-size:12px; font-weight:700; color:{_sig_color};">'
        f'{_sig_icon}&nbsp;{_sig}</span>'
        f'<span style="font-size:11px; color:{_sig_color};">{_score:+.0f}</span>'
        f'</div>'
        f'<div style="background:#333; border-radius:4px; height:10px; width:100%;'
        f' position:relative; overflow:hidden; margin-bottom:8px;">'
        f'<div style="position:absolute; left:50%; top:0; height:100%;'
        f' width:2px; background:#ffffff44;"></div>'
        f'<div style="position:absolute; left:{_bar_left:.1f}%; top:0; height:100%;'
        f' width:{_bar_width:.1f}%; background:{_sig_color}; border-radius:4px;"></div>'
        f'</div>'
        f'<div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:6px;">'
        f'<div style="background:rgba(18,18,34,0.4); border:1px solid rgba(255,255,255,0.05);'
        f' border-radius:6px; padding:6px 8px; text-align:center;">'
        f'<div style="font-size:9px; color:#555; text-transform:uppercase;'
        f' letter-spacing:0.8px; margin-bottom:3px;">Pressure</div>'
        f'<div style="font-size:13px; font-weight:700; color:{_accel_color};">'
        f'{_accel_arrow} {ofs["pressure_accel"]}</div>'
        f'<div style="font-size:10px; color:#666; margin-top:2px;">'
        f'3b:{ofs["pressure_short"]:.0f}% | 10b:{ofs["pressure_medium"]:.0f}%</div>'
        f'</div>'
        f'<div style="background:rgba(18,18,34,0.4); border:1px solid rgba(255,255,255,0.05);'
        f' border-radius:6px; padding:6px 8px; text-align:center;">'
        f'<div style="font-size:9px; color:#555; text-transform:uppercase;'
        f' letter-spacing:0.8px; margin-bottom:3px;">Bar Quality</div>'
        f'<div style="font-size:13px; font-weight:700; color:{_bq_color};">{_bq:.0f}%</div>'
        f'<div style="font-size:10px; color:#666; margin-top:2px;">{ofs["bar_quality_label"]}</div>'
        f'</div>'
        f'<div style="background:rgba(18,18,34,0.4); border:1px solid rgba(255,255,255,0.05);'
        f' border-radius:6px; padding:6px 8px; text-align:center;">'
        f'<div style="font-size:9px; color:#555; text-transform:uppercase;'
        f' letter-spacing:0.8px; margin-bottom:3px;">Vol Surge</div>'
        f'<div style="font-size:13px; font-weight:700; color:{_vs_color};">{_vsr:.1f}x</div>'
        f'<div style="font-size:10px; color:#666; margin-top:2px;">{ofs["vol_surge_label"]}</div>'
        f'</div>'
        f'<div style="background:rgba(18,18,34,0.4); border:1px solid rgba(255,255,255,0.05);'
        f' border-radius:6px; padding:6px 8px; text-align:center;">'
        f'<div style="font-size:9px; color:#555; text-transform:uppercase;'
        f' letter-spacing:0.8px; margin-bottom:3px;">Tape Streak</div>'
        f'<div style="font-size:13px; font-weight:700; color:{_stk_color};">'
        f'{_stk_sign}{_streak} bars</div>'
        f'<div style="font-size:10px; color:#666; margin-top:2px;">{_streak_short}</div>'
        f'</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(_of_html, unsafe_allow_html=True)


def render_pattern_widget(patterns):
    """Tier 3 — Chart Pattern Detection panel rendered below Tier 2 order flow."""
    dir_colors = {"Bullish": "#4caf50", "Bearish": "#ef5350"}
    dir_icons  = {"Bullish": "▲", "Bearish": "▼"}
    tf_colors  = {"1hr": "#90caf9", "5m": "#b0bec5"}

    if not patterns:
        st.markdown(
            '<div style="background:#1a1a2e; border:1px solid #2a2a4a; border-radius:8px; '
            'padding:8px 16px; margin:4px 0 6px 0; display:flex; align-items:center; gap:12px;">'
            '<span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">'
            'Chart Patterns <span style="color:#555;">(Tier 3)</span></span>'
            '<span style="font-size:12px; color:#555;">No patterns detected yet — '
            'more bars needed or no classic setup forming</span>'
            '</div>',
            unsafe_allow_html=True
        )
        return

    top = patterns[0]
    top_color = dir_colors.get(top["direction"], "#aaaaaa")
    top_icon  = dir_icons.get(top["direction"], "")
    extra_lbl = f" + {len(patterns) - 1} more" if len(patterns) > 1 else ""

    rows_html = ""
    for p in patterns:
        dc = dir_colors.get(p["direction"], "#aaaaaa")
        di = dir_icons.get(p["direction"], "")
        tfc = tf_colors.get(p["timeframe"], "#b0bec5")
        pct = int(p["score"] * 100)
        sc  = "#4caf50" if pct >= 80 else "#ffa726" if pct >= 65 else "#ef9a9a"
        conf_str = " &middot; ".join(p["confluence"]) if p["confluence"] else ""
        nl = p.get("neckline")
        nl_html = (f'&nbsp;&middot;&nbsp;Neckline <b style="color:#FFD700;">'
                   f'${nl:.2f}</b>') if nl else ""
        conf_html = (f'<div style="font-size:10px; color:#FFD700; margin-top:2px;">'
                     f'&#9889; {conf_str}</div>') if conf_str else ""
        rows_html += (
            f'<div style="border-bottom:1px solid #1e1e3a; padding:7px 0 5px 0;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-size:12px; font-weight:700; color:{dc};">{di}&nbsp;{p["name"]}</span>'
            f'<span style="font-size:11px; color:{tfc}; margin:0 8px;">{p["timeframe"]}</span>'
            f'<span style="font-size:12px; font-weight:700; color:{sc};">{pct}%</span>'
            f'</div>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{p["description"]}{nl_html}</div>'
            f'{conf_html}'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:#1a1a2e; border:1px solid {top_color}44;'
        f' border-radius:8px; padding:10px 16px; margin:4px 0 6px 0;">'
        f'<div style="display:flex; justify-content:space-between; align-items:center;'
        f' margin-bottom:8px;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">'
        f'Chart Patterns <span style="color:#555;">(Tier 3)</span></span>'
        f'<span style="font-size:12px; font-weight:700; color:{top_color};">'
        f'{top_icon}&nbsp;{top["name"]}{extra_lbl}</span>'
        f'</div>'
        f'{rows_html}'
        f'</div>',
        unsafe_allow_html=True
    )


def render_model_prediction(outcome, reasoning):
    """Show the volume-price divergence model prediction in a styled text box.

    'Market Closed' outcome → shows a blue sleep-mode info box instead of
    a directional warning so stale/historical analysis isn't misread as live signal.
    """
    # ── Sleep mode: market is closed, suppress directional warnings ───────────
    if outcome == "Market Closed":
        st.markdown("""
        <div style="background:#0d47a114; border:1px solid #1565c066;
                    border-radius:8px; padding:12px 20px; margin:8px 0 4px 0;">
            <div style="font-size:10px; color:#5c8ecc; text-transform:uppercase;
                        letter-spacing:1.2px; margin-bottom:6px;">
                🤖 Model Prediction — Volume-Price Divergence
            </div>
            <div style="font-size:20px; font-weight:800; color:#64b5f6; margin-bottom:4px;">
                💤 MARKET CLOSED — Analyzing Historical Data
            </div>
            <div style="font-size:13px; color:#90caf9; line-height:1.6;">
                Live pattern alerts are suppressed outside 9:30 AM – 4:00 PM EST.
                Use the volume profile structure and TCS score for session-level context.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Live / historical session directional signal ───────────────────────────
    _colors = {"Fake-out": "#ef5350", "High Conviction": "#4caf50", "Consolidation": "#ffa726"}
    _icons  = {"Fake-out": "⚠️", "High Conviction": "🎯", "Consolidation": "📊"}
    c    = _colors.get(outcome, "#aaaaaa")
    icon = _icons.get(outcome, "📊")
    st.markdown(f"""
    <div style="background:{c}0e; border:1px solid {c}44; border-radius:8px;
                padding:12px 20px; margin:8px 0 4px 0;">
        <div style="font-size:10px; color:#666; text-transform:uppercase;
                    letter-spacing:1.2px; margin-bottom:6px;">
            🤖 Model Prediction — Volume-Price Divergence
        </div>
        <div style="font-size:20px; font-weight:800; color:{c}; margin-bottom:4px;">
            {icon}&nbsp;{outcome}
        </div>
        <div style="font-size:13px; color:#c0c0c0; line-height:1.6;">
            {reasoning}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_velocity_widget(df):
    vol, chg, direction = compute_volume_velocity(df)
    if vol is None:
        return
    color = "#26a69a" if direction == "↑" else "#ef5350" if direction == "↓" else "#aaa"
    chg_str = f"{direction} {chg:.1f}%" if chg is not None else "—"

    intensity = ""
    if chg is not None:
        if chg > 50:
            intensity = " — 🔥 Surging"
        elif chg > 20:
            intensity = " — ⬆ Accelerating"
        elif chg < -50:
            intensity = " — 🧊 Drying up"
        elif chg < -20:
            intensity = " — ⬇ Fading"

    st.markdown(f"""
    <div style="display:inline-flex; align-items:center; background:#16213e;
                border:1px solid #2a2a4a; border-radius:6px; padding:8px 18px; margin:4px 0 8px 0; gap:14px;">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">⚡ Vol Velocity</span>
        <span style="font-size:19px; font-weight:700; color:#e0e0e0;">{vol:,.0f}/min</span>
        <span style="font-size:16px; font-weight:600; color:{color};">{chg_str}{intensity}</span>
    </div>
    """, unsafe_allow_html=True)


def render_analysis(df, num_bins, ticker, chart_title, is_ib_live=False,
                    avg_daily_vol=None, sector_bonus=0.0, sector_etf="IWM",
                    intraday_curve=None, is_live=False):
    ib_high, ib_low = compute_initial_balance(df)

    # ── IB override: user can correct from their broker ───────────────────────
    _ib_ovr_key = f"ib_override_{ticker}"
    _ib_ovr = st.session_state.get(_ib_ovr_key)
    if _ib_ovr:
        ib_high = _ib_ovr.get("high") or ib_high
        ib_low  = _ib_ovr.get("low")  or ib_low

    bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
    # ── Cache raw bars + VP data for Small Account Challenge tab ─────────────
    st.session_state.last_bars      = df.copy()
    st.session_state.sa_bin_centers = bin_centers
    st.session_state.sa_vap         = vap
    _sa_val, _sa_vah = _compute_value_area(bin_centers, vap)
    label, color, detail, insight = classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price, avg_daily_vol=avg_daily_vol
    )
    probs = compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price)
    tcs = compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=sector_bonus)
    target_zones = compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs)

    # ── Edge Score (live, for this chart) ─────────────────────────────────────
    _top_struct_key  = max(probs, key=probs.get) if probs else ""
    _top_struct_conf = round(float(probs.get(_top_struct_key, 0.0)), 1) if probs else 0.0
    try:
        _chart_weights  = compute_adaptive_weights(_AUTH_USER_ID)
        _chart_env      = get_recent_env_stats(_AUTH_USER_ID, days=5)
        _chart_edge, _chart_edge_bkd = compute_edge_score(
            tcs=tcs,
            structure_conf=_top_struct_conf,
            env_long_rate=_chart_env["long_rate"],
            recent_false_brk_rate=_chart_env["false_brk_rate"],
            weights=_chart_weights,
        )
    except Exception:
        _chart_edge, _chart_edge_bkd = None, {}

    # ── High Conviction logger ─────────────────────────────────────────────────
    try:
        _top_struct, _top_prob = max(probs.items(), key=lambda x: x[1])
        if _top_prob >= HICONS_THRESHOLD and ib_high is not None and ib_low is not None:
            log_high_conviction(ticker, selected_date, _top_struct, _top_prob,
                                ib_high=ib_high, ib_low=ib_low, poc_price=poc_price)
    except Exception:
        pass
    audio_enabled = st.session_state.get("audio_alerts_enabled", True)
    check_tcs_alerts(tcs, audio_enabled)

    # ── Signal conditions snapshot (predictive engine feed) ─────────────────────
    # Save conditions every time the chart runs so EOD verification can pair them
    # with outcomes later and build your personal win-rate probability database.
    try:
        if _chart_edge is not None and _AUTH_USER_ID and rvol_pre is not None:
            _bp, _ = compute_buy_sell_pressure(df)
            save_signal_conditions(
                user_id=_AUTH_USER_ID,
                ticker=ticker,
                trade_date=selected_date,
                edge_score=float(_chart_edge),
                rvol=float(rvol_pre),
                structure=label,
                tcs=float(tcs),
                buy_pressure=float(_bp) if _bp is not None else 0.0,
            )
    except Exception:
        pass

    # ── MarketBrain — real-time structure prediction ───────────────────────────
    brain = MarketBrain()
    brain.load_from_session()
    rvol_pre = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    try:
        _brain_ivp, _ = compute_ib_volume_stats(df, ib_high, ib_low) if (
            ib_high is not None and ib_low is not None) else (None, None)
    except Exception:
        _brain_ivp = None
    _brain_has_dd = _detect_double_distribution(bin_centers, vap) is not None
    brain.update(df, rvol=rvol_pre, ib_vol_pct=_brain_ivp,
                 poc_price=poc_price, has_double_dist=_brain_has_dd)
    st.session_state.brain_predicted = brain.prediction

    # ── Time context ───────────────────────────────────────────────────────────
    # elapsed_bars = minutes of session captured so far (used for time-seg RVOL + Fuel Check)
    elapsed_bars = len(df)
    # market_open:
    #   • Live mode  → check actual clock (suppress fake-out if market is closed)
    #   • Historical → True — session data is self-contained, show all signals
    market_open = is_market_open() if is_live else True

    # ── RVOL + Pattern Label ───────────────────────────────────────────────────
    price_start = float(df["open"].iloc[0]) if len(df) else 0.0
    price_now   = float(df["close"].iloc[-1]) if len(df) else 0.0
    pct_chg_today = (price_now - price_start) / price_start * 100.0 if price_start > 0 else 0.0

    rvol_val = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    rvol_lbl, rvol_color, is_runner, is_play = rvol_classify(
        rvol_val, pct_chg_today,
        elapsed_bars=elapsed_bars if is_live else None,   # open-window check only in live mode
        price_now=price_now
    )

    # ── Model prediction ───────────────────────────────────────────────────────
    pred_outcome, pred_reasoning = compute_model_prediction(
        df, rvol_val, tcs, sector_bonus, market_open=market_open
    )

    day_high = float(df["high"].max())
    day_low  = float(df["low"].min())
    ib_range = (ib_high - ib_low) if ib_high and ib_low else 0.0

    # IB status badge for live mode
    if is_ib_live and ib_high and ib_low:
        now_est = datetime.now(EASTERN)
        mins_left = max(0, int((datetime.combine(date.today(), dtime(10, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60))
        st.markdown(
            f'<div style="display:inline-block; background:#ff980033; border:1px solid #ff9800; '
            f'border-radius:4px; padding:3px 10px; font-size:13px; color:#ff9800; margin-bottom:8px;">'
            f'📐 IB FORMING — {mins_left} min remaining until 10:30 EST</div>',
            unsafe_allow_html=True
        )

    # ── Top metrics row ───────────────────────────────────────────────────────
    rvol_display = f"{rvol_val:.1f}×" if rvol_val is not None else "—"
    sector_display = (f"{sector_etf} +{sector_bonus:.0f}pts" if sector_bonus > 0
                      else f"{sector_etf} —")
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
    col1.metric("Bars", len(df))
    col2.metric("IB High", f"${ib_high:.2f}" if ib_high else "—")
    col3.metric("IB Low",  f"${ib_low:.2f}"  if ib_low  else "—")
    col4.metric("IB Range", f"${ib_range:.2f}")
    col5.metric("Day Range", f"${day_high - day_low:.2f}")
    col6.metric("POC", f"${poc_price:.2f}" if poc_price is not None else "—")
    col7.metric("RVOL", rvol_display)
    col8.metric("Sector", sector_display)

    # ── IB Manual Override ────────────────────────────────────────────────────
    _ib_ovr_key = f"ib_override_{ticker}"
    _has_override = bool(st.session_state.get(_ib_ovr_key))
    _ovr_label = "✏️ IB Override — ACTIVE" if _has_override else "✏️ Override IB Levels (broker correction)"
    with st.expander(_ovr_label, expanded=_has_override):
        st.caption("Type your broker's IB values and click **Apply**. "
                   "Leave at 0 to keep the auto-computed value.")
        with st.form(key=f"ib_override_form_{ticker}", clear_on_submit=False):
            _fc1, _fc2, _fc3 = st.columns([2, 2, 1])
            _curr_high = float(st.session_state.get(_ib_ovr_key, {}).get("high") or ib_high or 0.0)
            _curr_low  = float(st.session_state.get(_ib_ovr_key, {}).get("low")  or ib_low  or 0.0)
            _ovr_high = _fc1.number_input("IB High", value=_curr_high,
                                           min_value=0.0, step=0.001, format="%.3f")
            _ovr_low  = _fc2.number_input("IB Low",  value=_curr_low,
                                           min_value=0.0, step=0.001, format="%.3f")
            _fc3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            _sub_cols = _fc3.columns(2)
            _submitted = _sub_cols[0].form_submit_button("✓", use_container_width=True,
                                                          help="Apply override")
            _cleared   = _sub_cols[1].form_submit_button("✕", use_container_width=True,
                                                          help="Clear override")
        if _submitted:
            st.session_state[_ib_ovr_key] = {
                "high": _ovr_high if _ovr_high > 0 else None,
                "low":  _ovr_low  if _ovr_low  > 0 else None,
            }
            st.rerun()
        if _cleared:
            st.session_state.pop(_ib_ovr_key, None)
            st.rerun()

    # ── IB Volume Stats widget ─────────────────────────────────────────────────
    ib_vol_pct_disp, ib_range_ratio_disp = compute_ib_volume_stats(df, ib_high, ib_low)
    _ivp_pct = (ib_vol_pct_disp or 0.0) * 100
    _irr_pct = (ib_range_ratio_disp or 0.0) * 100
    # Color coding: balanced (green) vs directional (orange/red)
    _ivp_color = ("#4caf50" if _ivp_pct >= 60 else "#ffa726" if _ivp_pct >= 35 else "#ef5350")
    _irr_color = ("#4caf50" if _irr_pct >= 50 else "#ffa726" if _irr_pct >= 25 else "#ef5350")
    _ivp_label = "Balanced" if _ivp_pct >= 60 else ("Neutral" if _ivp_pct >= 35 else "Directional")
    _irr_label = "Contained" if _irr_pct >= 50 else ("Moderate" if _irr_pct >= 25 else "Expanded")
    _ib_high_str  = f"${ib_high:.2f}"   if ib_high   is not None else "—"
    _ib_low_str   = f"${ib_low:.2f}"    if ib_low    is not None else "—"
    _poc_str      = f"${poc_price:.2f}" if poc_price  is not None else "—"
    st.markdown(
        f'<div style="background:#0f3460; border:1px solid #1a3a6e; border-radius:6px; '
        f'padding:8px 16px; margin:4px 0 6px 0; display:flex; align-items:center; gap:20px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">📐 IB Structure</span>'
        f'<span style="font-size:12px; color:#aaa;">IB Vol%: '
        f'<b style="color:{_ivp_color};">{_ivp_pct:.0f}%</b> '
        f'<span style="color:{_ivp_color}; font-size:11px;">({_ivp_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">IB/Day Range: '
        f'<b style="color:{_irr_color};">{_irr_pct:.0f}%</b> '
        f'<span style="color:{_irr_color}; font-size:11px;">({_irr_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:11px; color:#555; white-space:nowrap;">'
        f'IB {_ib_high_str} – {_ib_low_str} &nbsp;|&nbsp; POC {_poc_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    render_velocity_widget(df)
    render_rvol_widget(rvol_val, rvol_lbl, rvol_color, is_runner)
    _bsp_social = compute_buy_sell_pressure(df)
    render_buy_sell_widget(_bsp_social, rvol_val=rvol_val)
    _social_sentiment = fetch_stocktwits_sentiment(ticker)
    render_social_sentiment_widget(
        _social_sentiment,
        rvol_val=rvol_val,
        buy_pct=float(_bsp_social.get("buy_pct", 50) if _bsp_social else 50),
    )
    render_order_flow_widget(
        compute_order_flow_signals(df, ib_high=ib_high, ib_low=ib_low)
    )
    render_pattern_widget(
        detect_chart_patterns(df, poc_price=poc_price, ib_high=ib_high, ib_low=ib_low)
    )
    # ── Runner DNA similarity badge ───────────────────────────────────────────
    _runner_sim = compute_runner_similarity(bin_centers, vap)
    if _runner_sim.get("is_strong"):
        _rsim_pct   = _runner_sim["similarity"]
        _rsim_arch  = _runner_sim["archetype"]
        _rsim_color = "#4caf50" if _rsim_arch not in ("Dump (Distribution)", "News Spike Fade") else "#ef5350"
        st.markdown(
            f'<div style="background:#1a1a2e; border:1px solid {_rsim_color}55; '
            f'border-radius:6px; padding:6px 14px; margin:4px 0 6px 0; '
            f'font-size:12px; color:{_rsim_color}; font-weight:600;">'
            f'🧬 Runner DNA: <b>{_rsim_pct:.0f}% match</b> — {_rsim_arch}'
            f'</div>',
            unsafe_allow_html=True,
        )
    # ── Round Number Magnetism ─────────────────────────────────────────────────
    if price_now > 0:
        _rn_ceil = (int(price_now) + 1) if price_now % 1.0 != 0 else price_now
        _rn_half_ceil = (int(price_now * 2) + 1) / 2.0
        _dist_whole = abs(_rn_ceil - price_now) / price_now * 100
        _dist_half  = abs(_rn_half_ceil - price_now) / price_now * 100
        _nearest_dist   = min(_dist_whole, _dist_half)
        _nearest_level  = _rn_ceil if _dist_whole <= _dist_half else _rn_half_ceil
        if _nearest_dist < 3.0:
            if _nearest_dist < 0.5:
                _mag_label, _mag_col, _mag_e = "Strong", "#ef5350", "🔴"
            elif _nearest_dist < 1.5:
                _mag_label, _mag_col, _mag_e = "Moderate", "#ff9800", "🟡"
            else:
                _mag_label, _mag_col, _mag_e = "Weak", "#9e9e9e", "⚪"
            _rn_type = "whole dollar" if _dist_whole <= _dist_half else "half dollar"
            st.markdown(
                f'<div style="background:#1a1a2e; border:1px solid {_mag_col}55; '
                f'border-radius:6px; padding:6px 14px; margin:4px 0 6px 0; '
                f'font-size:12px; color:{_mag_col}; font-weight:600;">'
                f'🧲 ${_nearest_level:.2f} {_rn_type} ceiling — {_nearest_dist:.1f}% away '
                f'({_mag_e} {_mag_label} Magnetism)</div>',
                unsafe_allow_html=True,
            )
    render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=is_runner, sector_bonus=sector_bonus,
                            insight=insight)
    render_model_prediction(pred_outcome, pred_reasoning)

    # ── MarketBrain: compare prediction vs actual + running counter ───────────
    bc = brain.color()
    _brain_correct_now = False
    _brain_newly_logged = False

    # Auto-compare once IB is complete and brain has a real prediction
    if brain.ib_set and brain.prediction != "Analyzing IB…" \
            and ib_high is not None and ib_low is not None \
            and ib_high != float("inf") and ib_low != float("inf"):
        _today_str   = datetime.now(EASTERN).strftime("%Y-%m-%d")
        _compare_key = f"{ticker}_{_today_str}_{float(ib_high):.4f}_{float(ib_low):.4f}"

        # Dedup: check both session state AND the CSV (survives reloads)
        _already_in_csv = False
        if st.session_state.brain_last_compared != _compare_key:
            try:
                _chk = pd.read_csv(TRACKER_FILE) if os.path.exists(TRACKER_FILE) else pd.DataFrame()
                if "compare_key" in _chk.columns:
                    _already_in_csv = (_chk["compare_key"] == _compare_key).any()
            except Exception:
                pass

        if st.session_state.brain_last_compared != _compare_key and not _already_in_csv:
            st.session_state.brain_last_compared = _compare_key
            # Fuzzy match: strip emojis/punctuation and compare core words
            _pred_clean   = _strip_emoji(brain.prediction)
            _actual_clean = _strip_emoji(label)
            _brain_correct_now = (
                _pred_clean in _actual_clean or _actual_clean in _pred_clean
                or any(w in _actual_clean for w in _pred_clean.split() if len(w) > 4)
            )
            st.session_state.brain_session_total   += 1
            if _brain_correct_now:
                st.session_state.brain_session_correct += 1
            # Log to CSV with the compare_key stored for reload dedup
            log_accuracy_entry(ticker, brain.prediction, label,
                               compare_key=_compare_key,
                               user_id=st.session_state.get("auth_user_id", ""))
            _brain_newly_logged = True
        elif st.session_state.brain_last_compared != _compare_key and _already_in_csv:
            # Already in CSV from a previous session — just sync the session key
            st.session_state.brain_last_compared = _compare_key

    _b_corr  = st.session_state.brain_session_correct
    _b_total = st.session_state.brain_session_total
    _b_rate  = (_b_corr / _b_total * 100) if _b_total > 0 else 0
    _counter_str = (f"Today: {_b_corr}/{_b_total} ({_b_rate:.0f}%)"
                    if _b_total > 0 else "Today: —")
    _counter_col = "#4caf50" if _b_rate >= 60 else "#ffa726" if _b_rate >= 40 else "#ef5350"

    # ── All-time win rate — reads directly from CSV, never resets ─────────────
    _at_total, _at_correct, _at_rate = 0, 0, None
    try:
        if os.path.exists(TRACKER_FILE):
            _at_df = pd.read_csv(TRACKER_FILE)
            _at_total = int(len(_at_df))
            if "correct" in _at_df.columns and _at_total > 0:
                _at_correct = int((_at_df["correct"] == "✅").sum())
                _at_rate    = round(float(_at_correct) / float(_at_total) * 100.0, 1)
    except Exception:
        _at_total, _at_correct, _at_rate = 0, 0, None

    _at_is_valid = (_at_rate is not None and isinstance(_at_rate, (int, float))
                    and _at_rate == _at_rate)   # NaN check
    _at_col = ("#4caf50" if _at_is_valid and _at_rate >= 60
               else "#ffa726" if _at_is_valid and _at_rate >= 40
               else "#ef5350" if _at_is_valid else "#555")
    _at_str  = f"{_at_rate:.0f}%" if _at_is_valid else "—"
    _at_lbl  = f"All-time: <b style='font-size:18px; color:{_at_col};'>{_at_str}</b>"
    if _at_is_valid:
        _at_lbl += f" <span style='font-size:10px; color:#555;'>({_at_correct}/{_at_total})</span>"

    st.markdown(
        f'<div style="background:{bc}11; border-left:3px solid {bc}; border-radius:6px; '
        f'padding:10px 16px; margin:6px 0 4px 0; display:flex; align-items:center; gap:16px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">🧠 Brain</span>'
        f'<span style="font-size:15px; font-weight:700; color:{bc};">{brain.prediction}</span>'
        f'<span style="font-size:11px; color:#555;">vs <span style="color:{color};">{label}</span></span>'
        f'<span style="font-size:11px; color:#444; margin-left:auto;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">{_at_lbl}</span>'
        f'<span style="font-size:11px; font-weight:600; color:{_counter_col}; '
        f'background:{_counter_col}22; padding:2px 8px; border-radius:4px; '
        f'border:1px solid {_counter_col}44; white-space:nowrap;">{_counter_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Flash notification when brain is newly correct / newly wrong ──────────
    if _brain_newly_logged:
        if _brain_correct_now:
            st.markdown(
                f'<div style="background:#4caf5022; border:1px solid #4caf50; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; font-weight:700; color:#4caf50;">'
                f'✅ Brain Correct! Predicted <em>{brain.prediction}</em> — matches <em>{label}</em>. '
                f'Running accuracy: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#ffa72622; border:1px solid #ffa726; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; color:#ffa726;">'
                f'🔄 Brain predicted <em>{brain.prediction}</em> — actual was <em>{label}</em>. '
                f'Data logged for learning. Running: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )

    # ── Persist snapshot for Live Pulse header + Log Entry ────────────────────
    st.session_state.last_analysis_state = {
        "ticker":      ticker,
        "price":       price_now,
        "structure":   label,
        "tcs":         tcs,
        "rvol":        rvol_val,
        "ib_high":     ib_high,
        "ib_low":      ib_low,
        "poc_price":   poc_price,
        "val_price":   _sa_val,
        "vah_price":   _sa_vah,
        "pct_change":  pct_chg_today,
        "rvol_color":  rvol_color,
        "is_runner":   is_runner,
        "label_color": color,
        "vol_velocity_str": "",
        "brain_predicted": brain.prediction,
        "edge_score":       _chart_edge,
        "edge_breakdown":   _chart_edge_bkd,
        "struct_conf":      _top_struct_conf,
        "social_bull_pct":  (_social_sentiment or {}).get("bull_pct"),
        "social_bear_pct":  (_social_sentiment or {}).get("bear_pct"),
        "social_msg_count": (_social_sentiment or {}).get("msg_count", 0),
    }

    # ── Update peak price + auto-alerts for open position ────────────────────
    if st.session_state.position_in and st.session_state.position_ticker == ticker:
        avg_entry = st.session_state.position_avg_entry

        # Track MFE
        if price_now > st.session_state.position_peak_price:
            st.session_state.position_peak_price = price_now
            save_position_state()

        # 1. Breakeven alert — price reached +2% from entry
        if avg_entry > 0 and price_now >= avg_entry * 1.02:
            be_pct = (price_now - avg_entry) / avg_entry * 100
            st.markdown(
                f'<div style="background:#ff980022; border:1px solid #ff9800; border-radius:6px; '
                f'padding:10px 18px; margin:6px 0; font-size:14px; font-weight:700; color:#ff9800;">'
                f'⚡ MOVE STOP TO BREAKEVEN — Price is +{be_pct:.1f}% from entry '
                f'(${avg_entry:.2f} → ${price_now:.2f}). '
                f'Set stop at ${avg_entry:.2f} to lock in a risk-free trade.</div>',
                unsafe_allow_html=True,
            )

        # 2. Auto Take-Profit — price hit IB High on Neutral or Normal structure
        _tp_structures = {"Neutral", "Neutral Extreme", "Normal", "Normal Variation"}
        _label_base    = label.split(" ")[0] if label else ""
        _tp_triggered  = (
            avg_entry > 0
            and ib_high is not None
            and price_now >= ib_high
            and any(s in label for s in _tp_structures)
        )
        if _tp_triggered:
            _mfe      = st.session_state.position_peak_price   # capture before exit clears it
            _realized = exit_position(price_now, actual_structure=label)
            _pnl_col  = "#4caf50" if _realized >= 0 else "#ef5350"
            st.markdown(
                f'<div style="background:{_pnl_col}22; border:1px solid {_pnl_col}; '
                f'border-radius:6px; padding:10px 18px; margin:6px 0; font-size:14px; '
                f'font-weight:700; color:{_pnl_col};">'
                f'🎯 AUTO TAKE PROFIT — Price reached IB High ${ib_high:.2f} on a '
                f'<em>{label}</em> day. '
                f'Position closed at ${price_now:.2f}. '
                f'Realized: ${_realized:+.2f} | MFE: ${_mfe:.2f}</div>',
                unsafe_allow_html=True,
            )
            time.sleep(0.5)
            st.rerun()

    # ── Target zone alerts + sidebar "Distance to Target" ─────────────────────
    check_target_alerts(price_now, target_zones, audio_enabled)
    if target_zones:
        with st.sidebar:
            st.markdown("---")
            st.markdown("**🎯 Distance to Target**")
            for tz in target_zones:
                dist_pct = abs(price_now - tz["price"]) / price_now * 100
                arrow = "▲" if tz["price"] > price_now else "▼"
                tc = tz["color"]
                st.markdown(
                    f'<div style="background:#1a2744; border-left:3px solid {tc}; '
                    f'border-radius:4px; padding:6px 10px; margin:4px 0; font-size:12px;">'
                    f'<span style="color:{tc}; font-weight:700;">{tz["label"]}</span> '
                    f'<span style="color:#ccc;">${tz["price"]:.2f}</span> '
                    f'<span style="color:#888;">{arrow} {dist_pct:.1f}% away</span></div>',
                    unsafe_allow_html=True,
                )

    pos_state = {
        "in":         st.session_state.position_in,
        "avg_entry":  st.session_state.position_avg_entry,
        "peak_price": st.session_state.position_peak_price,
        "shares":     st.session_state.position_shares,
    }
    fig = build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title,
                      target_zones=target_zones, position=pos_state)
    st.plotly_chart(fig, use_container_width=True)

    # ── RVOL Trend Subplot (T007) ──────────────────────────────────────────────
    try:
        if avg_daily_vol and avg_daily_vol > 0 and intraday_curve is not None:
            _rvol_times, _rvol_vals = [], []
            _cum_vol = 0.0
            for _bar_ts, _bar in df.iterrows():
                _cum_vol += float(_bar["volume"])
                _bar_min = int(_bar_ts.hour * 60 + _bar_ts.minute - 9 * 60 - 30)
                _bar_min = max(0, min(_bar_min, len(intraday_curve) - 1))
                _expected_frac = intraday_curve[_bar_min] if _bar_min < len(intraday_curve) else 1.0
                _expected_vol  = avg_daily_vol * max(_expected_frac, 0.001)
                _rv = round(_cum_vol / _expected_vol, 2)
                _rvol_times.append(_bar_ts.strftime("%H:%M"))
                _rvol_vals.append(_rv)

            _rv_colors = [
                "rgba(76,175,80,0.85)"  if v >= 5 else
                "rgba(255,167,38,0.75)" if v >= 2 else
                "rgba(84,110,122,0.6)"
                for v in _rvol_vals
            ]
            _fig_rv = go.Figure()
            _fig_rv.add_trace(go.Bar(
                x=_rvol_times, y=_rvol_vals,
                marker_color=_rv_colors,
                name="RVOL",
                hovertemplate="<b>%{x}</b><br>RVOL: %{y:.2f}×<extra></extra>",
            ))
            _fig_rv.add_hline(y=2.0, line=dict(color="rgba(255,167,38,0.5)", dash="dot", width=1),
                              annotation_text="2× floor", annotation_font_color="#ffa726",
                              annotation_font_size=9)
            _fig_rv.add_hline(y=5.0, line=dict(color="rgba(76,175,80,0.5)", dash="dot", width=1),
                              annotation_text="5× conviction", annotation_font_color="#4caf50",
                              annotation_font_size=9)
            _fig_rv.update_layout(
                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                font=dict(color="#e0e0e0", size=10),
                height=140,
                margin=dict(l=40, r=10, t=6, b=30),
                showlegend=False,
                xaxis=dict(gridcolor="#1e2a3a", showgrid=False,
                           tickfont=dict(size=9, color="#546e7a")),
                yaxis=dict(title="RVOL ×", gridcolor="#1e2a3a",
                           tickfont=dict(size=9, color="#546e7a")),
                bargap=0.1,
            )
            st.plotly_chart(_fig_rv, use_container_width=True)
    except Exception:
        pass

    with st.expander("📋 Raw Bar Data"):
        disp = df[["open", "high", "low", "close", "volume"]].copy()
        disp.index = disp.index.strftime("%H:%M")
        disp.columns = ["Open", "High", "Low", "Close", "Volume"]
        st.dataframe(disp, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRE-MARKET GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_WATCHLIST = (
    "MSTR,COIN,SOFI,LCID,SIRI,SPCE,FFIE,WKHS,NKLA,MVIS,"
    "CLOV,BB,MMAT,SNDL,TLRY,AFRM,UPST,DKNG,BYND,PLTR,"
    "RIVN,CHPT,BLNK,ASTS,ACHR,JOBY,HOOD,OPEN,PSFE,NRDS"
)

_DEFAULT_RANKING_TICKERS = [
    "CREG", "CUE", "WGRX", "AIXI", "FUSE", "MGN", "SAFX", "UCAR", "SQFT",
    "SIDU", "ZNTL", "SKYQ", "SOAR", "GAME", "AAOI", "IPST", "PROP", "BENF",
    "TMCI", "NVVE", "MAXN", "SPIR", "CVLT", "LOBO", "SBEV", "DPRO", "BBGI",
    "RMSG", "HKIT", "EZRA", "CETX", "STAK", "TPST", "APRE", "UGRO", "LPCN",
    "PSTV",
]


# ══════════════════════════════════════════════════════════════════════════════
# SESSION RESTORE / AUTH GATE / PREFS  (must run before sidebar renders)
# ══════════════════════════════════════════════════════════════════════════════

if not st.session_state.get("auth_user") and not st.session_state.get("_restore_tried"):
    st.session_state["_restore_tried"] = True
    _restored = try_restore_session()
    if _restored.get("user"):
        _ru = _restored["user"]
        st.session_state["auth_user"]    = _ru
        st.session_state["auth_user_id"] = str(_ru.id)
        st.session_state["auth_email"]   = _restored.get("email", "")
        _rat = _restored.get("access_token",  "")
        _rrt = _restored.get("refresh_token", "")
        st.session_state["auth_access_token"]  = _rat
        st.session_state["auth_refresh_token"] = _rrt
        set_user_session(_rat, _rrt)
        st.rerun()

# ── Beta portal intercept — must come before auth gate ────────────────────────
_beta_user_id = st.query_params.get("beta", "")
if _beta_user_id:
    render_beta_portal(_beta_user_id)
    st.stop()

# ── Build notes intercept — accessible via /?notes=<USER_ID> or /?notes=<CODE>
_NOTES_USER_ID = "a5e1fcab-8369-42c4-8550-a8a19734510c"
_PRIVATE_KEY   = "7c3f9b2a-4e1d-4a8c-b05f-3d8e6f1a9c4b"
_PUBLIC_NOTES_CODE = "edgeiq"
_notes_param   = st.query_params.get("notes", "")
if _notes_param == _NOTES_USER_ID or _notes_param == _PUBLIC_NOTES_CODE:
    render_build_notes()
    st.stop()

# ── Private build notes — accessible via /?private=<KEY> ─────────────────────
_private_param = st.query_params.get("private", "")
if _private_param == _PRIVATE_KEY:
    render_private_build_notes()
    st.stop()

# ── Trade Journal Logger — accessible via /?journal=<USER_ID> ────────────────
_journal_param = st.query_params.get("journal", "")
if _journal_param == _NOTES_USER_ID:
    render_trade_journal_page()
    st.stop()

if not st.session_state.get("auth_user"):
    render_login_page()
    st.stop()

_AUTH_USER_ID = st.session_state.get("auth_user_id", "")

if _AUTH_USER_ID and not st.session_state.get("_prefs_loaded"):
    _prefs = _cached_load_user_prefs(user_id=_AUTH_USER_ID)
    if _prefs.get("alpaca_key"):
        st.session_state["_pref_alpaca_key"]    = _prefs["alpaca_key"]
    if _prefs.get("alpaca_secret"):
        st.session_state["_pref_alpaca_secret"] = _prefs["alpaca_secret"]
    if "min_tcs_trades" in _prefs:
        try:
            st.session_state["min_tcs_trades"] = int(_prefs["min_tcs_trades"])
        except (ValueError, TypeError):
            pass
    if "rp_min_tcs_slider" in _prefs:
        try:
            st.session_state["rp_min_tcs_slider"] = int(_prefs["rp_min_tcs_slider"])
        except (ValueError, TypeError):
            pass
    if "rp_min_gap" in _prefs:
        try:
            st.session_state["rp_min_gap"] = float(_prefs["rp_min_gap"])
        except (ValueError, TypeError):
            pass
    if "rp_min_gap_vs_ib" in _prefs:
        try:
            st.session_state["rp_min_gap_vs_ib"] = float(_prefs["rp_min_gap_vs_ib"])
        except (ValueError, TypeError):
            pass
    if "rp_min_ft" in _prefs:
        try:
            st.session_state["rp_min_ft"] = float(_prefs["rp_min_ft"])
        except (ValueError, TypeError):
            pass
    if "pt_min_tcs" in _prefs:
        try:
            st.session_state["pt_min_tcs"] = int(_prefs["pt_min_tcs"])
        except (ValueError, TypeError):
            pass
    if "pt_price_range" in _prefs:
        try:
            _pr = _prefs["pt_price_range"]
            if isinstance(_pr, (list, tuple)) and len(_pr) == 2:
                st.session_state["pt_price_range"] = (float(_pr[0]), float(_pr[1]))
        except (ValueError, TypeError):
            pass
    if "pt_extra_tickers" in _prefs:
        st.session_state["pt_extra_tickers"] = str(_prefs["pt_extra_tickers"])
    if "rk_extra_tickers" in _prefs:
        st.session_state["rk_extra_tickers"] = str(_prefs["rk_extra_tickers"])
    if "bts_dr_start" in _prefs:
        try:
            import datetime as _dt_bts
            _bts_d = _prefs["bts_dr_start"]
            if _bts_d:
                st.session_state["bts_dr_start"] = _dt_bts.date.fromisoformat(str(_bts_d))
        except (ValueError, TypeError):
            pass
    if "bts_dr_end" in _prefs:
        try:
            import datetime as _dt_bts
            _bts_d = _prefs["bts_dr_end"]
            if _bts_d:
                st.session_state["bts_dr_end"] = _dt_bts.date.fromisoformat(str(_bts_d))
        except (ValueError, TypeError):
            pass
    if "trading_mode" in _prefs:
        _saved_is_paper = _prefs["trading_mode"] == "paper"
        set_trading_mode(_saved_is_paper)
        st.session_state["_trading_mode"] = _prefs["trading_mode"]
    else:
        st.session_state["_trading_mode"] = "paper" if get_trading_mode() else "live"
    st.session_state["_cached_prefs"] = _prefs
    st.session_state["_prefs_loaded"] = True

if _AUTH_USER_ID and not st.session_state.get("_watchlist_loaded"):
    _early_wl = _cached_load_watchlist(user_id=_AUTH_USER_ID)
    _joined = ", ".join(_early_wl) if _early_wl else _DEFAULT_WATCHLIST
    st.session_state["_watchlist_tickers"] = _joined
    st.session_state["watchlist_raw"]      = _joined
    st.session_state["watchlist_textarea"] = _joined
    st.session_state["_watchlist_loaded"]  = True

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    # ── Database connection status ─────────────────────────────────────────
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_db_status() -> tuple[bool, str]:
        return check_db_connection()

    _db_ok, _db_err = _cached_db_status()
    if _db_ok:
        st.markdown(
            '<div style="background:#0a1a0a; border:1px solid #2e7d32; border-radius:8px; '
            'padding:8px 12px; margin-bottom:8px;">'
            '<span style="font-size:12px; font-weight:700; color:#66bb6a;">'
            '🟢 Database: Connected</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    elif _db_err == "Credentials not configured":
        st.markdown(
            '<div style="background:#1a1a1a; border:1px solid #555; border-radius:8px; '
            'padding:8px 12px; margin-bottom:8px;">'
            '<span style="font-size:12px; font-weight:700; color:#aaa;">'
            '⚪ Database: Not configured</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        _db_badge_col, _db_btn_col = st.columns([3, 1])
        with _db_badge_col:
            st.markdown(
                '<div style="background:#1a0a0a; border:1px solid #c62828; border-radius:8px; '
                'padding:8px 12px; margin-bottom:8px;">'
                f'<span style="font-size:12px; font-weight:700; color:#ef5350;">'
                f'🔴 Database: Unreachable</span>'
                f'<br><span style="font-size:10px; color:#e57373;">{_db_err}</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        with _db_btn_col:
            st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
            if st.button("↩", key="_db_retry_btn",
                         help="Retry: clear cached status and recheck the database connection"):
                _cached_db_status.clear()
                st.rerun()

    # ── Credential health indicator ────────────────────────────────────────
    _cred_healthy_ts = get_runtime_last_healthy_ts()
    if _cred_healthy_ts > 0.0:
        _cred_elapsed_s = time.monotonic() - _cred_healthy_ts
        if _cred_elapsed_s < 60:
            _cred_age_label = "just now"
        elif _cred_elapsed_s < 3600:
            _cred_age_label = f"{int(_cred_elapsed_s // 60)} min ago"
        else:
            _cred_age_label = f"{int(_cred_elapsed_s // 3600)} hr ago"
        st.markdown(
            f'<div style="font-size:11px; color:#888; text-align:right; '
            f'margin-bottom:6px;" '
            f'title="All credentials last confirmed healthy {_cred_age_label}">'
            f"🔒 Credentials healthy {_cred_age_label}"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.header("🔑 Alpaca Credentials")
    api_key = st.text_input(
        "API Key", type="password", placeholder="Alpaca API Key",
        value=st.session_state.get("_pref_alpaca_key", ""),
        key="_sb_api_key",
    )
    secret_key = st.text_input(
        "Secret Key", type="password", placeholder="Alpaca Secret Key",
        value=st.session_state.get("_pref_alpaca_secret", ""),
        key="_sb_secret_key",
    )
    # Auto-save credentials whenever they're filled in
    _sb_uid = st.session_state.get("auth_user_id", "")

    if api_key and secret_key and _sb_uid:
        _cur_prefs = st.session_state.get("_cached_prefs", {})
        if (_cur_prefs.get("alpaca_key") != api_key or
                _cur_prefs.get("alpaca_secret") != secret_key):
            _new_prefs = {**_cur_prefs, "alpaca_key": api_key, "alpaca_secret": secret_key}
            save_user_prefs(_sb_uid, _new_prefs)
            st.session_state["_cached_prefs"]       = _new_prefs
            st.session_state["_pref_alpaca_key"]    = api_key
            st.session_state["_pref_alpaca_secret"] = secret_key

    st.markdown("---")
    st.markdown('<a id="trading-mode"></a>', unsafe_allow_html=True)
    st.header("🔀 Trading Mode")

    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_cred_check(_key: str, _secret: str, _is_paper: bool) -> dict:
        return check_credential_match_sync(_key, _secret, _is_paper)

    # If the user cancelled a Paper→Live switch in the previous run, reset the
    # radio widget key BEFORE the widget is rendered so Streamlit honours the
    # value (setting it after render is silently ignored).
    if st.session_state.pop("_tm_cancel_requested", False):
        st.session_state["_tm_radio"] = "Paper"

    _tm_current = st.session_state.get("_trading_mode", "paper" if get_trading_mode() else "live")
    _tm_idx = 0 if _tm_current == "paper" else 1
    _tm_choice = st.radio(
        "Account type",
        ["Paper", "Live"],
        index=_tm_idx,
        horizontal=True,
        key="_tm_radio",
        help=(
            "Paper — simulated trades via paper-api.alpaca.markets.\n"
            "Live — real orders via api.alpaca.markets."
        ),
    )
    _tm_is_paper = _tm_choice == "Paper"
    _tm_label = "paper" if _tm_is_paper else "live"

    # If the user toggled back to Paper while a confirmation was pending, clear it
    if _tm_label == "paper" and st.session_state.get("_tm_pending_live"):
        st.session_state.pop("_tm_pending_live", None)

    if _tm_label != _tm_current:
        if _tm_label == "live" and _tm_current == "paper":
            # Paper → Live: require explicit confirmation before committing
            st.session_state["_tm_pending_live"] = True
        else:
            # Live → Paper (or same): commit immediately, no confirmation needed
            set_trading_mode(_tm_is_paper)
            st.session_state["_trading_mode"] = _tm_label
            st.session_state.pop("_tm_pending_live", None)
            if _sb_uid:
                _tm_prefs = {**st.session_state.get("_cached_prefs", {}), "trading_mode": _tm_label}
                save_user_prefs(_sb_uid, _tm_prefs)
                st.session_state["_cached_prefs"] = _tm_prefs

    # Confirmation gate for Paper → Live switch
    if st.session_state.get("_tm_pending_live"):
        st.warning(
            "⚠️ **You are switching to LIVE mode.** "
            "Real orders will use real money. Continue?",
        )
        _conf_col1, _conf_col2 = st.columns(2)
        with _conf_col1:
            if st.button("✅ Yes, switch to Live", use_container_width=True, type="primary"):
                set_trading_mode(False)
                st.session_state["_trading_mode"] = "live"
                st.session_state.pop("_tm_pending_live", None)
                if _sb_uid:
                    _tm_prefs = {**st.session_state.get("_cached_prefs", {}), "trading_mode": "live"}
                    save_user_prefs(_sb_uid, _tm_prefs)
                    st.session_state["_cached_prefs"] = _tm_prefs
                st.rerun()
        with _conf_col2:
            if st.button("❌ Cancel", use_container_width=True):
                set_trading_mode(True)
                st.session_state["_trading_mode"] = "paper"
                # Signal the next render to reset the radio BEFORE it's drawn
                st.session_state["_tm_cancel_requested"] = True
                st.session_state.pop("_tm_pending_live", None)
                st.rerun()

    # Resolve the effective mode for the status badge below
    _effective_is_paper = (
        st.session_state.get("_trading_mode", _tm_label) == "paper"
    )

    if _effective_is_paper:
        st.markdown(
            '<div style="background:#0a1a2a; border:1px solid #1565c0; border-radius:8px; '
            'padding:8px 12px; margin:4px 0;">'
            '<span style="font-size:12px; font-weight:700; color:#90caf9;">🔵 Paper mode active</span><br>'
            '<span style="font-size:11px; color:#64b5f6;">Orders go to the Alpaca paper endpoint. '
            'No real money at risk.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:#1a0a0a; border:1px solid #b71c1c; border-radius:8px; '
            'padding:8px 12px; margin:4px 0;">'
            '<span style="font-size:12px; font-weight:700; color:#ef5350;">🔴 Live mode active</span><br>'
            '<span style="font-size:11px; color:#e57373;">Orders route to your real brokerage account.</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    if api_key and secret_key:
        _cred_result = _cached_cred_check(api_key, secret_key, _effective_is_paper)
        if _cred_result.get("error"):
            st.caption(f"⚠️ Could not verify credentials: {_cred_result['error']}")
        elif not _cred_result.get("matched"):
            _key_type = "paper" if _cred_result.get("key_is_paper") else "live"
            _wanted   = "paper" if _effective_is_paper else "live"
            st.warning(
                f"⚠️ **Credential mismatch:** your Alpaca keys belong to a **{_key_type}** "
                f"account but Trading Mode is set to **{_wanted}**. "
                f"Switch the toggle or replace your keys.",
                icon="⚠️",
            )

    st.markdown("---")
    st.header("📱 Telegram Alerts")
    import os as _os_tg
    _tg_live = bool(
        _os_tg.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and
        _os_tg.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )
    if _tg_live:
        st.markdown(
            '<div style="background:#0a1a0a; border:1px solid #2e7d32; border-radius:8px; '
            'padding:10px 14px;">'
            '<span style="font-size:13px; font-weight:700; color:#66bb6a;">✅ Telegram Active</span><br>'
            '<span style="font-size:11px; color:#388e3c;">'
            'Alerts fire automatically via <b>@edgeiq_alerts_bot</b>.<br>'
            'Triggers: TCS ≥ 80 + Edge Score ≥ 75 (Playbook) · '
            'Morning scan setups · EOD summary.'
            '</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.warning("Telegram not configured. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to Secrets.", icon="⚠️")

    st.markdown("**TCS Threshold-Shift Alert Structures**")
    st.caption(
        "Choose which market structures trigger a Telegram alert when the TCS "
        "threshold shifts ≥ 5 pts overnight. Untick all to silence every TCS shift alert."
    )
    _tcs_opted = _cached_load_tcs_alert_structures()
    _tcs_all_keys = list(WK_DISPLAY.keys())
    _tcs_sel: dict = {}
    _tcs_col_a, _tcs_col_b = st.columns(2)
    for _tcs_i, _tcs_key in enumerate(_tcs_all_keys):
        _tcs_label = WK_DISPLAY[_tcs_key]
        _tcs_default = (_tcs_opted is None) or (_tcs_key in _tcs_opted)
        _tcs_col = _tcs_col_a if _tcs_i % 2 == 0 else _tcs_col_b
        with _tcs_col:
            _tcs_sel[_tcs_key] = st.checkbox(
                _tcs_label, value=_tcs_default, key=f"tcs_alert_struct_{_tcs_key}"
            )
    if st.button("💾 Save alert preferences", key="tcs_alert_save_btn", use_container_width=True):
        _tcs_chosen = {k for k, v in _tcs_sel.items() if v}
        _tcs_ok = save_tcs_alert_structures(_tcs_chosen)
        if _tcs_ok:
            st.success("Alert preferences saved.", icon="✅")
        else:
            st.error("Could not save preferences — check file permissions.", icon="⚠️")

    import os as _os_alert, datetime as _dt_alert
    _tcs_cfg_path = _os_alert.path.join(_os_alert.path.dirname(__file__) or ".", "tcs_alert_config.json")
    if _os_alert.path.exists(_tcs_cfg_path):
        _tcs_mtime = _os_alert.path.getmtime(_tcs_cfg_path)
        _tcs_saved_str = _dt_alert.datetime.fromtimestamp(_tcs_mtime).strftime("%b %d, %Y at %I:%M %p")
        st.caption(f"Last saved: {_tcs_saved_str}")
    else:
        st.caption("Preferences not yet saved — defaulting to all structures opted in.")

    st.markdown("---")
    mode = st.radio("Mode", ["📅 Historical", "🎬 Replay", "🔴 Live Stream"], index=0)

    st.markdown("---")
    st.header("📈 Settings")
    _pending_ticker = st.session_state.get("_load_ticker", "")
    if _pending_ticker:
        st.session_state["ticker_input"] = _pending_ticker
        try:
            del st.session_state["_load_ticker"]
        except Exception:
            st.session_state["_load_ticker"] = ""
    ticker = st.text_input("Ticker Symbol", key="ticker_input",
                           placeholder="e.g. AAPL, GME").upper().strip()
    num_bins = st.slider("Volume Profile Bins", min_value=20, max_value=200, value=100, step=10)
    sector_etf = st.selectbox(
        "Sector ETF (for Tailwind)",
        ["IWM", "XBI", "SMH", "QQQ", "SPY", "XLF", "XLE"],
        index=0,
        help="If this ETF is up > 1% on the day, TCS gets a +10 pt Sector Tailwind bonus."
    )

    st.markdown("---")
    # ── Lunar Cycle ─────────────────────────────────────────────────────────
    _lunar = get_lunar_phase()
    _lunar_color = "#ff6b35" if _lunar["retail_mania"] else "#888"
    st.markdown(
        f'<div style="font-size:13px; padding:4px 0 2px 0;">'
        f'{_lunar["emoji"]} <span style="color:{_lunar_color}; font-weight:{"700" if _lunar["retail_mania"] else "400"};">'
        f'{_lunar["icon_label"]}</span> '
        f'<span style="color:#555; font-size:10px;">(day {_lunar["moon_age_days"]:.0f})</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")
    st.header("🌡️ Macro Regime")
    st.caption("Stockbee breadth data — top-down tape filter")

    _br_c1, _br_c2 = st.columns(2)
    with _br_c1:
        _br_4pct = st.number_input(
            "Stocks up 4%+/day", min_value=0, max_value=3000, step=10,
            value=st.session_state.get("breadth_4pct_count", 0),
            key="breadth_4pct_input",
            help="From Stockbee — # of stocks up 4%+ today. 300+ = strong, 600+ = stampede.",
        )
    with _br_c2:
        _br_ratio = st.number_input(
            "13%/34d A/D Ratio", min_value=0.0, max_value=15.0, step=0.1,
            format="%.1f",
            value=st.session_state.get("breadth_13_34_ratio", 0.0),
            key="breadth_ratio_input",
            help=(
                "Stockbee 13%/34d Advance/Decline ratio. "
                "Stocks up 13% in the last month vs stocks up 34% in the last two months. "
                "Above 1.0 = more advances than declines."
            ),
        )
    _br_c3, _br_c4 = st.columns(2)
    with _br_c3:
        _br_qup = st.number_input(
            "Stocks +25%/Q", min_value=0, max_value=4000, step=10,
            value=st.session_state.get("breadth_q_up", 0),
            key="breadth_qup_input",
            help="Stocks up 25%+ in a quarter (green line on Stockbee chart).",
        )
    with _br_c4:
        _br_qdown = st.number_input(
            "Stocks -25%/Q", min_value=0, max_value=4000, step=10,
            value=st.session_state.get("breadth_q_down", 0),
            key="breadth_qdown_input",
            help="Stocks down 25%+ in a quarter (red line on Stockbee chart).",
        )

    _table_ready   = ensure_macro_breadth_log_table()
    _col_ready     = ensure_paper_trades_regime_column()
    if not _table_ready or not _col_ready:
        with st.expander("⚠️ One-time setup required", expanded=False):
            st.caption(
                "Run the SQL below in your "
                "[Supabase SQL Editor](https://supabase.com/dashboard) to finish setup:"
            )
            if not _table_ready:
                st.markdown("**1. Create macro_breadth_log table:**")
                st.code(_MACRO_BREADTH_SQL, language="sql")
            if not _col_ready:
                st.markdown("**2. Add regime_tag column to paper_trades:**")
                st.code(_PAPER_TRADES_REGIME_MIGRATION, language="sql")

    if st.button("💾 Save Regime", use_container_width=True, key="breadth_save_btn"):
        if _br_4pct > 0 or _br_ratio > 0:
            _new_regime = classify_macro_regime(_br_4pct, _br_ratio, _br_qup, _br_qdown)
            st.session_state["breadth_4pct_count"]  = int(_br_4pct)
            st.session_state["breadth_13_34_ratio"] = float(_br_ratio)
            st.session_state["breadth_q_up"]        = int(_br_qup)
            st.session_state["breadth_q_down"]      = int(_br_qdown)
            st.session_state["breadth_regime"]      = _new_regime
            _uid = st.session_state.get("auth_user_id", "")
            _saved = save_breadth_regime(
                date.today(), _br_4pct, _br_ratio, _br_qup, _br_qdown, user_id=_uid
            )
            _save_label = "saved" if _saved else "saved locally (Supabase table missing)"
            st.success(f"{_new_regime['label']} — {_new_regime['mode'].replace('_', ' ').title()} {_save_label}")
            st.rerun()
        else:
            st.warning("Enter at least the 4%/day count to save.")

    # Show current regime inline in sidebar
    _sb_regime = st.session_state.get("breadth_regime")
    if _sb_regime and _sb_regime.get("regime_tag", "unknown") != "unknown":
        _sbrc = _sb_regime["color"]
        _sbrl = _sb_regime["label"]
        _sbrm = {"home_run": "🏠 Home Run", "singles": "⚾ Singles", "caution": "⚠️ Caution"}.get(
            _sb_regime.get("mode", ""), ""
        )
        _sbd = _sb_regime.get("trade_date", "")
        st.markdown(
            f'<div style="background:#111; border-left:3px solid {_sbrc}; border-radius:6px; '
            f'padding:8px 12px; margin-top:6px;">'
            f'<span style="font-size:13px; font-weight:700; color:{_sbrc};">{_sbrl}</span>'
            f'<span style="font-size:11px; color:#aaa;"> · {_sbrm}</span>'
            + (f'<br><span style="font-size:10px; color:#666;">{_sbd}</span>' if _sbd else "")
            + '</div>',
            unsafe_allow_html=True,
        )

    # ── Kalshi Prediction Bot panel ──────────────────────────────────────────
    with st.sidebar.expander("📊 Kalshi Prediction Bot", expanded=False):
        _kalshi_table_ok = ensure_kalshi_tables()
        if not _kalshi_table_ok:
            st.caption("One-time setup: run this SQL in your Supabase SQL Editor.")
            st.code(_KALSHI_PREDICTIONS_SQL, language="sql")
        else:
            _kalshi_uid = st.session_state.get("auth_user_id", "")
            _kalshi_perf = get_kalshi_performance_summary(user_id=_kalshi_uid)
            _kt = _kalshi_perf["total"]
            if _kt == 0:
                st.info(
                    "No paper predictions yet.\n\n"
                    "Start the **Kalshi Bot** workflow to begin paper trading "
                    "macro breadth signals against prediction markets."
                )
            else:
                _kw   = _kalshi_perf["won"]
                _kl   = _kalshi_perf["lost"]
                _kp   = _kalshi_perf["pending"]
                _kwr  = _kalshi_perf["win_rate"]
                _kpnl = _kalshi_perf["total_pnl_cents"]
                _kpnl_str = (f"+${_kpnl/100:.2f}" if _kpnl >= 0 else f"-${abs(_kpnl)/100:.2f}")
                st.markdown(
                    f'<div style="background:#0d1f2d;border-radius:8px;padding:10px 14px;">'
                    f'<div style="font-size:11px;color:#888;text-transform:uppercase;">Paper P&L</div>'
                    f'<div style="font-size:20px;font-weight:800;color:{"#4caf50" if _kpnl >= 0 else "#ef5350"};">'
                    f'{_kpnl_str}</div>'
                    f'<div style="font-size:11px;color:#aaa;margin-top:4px;">'
                    f'✅ {_kw} · ❌ {_kl} · ⏳ {_kp} pending</div>'
                    f'<div style="font-size:11px;color:#888;">Win rate: {_kwr:.1f}%  ·  {_kt} trades</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    _ctx_path = os.path.join(os.path.dirname(__file__), "edgeiq_context_for_gemini.md")
    if os.path.exists(_ctx_path):
        with open(_ctx_path, "rb") as _ctx_f:
            st.sidebar.download_button(
                label="⬇️ Download Gemini Context File",
                data=_ctx_f.read(),
                file_name="edgeiq_context_for_gemini.md",
                mime="text/markdown",
                key="dl_gemini_ctx",
            )

    st.sidebar.markdown(
        '<a href="/app/static/trade_secret.html" target="_blank" style="display:block;background:#1a1a2e;border:1px solid #2a2a4a;border-radius:6px;padding:8px 14px;color:#ef5350;font-size:13px;font-weight:700;text-decoration:none;margin:4px 0;text-align:center;">🔒 Trade Secret & NDA Document</a>',
        unsafe_allow_html=True,
    )

    with st.sidebar.expander("🔧 Database Migrations", expanded=False):
        st.caption("Run pending ALTER TABLE migrations on Supabase.")
        if st.button("🔄 Run Migrations", key="run_migrations_btn"):
            _mig_res = run_pending_migrations()
            if _mig_res.get("needs_exec_sql"):
                st.warning("The `exec_sql` function doesn't exist yet in your Supabase. "
                           "Copy and run this SQL in Supabase SQL Editor first:")
                st.code(_ALL_PENDING_MIGRATIONS, language="sql")
            elif _mig_res["ran"] > 0:
                st.success(f"✅ Ran {_mig_res['ran']} migration(s) successfully!")
                if _mig_res["already_exist"] > 0:
                    st.info(f"{_mig_res['already_exist']} column(s) already existed.")
            elif _mig_res["already_exist"] > 0:
                st.info(f"All {_mig_res['already_exist']} column(s) already exist — nothing to do.")
            else:
                st.error(f"Migration errors: {_mig_res['errors']}")
        with st.expander("📋 View SQL", expanded=False):
            st.code(_ALL_PENDING_MIGRATIONS, language="sql")

    # ── Close Price Backfill ───────────────────────────────────────────────────
    with st.sidebar.expander("📥 Close Price Backfill", expanded=False):
        st.caption(
            "Fetches missing EOD close prices from Alpaca for all historical trades, "
            "then recomputes P&L. Runs in the background — may take several minutes."
        )
        # Derive current status from status file (persists across Streamlit re-runs).
        # The in-process _BACKFILL_LOCK is the authoritative concurrency guard;
        # the status file is used purely for UI display.
        _bf_file_status = "idle"
        if os.path.exists(_BACKFILL_STATUS):
            try:
                with open(_BACKFILL_STATUS) as _sf:
                    _bf_file_status = _sf.read().strip() or "idle"
            except Exception:
                _bf_file_status = "idle"
        elif os.path.exists(_BACKFILL_LOG):
            # Log file exists but no status written yet → thread still starting
            _bf_file_status = "running"

        # Whether a thread is actively holding the lock right now
        _bf_lock_held = not _BACKFILL_LOCK.acquire(blocking=False)
        if not _bf_lock_held:
            # We acquired it just to check — release immediately
            _BACKFILL_LOCK.release()

        # ── Helper: clear temp files and launch a new pipeline run ───────────
        def _bf_launch():
            """Clear old logs/status and start a new pipeline thread."""
            for _p in [_BACKFILL_LOG, _BACKFILL_STATUS]:
                try:
                    os.remove(_p)
                except FileNotFoundError:
                    pass
            if _BACKFILL_LOCK.acquire(blocking=False):
                with open(_BACKFILL_STATUS, "w") as _sf:
                    _sf.write("running")
                _bt = threading.Thread(target=_backfill_pipeline_thread, daemon=True)
                _bt.start()
                return True
            return False  # Lock unexpectedly held

        if _bf_lock_held:
            # A pipeline is actively running (lock is held by the background thread)
            st.info("⏳ Backfill is running in the background…")
            st.caption("Click Refresh to see the latest log output.")
            if st.button("🔄 Refresh Status", use_container_width=True, key="bf_refresh_btn"):
                st.rerun()

        elif _bf_file_status == "idle":
            st.markdown(
                '<div style="font-size:12px;color:#888;margin-bottom:6px;">'
                'No backfill has been run yet.</div>',
                unsafe_allow_html=True,
            )
            if st.button("▶️ Start Backfill", use_container_width=True, key="bf_start_btn"):
                if _bf_launch():
                    st.rerun()
                else:
                    st.warning("A backfill is already in progress — please wait.")

        elif _bf_file_status == "running" and not _bf_lock_held:
            # Status file says "running" but the lock is free → the process
            # restarted or crashed while a run was in progress (stale state).
            st.warning(
                "A previous backfill run did not finish cleanly (the app may have "
                "restarted while it was running). The log below shows what ran before "
                "the interruption. You can start a fresh run now."
            )
            if st.button("▶️ Start Fresh Run", use_container_width=True, key="bf_stale_btn"):
                if _bf_launch():
                    st.rerun()
                else:
                    st.warning("Could not acquire lock — please try again.")

        elif _bf_file_status == "done":
            st.success("✅ Backfill complete!")
            if st.button("🔄 Run Again", use_container_width=True, key="bf_again_btn"):
                if _bf_launch():
                    st.rerun()
                else:
                    st.warning("A backfill is already in progress — please wait.")

        elif _bf_file_status == "error":
            st.error("❌ Backfill encountered an error. Check the log below.")
            if st.button("🔄 Retry", use_container_width=True, key="bf_retry_btn"):
                if _bf_launch():
                    st.rerun()
                else:
                    st.warning("A backfill is already in progress — please wait.")

        else:
            # Unexpected status value — allow manual reset
            st.warning(f"Unexpected status: {_bf_file_status!r}. Clear state to reset.")
            if st.button("🗑 Clear State", use_container_width=True, key="bf_clear_btn"):
                for _p in [_BACKFILL_LOG, _BACKFILL_STATUS]:
                    try:
                        os.remove(_p)
                    except FileNotFoundError:
                        pass
                st.rerun()

        if os.path.exists(_BACKFILL_LOG):
            with st.expander(
                "📋 View Log",
                expanded=(_bf_file_status in ("done", "error")),
            ):
                try:
                    with open(_BACKFILL_LOG) as _lf:
                        _log_txt = _lf.read()
                    if _log_txt:
                        # Show last 4 000 chars so the sidebar doesn't overflow
                        _preview = _log_txt[-4000:] if len(_log_txt) > 4000 else _log_txt
                        if len(_log_txt) > 4000:
                            st.caption(f"Showing last 4 000 of {len(_log_txt):,} characters.")
                        st.code(_preview, language="text")
                    else:
                        st.caption("Log is empty — pipeline may still be initialising.")
                except Exception as _le:
                    st.caption(f"Could not read log: {_le}")

    # ── Setup Checklist ────────────────────────────────────────────────────────
    _render_setup_checklist()

    run_button = start_live = stop_live = scan_button = replay_load = False
    selected_date = date.today()
    data_feed = "sip"
    watchlist_raw = ""
    scan_feed = "sip"

    if mode == "📅 Historical":
        today = date.today()
        # Default to today if it's a weekday, otherwise roll back to last Friday
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:   # Saturday → Friday
            def_d = today - timedelta(days=1)
        else:                         # Sunday → Friday
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a weekday (Mon–Fri). Today's intraday data is supported.")
        data_feed = st.selectbox("Data Feed", ["sip", "iex"], index=0,
                                  help="SIP = full national tape (recommended). IEX = free tier, regular hours only.")
        run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")

    elif mode == "🎬 Replay":
        today = date.today()
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:
            def_d = today - timedelta(days=1)
        else:
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a trading day to replay.", key="replay_date_input")
        data_feed = st.selectbox("Data Feed", ["sip", "iex"], index=0,
                                  help="SIP = full national tape (recommended). IEX = free tier fallback.", key="replay_feed_sel")
        replay_load = st.button("📥 Load Day for Replay", use_container_width=True, type="primary")

        # ── Replay controls (shown once bars are loaded) ───────────────────────
        if st.session_state.replay_bars is not None:
            _rb      = st.session_state.replay_bars
            _max_idx = len(_rb) - 1
            _cur_idx = st.session_state.replay_bar_idx

            # Current bar's ET time
            _cur_ts  = _rb.index[_cur_idx]
            _cur_et  = pd.Timestamp(_cur_ts).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rb.index[_max_idx]).tz_convert(EASTERN)

            st.markdown(
                f'<div style="background:#16213e; border:1px solid #5c6bc0; border-radius:6px; '
                f'padding:8px 14px; margin:6px 0; font-family:monospace; font-size:16px; '
                f'color:#90caf9; text-align:center; letter-spacing:1px;">'
                f'🕐 {_cur_et.strftime("%H:%M")} ET &nbsp;|&nbsp; '
                f'Bar {_cur_idx + 1} / {_max_idx + 1}</div>',
                unsafe_allow_html=True,
            )

            # Bar slider
            replay_bar_idx = st.slider(
                "Bar (time)", min_value=0, max_value=_max_idx,
                value=_cur_idx, step=1,
                format="%d",
                key="replay_slider",
            )
            if replay_bar_idx != _cur_idx:
                st.session_state.replay_bar_idx = replay_bar_idx
                st.session_state.replay_playing = False
                st.rerun()

            # Playback controls
            _sp_label = {"Slow (1 bar/step)": 1, "Normal (2 bars/step)": 2,
                         "Fast (5 bars/step)": 5, "Turbo (10 bars/step)": 10}
            _sp_sel = st.selectbox("Speed", list(_sp_label.keys()), index=1,
                                    key="replay_speed_sel")
            st.session_state.replay_speed = _sp_label[_sp_sel]

            rc1, rc2, rc3, rc4 = st.columns(4)
            if rc1.button("⏮", help="Jump to start"):
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()
            if rc2.button("◀", help="Step back"):
                st.session_state.replay_bar_idx = max(0, _cur_idx - st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()
            if rc3.button("▶" if not st.session_state.replay_playing else "⏸",
                          help="Play / Pause"):
                st.session_state.replay_playing = not st.session_state.replay_playing
                st.rerun()
            if rc4.button("▶▶", help="Step forward"):
                st.session_state.replay_bar_idx = min(_max_idx, _cur_idx + st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()

            if st.button("🗑 Clear / Load new day", use_container_width=True):
                st.session_state.replay_bars    = None
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()

    else:
        live_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX = free real-time feed. SIP = full national tape (requires paid Alpaca subscription).")
        if not st.session_state.live_active:
            start_live = st.button("▶ Start Live Stream", use_container_width=True, type="primary")
        else:
            stop_live = st.button("⏹ Stop", use_container_width=True)
            st.success(f"🔴 Live: **{st.session_state.live_ticker}**")

    st.markdown("---")

    # ── Position Management ────────────────────────────────────────────────────
    st.header("📍 Position")
    _snap   = st.session_state.get("last_analysis_state") or {}
    _p_in   = st.session_state.position_in
    _p_tkr  = st.session_state.position_ticker
    _p_ent  = st.session_state.position_avg_entry
    _p_mfe  = st.session_state.position_peak_price
    _p_shr  = st.session_state.position_shares
    _p_strc = st.session_state.position_structure
    _cur    = _snap.get("price", 0.0)

    if _p_in:
        pnl_pct = (_cur - _p_ent) / _p_ent * 100 if _p_ent > 0 else 0
        pnl_dol = (_cur - _p_ent) * _p_shr if _p_shr > 0 else 0
        pnl_col = "#4caf50" if pnl_pct >= 0 else "#ef5350"
        st.markdown(
            f'<div style="background:{pnl_col}11; border:1px solid {pnl_col}55; '
            f'border-radius:6px; padding:10px 14px; margin-bottom:8px;">'
            f'<div style="font-size:11px; color:#888; margin-bottom:4px;">OPEN — {_p_tkr} × {_p_shr} sh</div>'
            f'<div style="font-size:13px; color:#ccc;">Entry: <b>${_p_ent:.2f}</b> &nbsp;|&nbsp; '
            f'MFE: <b>${_p_mfe:.2f}</b></div>'
            f'<div style="font-size:20px; font-weight:800; color:{pnl_col}; margin-top:4px;">'
            f'{"▲" if pnl_pct>=0 else "▼"} {abs(pnl_pct):.2f}%'
            f'{"  ($" + f"{pnl_dol:+.0f}" + ")" if _p_shr > 0 else ""}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        exit_px = st.number_input("Exit Price", value=_cur if _cur else _p_ent,
                                   step=0.01, format="%.2f", key="pos_exit_px")
        exit_struct = st.text_input("Actual Structure (opt.)", value="", key="pos_exit_struct",
                                    placeholder="e.g. Trend Day")
        if st.button("🔴 Exit Position", use_container_width=True, key="pos_exit_btn"):
            _realized = exit_position(exit_px, actual_structure=exit_struct or _p_strc)
            st.success(f"✅ Exited — Realized P&L: ${_realized:+.2f}" if _p_shr > 0
                       else "✅ Position closed.")
            st.rerun()
    else:
        _default_tkr   = _snap.get("ticker", "")
        _default_strc  = _snap.get("structure", "")
        _default_price = _snap.get("price", 0.0)

        e_tkr  = st.text_input("Ticker", value=_default_tkr, key="pos_entry_tkr")
        e_px   = st.number_input("Entry Price",
                                  value=float(_default_price) if _default_price else 0.0,
                                  step=0.01, format="%.2f", key="pos_entry_px")
        e_strc = st.text_input("Structure at Entry", value=_default_strc, key="pos_entry_strc")

        # ── Auto-Size to Risk ─────────────────────────────────────────────────
        _auto_size = st.checkbox(
            "⚡ Auto-Size to Risk",
            value=False,
            key="pos_auto_size",
            help=(
                "Calculates shares from your SA Challenge account size & risk %.\n"
                "Risk Amount = Account Balance × Risk %\n"
                "Risk per Share = Entry Price − Stop Loss Price\n"
                "Shares = Risk Amount ÷ Risk per Share (rounded down)"
            ),
        )

        _acct_bal = float(st.session_state.get("sa_account_bal", 5000.0))
        _risk_pct = float(st.session_state.get("sa_risk_pct", 2.0))

        if _auto_size:
            # Stop Loss Price input replaces Shares input
            _sl_default = round(e_px - 0.10, 2) if e_px > 0 else 0.0
            e_sl = st.number_input(
                "Stop Loss Price",
                value=_sl_default,
                step=0.01,
                format="%.2f",
                key="pos_entry_sl",
                help="Your hard stop. Risk per share = Entry Price − Stop Loss Price.",
            )
            _risk_amt      = _acct_bal * (_risk_pct / 100.0)
            _risk_per_share = e_px - e_sl if e_px > e_sl else 0.0
            _calc_shares   = max(1, int(_risk_amt / _risk_per_share)) if _risk_per_share > 0 else 1
            # Live sizing card
            _sl_color = "#ef5350" if _risk_per_share <= 0 else "#4caf50"
            st.markdown(
                f'<div style="background:#0a0f1e; border:1px solid #1a2744; border-radius:6px; '
                f'padding:9px 13px; margin:4px 0 6px 0; font-size:12px; color:#90caf9; line-height:1.7;">'
                f'💰 Risk&nbsp;$: <b>${_risk_amt:.0f}</b> &nbsp;|&nbsp; '
                f'Risk/sh: <b style="color:{_sl_color};">'
                f'{"$" + f"{_risk_per_share:.2f}" if _risk_per_share > 0 else "⚠ entry ≤ stop"}'
                f'</b> &nbsp;|&nbsp; '
                f'<span style="font-size:14px; font-weight:800; color:#4caf50;">'
                f'Shares: {_calc_shares if _risk_per_share > 0 else "—"}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            e_shr = _calc_shares
        else:
            e_sl  = None
            e_shr = st.number_input("Shares", value=100, step=1, min_value=1, key="pos_entry_shr")

        # ── Enter Position button ─────────────────────────────────────────────
        if st.button("🟢 Enter Position", use_container_width=True,
                     key="pos_enter_btn", type="primary"):
            if not e_tkr:
                st.error("Enter a ticker symbol.")
            elif e_px <= 0:
                st.error("Enter a valid entry price.")
            elif _auto_size and e_sl is not None and e_sl >= e_px:
                st.error("Stop Loss must be below Entry Price.")
            elif api_key and secret_key:
                # ── Alpaca first — local state only updates on confirmed fill ─
                with st.spinner(f"Submitting Limit order to Alpaca — {e_tkr.upper()} × {e_shr} sh…"):
                    _alp = execute_alpaca_trade(
                        api_key=api_key,
                        secret_key=secret_key,
                        is_paper=True,
                        ticker=e_tkr.upper(),
                        qty=int(e_shr),
                        side="buy",
                        limit_price=round(e_px, 2),
                    )
                if _alp["success"]:
                    enter_position(e_tkr.upper(), e_px, e_shr, e_strc)
                    st.success(
                        f"✅ Limit order placed — {e_tkr.upper()} × {e_shr} sh @ ${e_px:.2f}  "
                        f"| Alpaca ID: `{_alp['order_id']}`"
                    )
                    st.rerun()
                else:
                    st.error(f"❌ Alpaca rejected the order: {_alp['message']}")
            else:
                # No API keys — log locally only
                st.warning("⚠️ No Alpaca keys — logging position locally only (no real order placed).")
                enter_position(e_tkr.upper(), e_px, e_shr, e_strc)
                st.success(f"✅ Logged {e_tkr.upper()} × {e_shr} sh @ ${e_px:.2f} (local only)")
                st.rerun()

    st.markdown("---")

    # ── Audio alert controls ───────────────────────────────────────────────────
    st.header("🔔 Alerts")
    audio_alerts_enabled = st.checkbox(
        "Enable Audio Alerts",
        value=True,
        key="audio_alerts_enabled",
        help="Play sounds when TCS crosses 80% (chime) or drops below 30% (low tone)."
    )

    if audio_alerts_enabled:
        import streamlit.components.v1 as _comp
        _comp.html(
            """
            <style>
              .ab{background:#16213e;border:1px solid #5c6bc0;color:#aaa;
                  padding:5px 12px;border-radius:5px;cursor:pointer;
                  font-size:12px;width:100%;margin:2px 0;transition:all 0.2s;}
              .ab:hover{border-color:#90caf9;color:#e0e0e0;}
              .ab.ok{border-color:#4caf50!important;color:#4caf50!important;}
              small{color:#555;font-size:10px;line-height:1.4;display:block;margin-top:4px;}
            </style>
            <button class="ab" id="ab" onclick="
              var C=new(window.AudioContext||window.webkitAudioContext)();
              var o=C.createOscillator(),g=C.createGain();
              o.type='sine'; o.frequency.value=880;
              o.connect(g); g.connect(C.destination);
              g.gain.setValueAtTime(0.12,C.currentTime);
              g.gain.exponentialRampToValueAtTime(0.001,C.currentTime+0.3);
              o.start(); o.stop(C.currentTime+0.3);
              this.textContent='✓ Audio Ready';
              this.classList.add('ok');
            ">🔊 Enable Browser Audio</button>
            <small>Browsers block auto-play until you click above once per session.</small>
            """,
            height=68,
            scrolling=False,
        )

    st.markdown("---")

    # ── Gap Scanner Controls ───────────────────────────────────────────────────
    st.header("🔍 Gap Scanner")
    st.caption("Enter tickers to watch. Scan fetches pre-market volume and gap data.")
    # Use saved watchlist if available, fall back to default
    _scanner_default = (
        st.session_state.get("_watchlist_tickers")
        or _DEFAULT_WATCHLIST
    )
    watchlist_raw = st.text_area(
        "Watchlist (comma-separated)",
        height=110,
        help="Tickers priced $1–$50 at scan time will be analysed.",
        key="watchlist_raw",
    )
    scan_feed = st.selectbox("Scanner Feed", ["sip", "iex"], index=0, key="scan_feed_select",
                             help="SIP = full national tape with PM RVOL (recommended). IEX = free tier, regular hours only.")
    if scan_feed == "iex":
        st.info("ℹ️ IEX (free tier): PM Volume will be blank. Switch to SIP for full pre-market data.")
    _price_cols = st.columns(3)
    scan_min_price = _price_cols[0].number_input(
        "Min Price ($)", min_value=0.01, max_value=999.0, value=0.10,
        step=0.10, format="%.2f", key="scan_min_price",
        help="Exclude tickers below this price"
    )
    scan_max_price = _price_cols[1].number_input(
        "Max Price ($)", min_value=0.10, max_value=9999.0, value=50.0,
        step=5.0, format="%.2f", key="scan_max_price",
        help="Exclude tickers above this price"
    )
    scan_min_rvol = _price_cols[2].number_input(
        "Min RVOL (x)", min_value=0.0, max_value=50.0, value=2.0,
        step=0.5, format="%.1f", key="scan_min_rvol",
        help="Minimum PM RVOL threshold (recommended 2.0x). Only applied with SIP feed."
    )
    scan_button = st.button("🔍 Scan Gap Plays", use_container_width=True)

    st.markdown("---")
    st.caption("SIP = full national tape + pre-market data. IEX = regular hours (9:30–4 PM) only.")

    # ── Saved Watchlist (T008) ────────────────────────────────────────────────
    st.markdown("---")
    st.header("⭐ My Watchlist")
    _wl_raw = st.text_area(
        "Tickers (comma-separated)",
        height=80, key="watchlist_textarea",
        placeholder="AAPL, GME, AMC, MSTR",
    )
    _wl_cols = st.columns(2)
    if _wl_cols[0].button("💾 Save", use_container_width=True, key="wl_save_btn"):
        _wl_list = [t.strip().upper() for t in _wl_raw.replace("\n", ",").split(",")
                    if t.strip()]
        _ok = save_watchlist(_wl_list, st.session_state.get("auth_user_id", ""))
        st.session_state["_watchlist_tickers"] = ", ".join(_wl_list)
        if _ok:
            st.success(f"Saved {len(_wl_list)} tickers")
        else:
            st.warning("Saved locally (Supabase unavailable)")
    if _wl_cols[1].button("▶ Next Ticker", use_container_width=True, key="wl_load_btn"):
        _wl_list = [t.strip().upper() for t in _wl_raw.replace("\n", ",").split(",")
                    if t.strip()]
        if _wl_list:
            _wl_idx = st.session_state.get("_wl_cycle_idx", 0) % len(_wl_list)
            _sym = _wl_list[_wl_idx]
            st.session_state["_load_ticker"] = _sym
            st.session_state["_wl_cycle_idx"] = (_wl_idx + 1) % len(_wl_list)
            st.caption(f"{_wl_idx + 1}/{len(_wl_list)}: {_sym}")

    # ── Account & Sign Out ────────────────────────────────────────────────────
    st.markdown("---")
    _auth_email_disp = st.session_state.get("auth_email", "")
    if _auth_email_disp:
        st.markdown(
            f'<div style="font-size:11px; color:#90caf9; margin-bottom:6px;">'
            f'👤 {_auth_email_disp}</div>',
            unsafe_allow_html=True,
        )
        if st.button("🚪 Sign Out", use_container_width=True, key="signout_btn"):
            auth_signout()
            for _k in ("auth_user", "auth_user_id", "auth_email",
                       "_watchlist_loaded", "_watchlist_tickers",
                       "_prefs_loaded", "_restore_tried",
                       "watchlist_raw", "watchlist_textarea",
                       "_cached_prefs", "_pref_alpaca_key", "_pref_alpaca_secret",
                       "min_tcs_trades"):
                st.session_state.pop(_k, None)
            st.rerun()
    else:
        st.markdown(
            '<div style="font-size:11px; color:#555;">Not logged in</div>',
            unsafe_allow_html=True,
        )


# ── Cross-tab High Conviction alert (T005) ────────────────────────────────────
_hc_alert_state = st.session_state.get("_hc_alert_state")
if _hc_alert_state:
    _hc_sym  = _hc_alert_state.get("ticker", "")
    _hc_rvol = _hc_alert_state.get("rvol", 0)
    _hc_dir  = _hc_alert_state.get("direction", "BUY")
    _hc_clr  = "#4caf50" if _hc_dir == "BUY" else "#ef5350"
    _hc_icon = "🚀" if _hc_dir == "BUY" else "🔻"
    st.markdown(
        f'<div style="background:{_hc_clr}22; border:1.5px solid {_hc_clr}; border-radius:6px; '
        f'padding:7px 16px; margin-bottom:6px; font-size:13px; font-weight:800; color:{_hc_clr};">'
        f'{_hc_icon} HIGH CONVICTION {_hc_dir} SIGNAL ACTIVE — {_hc_sym} | '
        f'RVOL {_hc_rvol:.1f}× | Go to Main Chart to trade</div>',
        unsafe_allow_html=True,
    )

# ── Schema migration reminder ─────────────────────────────────────────────────
if not st.session_state.get("_migration_checked"):
    _col_ok = check_user_id_column_exists()
    st.session_state["_migration_checked"] = True
    st.session_state["_migration_needed"] = not _col_ok

if st.session_state.get("_migration_needed"):
    with st.expander("⚠️ Database Migration Required — click to expand", expanded=True):
        st.error(
            "Your Supabase tables are missing the **user_id** column needed for "
            "multi-user data isolation. Run the following SQL in your Supabase SQL editor:"
        )
        st.code(
            "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS user_id TEXT;\n"
            "ALTER TABLE accuracy_tracker ADD COLUMN IF NOT EXISTS user_id TEXT;",
            language="sql",
        )
        st.caption(
            "Go to app.supabase.com → your project → SQL Editor → paste + run. "
            "This banner disappears once the columns exist."
        )

st.title("📊 Volume Profile Dashboard — Small Cap Stocks")

# ── Trading Mode Badge ─────────────────────────────────────────────────────────
_hdr_tm = st.session_state.get("_trading_mode", "paper" if get_trading_mode() else "live")
if _hdr_tm == "paper":
    st.markdown(
        '<span style="display:inline-block; background:#0a1a2a; border:1px solid #1565c0; '
        'border-radius:6px; padding:3px 10px; font-size:12px; font-weight:700; '
        'color:#90caf9; margin-bottom:8px;" '
        'title="Trading Mode: Paper — simulated orders only. Switch in the sidebar.">'
        '🔵 Paper</span>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<span style="display:inline-block; background:#1a0a0a; border:1px solid #b71c1c; '
        'border-radius:6px; padding:3px 10px; font-size:12px; font-weight:700; '
        'color:#ef5350; margin-bottom:8px;" '
        'title="Trading Mode: Live — real orders will be routed to your brokerage. Switch in the sidebar.">'
        '🔴 Live</span>',
        unsafe_allow_html=True,
    )

# ── Live Pulse Header ──────────────────────────────────────────────────────────
_las = st.session_state.get("last_analysis_state")
if _las:
    _lbl    = _las.get("structure", "")
    _tcs    = _las.get("tcs", 0.0)
    _rvol   = _las.get("rvol")
    _sym    = _las.get("ticker", "")
    _pr     = _las.get("price", 0.0)
    _lc     = _las.get("label_color", "#90caf9")
    _rc     = _las.get("rvol_color", "#aaa")
    _runner = _las.get("is_runner", False)
    _edge   = _las.get("edge_score")
    _e_bkd  = _las.get("edge_breakdown", {})

    _rvol_str = f"{_rvol:.1f}×" if _rvol is not None else "—"
    _tcs_fill = ("linear-gradient(90deg,#FFD700,#00BFFF)" if _runner
                 else "linear-gradient(90deg,#4caf50,#4caf50)" if _tcs >= 70
                 else "linear-gradient(90deg,#ef5350,#ef5350)" if _tcs <= 30
                 else "linear-gradient(90deg,#ffa726,#ffa726)")
    if _edge is None:
        _edge_str, _edge_col = "—", "#37474f"
    elif _edge >= 75:
        _edge_str, _edge_col = f"{_edge:.0f}", "#4caf50"
    elif _edge >= 50:
        _edge_str, _edge_col = f"{_edge:.0f}", "#ffa726"
    else:
        _edge_str, _edge_col = f"{_edge:.0f}", "#ef5350"
    _edge_fill = (
        "linear-gradient(90deg,#4caf50,#26a69a)" if (_edge or 0) >= 75
        else "linear-gradient(90deg,#ffa726,#ff7043)" if (_edge or 0) >= 50
        else "linear-gradient(90deg,#ef5350,#b71c1c)"
    ) if _edge is not None else "linear-gradient(90deg,#37474f,#37474f)"
    _edge_sub = (
        f"TCS {_e_bkd.get('tcs_pts',0):.0f} + "
        f"Struct {_e_bkd.get('struct_pts',0):.0f} + "
        f"Env {_e_bkd.get('env_pts',0):.0f} + "
        f"Tape {_e_bkd.get('fb_pts',0):.0f}"
    ) if _e_bkd else "Run analysis to compute"

    st.markdown(f"""
    <div style="display:flex; gap:12px; flex-wrap:wrap; margin:0 0 4px 0;">
        <div style="flex:1.2; min-width:200px; background:linear-gradient(135deg,{_lc}22,{_lc}0a);
                    border-left:4px solid {_lc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">Structure</div>
            <div style="font-size:20px; font-weight:800; color:{_lc};">{_lbl}</div>
            <div style="font-size:12px; color:#aaa; margin-top:2px;">{_sym} · ${_pr:.2f}</div>
        </div>
        <div style="flex:1; min-width:160px; background:#12122288;
                    border-left:4px solid #90caf9; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:6px;">TCS</div>
            <div style="background:#2a2a4a; border-radius:6px; height:8px; overflow:hidden; margin-bottom:6px;">
                <div style="width:{min(_tcs,100):.0f}%; background:{_tcs_fill};
                            height:100%; border-radius:6px;"></div>
            </div>
            <div style="font-size:22px; font-weight:900; color:{'#FFD700' if _runner else '#90caf9'};">{_tcs:.0f}%</div>
        </div>
        <div style="flex:1; min-width:140px; background:#12122288;
                    border-left:4px solid {_rc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">RVOL</div>
            <div style="font-size:22px; font-weight:900; color:{_rc};">{_rvol_str}</div>
            <div style="font-size:11px; color:#666; margin-top:2px;">
                {'⚡ RUNNER' if _runner else ('🔥 In Play' if _rvol and _rvol > 3 else '— Normal')}
            </div>
        </div>
        <div style="flex:1; min-width:160px; background:linear-gradient(135deg,{_edge_col}18,{_edge_col}08);
                    border-left:4px solid {_edge_col}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:6px;">Edge Score</div>
            <div style="background:#2a2a4a; border-radius:6px; height:8px; overflow:hidden; margin-bottom:6px;">
                <div style="width:{min((_edge or 0),100):.0f}%; background:{_edge_fill};
                            height:100%; border-radius:6px;"></div>
            </div>
            <div style="font-size:24px; font-weight:900; color:{_edge_col}; font-family:monospace;">{_edge_str}</div>
            <div style="font-size:9px; color:#546e7a; margin-top:3px;">{_edge_sub}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Predictive context — your personal historical win rate for this setup ───
    try:
        if _AUTH_USER_ID and _edge is not None and _rvol is not None:
            _pred_ctx = get_predictive_context(
                _AUTH_USER_ID, float(_edge), float(_rvol), str(_lbl)
            )
            _pe = _pred_ctx.get("exact", {})
            _pb = _pred_ctx.get("by_struct", {})
            _po = _pred_ctx.get("overall", {})
            _show_pred = _pe or (_pb and _pb.get("n", 0) >= 3) or (_po and _po.get("n", 0) >= 5)
            if _show_pred:
                if _pe and _pe.get("n", 0) >= 3:
                    _wr = _pe["win_rate"] * 100
                    _wn = _pe["n"]
                    _wlbl = f"Exact setup: {_wr:.0f}% win rate ({_wn} trades)"
                    _wcol = "#4caf50" if _wr >= 60 else "#ffa726" if _wr >= 45 else "#ef5350"
                elif _pb and _pb.get("n", 0) >= 3:
                    _wr = _pb["win_rate"] * 100
                    _wn = _pb["n"]
                    _wlbl = f"{_lbl}: {_wr:.0f}% win rate ({_wn} trades)"
                    _wcol = "#4caf50" if _wr >= 60 else "#ffa726" if _wr >= 45 else "#ef5350"
                elif _po and _po.get("n", 0) >= 5:
                    _wr = _po["win_rate"] * 100
                    _wn = _po["n"]
                    _wlbl = f"Overall: {_wr:.0f}% win rate ({_wn} trades logged)"
                    _wcol = "#4caf50" if _wr >= 60 else "#ffa726" if _wr >= 45 else "#ef5350"
                else:
                    _show_pred = False
                if _show_pred:
                    st.markdown(
                        f'<div style="background:{_wcol}18; border:1px solid {_wcol}55; '
                        f'border-radius:6px; padding:7px 16px; font-size:13px; '
                        f'font-weight:600; color:{_wcol}; margin:4px 0 6px 0;">'
                        f'📊 Your historical data: {_wlbl}</div>',
                        unsafe_allow_html=True
                    )
    except Exception:
        pass

    # Alert Banner
    if _runner or _tcs >= 80:
        st.markdown(
            '<div style="background:#FFD70022; border:1px solid #FFD700; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#FFD700; margin:4px 0 8px 0;">'
            f'🚀 STOCK IN PLAY — {_sym} | TCS {_tcs:.0f}% | RVOL {_rvol_str}</div>',
            unsafe_allow_html=True
        )
    elif _tcs <= 30:
        st.markdown(
            '<div style="background:#ef535022; border:1px solid #ef5350; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#ef5350; margin:4px 0 8px 0;">'
            f'⚠ CAUTION — Low Conviction | TCS {_tcs:.0f}% | Chop Risk Active</div>',
            unsafe_allow_html=True
        )

_STRUCTURE_COLORS_MAP = {
    "trend":       "#ff9800",
    "bear":        "#ff5722",
    "double":      "#00bcd4",
    "non":         "#78909c",
    "normal var":  "#aed581",
    "variation":   "#aed581",
    "neutral ext": "#7e57c2",
    "neutral":     "#80cbc4",
    "normal":      "#66bb6a",
    "balanced":    "#66bb6a",
}

def _structure_color(label_str):
    """Return a color for a structure label string."""
    s = label_str.lower()
    for key, col in _STRUCTURE_COLORS_MAP.items():
        if key in s:
            return col
    return "#5c6bc0"


# ── Live Playbook Screener Tab ──────────────────────────────────────────────────
def render_playbook_tab(api_key: str = "", secret_key: str = ""):
    """Render the 📋 Playbook tab — live small-cap screener with one-click journal routing."""

    # ── Header ─────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-bottom:4px;">'
        '<span style="font-size:22px; font-weight:800; color:#e0e0e0;">📋 Live Playbook</span>'
        '<span style="font-size:12px; color:#5c6bc0; margin-left:14px; text-transform:uppercase; '
        'letter-spacing:1px;">Small-Cap Screener · $2 – $20 · Alpaca Market Data</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-size:12px; color:#607d8b; margin-bottom:16px;">'
        'Combines <b>Most Active</b> (by volume) and <b>Top Gainers</b> from Alpaca. '
        'Filtered to your target range. Click <b>📝 Log</b> on any row to pre-load '
        'the ticker &amp; price in your Chart → Log Entry form.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── No credentials guard ────────────────────────────────────────────────────
    if not api_key or not secret_key:
        st.warning("Enter your **Alpaca API Key** and **Secret Key** in the sidebar to enable the Playbook scanner.")
        return

    # ── Scan controls ───────────────────────────────────────────────────────────
    _pb_c1, _pb_c2, _pb_c3 = st.columns([2, 1, 1])
    with _pb_c1:
        _pb_top = st.slider("Candidates to scan per source", 20, 100, 50, step=10,
                            key="playbook_top_slider")
    with _pb_c2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        _pb_refresh = st.button("🔄 Refresh Scan", use_container_width=True,
                                key="playbook_refresh_btn")
    with _pb_c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        _pb_auto = st.checkbox("Auto-refresh (60 s)", value=False, key="playbook_auto_refresh")

    # ── Quant scoring controls ───────────────────────────────────────────────────
    st.markdown(
        '<div style="background:#0a0f1e; border:1px solid #1a2744; border-radius:8px; '
        'padding:12px 16px; margin:12px 0;">'
        '<span style="font-size:12px; font-weight:700; color:#7c4dff; text-transform:uppercase; '
        'letter-spacing:1px;">🧠 Quant Engine Scoring</span>'
        '<span style="font-size:11px; color:#607d8b; margin-left:12px;">'
        'Fetches intraday bars for each ticker and runs TCS + Structure prediction</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    _qs_c1, _qs_c2, _qs_c3 = st.columns([1.5, 1, 1])
    with _qs_c1:
        # ── Restore playbook feed from localStorage (cross-session) ───────────
        import streamlit.components.v1 as _cmp_pb_feed
        _cmp_pb_feed.html("""
<script>
(function() {
    var _LS_KEY = 'playbook_feed_radio';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('pb_feed')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('pb_feed', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)
        _PB_FEED_OPTIONS = ["SIP (recommended)", "IEX (free)"]
        if "playbook_feed_radio" not in st.session_state:
            _qp_pb_feed = st.query_params.get("pb_feed", "SIP (recommended)")
            st.session_state["playbook_feed_radio"] = (
                _qp_pb_feed if _qp_pb_feed in _PB_FEED_OPTIONS else "SIP (recommended)"
            )
        _pb_feed = st.radio(
            "Bar data feed",
            _PB_FEED_OPTIONS,
            index=_PB_FEED_OPTIONS.index(
                st.session_state.get("playbook_feed_radio", "SIP (recommended)")
                if st.session_state.get("playbook_feed_radio", "SIP (recommended)") in _PB_FEED_OPTIONS
                else "SIP (recommended)"
            ),
            horizontal=True,
            key="playbook_feed_radio",
        )
        _qp_pb_feed_cur = st.query_params.get("pb_feed")
        if _qp_pb_feed_cur != _pb_feed:
            st.query_params["pb_feed"] = _pb_feed
        _cmp_pb_feed.html(
            f"<script>localStorage.setItem('playbook_feed_radio', {repr(_pb_feed)});</script>",
            height=0,
        )
        _pb_feed_str = "iex" if "IEX" in _pb_feed else "sip"
    with _qs_c2:
        _pb_max_score = st.number_input("Max tickers to score", min_value=5, max_value=100,
                                        value=30, step=5, key="playbook_max_score")
    with _qs_c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        _pb_score_btn = st.button("🧠 Run Quant Score", use_container_width=True,
                                  key="playbook_score_btn")

    # ── Cache keys ───────────────────────────────────────────────────────────────
    _pb_cache_key  = "playbook_results_cache"
    _pb_time_key   = "playbook_last_fetch"
    _pb_score_key  = "playbook_scored_cache"

    # ── Fetch raw scan ───────────────────────────────────────────────────────────
    _should_fetch = (
        _pb_refresh
        or _pb_cache_key not in st.session_state
        or (
            _pb_auto
            and (time.time() - st.session_state.get(_pb_time_key, 0)) > 60
        )
    )

    _RVOL_MIN = 2.0   # minimum pre-market RVOL to appear in Playbook

    if _should_fetch:
        with st.spinner("Scanning Alpaca market data…"):
            _rows, _err = scan_playbook(api_key, secret_key, top=_pb_top)
        # Filter out low-RVOL stocks when SIP data is available.
        # IEX rows have pm_rvol=None — keep those since we can't measure them.
        _before = len(_rows)
        _rows = [r for r in _rows
                 if r.get("pm_rvol") is None or r.get("pm_rvol", 0) >= _RVOL_MIN]
        _filtered_out = _before - len(_rows)
        if _filtered_out:
            st.session_state["_pb_filtered_count"] = _filtered_out
        else:
            st.session_state.pop("_pb_filtered_count", None)
        st.session_state[_pb_cache_key] = (_rows, _err)
        st.session_state[_pb_time_key]  = time.time()
        st.session_state.pop(_pb_score_key, None)   # invalidate old scores
    else:
        _rows, _err = st.session_state.get(_pb_cache_key, ([], ""))

    # ── Weekend/holiday/after-hours notice ───────────────────────────────────────
    import datetime as _dt
    _today = _dt.date.today()
    _market_closed_today = not is_trading_day(_today)
    if _market_closed_today:
        _last_td = get_last_trading_day(as_of=_today, api_key=api_key, secret_key=secret_key)
        _reason  = "weekend" if _today.weekday() >= 5 else "holiday"
        st.info(
            f"📅 Market is closed ({_reason}). Quant Score will use the most recent "
            f"trading day: **{_last_td.strftime('%A, %b %d')}**."
        )
    elif _dt.datetime.now().hour < 9 or (_dt.datetime.now().hour == 9 and _dt.datetime.now().minute < 30):
        st.info("⏰ Pre-market — Quant Score will use yesterday's intraday bars.")

    # ── Run quant scoring ────────────────────────────────────────────────────────
    if _pb_score_btn and _rows:
        with st.spinner(
            f"Running quant engine on top {min(int(_pb_max_score), len(_rows))} tickers "
            f"(fetching intraday bars via {_pb_feed_str.upper()})…"
        ):
            import copy as _copy
            _rows_to_score = _copy.deepcopy(_rows)
            _scored_rows = score_playbook_tickers(
                _rows_to_score, api_key, secret_key,
                feed=_pb_feed_str, max_tickers=int(_pb_max_score),
                user_id=_AUTH_USER_ID,
            )
        st.session_state[_pb_score_key] = _scored_rows
        _rows = _scored_rows
    elif _pb_score_key in st.session_state:
        _rows = st.session_state[_pb_score_key]

    _scores_loaded = any(r.get("tcs") is not None for r in _rows) if _rows else False

    # ── Pre-load success banner ─────────────────────────────────────────────────
    _pb_loaded = st.session_state.pop("_playbook_just_loaded", None)
    if _pb_loaded:
        st.success(
            f"✅ **{_pb_loaded['ticker']}** pre-loaded at **${_pb_loaded['price']:.2f}** — "
            f"click the **📈 Main Chart** tab and scroll down to the "
            f"**Log This Trade Entry** form to complete your journal."
        )

    # ── Error handling ──────────────────────────────────────────────────────────
    if _err and _err != "market_closed":
        st.error(f"Screener error: {_err}")
    elif _err == "market_closed" and not _market_closed_today:
        st.warning("⏰ Screener returned no data — market may not be open yet. Try again after 9:30 AM ET.")

    if not _rows:
        if not _err:
            st.info("No small-cap stocks in the $2–$20 range found right now. Try refreshing after market open.")
        return

    # ── Last fetch timestamp + score hint ───────────────────────────────────────
    _ts = st.session_state.get(_pb_time_key, 0)
    if _ts:
        _ago     = int(time.time() - _ts)
        _ago_str = f"{_ago}s ago" if _ago < 120 else f"{_ago // 60}m ago"
        _scored_badge = (
            f' · <span style="color:#7c4dff;">🧠 Quant scored</span>'
            if _scores_loaded else
            f' · <span style="color:#546e7a;">Click <b>🧠 Run Quant Score</b> to add TCS & Setup</span>'
        )
        _filt_n = st.session_state.get("_pb_filtered_count", 0)
        _filt_note = (
            f' · <span style="color:#ef5350;">{_filt_n} low-RVOL (&lt;2×) removed</span>'
            if _filt_n else ""
        )
        st.markdown(
            f'<div style="font-size:11px; color:#546e7a; margin-bottom:12px;">'
            f'Last fetched: <b>{_ago_str}</b> · {len(_rows)} stocks in range'
            f'{_filt_note}{_scored_badge}</div>',
            unsafe_allow_html=True,
        )

    # ── Column layout ───────────────────────────────────────────────────────────
    # ── Calibration status banner ────────────────────────────────────────────────
    if _scores_loaded:
        _w_info = compute_adaptive_weights(_AUTH_USER_ID)
        _rows_used = _w_info.get("rows_used", 0)
        if _w_info.get("calibrated"):
            st.markdown(
                f'<div style="background:#0a1628; border:1px solid #1565c0; border-radius:6px; '
                f'padding:8px 16px; margin-bottom:10px; font-size:11px; color:#90caf9; '
                f'font-family:monospace;">🧠 EDGE SCORE AUTO-CALIBRATED · '
                f'{_rows_used} historical rows · '
                f'TCS weight {_w_info["tcs"]*100:.0f}% · '
                f'Structure {_w_info["structure"]*100:.0f}% · '
                f'Environment {_w_info["environment"]*100:.0f}% · '
                f'False Break {_w_info["false_break"]*100:.0f}% · '
                f'Sorted by Edge Score ↓</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#0a1628; border:1px solid #37474f; border-radius:6px; '
                f'padding:8px 16px; margin-bottom:10px; font-size:11px; color:#546e7a; '
                f'font-family:monospace;">⚙ EDGE SCORE using DEFAULT weights '
                f'(TCS 35% · Structure 25% · Environment 25% · False Break 15%) · '
                f'Run backtests to auto-calibrate · {_rows_used} rows saved so far</div>',
                unsafe_allow_html=True,
            )

    _COL_W = [0.85, 0.75, 0.8, 1.0, 0.8, 0.7, 0.85, 1.1, 0.7]
    _COL_LABELS = ["Ticker", "Price", "% Change", "Volume", "Source",
                   "TCS", "Edge Score", "Predicted Setup", "Action"]

    # ── Column headers ──────────────────────────────────────────────────────────
    _hdr = st.columns(_COL_W)
    for _col, _lbl in zip(_hdr, _COL_LABELS):
        _hdr_clr = "#4caf50" if _lbl == "Edge Score" else "#7c4dff" if _lbl in ("TCS", "Predicted Setup") else "#90caf9"
        _col.markdown(
            f'<div style="font-size:11px; font-weight:700; color:{_hdr_clr}; '
            f'text-transform:uppercase; letter-spacing:0.8px; padding:4px 0 8px 0; '
            f'border-bottom:1px solid #1e2a3a;">{_lbl}</div>',
            unsafe_allow_html=True,
        )

    # ── Data rows ───────────────────────────────────────────────────────────────
    for _i, _row in enumerate(_rows):
        _sym   = _row["ticker"]
        _price = _row["price"]
        _chg   = _row["change_pct"]
        _vol   = _row["volume"]
        _src   = _row["source"]
        _tcs   = _row.get("tcs")
        _setup = _row.get("structure", "—")

        _chg_color = "#4caf50" if _chg >= 0 else "#ef5350"
        _chg_sign  = "+" if _chg >= 0 else ""
        _vol_str   = f"{_vol:,}" if _vol else "—"

        _src_color = (
            "#4caf50" if _src == "Active + Gainer"
            else "#66bb6a" if _src == "Gainer"
            else "#5c6bc0"
        )

        # TCS color-coding
        if _tcs is None:
            _tcs_color = "#37474f"
            _tcs_str   = "—"
        elif _tcs >= 70:
            _tcs_color = "#4caf50"
            _tcs_str   = f"{_tcs:.0f}"
        elif _tcs >= 40:
            _tcs_color = "#ffa726"
            _tcs_str   = f"{_tcs:.0f}"
        else:
            _tcs_color = "#ef5350"
            _tcs_str   = f"{_tcs:.0f}"

        _row_cols = st.columns(_COL_W)

        _row_cols[0].markdown(
            f'<div style="padding:10px 0; font-size:15px; font-weight:800; color:#e0e0e0;">'
            f'{_sym}</div>', unsafe_allow_html=True,
        )
        _row_cols[1].markdown(
            f'<div style="padding:10px 0; font-size:14px; color:#cfd8dc;">'
            f'${_price:.2f}</div>', unsafe_allow_html=True,
        )
        _row_cols[2].markdown(
            f'<div style="padding:10px 0; font-size:14px; font-weight:700; color:{_chg_color};">'
            f'{_chg_sign}{_chg:.2f}%</div>', unsafe_allow_html=True,
        )
        _row_cols[3].markdown(
            f'<div style="padding:10px 0; font-size:13px; color:#90a4ae;">'
            f'{_vol_str}</div>', unsafe_allow_html=True,
        )
        _row_cols[4].markdown(
            f'<div style="padding:10px 0;">'
            f'<span style="background:{_src_color}22; color:{_src_color}; '
            f'font-size:10px; font-weight:700; padding:2px 8px; border-radius:10px; '
            f'border:1px solid {_src_color}55; text-transform:uppercase;">'
            f'{_src}</span></div>', unsafe_allow_html=True,
        )
        # TCS cell
        _row_cols[5].markdown(
            f'<div style="padding:8px 0;">'
            f'<span style="background:{_tcs_color}22; color:{_tcs_color}; '
            f'font-size:13px; font-weight:800; padding:3px 10px; border-radius:12px; '
            f'border:1px solid {_tcs_color}55;">{_tcs_str}</span>'
            f'</div>', unsafe_allow_html=True,
        )
        # Edge Score cell
        _edge = _row.get("edge_score")
        _bkd  = _row.get("edge_breakdown", {})
        if _edge is None:
            _edge_str   = "—"
            _edge_color = "#37474f"
            _edge_bg    = "#37474f"
        elif _edge >= 75:
            _edge_str   = f"{_edge:.0f}"
            _edge_color = "#4caf50"
            _edge_bg    = "#4caf50"
        elif _edge >= 50:
            _edge_str   = f"{_edge:.0f}"
            _edge_color = "#ffa726"
            _edge_bg    = "#ffa726"
        else:
            _edge_str   = f"{_edge:.0f}"
            _edge_color = "#ef5350"
            _edge_bg    = "#ef5350"
        _bkd_tip = (
            f"TCS {_bkd.get('tcs_pts',0):.0f} + "
            f"Struct {_bkd.get('struct_pts',0):.0f} + "
            f"Env {_bkd.get('env_pts',0):.0f} + "
            f"Tape {_bkd.get('fb_pts',0):.0f}"
        ) if _bkd else ""
        _row_cols[6].markdown(
            f'<div style="padding:8px 0;" title="{_bkd_tip}">'
            f'<span style="background:{_edge_bg}22; color:{_edge_color}; '
            f'font-size:14px; font-weight:900; padding:3px 12px; border-radius:12px; '
            f'border:2px solid {_edge_bg}66; font-family:monospace;">{_edge_str}</span>'
            f'</div>', unsafe_allow_html=True,
        )
        # Predicted Setup cell
        _setup_short = (_setup[:16] + "…") if len(str(_setup)) > 16 else _setup
        _row_cols[7].markdown(
            f'<div style="padding:10px 0; font-size:12px; color:#ce93d8; font-weight:600;">'
            f'{_setup_short}</div>', unsafe_allow_html=True,
        )
        with _row_cols[8]:
            if st.button("📝 Log", key=f"playbook_log_{_i}_{_sym}",
                         use_container_width=True):
                st.session_state["_fetched_price"]        = _price
                st.session_state["_fetched_volume"]        = _vol
                st.session_state["_fetched_symbol"]        = _sym
                st.session_state["_playbook_just_loaded"] = {
                    "ticker": _sym, "price": _price,
                }
                st.rerun()

        st.markdown(
            '<div style="border-bottom:1px solid #0d1520; margin:0;"></div>',
            unsafe_allow_html=True,
        )

    # ── Auto-refresh trigger ────────────────────────────────────────────────────
    if _pb_auto:
        _remaining = max(0, 60 - int(time.time() - st.session_state.get(_pb_time_key, 0)))
        st.markdown(
            f'<div style="font-size:11px; color:#37474f; margin-top:12px;">'
            f'Auto-refresh in {_remaining}s</div>',
            unsafe_allow_html=True,
        )

def _clean_structure_label(raw):
    """Strip emojis + extra words for a readable short label."""
    import re
    s = re.sub(r"[^\w\s()/\-]", "", str(raw)).strip()
    return s[:30] if len(s) > 30 else s


# ── Historical Backtester Tab ───────────────────────────────────────────────────
_BT_DEFAULT_TICKERS = (
    "GME,AMC,SOFI,SPCE,SNDL,TLRY,XELA,OCGN,CLOV,MVIS,"
    "WKHS,NKLA,RIDE,WISH,BBIG,SENS,CIDM,FFIE,MULN,PHUN,"
    "ATER,PROG,BIOR,CTRM,SIGA,GFAI,ONDS,ATNM,CTXR,PAVS"
)

_CALIBRATION_TICKERS = [
    "GME", "AMC", "SOFI", "SPCE", "SNDL", "TLRY", "OCGN", "CLOV", "MVIS",
    "WKHS", "NKLA", "RIDE", "BBIG", "SENS", "FFIE", "MULN",
    "ATER", "PROG", "CTRM", "GFAI", "ONDS", "ATNM", "CTXR", "PAVS",
    "PHUN", "CIDM", "WISH", "XELA",
]

_BT_WIN_CLR   = "#4caf50"
_BT_LOSS_CLR  = "#ef5350"
_BT_NEUT_CLR  = "#546e7a"


def _bt_tcs_color(tcs):
    if tcs is None:  return _BT_NEUT_CLR
    if tcs >= 70:    return "#4caf50"
    if tcs >= 40:    return "#ffa726"
    return "#ef5350"


def _bt_outcome_color(outcome):
    return {
        "Bullish Break": "#4caf50",
        "Bearish Break": "#ef5350",
        "Both Sides":    "#ffa726",
        "Range-Bound":   "#5c6bc0",
    }.get(outcome, _BT_NEUT_CLR)


def render_backtest_tab(api_key: str = "", secret_key: str = ""):
    """Render the 🔬 Backtest Engine tab — institutional backtesting terminal."""

    with st.expander("📋 What each section does — read before running anything", expanded=False):
        st.markdown(
            """
**This tab has two separate tools. They serve different purposes and write to different places.**

---

### 🧠 SECTION 1 — One-Click Calibration Run  *(top of this tab)*
**What it does:** Runs the quant model across a broad list of small-cap stocks over a historical date range.
Measures how accurately the 7-structure framework classified those days in hindsight.

**Who enters the tickers?** The system uses a pre-built training universe. You should leave it alone unless you have a specific reason to change it.

**⚠️ Do NOT paste your personal watchlist into the calibration ticker box.**

**Where results go:** A separate Supabase table called `backtest_sim_runs`. This does **NOT** affect your personal win rate, journal, or Analytics data in any way.

**When to run it:** Once per week or after adding new tickers to your watchlist, to keep the model calibrated on recent market behavior.

---

### 🔬 SECTION 2 — Single-Ticker / Multi-Ticker Simulation  *(below Calibration)*
**What it does:** Lets you test any ticker on any date or date range to see how the model would have traded it.

**Who enters the tickers?** You do — for research purposes. You can test a ticker you're considering adding to your watchlist, or check how the model did on a specific date.

**Where results go:** Also saved to `backtest_sim_runs`. Does NOT affect your personal analytics.

**When to use it:** When you want to audit a specific ticker or date before trading it live.

---

### 📊 Your personal data lives in:
- **📖 Journal tab** → your real Webull imports
- **📊 Analytics tab** → your real win rates derived from the journal
- Neither is touched by anything in this tab.
            """,
        )

    # ── Terminal header ─────────────────────────────────────────────────────────
    st.markdown(
        '<div style="background:#020813; border:1px solid #0d2137; border-radius:10px; '
        'padding:16px 22px; margin-bottom:18px;">'
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:2px; margin-bottom:4px;">QUANT RESEARCH TERMINAL v2</div>'
        '<div style="font-size:24px; font-weight:900; color:#e0e0e0; '
        'font-family:monospace;">🔬 Historical Backtest Engine</div>'
        '<div style="font-size:12px; color:#455a64; margin-top:4px;">'
        'Simulates your quant model on historical morning data → measures afternoon accuracy. '
        'Engine sees only 9:30–10:30 AM, then we check what actually happened 10:30 AM–4:00 PM.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Supabase migration reminder ──────────────────────────────────────────────
    if supabase:
        _bt_table_ok = False
        _bt_cols_missing = []
        try:
            _bt_probe = supabase.table("backtest_sim_runs").select("id").limit(1).execute()
            _bt_table_ok = True
        except Exception:
            pass

        if not _bt_table_ok:
            st.info(
                "**One-time setup:** Run this SQL in your Supabase SQL editor to enable "
                "automatic backtest history saving:\n\n"
                "```sql\n"
                "CREATE TABLE IF NOT EXISTS backtest_sim_runs (\n"
                "  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,\n"
                "  user_id TEXT,\n"
                "  sim_date DATE,\n"
                "  ticker TEXT,\n"
                "  open_price NUMERIC,\n"
                "  ib_low NUMERIC,\n"
                "  ib_high NUMERIC,\n"
                "  tcs NUMERIC,\n"
                "  predicted TEXT,\n"
                "  actual_outcome TEXT,\n"
                "  win_loss TEXT,\n"
                "  follow_thru_pct NUMERIC,\n"
                "  false_break_up BOOLEAN DEFAULT FALSE,\n"
                "  false_break_down BOOLEAN DEFAULT FALSE,\n"
                "  run_at TIMESTAMPTZ DEFAULT NOW()\n"
                ");\n"
                "```",
                icon="🗄️",
            )
        else:
            # Check whether key analytic columns exist (table may pre-date them)
            for _col in ["tcs", "ib_high", "ib_low"]:
                try:
                    supabase.table("backtest_sim_runs").select(_col).limit(1).execute()
                except Exception:
                    _bt_cols_missing.append(_col)

            if _bt_cols_missing:
                _alter_sql = "\n".join(
                    f"ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS {c} NUMERIC;"
                    for c in _bt_cols_missing
                )
                st.warning(
                    f"**Schema upgrade needed.** Your `backtest_sim_runs` table is missing "
                    f"column(s): `{'`, `'.join(_bt_cols_missing)}`. "
                    f"Run the SQL below in your **Supabase SQL editor**, then re-run calibration "
                    f"once to populate the new columns.\n\n"
                    f"```sql\n{_alter_sql}\n```",
                    icon="⚠️",
                )

    if not api_key or not secret_key:
        st.warning("Enter your **Alpaca API Key** and **Secret Key** in the sidebar to run backtests.")
        return

    # ══════════════════════════════════════════════════════════════════════════
    # BRAIN CALIBRATION RUN — uses YOUR journal tickers, falls back to defaults
    # Build calibration ticker list: journal tickers first, fallback to defaults
    # ══════════════════════════════════════════════════════════════════════════
    _cal_uid = st.session_state.get("auth_user_id", "")
    _cal_journal_df = _cached_load_journal(user_id=_cal_uid)
    if not _cal_journal_df.empty and "ticker" in _cal_journal_df.columns:
        _journal_tickers = sorted(set(
            str(t).upper().strip()
            for t in _cal_journal_df["ticker"].dropna().unique()
            if str(t).strip()
        ))
        _cal_ticker_pool = _journal_tickers
        _cal_source_label = f"your journal ({len(_cal_ticker_pool)} tickers)"
    else:
        _cal_ticker_pool = _CALIBRATION_TICKERS
        _cal_source_label = f"default list ({len(_cal_ticker_pool)} tickers)"

    st.markdown(
        f'<div style="background:#0a1628; border:1px solid #1565c044; border-radius:10px; '
        f'padding:14px 20px; margin-bottom:14px;">'
        f'<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        f'letter-spacing:2px; margin-bottom:4px;">🧠 BRAIN CALIBRATION</div>'
        f'<div style="font-size:15px; font-weight:700; color:#e0e0e0; margin-bottom:4px;">'
        f'One-Click Calibration Run</div>'
        f'<div style="font-size:12px; color:#546e7a;">'
        f'Runs the quant model across <b style="color:#90caf9">{_cal_source_label}</b> '
        f'over your chosen lookback window. '
        f'Saves all results to Supabase so the structure probability model can recalibrate '
        f'against correctly-classified historical sessions.'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.warning(
        "⚠️ **CALIBRATION TICKERS ≠ YOUR WATCHLIST.**  "
        "The tickers below are a broad small-cap universe used to train the model's "
        "structure recognition on historical data. "
        "**Do NOT paste your personal watchlist here.** "
        "Your watchlist goes in the sidebar (Gap Scanner) and the ⭐ My Watchlist section — not here.",
        icon="⚠️",
    )

    with st.expander(
        f"📋 Calibration ticker list ({len(_cal_ticker_pool)}) — click to view or edit",
        expanded=False,
    ):
        st.markdown(
            '<div style="background:#1a0000;border:1px solid #b71c1c;border-radius:6px;'
            'padding:10px 14px;margin-bottom:10px;font-size:12px;color:#ef9a9a;">'
            '<b>🚫 DO NOT put your personal watchlist here.</b><br>'
            'These tickers are the model\'s training universe — small-cap stocks used to '
            'teach the algorithm what each structure type looks like historically. '
            'Results are saved to a separate simulation table and do NOT affect your personal '
            'win rate or Analytics data.'
            '</div>',
            unsafe_allow_html=True,
        )
        _cal_ticker_edit = st.text_area(
            "One ticker per line or comma-separated — edits apply to this run only:",
            value="\n".join(_cal_ticker_pool),
            height=160,
            key="cal_ticker_edit",
        )
        _cal_ticker_pool = [
            t.strip().upper() for t in
            _cal_ticker_edit.replace(",", "\n").splitlines()
            if t.strip()
        ]
        st.caption(f"{len(_cal_ticker_pool)} tickers queued for calibration")

    _cal_feed_col, _cal_btn_col = st.columns([2, 1])
    with _cal_feed_col:
        _cal_days = st.slider(
            "Trading days to look back", min_value=1, max_value=22,
            value=5, step=1, key="cal_lookback_days",
            help="1 day = yesterday only. 5 = last full week. 22 = ~1 month."
        )
        # ── Restore cal feed from localStorage (cross-session) ───────────────
        import streamlit.components.v1 as _cmp_cal_feed
        _cmp_cal_feed.html("""
<script>
(function() {
    var _LS_KEY = 'cal_feed_radio';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('cal_feed')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('cal_feed', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)
        _CAL_FEED_OPTIONS = ["SIP (paid — accurate)", "IEX (free)"]
        if "cal_feed_radio" not in st.session_state:
            _qp_cal_feed = st.query_params.get("cal_feed", "SIP (paid — accurate)")
            st.session_state["cal_feed_radio"] = (
                _qp_cal_feed if _qp_cal_feed in _CAL_FEED_OPTIONS else "SIP (paid — accurate)"
            )
        _cal_feed = st.radio(
            "Feed",
            _CAL_FEED_OPTIONS,
            index=_CAL_FEED_OPTIONS.index(
                st.session_state.get("cal_feed_radio", "SIP (paid — accurate)")
                if st.session_state.get("cal_feed_radio", "SIP (paid — accurate)") in _CAL_FEED_OPTIONS
                else "SIP (paid — accurate)"
            ),
            key="cal_feed_radio",
            horizontal=True,
        )
        _qp_cal_feed_cur = st.query_params.get("cal_feed")
        if _qp_cal_feed_cur != _cal_feed:
            st.query_params["cal_feed"] = _cal_feed
        _cmp_cal_feed.html(
            f"<script>localStorage.setItem('cal_feed_radio', {repr(_cal_feed)});</script>",
            height=0,
        )
        _cal_feed_str = "sip" if "SIP" in _cal_feed else "iex"
        _cal_confirmed = st.checkbox(
            f"✅ I confirm: these are calibration tickers, NOT my personal watchlist",
            key="cal_confirm_check",
        )
    with _cal_btn_col:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        _cal_run = False
        st.markdown(
            '<div style="background:#1a0a00; border:1px solid #e65100; border-radius:8px; '
            'padding:10px 12px; text-align:center;">'
            '<span style="font-size:13px; font-weight:700; color:#ff6d00;">🔒 Locked</span><br>'
            '<span style="font-size:11px; color:#bf360c;">Ask in Replit chat before running calibration</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    if _cal_run:
        _cal_end = get_last_trading_day(as_of=date.today())
        import math as _math
        _cal_days_back = _math.ceil(_cal_days * 7 / 5) + 3
        _cal_start = _cal_end - timedelta(days=_cal_days_back)
        _cal_label = f"{_cal_start} → {_cal_end}"
        st.info(
            f"Running {len(_cal_ticker_pool)} tickers · {_cal_start} → {_cal_end} "
            f"· Feed: {_cal_feed_str.upper()} · Saving to Supabase…"
        )
        with st.spinner(
            f"⏳ Calibrating model on {len(_cal_ticker_pool)} tickers × "
            f"{_cal_days} days ({_cal_label}) — this may take a few minutes…"
        ):
            try:
                _cal_results, _cal_summary, _cal_daily = run_backtest_range(
                    api_key, secret_key,
                    start_date=_cal_start,
                    end_date=_cal_end,
                    tickers=_cal_ticker_pool,
                    feed=_cal_feed_str,
                    price_min=0.10,
                    price_max=500.0,
                    slippage_pct=0.5,
                )
                if _cal_results:
                    try:
                        save_backtest_sim_runs(_cal_results, user_id=_AUTH_USER_ID)
                        _saved_ok = True
                    except Exception:
                        _saved_ok = False
                    _wins    = sum(1 for r in _cal_results if r.get("win_loss") == "Win")
                    _losses  = sum(1 for r in _cal_results if r.get("win_loss") == "Loss")
                    _total_r = len(_cal_results)
                    _wr = _wins / _total_r * 100 if _total_r else 0
                    _save_msg = "✅ Saved to Supabase" if _saved_ok else "⚠️ Supabase save failed"
                    st.success(
                        f"Calibration complete — {_total_r} sessions processed · "
                        f"Win rate: {_wr:.1f}% ({_wins}W / {_losses}L) · {_save_msg}"
                    )
                    _cal_struct_counts: dict = {}
                    for _r in _cal_results:
                        _s = _r.get("predicted", "Unknown")
                        _cal_struct_counts[_s] = _cal_struct_counts.get(_s, 0) + 1
                    _struct_parts = [f"{k}: {v}" for k, v in
                                     sorted(_cal_struct_counts.items(), key=lambda x: -x[1])]
                    st.caption("Structure distribution: " + " · ".join(_struct_parts))
                    if _saved_ok:
                        _cal_tiered_pending = count_backtest_tiered_pending(user_id=_AUTH_USER_ID)
                        if _cal_tiered_pending > 0:
                            st.caption(
                                f"⚠️ {_cal_tiered_pending:,} backtest rows are missing tiered P&L "
                                f"(50/25/25 ladder). Visit the **Performance tab → Backtest Sim P&L** "
                                f"section to run the one-click backfill, or execute "
                                f"`python run_tiered_pnl_backfill.py --backtest-only` from the shell."
                            )
                else:
                    st.warning("No results returned — check tickers or date range. "
                               "Tickers not in the price range or with no data are skipped.")
            except Exception as _cal_err:
                st.error(f"Calibration run failed: {_cal_err}")

    st.markdown("---")

    # ── Configuration panel ─────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; margin-bottom:10px; font-weight:700;">⚙ Simulation Parameters</div>',
        unsafe_allow_html=True,
    )
    _bt_col1, _bt_col2, _bt_col3, _bt_col4 = st.columns([1.2, 1.2, 1, 1])
    with _bt_col1:
        yesterday = date.today() - timedelta(days=1)
        _bt_date  = st.date_input(
            "Backtest Date", value=yesterday,
            max_value=date.today() - timedelta(days=1),
            key="bt_date_picker",
            help="Select any past trading day. Weekends/holidays will return no data.",
        )
    with _bt_col2:
        # ── Restore backtest feed from localStorage (cross-session) ───────────
        import streamlit.components.v1 as _cmp_bt_feed
        _cmp_bt_feed.html("""
<script>
(function() {
    var _LS_KEY = 'bt_feed_radio';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('bt_feed')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('bt_feed', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)
        _BT_FEED_OPTIONS = ["SIP (paid — accurate)", "IEX (free — limited)"]
        if "bt_feed_radio" not in st.session_state:
            _qp_bt_feed = st.query_params.get("bt_feed", "SIP (paid — accurate)")
            st.session_state["bt_feed_radio"] = (
                _qp_bt_feed if _qp_bt_feed in _BT_FEED_OPTIONS else "SIP (paid — accurate)"
            )
        _bt_feed = st.radio(
            "Bar Data Feed",
            _BT_FEED_OPTIONS,
            index=_BT_FEED_OPTIONS.index(
                st.session_state.get("bt_feed_radio", "SIP (paid — accurate)")
                if st.session_state.get("bt_feed_radio", "SIP (paid — accurate)") in _BT_FEED_OPTIONS
                else "SIP (paid — accurate)"
            ),
            key="bt_feed_radio",
            horizontal=True,
        )
        _qp_bt_feed_cur = st.query_params.get("bt_feed")
        if _qp_bt_feed_cur != _bt_feed:
            st.query_params["bt_feed"] = _bt_feed
        _cmp_bt_feed.html(
            f"<script>localStorage.setItem('bt_feed_radio', {repr(_bt_feed)});</script>",
            height=0,
        )
        _bt_feed_str = "sip" if "SIP" in _bt_feed else "iex"
    with _bt_col3:
        _bt_price_range = st.slider(
            "Price Range ($)", min_value=1.0, max_value=50.0,
            value=(2.0, 20.0), step=0.5, key="bt_price_range",
        )
    with _bt_col4:
        _bt_end_date = st.date_input(
            "To Date (range)",
            value=yesterday,
            max_value=date.today() - timedelta(days=1),
            key="bt_end_date_picker",
            help="Same as From Date = single day. Earlier From Date = multi-day range (max 10 weekdays). "
                 "Use a full week to see market bias patterns.",
        )
    _bt_is_range = _bt_end_date > _bt_date

    st.markdown(
        '<div style="font-size:11px; color:#37474f; margin:10px 0 2px 0;">'
        '🔬 <b>SIMULATION TICKERS</b> — Research use only. '
        'Enter any tickers you want to test on the selected date(s). '
        'This is separate from calibration and does not affect your personal analytics.'
        '</div>'
        '<div style="font-size:10px; color:#546e7a; margin-bottom:6px;">'
        'comma-separated · engine keeps only tickers in your price range on that date'
        '</div>',
        unsafe_allow_html=True,
    )
    _bt_tickers_raw = st.text_area(
        "Simulation Tickers", value=_BT_DEFAULT_TICKERS,
        height=68, key="bt_tickers_input", label_visibility="collapsed",
    )

    _bt_adv_cols = st.columns([2, 2, 1])
    with _bt_adv_cols[0]:
        _bt_slippage = st.slider(
            "Slippage % (each side)",
            min_value=0.0, max_value=2.0, value=0.5, step=0.1,
            key="bt_slippage_pct",
            help=(
                "Real-world cost of entering and exiting a trade. "
                "Applied once on entry + once on exit. "
                "0.5% each side = 1% total drag per trade. "
                "Small caps typically need 0.3–0.8%."
            ),
        )
    with _bt_adv_cols[1]:
        _bt_monte_equity = st.number_input(
            "Monte Carlo Starting Equity ($)",
            min_value=1000, max_value=1_000_000, value=10_000, step=1000,
            key="bt_mc_equity",
            help="Simulated starting account size for equity curve projection.",
        )
    with _bt_adv_cols[2]:
        _bt_monte_risk = st.slider(
            "Risk per Trade (%)",
            min_value=0.5, max_value=5.0, value=2.0, step=0.5,
            key="bt_mc_risk",
            help="What % of equity is risked per trade in the Monte Carlo simulation.",
        )

    _bt_run = st.button(
        "▶ RUN SIMULATION", use_container_width=True, key="backtest_sim_run_btn",
        type="primary",
    )

    st.markdown(
        f'<div style="font-size:10px; color:#263238; margin-top:6px;">'
        f'IB fixed at 9:30–10:30 AM · Set From = To for single day · '
        f'Set a date range (max 22 weekdays ≈ 1 month) for walk-forward analysis · '
        f'Results auto-saved · Slippage {_bt_slippage:.1f}% each side applied'
        f'</div>',
        unsafe_allow_html=True,
    )
    # ── Load Saved Results ──────────────────────────────────────────────────────
    @st.cache_data(ttl=300, show_spinner=False)
    def _load_ls_bt_sim_history(uid):
        return load_backtest_sim_history(user_id=uid)

    with st.expander("📂 Load Saved Simulation Results", expanded=False):
        st.caption("Load any previous simulation run from your history without re-fetching Alpaca data.")
        _ls_col1, _ls_col2 = st.columns([1, 1])
        with _ls_col1:
            if st.button("🔄 Fetch My Saved Dates", use_container_width=True, key="bt_ls_fetch"):
                _load_ls_bt_sim_history.clear()
                _ls_hist = _load_ls_bt_sim_history(uid=_AUTH_USER_ID)
                if _ls_hist.empty or "sim_date" not in _ls_hist.columns:
                    st.session_state["_bt_ls_dates"]   = []
                    st.session_state["_bt_ls_hist_all"] = pd.DataFrame()
                else:
                    _ls_avail = sorted(_ls_hist["sim_date"].astype(str).unique(), reverse=True)
                    st.session_state["_bt_ls_dates"]   = _ls_avail
                    st.session_state["_bt_ls_hist_all"] = _ls_hist

        _ls_dates_avail = st.session_state.get("_bt_ls_dates", [])
        if _ls_dates_avail:
            _ls_sel = st.multiselect(
                "Select date(s) to load",
                options=_ls_dates_avail,
                default=_ls_dates_avail[:1],
                key="bt_ls_date_sel",
                help="Select one date for a single-day view, or multiple to reconstruct a range.",
            )
            with _ls_col2:
                _ls_load_btn = st.button(
                    "📂 Load Selected", use_container_width=True,
                    key="bt_ls_load_btn",
                    disabled=not _ls_sel,
                )
            if _ls_load_btn and _ls_sel:
                _ls_all_df = st.session_state.get("_bt_ls_hist_all", pd.DataFrame())
                _ls_rows_df = _ls_all_df[_ls_all_df["sim_date"].astype(str).isin([str(d) for d in _ls_sel])]

                def _icon_for(outcome: str) -> str:
                    _lc = (outcome or "").lower()
                    if "bull" in _lc:   return "+"
                    if "bear" in _lc:   return "↓"
                    if "both" in _lc:   return "⇅"
                    if "range" in _lc:  return "—"
                    return "·"

                def _safe_r(v):
                    try:
                        fv = float(v)
                        return fv if fv == fv else None
                    except (TypeError, ValueError):
                        return None

                _ls_results = []
                for _, _lrow in _ls_rows_df.iterrows():
                    _ls_eod_r    = _lrow.get("eod_pnl_r")
                    _ls_tiered_r = _lrow.get("tiered_pnl_r")
                    _ls_results.append({
                        "ticker":         str(_lrow.get("ticker", "")),
                        "sim_date":       str(_lrow.get("sim_date", "")),
                        "open_price":     float(_lrow.get("open_price") or 0),
                        "close_price":    float(_lrow.get("close_price") or 0),
                        "ib_low":         float(_lrow.get("ib_low") or 0),
                        "ib_high":        float(_lrow.get("ib_high") or 0),
                        "tcs":            float(_lrow.get("tcs") or 0),
                        "predicted":      str(_lrow.get("predicted", "")),
                        "actual_outcome": str(_lrow.get("actual_outcome", "")),
                        "actual_icon":    _icon_for(str(_lrow.get("actual_outcome", ""))),
                        "win_loss":       str(_lrow.get("win_loss", "")),
                        "aft_move_pct":   float(_lrow.get("follow_thru_pct") or _lrow.get("aft_move_pct") or 0),
                        "false_break_up":   bool(_lrow.get("false_break_up", False)),
                        "false_break_down": bool(_lrow.get("false_break_down", False)),
                        "eod_pnl_r":      _safe_r(_ls_eod_r),
                        "tiered_pnl_r":   _safe_r(_ls_tiered_r),
                    })

                if _ls_results:
                    _ls_wins   = sum(1 for r in _ls_results if r["win_loss"] == "Win")
                    _ls_losses = sum(1 for r in _ls_results if r["win_loss"] == "Loss")
                    _ls_total  = len(_ls_results)
                    _ls_wr     = round(_ls_wins / _ls_total * 100, 1) if _ls_total > 0 else 0.0
                    _ls_tcs_vals  = [r["tcs"] for r in _ls_results if r["tcs"] > 0]
                    _ls_bull_ft   = [r["aft_move_pct"] for r in _ls_results
                                     if "bull" in r["actual_outcome"].lower() and r["aft_move_pct"] > 0]
                    _ls_bear_ft   = [abs(r["aft_move_pct"]) for r in _ls_results
                                     if "bear" in r["actual_outcome"].lower() and r["aft_move_pct"] < 0]
                    _ls_bull_breaks  = sum(1 for r in _ls_results if "bull" in r["actual_outcome"].lower())
                    _ls_bear_breaks  = sum(1 for r in _ls_results if "bear" in r["actual_outcome"].lower())
                    _ls_both_breaks  = sum(1 for r in _ls_results if "both" in r["actual_outcome"].lower())
                    _ls_range_bound  = sum(1 for r in _ls_results if "range" in r["actual_outcome"].lower())
                    _ls_long_wins    = sum(1 for r in _ls_results
                                         if "bull" in r["actual_outcome"].lower() and r["win_loss"] == "Win")
                    _ls_long_total   = _ls_bull_breaks or 1
                    _ls_fb_count     = sum(1 for r in _ls_results if r["false_break_up"] or r["false_break_down"])
                    _ls_eod_vals    = [r["eod_pnl_r"]    for r in _ls_results if r.get("eod_pnl_r")    is not None]
                    _ls_tiered_vals = [r["tiered_pnl_r"] for r in _ls_results if r.get("tiered_pnl_r") is not None]
                    _ls_summary = {
                        "win_rate":       _ls_wr,
                        "total":          _ls_total,
                        "wins":           _ls_wins,
                        "losses":         _ls_losses,
                        "highest_tcs":    max(_ls_tcs_vals) if _ls_tcs_vals else 0,
                        "avg_tcs":        sum(_ls_tcs_vals) / len(_ls_tcs_vals) if _ls_tcs_vals else 0,
                        "avg_bull_ft":    sum(_ls_bull_ft) / len(_ls_bull_ft) if _ls_bull_ft else 0,
                        "avg_bear_ft":    sum(_ls_bear_ft) / len(_ls_bear_ft) if _ls_bear_ft else 0,
                        "bull_breaks":    _ls_bull_breaks,
                        "bear_breaks":    _ls_bear_breaks,
                        "both_breaks":    _ls_both_breaks,
                        "range_bound":    _ls_range_bound,
                        "long_win_rate":  round(_ls_long_wins / _ls_long_total * 100, 1),
                        "false_break_rate": round(_ls_fb_count / _ls_total * 100, 1) if _ls_total > 0 else 0,
                        "avg_eod_pnl_r":    round(sum(_ls_eod_vals) / len(_ls_eod_vals), 3) if _ls_eod_vals else None,
                        "avg_tiered_pnl_r": round(sum(_ls_tiered_vals) / len(_ls_tiered_vals), 3) if _ls_tiered_vals else None,
                        "eod_pnl_r_count":  len(_ls_eod_vals),
                        "tiered_pnl_r_count": len(_ls_tiered_vals),
                    }
                    _ls_date_label = (
                        f"{min(_ls_sel)} → {max(_ls_sel)}" if len(_ls_sel) > 1 else _ls_sel[0]
                    )
                    _ls_is_range = len(_ls_sel) > 1
                    st.session_state["bt_results_cache"] = (
                        _ls_results, _ls_summary, _ls_date_label, _ls_is_range, None
                    )
                    st.success(f"Loaded {_ls_total} rows from {len(_ls_sel)} date(s). Scroll down to view results.")
                    st.rerun()
                else:
                    st.warning("No data found for the selected date(s).")
        elif not _ls_dates_avail and st.session_state.get("_bt_ls_dates") is not None:
            st.info("No saved simulation runs found for your account.")

    st.markdown("---")

    # ── Historical Paper Trade Replay ───────────────────────────────────────────
    with st.expander("📈 Historical Paper Trade Replay", expanded=True):
        st.caption(
            "Simulates your paper trader's entry/exit logic on historical backtest data. "
            "Uses IB breakout entries with fixed risk per trade to show real dollar P&L over time."
        )

        _rp_uid = st.session_state.get("auth_user_id", "")

        # Cache TCS thresholds — avoids slow 11k-row paginated query on every render
        @st.cache_data(ttl=3600, show_spinner=False)
        def _cached_tcs_thresholds():
            try:
                return compute_structure_tcs_thresholds()
            except Exception:
                return []

        _RP_SIZING_OPTIONS = ["📊 % Risk (custom)", "🤖 Match Live Bot (100 shares flat)"]

        # ── Restore sizing mode from localStorage (cross-session) ──────────────
        import streamlit.components.v1 as _cmp_sizing
        _cmp_sizing.html("""
<script>
(function() {
    var _LS_KEY = 'rp_sizing_mode';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('rp_sizing')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('rp_sizing', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

        if "rp_sizing_mode" not in st.session_state:
            _qp_sizing = st.query_params.get("rp_sizing", "🤖 Match Live Bot (100 shares flat)")
            st.session_state["rp_sizing_mode"] = (
                _qp_sizing if _qp_sizing in _RP_SIZING_OPTIONS else "🤖 Match Live Bot (100 shares flat)"
            )

        _rp_mode = st.radio(
            "Sizing Mode",
            options=_RP_SIZING_OPTIONS,
            index=_RP_SIZING_OPTIONS.index(
                st.session_state.get("rp_sizing_mode", "🤖 Match Live Bot (100 shares flat)")
                if st.session_state.get("rp_sizing_mode", "🤖 Match Live Bot (100 shares flat)") in _RP_SIZING_OPTIONS
                else "🤖 Match Live Bot (100 shares flat)"
            ),
            key="rp_sizing_mode",
            horizontal=True,
            help=(
                "Match Live Bot = flat 100 shares per trade, TCS ≥ 50 — "
                "exactly how paper_trader_bot.py calculates its simulated P&L. "
                "% Risk = fixed dollar risk per trade based on starting equity."
            ),
        )

        _qp_sizing_cur = st.query_params.get("rp_sizing")
        if _qp_sizing_cur != _rp_mode:
            st.query_params["rp_sizing"] = _rp_mode
        _cmp_sizing.html(
            f"<script>localStorage.setItem('rp_sizing_mode', {repr(_rp_mode)});</script>",
            height=0,
        )

        _rp_bot_mode = "bot" in _rp_mode.lower()

        # ── Restore numeric replay filters from localStorage (cross-session) ─────
        import streamlit.components.v1 as _cmp_rp_filters
        _cmp_rp_filters.html("""
<script>
(function() {
    var url = new URL(window.parent.location.href);
    var params = [
        ['rp_min_tcs', 'rp_min_tcs_slider'],
        ['rp_min_gap',       'rp_min_gap'],
        ['rp_min_gap_vs_ib', 'rp_min_gap_vs_ib'],
        ['rp_min_ft',        'rp_min_ft'],
    ];
    var changed = false;
    params.forEach(function(pair) {
        var qp = pair[0], lsKey = pair[1];
        if (url.searchParams.has(qp)) return;
        var saved = localStorage.getItem(lsKey);
        if (saved === null) return;
        url.searchParams.set(qp, saved);
        changed = true;
    });
    if (changed) window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

        # Initialise date defaults before any widget renders so values are
        # available in session state for the stale-badge check below.
        if "rp_start_date" not in st.session_state:
            st.session_state["rp_start_date"] = datetime.now(EASTERN).date() - timedelta(days=60)
        if "rp_end_date" not in st.session_state:
            st.session_state["rp_end_date"] = datetime.now(EASTERN).date()

        # Clear saved floor selections whenever any replay filter changes.
        # Streamlit writes widget values to session state before each rerun, so
        # reading the keys here gives the *current* (newly selected) values —
        # even though the widgets themselves render further down the page.
        # The full parameter signature covers every input that affects which
        # trades are included, so changing any one of them will invalidate the
        # saved per-ticker TCS floors.
        # Note: on the very first render of a new session _rp_prev_sig won't
        # exist, so the comparison always triggers a clear.  This is intentional
        # — rp_best_tcs_source won't be set yet, so the pop() is a no-op.
        _rp_cur_start = st.session_state["rp_start_date"]
        _rp_cur_end   = st.session_state["rp_end_date"]
        _rp_sig = (
            _rp_cur_start,
            _rp_cur_end,
            _rp_bot_mode,
            st.session_state.get("rp_min_gap", 0.0),
            st.session_state.get("rp_min_gap_vs_ib", 0.0),
            st.session_state.get("rp_scan_type", "Morning (10:47 AM)"),
            st.session_state.get("rp_tcs_offset", 0),
            st.session_state.get("rp_min_tcs_slider", 0),
            st.session_state.get("min_tcs_trades", 5),
            st.session_state.get("rp_min_ft", 0.0),
        )
        if st.session_state.get("_rp_prev_sig") != _rp_sig:
            st.session_state.pop("rp_best_tcs_source", None)
            # Only reset the TCS slider on actual user-driven date changes, not
            # on the very first render of a new session (when _rp_prev_sig is
            # absent).  Skipping the reset on first render lets the
            # prefs-restored slider value survive.
            if "_rp_prev_sig" in st.session_state:
                _prev_sig = st.session_state["_rp_prev_sig"]
                if _prev_sig[0] != _rp_cur_start or _prev_sig[1] != _rp_cur_end:
                    st.session_state["rp_min_tcs_slider"] = 0
                    st.session_state["rp_min_ft"] = 0.0
                    st.session_state["rp_min_gap"] = 0.0
                    st.session_state["rp_min_gap_vs_ib"] = 0.0
                    st.session_state["rp_scan_type"] = "Morning (10:47 AM)"
                    # Reflect all resets in the signature so the stored value
                    # matches actual session state and does not trigger a
                    # spurious extra clear on the very next rerun.
                    _rp_sig = (
                        _rp_sig[0], _rp_sig[1], _rp_sig[2],
                        0.0, 0.0,
                        "Morning (10:47 AM)", _rp_sig[6],
                        0, _rp_sig[8],
                        0.0,
                    )
            _cleared_floors = [
                _k for _k in list(st.session_state.keys())
                if _k.startswith("_drill_tcs_persist_")
            ]
            for _k in _cleared_floors:
                del st.session_state[_k]
            if _cleared_floors and "_rp_prev_sig" in st.session_state:
                st.toast(
                    "Your pinned TCS floor selections were reset because the "
                    "replay parameters changed.",
                    icon="ℹ️",
                )
        st.session_state["_rp_prev_sig"] = _rp_sig

        _rp_col1, _rp_col2, _rp_col3, _rp_col4 = st.columns([1, 1, 1, 1])
        with _rp_col1:
            if _rp_bot_mode:
                _rp_pos_size = st.number_input(
                    "Position Size ($)", min_value=100, max_value=50000,
                    value=500, step=100, key="rp_pos_size",
                    help="Fixed dollar amount invested per trade (or starting position if compounding is on).",
                )
                _rp_risk_pct = 2.0
                _rp_compound = st.checkbox(
                    "Compound position size",
                    value=False, key="rp_compound",
                    help=(
                        "When ON: position size scales with equity. "
                        "If equity doubles from $10K to $20K, your $500 position becomes $1,000. "
                        "Formula: pos = starting_pos × (current_equity / starting_equity)."
                    ),
                )
            else:
                _rp_pos_size = 500
                _rp_compound = False
            _rp_equity = st.number_input(
                "Starting Equity ($)", min_value=1000, max_value=500000,
                value=10000, step=500, key="rp_equity",
                help="Starting account size for the equity curve.",
            )
        with _rp_col2:
            if _rp_bot_mode:
                _rp_tcs_offset = st.slider(
                    "TCS Adjustment", min_value=-20, max_value=20,
                    value=0, step=5, key="rp_tcs_offset",
                    help=(
                        "Shifts all per-structure TCS thresholds up or down. "
                        "−10 = lower every structure's bar by 10 (more trades). "
                        "+10 = raise every bar by 10 (fewer, higher-quality trades). "
                        "0 = use each structure's calculated threshold as-is."
                    ),
                )
                _rp_effective_floor = 50 + _rp_tcs_offset
                _rp_offset_sign = f"+{_rp_tcs_offset}" if _rp_tcs_offset >= 0 else str(_rp_tcs_offset)
                st.caption(f"≈ TCS {_rp_effective_floor} effective floor (base 50 + offset {_rp_offset_sign})")
                _rp_min_tcs = 0
            else:
                _rp_tcs_offset = 0
                _rp_risk_pct = st.number_input(
                    "Risk per Trade (%)", min_value=0.5, max_value=10.0,
                    value=2.0, step=0.5, key="rp_risk_pct",
                )
                if "rp_min_tcs_slider" not in st.session_state:
                    try:
                        st.session_state["rp_min_tcs_slider"] = int(st.query_params.get("rp_min_tcs", 0))
                    except (ValueError, TypeError):
                        st.session_state["rp_min_tcs_slider"] = 0
                else:
                    st.session_state["rp_min_tcs_slider"] = int(st.session_state["rp_min_tcs_slider"])
                _rp_min_tcs = st.number_input(
                    "Min TCS",
                    min_value=0,
                    max_value=100,
                    step=1,
                    key="rp_min_tcs_slider",
                    help="Only include setups where TCS meets this threshold. 0 = no filter.",
                )
                _rp_best_tcs_src = st.session_state.get("rp_best_tcs_source")
                if _rp_best_tcs_src and _rp_min_tcs == _rp_best_tcs_src["floor"]:
                    st.caption(
                        f"📌 Using {_rp_best_tcs_src['ticker']}'s Best TCS"
                        f" ({_rp_best_tcs_src['floor']})"
                    )
                # Sync rp_min_tcs to query params + localStorage
                if st.query_params.get("rp_min_tcs") != str(_rp_min_tcs):
                    st.query_params["rp_min_tcs"] = str(_rp_min_tcs)
                _cmp_rp_filters.html(
                    f"<script>localStorage.setItem('rp_min_tcs_slider', {repr(str(_rp_min_tcs))});</script>",
                    height=0,
                )
                # Persist rp_min_tcs_slider to user prefs when it changes
                if _AUTH_USER_ID:
                    _rp_cached = st.session_state.get("_cached_prefs", {})
                    if _rp_cached.get("rp_min_tcs_slider") != _rp_min_tcs:
                        _rp_new_prefs = {**_rp_cached, "rp_min_tcs_slider": _rp_min_tcs}
                        save_user_prefs(_AUTH_USER_ID, _rp_new_prefs)
                        st.session_state["_cached_prefs"] = _rp_new_prefs
        with _rp_col3:
            if "rp_min_ft" not in st.session_state:
                try:
                    st.session_state["rp_min_ft"] = float(st.query_params.get("rp_min_ft", 0.0))
                except (ValueError, TypeError):
                    st.session_state["rp_min_ft"] = 0.0
            _rp_min_ft = st.slider(
                "Min Follow-Through %", min_value=0.0, max_value=10.0,
                value=0.0, step=0.5, key="rp_min_ft",
                help="Only include trades where the stock moved at least this % past the IB.",
            )
            if st.query_params.get("rp_min_ft") != str(_rp_min_ft):
                st.query_params["rp_min_ft"] = str(_rp_min_ft)
            _cmp_rp_filters.html(
                f"<script>localStorage.setItem('rp_min_ft', {repr(str(_rp_min_ft))});</script>",
                height=0,
            )
            if _AUTH_USER_ID:
                _rp_cached = st.session_state.get("_cached_prefs", {})
                if _rp_cached.get("rp_min_ft") != _rp_min_ft:
                    _rp_new_prefs = {**_rp_cached, "rp_min_ft": _rp_min_ft}
                    save_user_prefs(_AUTH_USER_ID, _rp_new_prefs)
                    st.session_state["_cached_prefs"] = _rp_new_prefs
        with _rp_col4:
            if not _rp_bot_mode:
                _rp_max_move = st.slider(
                    "Max Move Cap %", min_value=10.0, max_value=200.0,
                    value=50.0, step=5.0, key="rp_max_move",
                    help="Caps follow-through % used in P&L to prevent outlier inflation.",
                )
            else:
                _rp_max_move = 9999.0
                st.caption("**Max Move**\nUncapped in Bot mode\n(uses actual recorded move)")

        _RP_SNAP_OPTIONS = ["Morning (10:47 AM)", "Intraday (2:00 PM)", "EOD (4:00 PM)", "All", "🏆 Best (Most Profit Combined)"]

        # ── Restore snapshot preference from localStorage (cross-session) ───────
        import streamlit.components.v1 as _cmp_snap
        _cmp_snap.html("""
<script>
(function() {
    var _LS_KEY = 'rp_scan_type';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('rp_snap')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('rp_snap', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

        if "rp_scan_type" not in st.session_state:
            _qp_snap = st.query_params.get("rp_snap", "Morning (10:47 AM)")
            st.session_state["rp_scan_type"] = (
                _qp_snap if _qp_snap in _RP_SNAP_OPTIONS else "Morning (10:47 AM)"
            )

        _rp_snap_col, _rp_date_col1, _rp_date_col2 = st.columns([1, 1, 1])
        with _rp_snap_col:
            _rp_snap = st.selectbox(
                "Snapshot",
                options=_RP_SNAP_OPTIONS,
                index=_RP_SNAP_OPTIONS.index(
                    st.session_state.get("rp_scan_type", "Morning (10:47 AM)")
                    if st.session_state.get("rp_scan_type", "Morning (10:47 AM)") in _RP_SNAP_OPTIONS
                    else "Morning (10:47 AM)"
                ),
                key="rp_scan_type",
                help=(
                    "Morning = entry decision snapshot (IB just formed, 10:47 AM). "
                    "Intraday = 2 PM position-management check. "
                    "EOD = full-day TCS (matches what paper_trades records). "
                    "All = no filter. "
                    "Best = picks whichever snapshot (morning/intraday/EOD) gave the "
                    "highest follow-through % for each ticker+date — shows the theoretical "
                    "ceiling if you always timed your entry perfectly."
                ),
            )
            _qp_snap_cur = st.query_params.get("rp_snap")
            if _qp_snap_cur != _rp_snap:
                st.query_params["rp_snap"] = _rp_snap
            _cmp_snap.html(
                f"<script>localStorage.setItem('rp_scan_type', {repr(_rp_snap)});</script>",
                height=0,
            )
            _rp_scan_type_map = {
                "Morning (10:47 AM)":           "morning",
                "Intraday (2:00 PM)":           "intraday",
                "EOD (4:00 PM)":                "eod",
                "All":                          None,
                "🏆 Best (Most Profit Combined)": None,  # fetch all, pick best per ticker+date below
            }
            _rp_scan_type_val  = _rp_scan_type_map[_rp_snap]
            _rp_best_mode      = _rp_snap == "🏆 Best (Most Profit Combined)"
        with _rp_date_col1:
            _rp_start = st.date_input("From", key="rp_start_date",
                                      min_value=date(2018, 1, 1))
        with _rp_date_col2:
            _rp_end = st.date_input("To", key="rp_end_date",
                                    min_value=date(2018, 1, 1))

        _rp_gap_col1, _rp_gap_col2, _rp_gap_col3 = st.columns([1, 1, 2])
        with _rp_gap_col1:
            if "rp_min_gap" not in st.session_state:
                try:
                    st.session_state["rp_min_gap"] = float(st.query_params.get("rp_min_gap", 0.0))
                except (ValueError, TypeError):
                    st.session_state["rp_min_gap"] = 0.0
            _rp_min_gap = st.number_input(
                "Min Gap % (open vs prev close)",
                min_value=0.0, max_value=50.0, value=0.0, step=0.5,
                key="rp_min_gap",
                help=(
                    "Only include setups where the stock gapped up ≥ this % at open. "
                    "0 = no filter. Requires the new batch run (gap_pct column). "
                    "Records from before the migration will show gap_pct = 0 and will still appear."
                ),
            )
            if st.query_params.get("rp_min_gap") != str(_rp_min_gap):
                st.query_params["rp_min_gap"] = str(_rp_min_gap)
            _cmp_rp_filters.html(
                f"<script>localStorage.setItem('rp_min_gap', {repr(str(_rp_min_gap))});</script>",
                height=0,
            )
            if _AUTH_USER_ID:
                _rp_cached = st.session_state.get("_cached_prefs", {})
                if _rp_cached.get("rp_min_gap") != _rp_min_gap:
                    _rp_new_prefs = {**_rp_cached, "rp_min_gap": _rp_min_gap}
                    save_user_prefs(_AUTH_USER_ID, _rp_new_prefs)
                    st.session_state["_cached_prefs"] = _rp_new_prefs
        with _rp_gap_col2:
            if "rp_min_gap_vs_ib" not in st.session_state:
                try:
                    st.session_state["rp_min_gap_vs_ib"] = float(st.query_params.get("rp_min_gap_vs_ib", 0.0))
                except (ValueError, TypeError):
                    st.session_state["rp_min_gap_vs_ib"] = 0.0
            _rp_min_gap_vs_ib = st.number_input(
                "Min Gap vs IB (×)",
                min_value=0.0, max_value=10.0, value=0.0, step=0.25,
                key="rp_min_gap_vs_ib",
                help=(
                    "Gap size expressed as a multiple of the IB range. "
                    "1.0 = gap was exactly as wide as the IB. "
                    "2.0 = gap was twice the IB width — strong directional conviction. "
                    "0 = no filter."
                ),
            )
            if st.query_params.get("rp_min_gap_vs_ib") != str(_rp_min_gap_vs_ib):
                st.query_params["rp_min_gap_vs_ib"] = str(_rp_min_gap_vs_ib)
            _cmp_rp_filters.html(
                f"<script>localStorage.setItem('rp_min_gap_vs_ib', {repr(str(_rp_min_gap_vs_ib))});</script>",
                height=0,
            )
            if _AUTH_USER_ID:
                _rp_cached = st.session_state.get("_cached_prefs", {})
                if _rp_cached.get("rp_min_gap_vs_ib") != _rp_min_gap_vs_ib:
                    _rp_new_prefs = {**_rp_cached, "rp_min_gap_vs_ib": _rp_min_gap_vs_ib}
                    save_user_prefs(_AUTH_USER_ID, _rp_new_prefs)
                    st.session_state["_cached_prefs"] = _rp_new_prefs
        with _rp_gap_col3:
            if _rp_min_gap > 0 or _rp_min_gap_vs_ib > 0:
                st.caption(
                    f"Gap filter active: gap ≥ **{_rp_min_gap}%** "
                    + (f"& gap/IB ≥ **{_rp_min_gap_vs_ib}×**" if _rp_min_gap_vs_ib > 0 else "")
                    + "\n\nNote: records before the latest backtest run have no gap data and will be excluded when this filter is active."
                )
            else:
                st.caption("Gap filters off — all setups included regardless of gap size.")

        # ── Per-structure TCS cutoff guide (bot mode only) ──────────────────────
        if _rp_bot_mode and supabase and _rp_uid:
            with st.expander("📋 View per-structure TCS cutoffs (based on your accuracy data)", expanded=False):
                try:
                    _guide_rows = _cached_tcs_thresholds()
                    if _guide_rows:
                        # Map weight keys back to display labels
                        _WKEY_DISP = {
                            "trending":  "Trend Day",
                            "neutral":   "Neutral",
                            "neutral_extreme": "Ntrl Extreme",
                            "rotational": "Rotational",
                            "normal":    "Normal",
                        }
                        _guide_lines = []
                        for _gd in _guide_rows:
                            _disp     = _gd.get("structure", "Unknown")
                            _n        = int(_gd.get("sample_count") or 0)
                            _j_n      = int(_gd.get("journal_n") or 0)
                            _b_n      = int(_gd.get("bot_n") or 0)
                            _h_n      = int(_gd.get("historical_n") or 0)
                            _base_tcs = int(_gd.get("recommended_tcs") or 50) if _n >= 5 else 50
                            _eff_tcs  = max(0, min(100, _base_tcs + _rp_tcs_offset))
                            _conf     = _gd.get("confidence", "")
                            _status   = _gd.get("status", "")
                            if _n >= 5:
                                _src_parts = []
                                if _j_n: _src_parts.append(f"journal:{_j_n}")
                                if _b_n: _src_parts.append(f"bot:{_b_n}")
                                if _h_n: _src_parts.append(f"hist:{_h_n}")
                                _src = f"n={_n} ({', '.join(_src_parts)}), {_conf}"
                            else:
                                _src = "insufficient data → default 50"
                            _guide_lines.append(f"{_status} **{_disp}**: TCS {_base_tcs} → **effective {_eff_tcs}** ({_src})")
                        # Unknown / fallback line
                        _fb = max(0, min(100, 50 + _rp_tcs_offset))
                        _guide_lines.append(f"**Other / unmapped**: base 50 → **effective {_fb}** (default fallback)")
                        for _gl in _guide_lines:
                            st.markdown("- " + _gl)
                        st.caption(
                            "📌 **Why fewer trades than TCS ≥ 50?** — Each structure's threshold is calibrated from your "
                            "accuracy data (journal + bot + 11k backtest rows). Neutral is currently **TCS 59** (71.5% accuracy) "
                            "— setups below 59 are filtered out as lower-edge. Use TCS Adjustment slider −9 to restore TCS 50 baseline."
                        )
                        if _rp_tcs_offset != 0:
                            st.caption(
                                f"TCS Adjustment is set to **{_rp_tcs_offset:+d}** — every structure's threshold has been shifted by this amount. "
                                "Negative = looser (more trades), Positive = stricter (fewer, higher-quality trades)."
                            )
                    else:
                        st.caption("No accuracy data found yet. All structures default to TCS ≥ 50.")
                except Exception:
                    st.caption("Could not load threshold data. All structures default to TCS ≥ 50.")

        _rp_run = st.button("▶ Run Replay", use_container_width=True, key="rp_run_btn",
                            type="primary")

        # ── 🎯 TCS + Scan Type Optimizer ─────────────────────────────────────────
        with st.expander("🎯 Find Optimal Filter Combo — maximize +R", expanded=False):
            st.caption(
                "Scans every combination of **Scan Type × TCS floor** across your selected date range "
                "and ranks them by expectancy per trade. Set your date range above first, then click Run."
            )
            _opt_min_col, _opt_btn_col = st.columns([1, 2])
            with _opt_min_col:
                _opt_min_trades = st.number_input(
                    "Min trades per combo", min_value=5, max_value=500, value=20, step=5,
                    key="rp_opt_min_trades",
                    help="Combos with fewer trades than this are hidden — avoids statistically meaningless results.",
                )
            with _opt_btn_col:
                _opt_run_btn = st.button("🔍 Run Optimizer", key="rp_opt_run_btn", use_container_width=True)

            if _opt_run_btn and supabase and _rp_uid:
                with st.spinner("Fetching all setups across date range…"):
                    try:
                        _opt_all: list = []
                        _opt_off = 0
                        _opt_ps  = 1000
                        while True:
                            _opt_resp = (
                                supabase.table("backtest_sim_runs")
                                .select("scan_type,tcs,actual_outcome,follow_thru_pct,false_break_up,false_break_down,stop_dist_pct")
                                .eq("user_id", _rp_uid)
                                .gte("sim_date", str(_rp_start))
                                .lte("sim_date", str(_rp_end))
                                .range(_opt_off, _opt_off + _opt_ps - 1)
                                .execute()
                            )
                            _opt_pg = _opt_resp.data or []
                            _opt_all.extend(_opt_pg)
                            if len(_opt_pg) < _opt_ps:
                                break
                            _opt_off += _opt_ps
                        st.session_state["_rp_opt_rows"]  = _opt_all
                        st.session_state["_rp_opt_range"] = f"{_rp_start} → {_rp_end}"
                    except Exception as _opt_e:
                        st.error(f"Optimizer fetch failed: {_opt_e}")

            _opt_rows     = st.session_state.get("_rp_opt_rows", [])
            _opt_range_lbl = st.session_state.get("_rp_opt_range", "")

            if _opt_rows:
                st.caption(f"Dataset: **{len(_opt_rows):,}** total setups  |  {_opt_range_lbl}")

                _OPT_TCS_FLOORS   = [0, 40, 50, 55, 60, 65, 70, 75, 80]
                _OPT_SCAN_BUCKETS = [
                    ("All Scan Types", None),
                    ("Morning only",   "morning"),
                    ("Intraday only",  "intraday"),
                    ("EOD only",       "eod"),
                ]

                _opt_table_rows = []
                for _os_lbl, _os_val in _OPT_SCAN_BUCKETS:
                    for _ofloor in _OPT_TCS_FLOORS:
                        _combo_pnls: list = []
                        for _or in _opt_rows:
                            if _os_val and _or.get("scan_type") != _os_val:
                                continue
                            _ao = _or.get("actual_outcome", "")
                            if _ao not in ("Bullish Break", "Bearish Break"):
                                continue
                            if float(_or.get("tcs") or 0) < _ofloor:
                                continue
                            _fb_up  = bool(_or.get("false_break_up"))
                            _fb_dn  = bool(_or.get("false_break_down"))
                            _ft_pct = float(_or.get("follow_thru_pct") or 0)
                            _sd_pct = float(_or.get("stop_dist_pct") or 0)
                            if _fb_up or _fb_dn:
                                _opnl = -1.0
                            elif _sd_pct > 0:
                                _opnl = min(abs(_ft_pct) / _sd_pct, 20.0)
                            else:
                                continue
                            _combo_pnls.append(_opnl)

                        _on = len(_combo_pnls)
                        if _on < int(_opt_min_trades):
                            continue
                        _owins   = sum(1 for v in _combo_pnls if v > 0)
                        _olosses = _on - _owins
                        _owr     = _owins / _on * 100
                        _oavgw   = sum(v for v in _combo_pnls if v > 0) / _owins if _owins else 0
                        _oavgl   = sum(v for v in _combo_pnls if v < 0) / _olosses if _olosses else 0
                        _oexp    = sum(_combo_pnls) / _on
                        _ototr   = sum(_combo_pnls)
                        _ocum    = 0.0
                        _opeak   = 0.0
                        _omaxdd  = 0.0
                        for _ov in _combo_pnls:
                            _ocum  += _ov
                            if _ocum > _opeak:
                                _opeak = _ocum
                            _odd = _opeak - _ocum
                            if _odd > _omaxdd:
                                _omaxdd = _odd
                        _opt_table_rows.append({
                            "Scan Type":    _os_lbl,
                            "Min TCS":      _ofloor if _ofloor > 0 else "Any",
                            "Trades":       _on,
                            "Win Rate %":   round(_owr, 1),
                            "Avg Win (R)":  round(_oavgw, 3),
                            "Avg Loss (R)": round(_oavgl, 3),
                            "Expectancy":   round(_oexp, 3),
                            "Total R":      round(_ototr, 1),
                            "Max DD (R)":   round(_omaxdd, 2),
                        })

                if not _opt_table_rows:
                    st.warning(
                        f"No combinations had ≥ {int(_opt_min_trades)} trades. "
                        "Lower the minimum or expand your date range."
                    )
                else:
                    _opt_sort_col = st.radio(
                        "Sort results by",
                        ["Expectancy", "Total R", "Win Rate %", "Max DD (R)"],
                        horizontal=True,
                        key="opt_sort_col",
                    )
                    _opt_sort_asc = _opt_sort_col == "Max DD (R)"
                    _opt_df = pd.DataFrame(_opt_table_rows).sort_values(_opt_sort_col, ascending=_opt_sort_asc).reset_index(drop=True)
                    _opt_best = _opt_df.iloc[0]

                    _opt_sort_label = (
                        f"lowest Max DD {_opt_best['Max DD (R)']:.2f}R"
                        if _opt_sort_col == "Max DD (R)"
                        else f"{_opt_best['Expectancy']:+.3f}R expectancy"
                        if _opt_sort_col == "Expectancy"
                        else f"{_opt_best['Total R']:+.1f} Total R"
                        if _opt_sort_col == "Total R"
                        else f"{_opt_best['Win Rate %']}% Win Rate"
                    )
                    st.markdown(
                        f'<div style="background:#0a2a0a;border-left:3px solid #00e676;padding:8px 14px;'
                        f'border-radius:4px;margin-bottom:10px;">'
                        f'<span style="color:#00e676;font-weight:700;">🏆 Best combo (by {_opt_sort_col}): '
                        f'{_opt_best["Scan Type"]} · TCS ≥ {_opt_best["Min TCS"]} — '
                        f'{_opt_sort_label} · '
                        f'{_opt_best["Win Rate %"]}% WR · {int(_opt_best["Trades"])} trades · '
                        f'Max DD {_opt_best["Max DD (R)"]:.2f}R</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    def _opt_style(row):
                        if row.name == 0:
                            return ["background-color:#0d2a1a;color:#00e676;font-weight:700;"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        _opt_df.style.apply(_opt_style, axis=1),
                        use_container_width=True,
                        hide_index=True,
                    )

                    _opt_snap_map = {
                        "All Scan Types": "All",
                        "Morning only":   "Morning (10:47 AM)",
                        "Intraday only":  "Intraday (2:00 PM)",
                        "EOD only":       "EOD (4:00 PM)",
                    }
                    if st.button(
                        f"✅ Apply best combo to replay filters",
                        key="rp_opt_apply_best",
                        use_container_width=True,
                    ):
                        st.session_state["rp_scan_type"] = _opt_snap_map.get(_opt_best["Scan Type"], "All")
                        _opt_tcs_val = int(_opt_best["Min TCS"]) if str(_opt_best["Min TCS"]).lstrip("-").isdigit() else 0
                        st.session_state["rp_min_tcs_slider"] = _opt_tcs_val
                        st.rerun()

        if _rp_run:
            if not supabase or not _rp_uid:
                st.warning("Log in and connect Supabase to use this feature.")
            else:
                with st.spinner("Loading historical trades…"):
                    try:
                        # Paginate — Supabase caps at 1000 rows per request
                        _rp_rows: list = []
                        _rp_page_size = 1000
                        _rp_offset = 0
                        while True:
                            _rp_q = (
                                supabase.table("backtest_sim_runs")
                                .select("sim_date,ticker,open_price,ib_low,ib_high,tcs,predicted,actual_outcome,win_loss,follow_thru_pct,scan_type,gap_pct,gap_vs_ib_pct,pnl_r_sim,false_break_up,false_break_down,eod_pnl_r,tiered_pnl_r")
                                .eq("user_id", _rp_uid)
                                .gte("sim_date", str(_rp_start))
                                .lte("sim_date", str(_rp_end))
                                .range(_rp_offset, _rp_offset + _rp_page_size - 1)
                            )
                            if _rp_scan_type_val is not None:
                                _rp_q = _rp_q.eq("scan_type", _rp_scan_type_val)
                            _rp_resp = _rp_q.execute()
                            _rp_page = _rp_resp.data or []
                            _rp_rows.extend(_rp_page)
                            if len(_rp_page) < _rp_page_size:
                                break  # last page
                            _rp_offset += _rp_page_size
                    except Exception as _rp_e:
                        _rp_rows = []
                        st.error(f"Failed to load data: {_rp_e}")

                # ── Best (Most Profit Combined) — pick scan with largest absolute move per ticker+date ──
                # De-dup across scan types (morning vs intraday) by |follow_thru_pct| (MFE).
                # pnl_r_sim is NOT used here: stored values are corrupted (NULL close_price backfill
                # gave all-positive pnl_r_sim regardless of false breaks). Using |ft_pct| correctly
                # picks the scan that saw the biggest raw market move, which is what "Best" means.
                # P&L (including false-break losses) is computed separately per-trade below.
                if _rp_rows and _rp_best_mode:
                    _best_scores: dict = {}
                    _best_seen: dict   = {}
                    for _brow in _rp_rows:
                        _bkey   = (_brow.get("ticker",""), _brow.get("sim_date",""))
                        _bscore = abs(float(_brow.get("follow_thru_pct") or 0))
                        if _bkey not in _best_seen or _bscore > _best_scores[_bkey]:
                            _best_seen[_bkey]   = _brow
                            _best_scores[_bkey] = _bscore
                    _rp_rows = list(_best_seen.values())
                    # Label each row so Snapshot column in trade log shows "best (morning)" etc.
                    for _brow in _rp_rows:
                        _brow["scan_type"] = f"best ({_brow.get('scan_type','?')})"

                # Cache raw rows for TCS Optimizer (needs unfiltered dataset)
                if _rp_rows:
                    st.session_state["_bt_replay_rows"] = _rp_rows

                if not _rp_rows:
                    st.info("No backtest data found for this date range. Run the Batch Backtest first.")
                else:
                    # ── Raw data snapshot (before any simulation math) ─────────────
                    _raw_wins   = sum(1 for r in _rp_rows if r.get("win_loss") == "Win")
                    _raw_losses = sum(1 for r in _rp_rows if r.get("win_loss") == "Loss")
                    _raw_dir    = sum(1 for r in _rp_rows
                                      if "bullish" in str(r.get("actual_outcome","")).lower()
                                      or "bearish" in str(r.get("actual_outcome","")).lower())
                    _raw_wr = round(_raw_wins / (_raw_wins + _raw_losses) * 100, 1) if (_raw_wins + _raw_losses) else 0

                    # Load per-structure TCS thresholds for bot mode
                    # Default = 50, matching live bot's MIN_TCS env var.
                    # compute_structure_tcs_thresholds() uses 65 as its no-data
                    # fallback, but that's based on zero accuracy data — 50 is the
                    # correct operational baseline until each structure has data.
                    _BOT_TCS_DEFAULT = 50
                    _struct_tcs_map: dict = {}
                    if _rp_bot_mode:
                        try:
                            _thresh_list = _cached_tcs_thresholds()
                            _WKEY_TO_SKEY = {
                                "trend_bull":     "trend",
                                "trend_bear":     "trend",
                                "nrml_variation": "normal",
                                "non_trend":      "neutral",
                                "ntrl_extreme":   "ntrl_extreme",
                                "neutral":        "neutral",
                                "double_dist":    "double_dist",
                                "normal":         "normal",
                            }
                            _label_to_rec: dict = {}
                            for _td in _thresh_list:
                                _sl  = _td.get("structure", "").lower()
                                # If no accuracy data yet, honour bot baseline (50)
                                _has_data = (_td.get("sample_count") or 0) > 0
                                _rt  = int(_td.get("recommended_tcs") or _BOT_TCS_DEFAULT) if _has_data else _BOT_TCS_DEFAULT
                                if "extreme" in _sl:
                                    _label_to_rec["ntrl_extreme"] = _rt
                                elif "neutral" in _sl:
                                    _label_to_rec["neutral"] = _rt
                                elif "trend" in _sl:
                                    _label_to_rec["trend"] = _rt
                                elif "double" in _sl:
                                    _label_to_rec["double_dist"] = _rt
                                elif "rotation" in _sl:
                                    _label_to_rec["rotational"] = _rt
                                elif "normal" in _sl:
                                    _label_to_rec["normal"] = _rt
                            for _wk, _sk in _WKEY_TO_SKEY.items():
                                _base = _label_to_rec.get(_sk, _BOT_TCS_DEFAULT)
                                _struct_tcs_map[_wk] = max(0, min(100, _base + _rp_tcs_offset))
                        except Exception:
                            pass

                    # Effective fallback for structures not in the map (offset applied)
                    _bot_tcs_fallback = max(0, min(100, _BOT_TCS_DEFAULT + (_rp_tcs_offset if _rp_bot_mode else 0)))

                    if _rp_bot_mode:
                        _raw_tcs_filtered = sum(
                            1 for r in _rp_rows
                            if float(r.get("tcs") or 0) >= _struct_tcs_map.get(
                                _label_to_weight_key(str(r.get("predicted") or "")), _bot_tcs_fallback
                            )
                        )
                        _offset_str   = (f" (adj {_rp_tcs_offset:+d})" if _rp_tcs_offset != 0 else "")
                        _compound_str = " + compounding" if _rp_compound else ", no compounding"
                        _sizing_note  = f"${_rp_pos_size:,} position{_compound_str}, per-structure TCS thresholds{_offset_str}"
                    else:
                        _raw_tcs_filtered = sum(1 for r in _rp_rows if float(r.get("tcs") or 0) >= _rp_min_tcs)
                        _sizing_note = f"fixed ${round(float(_rp_equity)*(_rp_risk_pct/100),0):,.0f} risk (no compounding)"

                    st.caption(
                        f"**Raw DB snapshot** — {len(_rp_rows)} records loaded  |  "
                        f"Structure Win Rate: **{_raw_wr}%** ({_raw_wins}W / {_raw_losses}L)  |  "
                        f"Directional outcomes: **{_raw_dir}**  |  "
                        f"Passing TCS filter: **{_raw_tcs_filtered}**  |  "
                        f"Sizing: {_sizing_note}"
                    )
                    st.markdown("---")

                    _rp_trades = []
                    _rp_equity_cur = float(_rp_equity)
                    _rp_equity_curve = [_rp_equity_cur]
                    _rp_dates_seen = []
                    # Fixed dollar risk = % of STARTING equity only (no compounding)
                    _fixed_risk_amt = float(_rp_equity) * (_rp_risk_pct / 100.0)

                    _rp_by_date = {}
                    for _rp_r in _rp_rows:
                        _d = str(_rp_r.get("sim_date", ""))
                        _rp_by_date.setdefault(_d, []).append(_rp_r)

                    for _rp_date_str in sorted(_rp_by_date.keys()):
                        _day_rows = _rp_by_date[_rp_date_str]
                        _day_pnl = 0.0
                        _day_trades = 0

                        for _rp_r in _day_rows:
                            _tcs      = float(_rp_r.get("tcs") or 0)
                            _wl       = str(_rp_r.get("win_loss") or "")
                            _ibl      = float(_rp_r.get("ib_low") or 0)
                            _ibh      = float(_rp_r.get("ib_high") or 0)
                            _ft       = float(_rp_r.get("follow_thru_pct") or 0)
                            _pred     = str(_rp_r.get("predicted") or "")
                            _tkr      = str(_rp_r.get("ticker") or "")

                            # TCS filter — per-structure threshold in bot mode, flat slider otherwise
                            _rec_tcs = None
                            if _rp_bot_mode:
                                _pred_wk  = _label_to_weight_key(str(_rp_r.get("predicted") or ""))
                                _rec_tcs  = _struct_tcs_map.get(_pred_wk, _bot_tcs_fallback)
                                if _tcs < _rec_tcs:
                                    continue
                            else:
                                if _tcs < _rp_min_tcs:
                                    continue

                            if abs(_ft) < _rp_min_ft:
                                continue

                            # Gap filters (0 = off; records missing gap_pct treated as 0)
                            _gap     = float(_rp_r.get("gap_pct") or 0)
                            _gap_ib  = float(_rp_r.get("gap_vs_ib_pct") or 0)
                            if _rp_min_gap > 0 and _gap < _rp_min_gap:
                                continue
                            if _rp_min_gap_vs_ib > 0 and _gap_ib < _rp_min_gap_vs_ib:
                                continue

                            if _wl not in ("Win", "Loss"):
                                continue
                            if _ibh <= _ibl or _ibl <= 0:
                                continue

                            # ── Direction: ONLY from actual_outcome (confirmed market move) ─────
                            # actual_outcome = what the market DID ("Bullish Break"/"Bearish Break")
                            # predicted = structure TYPE ("Neutral", "Normal") — NEVER a direction.
                            # Using predicted as fallback included Range-Bound days as fake trades
                            # (market didn't break, but MFE was positive → fake 100% win rate).
                            _actual_out = str(_rp_r.get("actual_outcome") or "").strip()
                            _direction  = _actual_out if _actual_out in ("Bullish Break", "Bearish Break") else ""
                            if _direction == "Bullish Break":
                                _entry = _ibh
                                _stop  = _ibl
                                _dir   = 1
                            elif _direction == "Bearish Break":
                                _entry = _ibl
                                _stop  = _ibh
                                _dir   = -1
                            else:
                                continue  # Range-Bound / Both Sides / non-directional → no trade

                            _stop_dist = abs(_entry - _stop)
                            if _stop_dist < 0.01:
                                continue
                            if _entry > 0 and _stop_dist / _entry < 0.005:
                                continue

                            # ── Position sizing ───────────────────────────────────────────────
                            # Each ticker gets its own independent position — same as the live bot
                            # which places each trade with its own fixed risk allocation.
                            # The 20× compound cap prevents runaway growth if equity balloons.
                            if _rp_bot_mode:
                                if _rp_compound:
                                    _compound_factor = min(_rp_equity_cur / float(_rp_equity), 20.0)
                                    _eff_pos = _rp_pos_size * _compound_factor
                                else:
                                    _eff_pos = _rp_pos_size
                                _shares  = _eff_pos / max(_entry, 0.01)
                                _risk_1r = _eff_pos * (_stop_dist / max(_entry, 0.01))
                            else:
                                _shares  = _fixed_risk_amt / _stop_dist
                                _risk_1r = _fixed_risk_amt

                            # ── P&L: false_break detection + MFE R-multiple ───────────────────
                            # DO NOT use stored pnl_r_sim — it was computed from MFE fallback
                            # (close_price was NULL for all pre-migration rows), so every value
                            # is positive → 100% win rate before the simulation even starts.
                            #
                            # false_break_up / false_break_down ARE reliably populated for all
                            # batch backtest rows (computed from intraday bars at scan time).
                            # They are the only reliable loss signal without close_price.
                            #
                            # follow_thru_pct is MFE (max favorable excursion):
                            #   Bullish Break: always > 0  (how far up price went)
                            #   Bearish Break: always < 0  (how far down price went)
                            # abs() normalises both directions to the same R-multiple scale.
                            _stop_dist_pct = _stop_dist / max(_entry, 0.01) * 100.0
                            _false_break   = bool(
                                _rp_r.get("false_break_up")   if _direction == "Bullish Break"
                                else _rp_r.get("false_break_down") or False
                            )
                            if _false_break:
                                _pnl_r = -1.0   # price reversed back through stop → full -1R
                            else:
                                _raw_r  = abs(_ft) / _stop_dist_pct if _stop_dist_pct > 0 else 0.0
                                # Cap winners: bot mode → 50% of position cap; % risk → _rp_max_move
                                _max_r  = (50.0 / _stop_dist_pct) if _rp_bot_mode else (_rp_max_move / _stop_dist_pct)
                                _pnl_r  = min(_raw_r, _max_r)
                            _trade_pnl = _pnl_r * _risk_1r

                            _day_pnl      += _trade_pnl
                            _day_trades   += 1
                            _rp_equity_cur += _trade_pnl

                            _rp_scan_str = str(_rp_r.get("scan_type") or "morning").lower()
                            if _rp_scan_str == "intraday":
                                _rp_priority = "P1 🔴" if _tcs >= 70 else "P2 🟠"
                            else:
                                _rp_priority = "P3 🟡" if _tcs >= 70 else "P4 🟢"
                            _rp_eod_r    = _rp_r.get("eod_pnl_r")
                            _rp_tiered_r = _rp_r.get("tiered_pnl_r")
                            _rp_trades.append({
                                "Priority":    _rp_priority,
                                "Date":        _rp_date_str,
                                "Snapshot":    _rp_scan_str.capitalize(),
                                "Ticker":      _tkr,
                                "TCS":         int(_tcs),
                                "TCS Floor":   _rec_tcs,
                                "Structure":   _pred,
                                "Direction":   "Long" if _dir == 1 else "Short",
                                "Entry":       round(_entry, 2),
                                "Stop":        round(_stop, 2),
                                "Shares":      int(_shares),
                                "W/L":         "Win" if _trade_pnl > 0 else "Loss",
                                "False Break": _false_break,
                                "R (MFE)":     round(_pnl_r, 2),
                                "R (EOD)":     round(float(_rp_eod_r), 2) if _rp_eod_r is not None else None,
                                "R (Tiered)":  round(float(_rp_tiered_r), 2) if _rp_tiered_r is not None else None,
                                "Move %":      round(_ft, 2),
                                "P&L ($)":     round(_trade_pnl, 2),
                                "Equity":      round(_rp_equity_cur, 2),
                            })

                        if _day_trades > 0:
                            _rp_equity_curve.append(_rp_equity_cur)
                            _rp_dates_seen.append(_rp_date_str)

                    if not _rp_trades:
                        st.warning("No qualifying trades found. Try lowering the Min TCS filter or expanding the date range.")
                    else:
                        _rp_df = pd.DataFrame(_rp_trades)
                        if not _rp_bot_mode and "TCS Floor" in _rp_df.columns:
                            _rp_df = _rp_df.drop(columns=["TCS Floor"])
                        _total_trades  = len(_rp_df)
                        _total_pnl     = _rp_df["P&L ($)"].sum()
                        _net_return    = round((_rp_equity_cur - float(_rp_equity)) / float(_rp_equity) * 100, 2)

                        # Win/loss based on P&L sign (correct for both long and short)
                        _pnl_wins      = (_rp_df["P&L ($)"] > 0).sum()
                        _pnl_losses    = (_rp_df["P&L ($)"] <= 0).sum()
                        _win_rate      = round(_pnl_wins / _total_trades * 100, 1) if _total_trades else 0
                        _avg_win       = _rp_df[_rp_df["P&L ($)"] > 0]["P&L ($)"].mean() if _pnl_wins else 0
                        _avg_loss      = _rp_df[_rp_df["P&L ($)"] <= 0]["P&L ($)"].mean() if _pnl_losses else 0
                        _gross_wins    = _rp_df[_rp_df["P&L ($)"] > 0]["P&L ($)"].sum()
                        _gross_losses  = abs(_rp_df[_rp_df["P&L ($)"] <= 0]["P&L ($)"].sum())
                        _profit_factor = (_gross_wins / _gross_losses) if _gross_losses > 0 else float("inf")
                        _pf_str        = f"{_profit_factor:.2f}x" if _profit_factor != float("inf") else "∞"

                        if _rp_bot_mode:
                            if _rp_tcs_offset == 0:
                                _rp_floor_label = f"≈ TCS {_rp_effective_floor}"
                            else:
                                _rp_offset_sign_res = f"+{_rp_tcs_offset}" if _rp_tcs_offset > 0 else str(_rp_tcs_offset)
                                _rp_floor_label = f"≈ TCS {_rp_effective_floor} (base 50 + offset {_rp_offset_sign_res})"
                            st.caption(f"🤖 **Bot mode** · Effective TCS floor applied: **{_rp_floor_label}**")

                        _sm1, _sm2, _sm3, _sm4, _sm5 = st.columns(5)
                        _sm1.metric("Net P&L", f"${_total_pnl:,.0f}", f"{_net_return:+.1f}%")
                        _sm2.metric("Win Rate", f"{_win_rate}%",
                                    help="% of trades with positive P&L (not DB structure accuracy)")
                        _sm3.metric("Total Trades", _total_trades)
                        _sm4.metric("Avg Win", f"${_avg_win:,.0f}")
                        _sm5.metric("Profit Factor", _pf_str)

                        # ── R-based stats row ─────────────────────────────────────────────
                        _r_ser          = _rp_df["R (MFE)"]
                        _false_brk_n    = _rp_df["False Break"].sum()
                        _false_brk_rate = round(_false_brk_n / _total_trades * 100, 1) if _total_trades else 0
                        _avg_win_r      = round(_r_ser[_r_ser > 0].mean(), 2) if (_r_ser > 0).any() else 0
                        _avg_loss_r     = round(_r_ser[_r_ser < 0].mean(), 2) if (_r_ser < 0).any() else 0
                        _expectancy_r   = round(_r_ser.mean(), 3) if _total_trades else 0
                        _cum_r_vals     = _r_ser.cumsum().reset_index(drop=True)
                        _peak_r         = _cum_r_vals.cummax()
                        _max_dd_r       = round((_cum_r_vals - _peak_r).min(), 2) if _total_trades else 0
                        _sm6, _sm7, _sm8, _sm9, _sm10 = st.columns(5)
                        _sm6.metric("False-Break Rate",  f"{_false_brk_rate}%",
                                    help="% of trades where false_break_up/false_break_down triggered a -1R stop-out")
                        _sm7.metric("Avg Win (R)",    f"+{_avg_win_r}R")
                        _sm8.metric("Avg Loss (R)",   f"{_avg_loss_r}R")
                        _sm9.metric("Expectancy",     f"{_expectancy_r:+.3f}R / trade",
                                    help="Average R gained per trade — the raw edge, independent of position size")
                        _sm10.metric("Max Drawdown (R)", f"{abs(_max_dd_r)}R",
                                     help="Largest peak-to-trough loss in cumulative R — worst losing run in the sim (shown as a positive magnitude)")

                        _cum_r = _r_ser.cumsum().reset_index(drop=True)

                        _RP_CHART_OPTIONS = ["Equity ($)", "Cumulative R", "Both"]

                        # ── Restore chart view from localStorage (cross-session) ────────
                        import streamlit.components.v1 as _cmp_chart_view
                        _cmp_chart_view.html("""
<script>
(function() {
    var _LS_KEY = 'rp_chart_view';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('rp_chart_view')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('rp_chart_view', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

                        if "rp_chart_view" not in st.session_state:
                            _qp_chart = st.query_params.get("rp_chart_view", "Equity ($)")
                            st.session_state["rp_chart_view"] = (
                                _qp_chart if _qp_chart in _RP_CHART_OPTIONS else "Equity ($)"
                            )

                        _chart_view = st.radio(
                            "Chart view",
                            options=_RP_CHART_OPTIONS,
                            index=_RP_CHART_OPTIONS.index(
                                st.session_state.get("rp_chart_view", "Equity ($)")
                                if st.session_state.get("rp_chart_view", "Equity ($)") in _RP_CHART_OPTIONS
                                else "Equity ($)"
                            ),
                            horizontal=True,
                            key="rp_chart_view",
                            label_visibility="collapsed",
                        )

                        _qp_chart_cur = st.query_params.get("rp_chart_view")
                        if _qp_chart_cur != _chart_view:
                            st.query_params["rp_chart_view"] = _chart_view
                        _cmp_chart_view.html(
                            f"<script>localStorage.setItem('rp_chart_view', {repr(_chart_view)});</script>",
                            height=0,
                        )

                        # ── Compute max-drawdown period indices (shared by Equity and Cum-R charts) ──
                        _dd_series = _cum_r_vals - _peak_r
                        if _total_trades and _max_dd_r < 0:
                            _dd_trough_idx = int(_dd_series.idxmin())
                            _dd_peak_idx   = int(_cum_r_vals.iloc[:_dd_trough_idx + 1].idxmax())
                        else:
                            _dd_trough_idx = None
                            _dd_peak_idx   = None

                        if _chart_view == "Equity ($)":
                            # _rp_equity_curve index 0 = starting capital (before any trade).
                            # R-based trade index k → equity index k+1.
                            st.caption("**Equity Curve** — dollar P&L with position sizing applied")
                            _fig_eq = go.Figure()
                            _fig_eq.add_trace(go.Scatter(
                                x=list(range(len(_rp_equity_curve))),
                                y=_rp_equity_curve,
                                mode="lines",
                                name="Equity ($)",
                                line=dict(color="#4fc3f7", width=2),
                            ))
                            if _dd_trough_idx is not None and _dd_peak_idx is not None:
                                _eq_peak_idx   = _dd_peak_idx + 1
                                _eq_trough_idx = _dd_trough_idx + 1
                                _fig_eq.add_vrect(
                                    x0=_eq_peak_idx,
                                    x1=_eq_trough_idx,
                                    fillcolor="rgba(220, 50, 50, 0.15)",
                                    layer="below",
                                    line_width=0,
                                )
                                _fig_eq.add_trace(go.Scatter(
                                    x=[_eq_peak_idx],
                                    y=[float(_rp_equity_curve[_eq_peak_idx])],
                                    mode="markers",
                                    marker=dict(color="#2ca02c", size=10, symbol="triangle-down"),
                                    name=f"DD Start (trade #{_dd_peak_idx})",
                                ))
                                _fig_eq.add_trace(go.Scatter(
                                    x=[_eq_trough_idx],
                                    y=[float(_rp_equity_curve[_eq_trough_idx])],
                                    mode="markers",
                                    marker=dict(color="#d62728", size=10, symbol="triangle-up"),
                                    name=f"DD End (trade #{_dd_trough_idx})",
                                ))
                            _fig_eq.update_layout(
                                height=240,
                                margin=dict(l=0, r=0, t=10, b=30),
                                xaxis_title="Trade #",
                                yaxis_title="Equity ($)",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                plot_bgcolor="rgba(0,0,0,0)",
                                paper_bgcolor="rgba(0,0,0,0)",
                            )
                            _fig_eq.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            _fig_eq.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            st.plotly_chart(_fig_eq, use_container_width=True)
                            if _dd_trough_idx is not None and _dd_peak_idx is not None:
                                _eq_dd_dollars = float(_rp_equity_curve[_eq_trough_idx]) - float(_rp_equity_curve[_eq_peak_idx])
                                st.caption(
                                    f"🔴 Max drawdown period: trade \u00a0**#{_dd_peak_idx}** → **#{_dd_trough_idx}**"
                                    f"\u2002({_dd_trough_idx - _dd_peak_idx} trades)\u2002|\u2002dollar magnitude\u00a0**${abs(_eq_dd_dollars):,.0f}**"
                                )
                        elif _chart_view == "Cumulative R":
                            st.caption("**Cumulative R (raw edge)** — sum of R multiples trade by trade, no position sizing or compounding")

                            _fig_cum_r = go.Figure()
                            _fig_cum_r.add_trace(go.Scatter(
                                x=list(range(len(_cum_r))),
                                y=_cum_r.tolist(),
                                mode="lines",
                                name="Cumulative R",
                                line=dict(color="#1f77b4", width=2),
                            ))

                            if _dd_trough_idx is not None and _dd_peak_idx is not None:
                                _fig_cum_r.add_vrect(
                                    x0=_dd_peak_idx,
                                    x1=_dd_trough_idx,
                                    fillcolor="rgba(220, 50, 50, 0.15)",
                                    layer="below",
                                    line_width=0,
                                )
                                _fig_cum_r.add_trace(go.Scatter(
                                    x=[_dd_peak_idx],
                                    y=[float(_cum_r_vals.iloc[_dd_peak_idx])],
                                    mode="markers",
                                    marker=dict(color="#2ca02c", size=10, symbol="triangle-down"),
                                    name=f"DD Start (trade #{_dd_peak_idx})",
                                ))
                                _fig_cum_r.add_trace(go.Scatter(
                                    x=[_dd_trough_idx],
                                    y=[float(_cum_r_vals.iloc[_dd_trough_idx])],
                                    mode="markers",
                                    marker=dict(color="#d62728", size=10, symbol="triangle-up"),
                                    name=f"DD End (trade #{_dd_trough_idx})",
                                ))

                            _fig_cum_r.update_layout(
                                height=240,
                                margin=dict(l=0, r=0, t=10, b=30),
                                xaxis_title="Trade #",
                                yaxis_title="Cumulative R",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                plot_bgcolor="rgba(0,0,0,0)",
                                paper_bgcolor="rgba(0,0,0,0)",
                            )
                            _fig_cum_r.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            _fig_cum_r.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            st.plotly_chart(_fig_cum_r, use_container_width=True)

                            if _dd_trough_idx is not None and _dd_peak_idx is not None:
                                st.caption(
                                    f"🔴 Max drawdown period: trade \u00a0**#{_dd_peak_idx}** → **#{_dd_trough_idx}**"
                                    f"\u2002({_dd_trough_idx - _dd_peak_idx} trades)\u2002|\u2002magnitude\u00a0**{abs(_max_dd_r)}R**"
                                )
                        else:
                            # ── Dual-axis: Equity ($) left, Cumulative R right ──────────
                            # _rp_equity_curve has N+1 points (index 0 = starting capital).
                            # _cum_r has N points (one per trade).  Prepend 0 to cum_r so
                            # both series share the same x-axis: 0 = before any trades.
                            _cum_r_both = pd.concat(
                                [pd.Series([0.0]), _cum_r], ignore_index=True
                            )
                            _n_eq  = len(_rp_equity_curve)
                            _x_eq  = list(range(_n_eq))
                            _x_r   = list(range(len(_cum_r_both)))
                            # ── Compute normalised divergence to find the peak gap ────────
                            _eq_arr   = pd.Series(_rp_equity_curve, dtype=float)
                            _r_arr    = pd.Series(_cum_r_both, dtype=float)
                            _min_len  = min(len(_eq_arr), len(_r_arr))
                            _eq_arr   = _eq_arr.iloc[:_min_len]
                            _r_arr    = _r_arr.iloc[:_min_len]
                            _eq_range = _eq_arr.max() - _eq_arr.min()
                            _r_range  = _r_arr.max()  - _r_arr.min()
                            _eq_norm  = (_eq_arr - _eq_arr.min()) / _eq_range if _eq_range != 0 else _eq_arr * 0
                            _r_norm   = (_r_arr  - _r_arr.min())  / _r_range  if _r_range  != 0 else _r_arr  * 0
                            _div_arr  = (_eq_norm - _r_norm)
                            _div_abs  = _div_arr.abs()
                            _max_div_idx = int(_div_abs.idxmax())
                            _max_div_val = float(_div_arr.iloc[_max_div_idx])
                            if abs(_max_div_val) < 0.02:
                                _div_msg = "Equity and R track closely — position sizing matched raw edge well"
                            elif _max_div_val > 0:
                                _div_msg = "Position sizing amplified raw edge here (equity outpaced R)"
                            else:
                                _div_msg = "Position sizing dampened raw edge here (equity lagged R)"

                            _fig_dual = go.Figure()

                            # Shaded band around the max-divergence point
                            _band_half = max(1, round(_min_len * 0.015))
                            _fig_dual.add_vrect(
                                x0=max(0, _max_div_idx - _band_half),
                                x1=min(_min_len - 1, _max_div_idx + _band_half),
                                fillcolor="rgba(255, 214, 0, 0.12)",
                                layer="below",
                                line_width=0,
                            )
                            # Vertical dashed line at the exact peak
                            _fig_dual.add_vline(
                                x=_max_div_idx,
                                line=dict(color="rgba(255, 214, 0, 0.7)", width=1.5, dash="dot"),
                                annotation_text=f"Max divergence (trade #{_max_div_idx})",
                                annotation_position="top left",
                                annotation_font=dict(color="#ffd600", size=11),
                            )

                            _fig_dual.add_trace(go.Scatter(
                                x=_x_eq,
                                y=_rp_equity_curve,
                                name="Equity ($)",
                                mode="lines",
                                line=dict(color="#4fc3f7", width=2),
                                yaxis="y1",
                            ))
                            _fig_dual.add_trace(go.Scatter(
                                x=_x_r,
                                y=list(_cum_r_both),
                                name="Cumulative R",
                                mode="lines",
                                line=dict(color="#ef9a9a", width=2),
                                yaxis="y2",
                            ))
                            _fig_dual.update_layout(
                                height=340,
                                margin=dict(l=10, r=10, t=10, b=10),
                                paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)",
                                dragmode="zoom",
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom", y=1.02,
                                    xanchor="right", x=1,
                                    font=dict(color="#cccccc"),
                                ),
                                xaxis=dict(
                                    title="Trade #",
                                    gridcolor="#1a1a2e",
                                    color="#cccccc",
                                    zeroline=False,
                                    rangeslider=dict(visible=True, thickness=0.08),
                                ),
                                yaxis=dict(
                                    title=dict(text="Equity ($)", font=dict(color="#4fc3f7")),
                                    tickfont=dict(color="#4fc3f7"),
                                    gridcolor="#1a1a2e",
                                    zeroline=True,
                                    zerolinecolor="#555",
                                ),
                                yaxis2=dict(
                                    title=dict(text="Cumulative R", font=dict(color="#ef9a9a")),
                                    tickfont=dict(color="#ef9a9a"),
                                    overlaying="y",
                                    side="right",
                                    gridcolor="rgba(0,0,0,0)",
                                    zeroline=False,
                                ),
                            )
                            st.plotly_chart(
                                _fig_dual,
                                use_container_width=True,
                                config={
                                    "scrollZoom": True,
                                    "displayModeBar": True,
                                    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                                    "toImageButtonOptions": {"format": "png"},
                                },
                            )
                            st.caption(
                                f"**Equity & R** — divergences reveal where position sizing amplifies or dampens raw edge"
                                f"\u2002|\u2002🟡 **Trade\u00a0#{_max_div_idx}**: {_div_msg}"
                            )

                        # ── Replay CSV download ────────────────────────────────────────────
                        _rp_csv_df = _rp_df.copy()
                        _rp_csv_df.insert(
                            _rp_csv_df.columns.get_loc("R (MFE)") + 1,
                            "Cumulative R",
                            _cum_r.values,
                        ) if "R (MFE)" in _rp_csv_df.columns else None

                        # Rename R columns to human-readable headers for the spreadsheet
                        _rp_csv_df = _rp_csv_df.rename(columns={
                            "R (MFE)":    "MFE R",
                            "R (EOD)":    "EOD Hold R",
                            "R (Tiered)": "Tiered Exit R",
                        })

                        # ── Round numeric columns for clean spreadsheet output ─────────
                        _rp_csv_round_1dp = {"Move %"}
                        _rp_csv_round_2dp = {
                            "Entry", "Stop", "MFE R", "EOD Hold R",
                            "Tiered Exit R", "Cumulative R", "P&L ($)", "Equity",
                        }
                        _rp_csv_int_cols  = {"TCS", "TCS Floor", "Shares"}
                        for _rc in list(_rp_csv_df.columns):
                            if _rc in _rp_csv_round_1dp:
                                _rp_csv_df[_rc] = pd.to_numeric(_rp_csv_df[_rc], errors="coerce").round(1)
                            elif _rc in _rp_csv_round_2dp:
                                _rp_csv_df[_rc] = pd.to_numeric(_rp_csv_df[_rc], errors="coerce").round(2)
                            elif _rc in _rp_csv_int_cols:
                                _rp_csv_df[_rc] = pd.to_numeric(_rp_csv_df[_rc], errors="coerce").astype("Int64")

                        # ── R-column selector ──────────────────────────────────────────────
                        _r_col_options = ["MFE R", "EOD Hold R", "Tiered Exit R"]
                        _r_col_default = [c for c in _r_col_options if c in _rp_csv_df.columns]
                        _r_cols_selected = st.multiselect(
                            "R columns in CSV",
                            options=_r_col_options,
                            default=_r_col_default,
                            key="rp_r_col_select",
                            help=(
                                "Choose which R-multiple columns to include in the downloaded CSV. "
                                "Deselect columns you don't use to keep your spreadsheet tidy."
                            ),
                        )
                        # Drop unselected R columns from the export dataframe.
                        # Cumulative R is the running sum of MFE R, so it is dropped
                        # together when MFE R is excluded.
                        if "MFE R" not in _r_cols_selected:
                            _rp_csv_df = _rp_csv_df.drop(
                                columns=["MFE R", "Cumulative R"],
                                errors="ignore",
                            )
                        if "EOD Hold R" not in _r_cols_selected:
                            _rp_csv_df = _rp_csv_df.drop(columns=["EOD Hold R"], errors="ignore")
                        if "Tiered Exit R" not in _r_cols_selected:
                            _rp_csv_df = _rp_csv_df.drop(columns=["Tiered Exit R"], errors="ignore")

                        if "Structure" in _rp_csv_df.columns:
                            _rp_csv_df["Structure"] = _rp_csv_df["Structure"].apply(_clean_structure_label)

                        # Append a blank separator then a per-stat summary block
                        _csv_cols   = list(_rp_csv_df.columns)
                        _label_col  = _csv_cols[0]
                        _val_col    = _csv_cols[1] if len(_csv_cols) > 1 else _csv_cols[0]

                        def _stat_row(label, value):
                            r = {c: "" for c in _csv_cols}
                            r[_label_col] = label
                            r[_val_col]   = value
                            return r

                        _bt_tier_defs = [
                            ("P1", "🔴", "intraday", 70, 999, "#c62828", "Intraday 70+"),
                            ("P2", "🟠", "intraday", 50,  69, "#ef6c00", "Intraday 50–69"),
                            ("P3", "🟡", "morning",  70, 999, "#f9a825", "Morning 70+"),
                            ("P4", "🟢", "morning",  50,  69, "#2e7d32", "Morning 50–69"),
                        ]

                        # Overall EOD Hold R stats — drawn from _rp_df (stable source,
                        # unaffected by column-deselection in the CSV export)
                        _has_eod_col    = "R (EOD)"    in _rp_df.columns
                        _has_tiered_col = "R (Tiered)" in _rp_df.columns
                        _csv_ov_eod_ser  = pd.to_numeric(_rp_df["R (EOD)"],    errors="coerce").dropna() if _has_eod_col    else pd.Series(dtype=float)
                        _csv_ov_eod_n    = len(_csv_ov_eod_ser)
                        _csv_ov_eod_wr   = round((_csv_ov_eod_ser > 0).sum() / _csv_ov_eod_n * 100, 1) if _csv_ov_eod_n else 0
                        _csv_ov_eod_aw   = round(_csv_ov_eod_ser[_csv_ov_eod_ser > 0].mean(), 2) if (_csv_ov_eod_ser > 0).any() else 0
                        _csv_ov_eod_al   = round(_csv_ov_eod_ser[_csv_ov_eod_ser < 0].mean(), 2) if (_csv_ov_eod_ser < 0).any() else 0
                        _csv_ov_eod_exp  = round(_csv_ov_eod_ser.mean(), 3) if _csv_ov_eod_n else 0

                        # Overall Tiered Exit R stats
                        _csv_ov_tier_ser = pd.to_numeric(_rp_df["R (Tiered)"], errors="coerce").dropna() if _has_tiered_col else pd.Series(dtype=float)
                        _csv_ov_tier_n   = len(_csv_ov_tier_ser)
                        _csv_ov_tier_wr  = round((_csv_ov_tier_ser > 0).sum() / _csv_ov_tier_n * 100, 1) if _csv_ov_tier_n else 0
                        _csv_ov_tier_aw  = round(_csv_ov_tier_ser[_csv_ov_tier_ser > 0].mean(), 2) if (_csv_ov_tier_ser > 0).any() else 0
                        _csv_ov_tier_al  = round(_csv_ov_tier_ser[_csv_ov_tier_ser < 0].mean(), 2) if (_csv_ov_tier_ser < 0).any() else 0
                        _csv_ov_tier_exp = round(_csv_ov_tier_ser.mean(), 3) if _csv_ov_tier_n else 0

                        _summary_rows = [
                            {c: "" for c in _csv_cols},
                            _stat_row("--- R-STATS SUMMARY (MFE) ---", ""),
                            _stat_row("Stop-Out Rate",   f"{_false_brk_rate}%"),
                            _stat_row("Avg Win (R)",     f"+{_avg_win_r}R"),
                            _stat_row("Avg Loss (R)",    f"{_avg_loss_r}R"),
                            _stat_row("Expectancy",      f"{_expectancy_r:+.3f}R/trade"),
                            _stat_row("Max Drawdown (R)",f"{abs(_max_dd_r)}R"),
                        ]
                        if _has_eod_col and _csv_ov_eod_n:
                            _summary_rows += [
                                {c: "" for c in _csv_cols},
                                _stat_row("--- EOD HOLD R SUMMARY ---", ""),
                                _stat_row("Win Rate (EOD)",        f"{_csv_ov_eod_wr}%"),
                                _stat_row("Avg Win (EOD R)",       f"+{_csv_ov_eod_aw}R"),
                                _stat_row("Avg Loss (EOD R)",      f"{_csv_ov_eod_al}R"),
                                _stat_row("Expectancy (EOD R)",    f"{_csv_ov_eod_exp:+.3f}R/trade"),
                            ]
                        if _has_tiered_col and _csv_ov_tier_n:
                            _summary_rows += [
                                {c: "" for c in _csv_cols},
                                _stat_row("--- TIERED EXIT R SUMMARY ---", ""),
                                _stat_row("Win Rate (Tiered)",     f"{_csv_ov_tier_wr}%"),
                                _stat_row("Avg Win (Tiered R)",    f"+{_csv_ov_tier_aw}R"),
                                _stat_row("Avg Loss (Tiered R)",   f"{_csv_ov_tier_al}R"),
                                _stat_row("Expectancy (Tiered R)", f"{_csv_ov_tier_exp:+.3f}R/trade"),
                            ]

                        for _csv_btl, _csv_bte, _csv_btst, _csv_btlo, _csv_bthi, _csv_btc, _csv_btd in _bt_tier_defs:
                            _csv_tier_mask = (
                                (_rp_df["Snapshot"].str.lower() == _csv_btst) &
                                (_rp_df["TCS"] >= _csv_btlo) &
                                (_rp_df["TCS"] <= _csv_bthi)
                            )
                            _csv_td = _rp_df[_csv_tier_mask]
                            _summary_rows.append({c: "" for c in _csv_cols})
                            _summary_rows.append(_stat_row(f"--- {_csv_btl} ({_csv_btd}) ---", ""))
                            if _csv_td.empty:
                                _summary_rows.append(_stat_row("(no trades in this tier)", ""))
                            else:
                                _csv_tr_ser = _csv_td["R (MFE)"]
                                _csv_tr_n   = len(_csv_td)
                                _csv_fb_n   = _csv_td["False Break"].sum()
                                _csv_fb_rt  = round(_csv_fb_n / _csv_tr_n * 100, 1)
                                _csv_aw_r   = round(_csv_tr_ser[_csv_tr_ser > 0].mean(), 2) if (_csv_tr_ser > 0).any() else 0
                                _csv_al_r   = round(_csv_tr_ser[_csv_tr_ser < 0].mean(), 2) if (_csv_tr_ser < 0).any() else 0
                                _csv_exp_r  = round(_csv_tr_ser.mean(), 3)
                                _csv_cum_r  = _csv_tr_ser.cumsum().reset_index(drop=True)
                                _csv_pk_r   = _csv_cum_r.cummax()
                                _csv_mdd_r  = round((_csv_cum_r - _csv_pk_r).min(), 2)
                                # EOD Hold R per-tier stats
                                _csv_bt_eod_ser  = pd.to_numeric(_csv_td.get("R (EOD)",    pd.Series(dtype=float)), errors="coerce").dropna()
                                _csv_bt_eod_n    = len(_csv_bt_eod_ser)
                                _csv_bt_eod_wr   = round((_csv_bt_eod_ser > 0).sum() / _csv_bt_eod_n * 100, 1) if _csv_bt_eod_n else 0
                                _csv_bt_eod_aw   = round(_csv_bt_eod_ser[_csv_bt_eod_ser > 0].mean(), 2) if (_csv_bt_eod_ser > 0).any() else 0
                                _csv_bt_eod_al   = round(_csv_bt_eod_ser[_csv_bt_eod_ser < 0].mean(), 2) if (_csv_bt_eod_ser < 0).any() else 0
                                _csv_bt_eod_exp  = round(_csv_bt_eod_ser.mean(), 3) if _csv_bt_eod_n else 0
                                # Tiered Exit R per-tier stats
                                _csv_bt_tier_ser = pd.to_numeric(_csv_td.get("R (Tiered)", pd.Series(dtype=float)), errors="coerce").dropna()
                                _csv_bt_tier_n   = len(_csv_bt_tier_ser)
                                _csv_bt_tier_wr  = round((_csv_bt_tier_ser > 0).sum() / _csv_bt_tier_n * 100, 1) if _csv_bt_tier_n else 0
                                _csv_bt_tier_aw  = round(_csv_bt_tier_ser[_csv_bt_tier_ser > 0].mean(), 2) if (_csv_bt_tier_ser > 0).any() else 0
                                _csv_bt_tier_al  = round(_csv_bt_tier_ser[_csv_bt_tier_ser < 0].mean(), 2) if (_csv_bt_tier_ser < 0).any() else 0
                                _csv_bt_tier_exp = round(_csv_bt_tier_ser.mean(), 3) if _csv_bt_tier_n else 0
                                _summary_rows.extend([
                                    _stat_row("Stop-Out Rate",          f"{_csv_fb_rt}%"),
                                    _stat_row("Avg Win (R)",            f"+{_csv_aw_r}R"),
                                    _stat_row("Avg Loss (R)",           f"{_csv_al_r}R"),
                                    _stat_row("Expectancy",             f"{_csv_exp_r:+.3f}R/trade"),
                                    _stat_row("Max Drawdown (R)",       f"{abs(_csv_mdd_r)}R"),
                                    _stat_row("Win Rate (EOD)",         f"{_csv_bt_eod_wr}%"),
                                    _stat_row("Avg Win (EOD R)",        f"+{_csv_bt_eod_aw}R"),
                                    _stat_row("Avg Loss (EOD R)",       f"{_csv_bt_eod_al}R"),
                                    _stat_row("Expectancy (EOD R)",     f"{_csv_bt_eod_exp:+.3f}R/trade"),
                                    _stat_row("Win Rate (Tiered)",      f"{_csv_bt_tier_wr}%"),
                                    _stat_row("Avg Win (Tiered R)",     f"+{_csv_bt_tier_aw}R"),
                                    _stat_row("Avg Loss (Tiered R)",    f"{_csv_bt_tier_al}R"),
                                    _stat_row("Expectancy (Tiered R)",  f"{_csv_bt_tier_exp:+.3f}R/trade"),
                                ])
                        _rp_csv_export = pd.concat(
                            [_rp_csv_df,
                             pd.DataFrame(_summary_rows)],
                            ignore_index=True,
                        )
                        st.download_button(
                            label="⬇ Download Replay CSV",
                            data=_rp_csv_export.to_csv(index=False).encode("utf-8"),
                            file_name="replay_trades.csv",
                            mime="text/csv",
                            key="rp_dl_csv",
                            help="Full trade-by-trade log with R multiples, P&L, and cumulative R — includes overall R-stats summary and per-tier (P1–P4) breakdown at the bottom",
                        )

                        # ── P1/P2/P3/P4 Priority Tier Breakdown ───────────────────
                        st.markdown("<br>", unsafe_allow_html=True)
                        st.markdown(
                            '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                            'text-transform:uppercase;margin-bottom:8px;">'
                            'Priority Tier Breakdown — P1 / P2 / P3 / P4</div>',
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            "P1 🔴 = Intraday TCS 70+  ·  P2 🟠 = Intraday TCS 50–69  ·  "
                            "P3 🟡 = Morning TCS 70+  ·  P4 🟢 = Morning TCS 50–69"
                        )
                        _bt_tier_cols = st.columns(4)
                        # Pre-compute expectancy R per tier for best-edge highlight
                        _bt_tier_exp_map: dict = {}
                        for _bti2, (_btl2x, _bte2x, _btst2x, _btlo2x, _bthi2x, _btc2x, _btd2x) in enumerate(_bt_tier_defs):
                            _bt_mask2 = (
                                (_rp_df["Snapshot"].str.lower() == _btst2x) &
                                (_rp_df["TCS"] >= _btlo2x) &
                                (_rp_df["TCS"] <= _bthi2x)
                            )
                            _bt_td2 = _rp_df[_bt_mask2]
                            if not _bt_td2.empty:
                                _bt_tier_exp_map[_bti2] = round(_bt_td2["R (MFE)"].mean(), 3)
                        _bt_best_exp_r = max(_bt_tier_exp_map.values()) if len(_bt_tier_exp_map) > 1 else None

                        # Sort tier render order by expectancy R (best edge first) when multiple tiers have trades
                        if len(_bt_tier_exp_map) > 1:
                            _bt_render_order = sorted(
                                range(len(_bt_tier_defs)),
                                key=lambda _i: _bt_tier_exp_map.get(_i, float("-inf")),
                                reverse=True,
                            )
                        else:
                            _bt_render_order = list(range(len(_bt_tier_defs)))

                        for _bt_rpos, _bti in enumerate(_bt_render_order):
                            (_btl, _bte, _btst, _btlo, _bthi, _btc, _btd) = _bt_tier_defs[_bti]
                            _bt_tier_mask = (
                                (_rp_df["Snapshot"].str.lower() == _btst) &
                                (_rp_df["TCS"] >= _btlo) &
                                (_rp_df["TCS"] <= _bthi)
                            )
                            _bt_td = _rp_df[_bt_tier_mask]
                            with _bt_tier_cols[_bt_rpos]:
                                if _bt_td.empty:
                                    st.markdown(
                                        f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                        f'<div style="font-size:13px;font-weight:700;color:{_btc};">{_bte} {_btl}</div>'
                                        f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">{_btd}</div>'
                                        f'<div style="font-size:12px;color:#546e7a;margin-top:6px;">No trades</div>'
                                        f'</div>', unsafe_allow_html=True
                                    )
                                else:
                                    _btw  = (_bt_td["P&L ($)"] > 0).sum()
                                    _btl2 = (_bt_td["P&L ($)"] <= 0).sum()
                                    _btwr = _btw / len(_bt_td) * 100
                                    _bttot = _bt_td["P&L ($)"].sum()
                                    _btc2 = "#2e7d32" if _btwr >= 60 else ("#ef6c00" if _btwr >= 50 else "#c62828")
                                    _bt_r_ser   = _bt_td["R (MFE)"]
                                    _bt_fb_rate = round(_bt_td["False Break"].sum() / len(_bt_td) * 100, 1)
                                    _bt_avg_win_r = round(_bt_r_ser[_bt_r_ser > 0].mean(), 2) if (_bt_r_ser > 0).any() else 0
                                    _bt_avg_loss_r = round(_bt_r_ser[_bt_r_ser < 0].mean(), 2) if (_bt_r_ser < 0).any() else 0
                                    _bt_exp_r   = round(_bt_r_ser.mean(), 3)
                                    _bt_exp_r_str = f'{"+" if _bt_exp_r >= 0 else ""}{_bt_exp_r:.3f}R'
                                    _bt_exp_r_col = "#2e7d32" if _bt_exp_r > 0 else ("#ef6c00" if _bt_exp_r == 0 else "#c62828")
                                    _bt_is_best = _bt_best_exp_r is not None and abs(_bt_exp_r - _bt_best_exp_r) < 1e-9
                                    _bt_card_border = "2px solid #ffd54f" if _bt_is_best else "1px solid #263444"
                                    _bt_best_badge  = (
                                        '<div style="font-size:10px;font-weight:700;color:#ffd54f;'
                                        'background:rgba(255,213,79,0.12);border-radius:4px;'
                                        'padding:1px 6px;display:inline-block;margin-bottom:4px;">'
                                        'Best Edge ⭐</div>'
                                    ) if _bt_is_best else ""
                                    st.markdown(
                                        f'<div style="background:#1e2a3a;border:{_bt_card_border};'
                                        f'border-radius:8px;padding:12px;text-align:center;">'
                                        f'{_bt_best_badge}'
                                        f'<div style="font-size:13px;font-weight:700;color:{_btc};">{_bte} {_btl}</div>'
                                        f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">{_btd}</div>'
                                        f'<div style="font-size:22px;font-weight:700;color:{_btc2};margin-top:4px;">{_btwr:.1f}%</div>'
                                        f'<div style="font-size:12px;color:#cfd8dc;">{_btw}W / {_btl2}L  ·  {len(_bt_td)} trades</div>'
                                        f'<div style="font-size:11px;color:#90a4ae;margin-top:6px;border-top:1px solid #263444;padding-top:6px;">'
                                        f'Stop-Out: {_bt_fb_rate}%  ·  '
                                        f'<span style="color:#4fc3f7;">Avg Win R: +{_bt_avg_win_r}R</span>  ·  '
                                        f'<span style="color:#ef9a9a;">Avg Loss R: {_bt_avg_loss_r}R</span></div>'
                                        f'<div style="font-size:13px;font-weight:600;color:{_bt_exp_r_col};margin-top:2px;">'
                                        f'Exp: {_bt_exp_r_str} / trade</div>'
                                        f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">'
                                        f'Total: {"+" if _bttot >= 0 else ""}${_bttot:,.0f}</div>'
                                        f'</div>', unsafe_allow_html=True
                                    )

                        # ── TCS Optimizer ──────────────────────────────────────────
                        st.markdown("<br>", unsafe_allow_html=True)
                        with st.expander("🔍 TCS Optimizer — Find the optimal TCS cutoff for max profit", expanded=False):
                            st.caption(
                                "Sweeps TCS 40 → 80 using the same dataset (regardless of your current TCS filter). "
                                "Shows what each cutoff produces: trade count, win rate, net P&L, and expectancy. "
                                "Bold row = highest net P&L."
                            )
                            _opt_raw = st.session_state.get("_bt_replay_rows", [])
                            if not _opt_raw:
                                st.info("Run the replay above first to load data.")
                            else:
                                _opt_pos = float(_rp_pos_size)   # same position size as replay
                                _sweep_rows = []
                                for _sw_tcs in range(40, 85, 5):
                                    _sw_dir = [
                                        r for r in _opt_raw
                                        if ("bullish" in str(r.get("actual_outcome","")).lower()
                                            or "bearish" in str(r.get("actual_outcome","")).lower())
                                        and str(r.get("win_loss","")).strip() in ("Win","Loss")
                                        and float(r.get("tcs") or 0) >= _sw_tcs
                                    ]
                                    if not _sw_dir:
                                        continue
                                    _sw_wins = [r for r in _sw_dir if r.get("win_loss") == "Win"]
                                    _sw_loss = [r for r in _sw_dir if r.get("win_loss") == "Loss"]
                                    _sw_wr   = len(_sw_wins) / len(_sw_dir) * 100
                                    # Simple flat P&L: pos_size × |ft%| / 100 × sign
                                    _sw_pnl  = sum(
                                        _opt_pos * abs(float(r.get("follow_thru_pct") or 0)) / 100
                                        * (1 if r.get("win_loss") == "Win" else -1)
                                        for r in _sw_dir
                                    )
                                    _sw_exp  = _sw_pnl / len(_sw_dir)
                                    _sweep_rows.append({
                                        "TCS Floor":    _sw_tcs,
                                        "Trades":       len(_sw_dir),
                                        "Win Rate":     round(_sw_wr, 1),
                                        "Net P&L ($)":  round(_sw_pnl, 0),
                                        "Expectancy ($)": round(_sw_exp, 2),
                                    })

                                if _sweep_rows:
                                    _sw_df   = pd.DataFrame(_sweep_rows)
                                    _best_pnl = _sw_df["Net P&L ($)"].max()
                                    _best_wr  = _sw_df[_sw_df["Win Rate"] == _sw_df["Win Rate"].max()].iloc[0]["TCS Floor"]
                                    _best_row = _sw_df[_sw_df["Net P&L ($)"] == _best_pnl].iloc[0]

                                    st.markdown(
                                        f'<div style="background:#1e3a2a;border-radius:8px;padding:10px 14px;'
                                        f'margin-bottom:10px;font-size:13px;color:#a5d6a7;">'
                                        f'📈 <b>Optimal for Max Profit:</b> TCS ≥ {int(_best_row["TCS Floor"])} '
                                        f'→ {int(_best_row["Trades"])} trades · {_best_row["Win Rate"]:.1f}% WR · '
                                        f'${_best_pnl:,.0f} net P&L</div>',
                                        unsafe_allow_html=True,
                                    )

                                    def _sw_style(row):
                                        if row["Net P&L ($)"] == _best_pnl:
                                            return ["background:#1e3a2a;font-weight:bold"] * len(row)
                                        return [""] * len(row)

                                    st.dataframe(
                                        _sw_df.style.apply(_sw_style, axis=1),
                                        use_container_width=True,
                                        hide_index=True,
                                        column_config={
                                            "Net P&L ($)":    st.column_config.NumberColumn(format="$%.0f"),
                                            "Expectancy ($)": st.column_config.NumberColumn(format="$%.2f"),
                                            "Win Rate":       st.column_config.NumberColumn(format="%.1f%%"),
                                        }
                                    )

                        st.markdown("**Trade-by-Trade Log**")

                        _rp_log_fcol1, _rp_log_fcol2, _rp_log_fcol3 = st.columns([2, 1, 1])
                        with _rp_log_fcol1:
                            _rp_log_ticker_filter = st.text_input(
                                "Filter by ticker",
                                value="",
                                key="rp_log_ticker_filter",
                                placeholder="e.g. AAPL, NVDA",
                                label_visibility="collapsed",
                            )
                        with _rp_log_fcol2:
                            _rp_log_wl_filter = st.selectbox(
                                "W/L filter",
                                options=["All", "Win", "Loss"],
                                index=0,
                                key="rp_log_wl_filter",
                                label_visibility="collapsed",
                            )
                        with _rp_log_fcol3:
                            _rp_log_show_neutral = st.checkbox(
                                "Show neutral rows",
                                value=True,
                                key="rp_log_show_neutral",
                            )

                        _rp_display_df = _rp_df.copy()
                        if _rp_log_ticker_filter.strip():
                            _rp_tickers_input = [
                                t.strip().upper()
                                for t in _rp_log_ticker_filter.replace(",", " ").split()
                                if t.strip()
                            ]
                            if _rp_tickers_input:
                                _rp_display_df = _rp_display_df[
                                    _rp_display_df["Ticker"].str.upper().isin(_rp_tickers_input)
                                ]
                        if _rp_log_wl_filter != "All":
                            _rp_display_df = _rp_display_df[
                                _rp_display_df["W/L"] == _rp_log_wl_filter
                            ]
                        if not _rp_log_show_neutral:
                            _rp_display_df = _rp_display_df[
                                _rp_display_df["W/L"].isin(["Win", "Loss"])
                            ]

                        if len(_rp_display_df) == 0:
                            st.info("No trades match the current filters.")

                        # ── Win/Loss summary bar ─────────────────────────────
                        _rp_wl_col = _rp_display_df["W/L"].astype(str).str.strip() if "W/L" in _rp_display_df.columns else pd.Series(dtype=str)
                        _rp_wins    = int((_rp_wl_col == "Win").sum())
                        _rp_losses  = int((_rp_wl_col == "Loss").sum())
                        _rp_neutral = int((~_rp_wl_col.isin(["Win", "Loss"])).sum())
                        _rp_total   = _rp_wins + _rp_losses + _rp_neutral
                        _rp_wr_pct  = (_rp_wins / (_rp_wins + _rp_losses) * 100) if (_rp_wins + _rp_losses) > 0 else 0.0
                        st.markdown(
                            f"""
                            <div style="
                                display:flex;gap:12px;align-items:center;
                                padding:8px 12px;margin-bottom:6px;
                                background:rgba(30,30,30,0.45);
                                border-radius:8px;border:1px solid rgba(255,255,255,0.07);
                                font-size:0.88rem;flex-wrap:wrap;
                            ">
                              <span style="color:#66bb6a;font-weight:700;">
                                ✔ {_rp_wins} Win{"s" if _rp_wins != 1 else ""}
                              </span>
                              <span style="color:rgba(255,255,255,0.25);">|</span>
                              <span style="color:#ef5350;font-weight:700;">
                                ✘ {_rp_losses} Loss{"es" if _rp_losses != 1 else ""}
                              </span>
                              <span style="color:rgba(255,255,255,0.25);">|</span>
                              <span style="color:#90a4ae;font-weight:700;">
                                — {_rp_neutral} Neutral
                              </span>
                              <span style="color:rgba(255,255,255,0.25);">|</span>
                              <span style="color:rgba(255,255,255,0.55);">
                                {_rp_total} total &nbsp;·&nbsp;
                                <span style="color:{'#66bb6a' if _rp_wr_pct >= 50 else '#ef5350'};font-weight:700;">
                                  {_rp_wr_pct:.1f}% win rate
                                </span>
                              </span>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        def _rp_row_style(row):
                            wl = str(row.get("W/L", "")).strip()
                            # Detect marginal TCS (within 5 points of the floor)
                            _marginal = False
                            if "TCS" in row.index and "TCS Floor" in row.index:
                                try:
                                    _tcs_val   = float(row["TCS"])
                                    _floor_val = float(row["TCS Floor"])
                                    if 0 <= (_tcs_val - _floor_val) <= 5:
                                        _marginal = True
                                except (TypeError, ValueError):
                                    pass
                            if _marginal:
                                amber_base = "background-color:rgba(255,167,38,0.14)"
                                amber_hi   = "background-color:rgba(255,167,38,0.28);color:#ffb300;font-weight:700"
                                return [amber_hi if col == "W/L" else amber_base for col in row.index]
                            if wl == "Win":
                                base = "background-color:rgba(76,175,80,0.08)"
                                hi   = "background-color:rgba(76,175,80,0.18);color:#66bb6a;font-weight:700"
                            elif wl == "Loss":
                                base = "background-color:rgba(239,83,80,0.08)"
                                hi   = "background-color:rgba(239,83,80,0.18);color:#ef5350;font-weight:700"
                            else:
                                return ["background-color: rgba(144,164,174,0.08); color:#90a4ae"] * len(row)
                            return [hi if col == "W/L" else base for col in row.index]

                        _rp_styled_df = _rp_display_df.style.apply(_rp_row_style, axis=1)
                        st.dataframe(
                            _rp_styled_df,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "P&L ($)":    st.column_config.NumberColumn(format="$%.2f"),
                                "Equity":     st.column_config.NumberColumn(format="$%.2f"),
                                "Move %":     st.column_config.NumberColumn(format="%.2f%%"),
                                "R (MFE)":    st.column_config.NumberColumn(format="%.2fR"),
                                "R (EOD)":    st.column_config.NumberColumn(format="%.2fR",
                                              help="EOD hold P&L: full position held to close, no stops"),
                                "R (Tiered)": st.column_config.NumberColumn(format="%.2fR",
                                              help="50% at 1R → BE stop → 25% at 2R → 25% runner to close"),
                                "TCS Floor":  st.column_config.NumberColumn(
                                              format="%d",
                                              help="Per-structure TCS threshold applied to this trade (base 50 + offset). Trade passed because TCS ≥ this value."),
                            }
                        )
                        if _rp_bot_mode:
                            st.caption(
                                "🟡 **Amber rows** — TCS cleared the floor by ≤ 5 points (marginal entry). "
                                "These trades passed the bot's threshold but only barely; review them carefully."
                            )

    st.markdown("---")

    # ── Run simulation ──────────────────────────────────────────────────────────
    _bt_cache = "bt_results_cache"

    if _bt_run:
        _tickers = [t.strip().upper() for t in _bt_tickers_raw.replace("\n", ",").split(",")
                    if t.strip() and t.strip().isalpha()]
        if not _tickers:
            st.error("No valid tickers found. Check your watchlist.")
            st.stop()

        _pmin, _pmax = float(_bt_price_range[0]), float(_bt_price_range[1])
        _total = len(_tickers)

        if _bt_is_range:
            _range_label = f"{_bt_date} → {_bt_end_date}"
            with st.spinner(
                f"⏳ Range simulation {_range_label} · {_total} tickers — "
                f"this may take 30–60 seconds…"
            ):
                _results, _summary, _daily_list = run_backtest_range(
                    api_key, secret_key,
                    start_date=_bt_date,
                    end_date=_bt_end_date,
                    tickers=_tickers,
                    feed=_bt_feed_str,
                    price_min=_pmin,
                    price_max=_pmax,
                    slippage_pct=_bt_slippage,
                )
            _date_label = _range_label
        else:
            _daily_list  = None
            _range_label = str(_bt_date)
            with st.spinner(
                f"⏳ Simulating {_total} tickers on {_bt_date} — "
                f"fetching bars & running quant engine concurrently…"
            ):
                _results, _summary = run_historical_backtest(
                    api_key, secret_key,
                    trade_date=_bt_date,
                    tickers=_tickers,
                    feed=_bt_feed_str,
                    price_min=_pmin,
                    price_max=_pmax,
                    slippage_pct=_bt_slippage,
                )
            _date_label = str(_bt_date)

        # Auto-save to Supabase
        if _results and not _summary.get("error"):
            _rows_to_save = _results if _bt_is_range else [
                dict(r, sim_date=str(_bt_date)) for r in _results
            ]
            _bt_save_ok = False
            try:
                save_backtest_sim_runs(_rows_to_save, user_id=_AUTH_USER_ID)
                _bt_save_ok = True
            except Exception:
                pass
            if _bt_save_ok:
                _bt_tiered_pending = count_backtest_tiered_pending(user_id=_AUTH_USER_ID)
                if _bt_tiered_pending > 0:
                    st.info(
                        f"⚠️ **{_bt_tiered_pending:,} backtest rows are missing tiered P&L** "
                        f"(50/25/25 ladder exit). Tiered P&L requires intraday bar data and cannot "
                        f"be computed at save time.  \n"
                        f"→ Use the one-click backfill in **Performance → Backtest Sim P&L**, "
                        f"or run `python run_tiered_pnl_backfill.py --backtest-only` from the shell."
                    )

        st.session_state[_bt_cache] = (
            _results, _summary, _date_label,
            _bt_is_range, _daily_list if _bt_is_range else None,
        )

    # ── Load cached results ─────────────────────────────────────────────────────
    if _bt_cache not in st.session_state:
        st.markdown(
            '<div style="text-align:center; color:#263238; font-size:13px; '
            'padding:40px 0; font-family:monospace;">'
            '[ SELECT DATE + TICKERS → PRESS RUN SIMULATION ]'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    _bt_cached = st.session_state[_bt_cache]
    if len(_bt_cached) == 5:
        _results, _summary, _sim_date, _sim_is_range, _sim_daily = _bt_cached
    elif len(_bt_cached) == 4:
        _results, _summary, _sim_date, _ = _bt_cached
        _sim_is_range, _sim_daily = False, None
    else:
        _results, _summary, _sim_date = _bt_cached
        _sim_is_range, _sim_daily = False, None

    if _summary.get("error"):
        st.error(_summary["error"])
        return

    # ── Summary KPI header ──────────────────────────────────────────────────────
    _wr = _summary["win_rate"]
    _wr_color = (
        _BT_WIN_CLR  if _wr >= 60 else
        "#ffa726"    if _wr >= 45 else
        _BT_LOSS_CLR
    )

    st.markdown(
        f'<div style="background:#020813; border:1px solid #0d2137; border-radius:10px; '
        f'padding:18px 24px; margin-bottom:20px;">'
        f'<div style="font-size:10px; color:#1565c0; letter-spacing:2px; '
        f'text-transform:uppercase; margin-bottom:12px; font-family:monospace;">'
        f'SIMULATION RESULTS — {str(_sim_date).upper()}'
        f'{" · " + str(_summary.get("days_run", "")) + " TRADING DAYS" if _sim_is_range else ""}'
        f'</div>'
        f'<div style="display:flex; gap:40px; flex-wrap:wrap;">'

        f'<div>'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-bottom:2px;">Simulated Win Rate</div>'
        f'<div style="font-size:42px; font-weight:900; color:{_wr_color}; '
        f'font-family:monospace;">{_wr:.1f}%</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:32px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-bottom:2px;">Setups Found</div>'
        f'<div style="font-size:36px; font-weight:800; color:#e0e0e0; '
        f'font-family:monospace;">{_summary["total"]}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:32px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-bottom:2px;">Wins / Losses</div>'
        f'<div style="font-size:28px; font-weight:800; font-family:monospace;">'
        f'<span style="color:{_BT_WIN_CLR};">{_summary["wins"]}</span>'
        f'<span style="color:#37474f;"> / </span>'
        f'<span style="color:{_BT_LOSS_CLR};">{_summary["losses"]}</span>'
        f'</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:32px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-bottom:2px;">Highest TCS</div>'
        f'<div style="font-size:36px; font-weight:800; color:#7c4dff; '
        f'font-family:monospace;">{_summary["highest_tcs"]:.0f}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:32px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-bottom:2px;">Avg TCS</div>'
        f'<div style="font-size:36px; font-weight:800; color:#5c6bc0; '
        f'font-family:monospace;">{_summary["avg_tcs"]:.0f}</div>'
        f'</div>'

        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── EOD Hold vs Tiered Exit Strategy Comparison ─────────────────────────────
    _avg_eod    = _summary.get("avg_eod_pnl_r")
    _avg_tiered = _summary.get("avg_tiered_pnl_r")
    _eod_n      = _summary.get("eod_pnl_r_count", 0)
    _tiered_n   = _summary.get("tiered_pnl_r_count", 0)
    if _avg_eod is not None or _avg_tiered is not None:
        def _r_str(val, n):
            if val is None:
                return "—"
            sign = "+" if val >= 0 else ""
            return f"{sign}{val:.3f}R ({n} trades)"

        def _r_color(val):
            if val is None: return "#546e7a"
            return "#4caf50" if val >= 0 else "#ef5350"

        _eod_str    = _r_str(_avg_eod, _eod_n)
        _tiered_str = _r_str(_avg_tiered, _tiered_n)
        _eod_clr    = _r_color(_avg_eod)
        _tiered_clr = _r_color(_avg_tiered)

        if _avg_eod is not None and _avg_tiered is not None:
            _diff = _avg_tiered - _avg_eod
            _diff_sign = "+" if _diff >= 0 else ""
            if abs(_diff) < 0.001:
                _verdict = "Strategies tied"
                _verdict_clr = "#90a4ae"
            elif _diff > 0:
                _verdict = f"Tiered exits outperform EOD hold by {_diff_sign}{_diff:.3f}R per trade"
                _verdict_clr = "#ffb74d"
            else:
                _verdict = f"EOD hold outperforms tiered exits by {abs(_diff):.3f}R per trade"
                _verdict_clr = "#81c784"
        else:
            _verdict = "Run sim backfill to populate both metrics"
            _verdict_clr = "#546e7a"

        st.markdown(
            f'<div style="background:#020813; border:1px solid #1a2744; border-radius:8px; '
            f'padding:14px 24px; margin-bottom:20px;">'
            f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
            f'letter-spacing:1.5px; margin-bottom:10px; font-weight:700; font-family:monospace;">'
            f'📊 Strategy Comparison — EOD Hold vs Tiered Exits (avg R per trade)</div>'
            f'<div style="display:flex; gap:32px; flex-wrap:wrap; align-items:center;">'

            f'<div>'
            f'<div style="font-size:9px; color:#81c784; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:2px;">📅 Held to Close (EOD)</div>'
            f'<div style="font-size:26px; font-weight:800; color:{_eod_clr}; '
            f'font-family:monospace;">{_eod_str}</div>'
            f'</div>'

            f'<div style="font-size:20px; color:#37474f; align-self:center;">vs</div>'

            f'<div>'
            f'<div style="font-size:9px; color:#ffb74d; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:2px;">🪜 50/25/25 Ladder (Tiered)</div>'
            f'<div style="font-size:26px; font-weight:800; color:{_tiered_clr}; '
            f'font-family:monospace;">{_tiered_str}</div>'
            f'</div>'

            f'<div style="border-left:1px solid #1a2744; padding-left:24px; align-self:center;">'
            f'<div style="font-size:12px; font-weight:700; color:{_verdict_clr};">{_verdict}</div>'
            f'<div style="font-size:10px; color:#37474f; margin-top:3px;">'
            f'Positive = strategy added value vs a simple hold-to-close</div>'
            f'</div>'

            f'</div></div>',
            unsafe_allow_html=True,
        )

    # ── Directional breakdown row ────────────────────────────────────────────────
    _bull_ft_str = f"+{_summary['avg_bull_ft']:.1f}%" if _summary["avg_bull_ft"] else "—"
    _bear_ft_str = f"-{_summary['avg_bear_ft']:.1f}%" if _summary["avg_bear_ft"] else "—"
    _lr          = _summary["long_win_rate"]
    _lr_clr      = "#4caf50" if _lr >= 50 else "#ffa726" if _lr >= 35 else "#ef5350"
    st.markdown(
        f'<div style="background:#020813; border:1px solid #0d2137; border-radius:8px; '
        f'padding:12px 24px; margin-bottom:20px; display:flex; gap:32px; flex-wrap:wrap;">'

        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        f'letter-spacing:1px; margin-top:4px; align-self:center; min-width:90px;">'
        f'DIRECTIONAL BREAKDOWN</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">↑ Bull Breaks</div>'
        f'<div style="font-size:22px; font-weight:800; color:#4caf50; '
        f'font-family:monospace;">{_summary["bull_breaks"]}'
        f'<span style="font-size:13px; color:#37474f;"> / {_summary["total"]}</span></div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">Avg Bull Follow-Thru</div>'
        f'<div style="font-size:22px; font-weight:800; color:#4caf50; '
        f'font-family:monospace;">{_bull_ft_str}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">↓ Bear Breaks</div>'
        f'<div style="font-size:22px; font-weight:800; color:#ef5350; '
        f'font-family:monospace;">{_summary["bear_breaks"]}'
        f'<span style="font-size:13px; color:#37474f;"> / {_summary["total"]}</span></div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">Avg Bear Follow-Thru</div>'
        f'<div style="font-size:22px; font-weight:800; color:#ef5350; '
        f'font-family:monospace;">{_bear_ft_str}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">Long-Only Hit Rate</div>'
        f'<div style="font-size:22px; font-weight:800; color:{_lr_clr}; '
        f'font-family:monospace;">{_lr:.1f}%</div>'
        f'<div style="font-size:9px; color:#37474f;">% setups went bullish</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">Both Sides</div>'
        f'<div style="font-size:22px; font-weight:800; color:#ffa726; '
        f'font-family:monospace;">{_summary["both_breaks"]}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#546e7a; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">Range-Bound</div>'
        f'<div style="font-size:22px; font-weight:800; color:#546e7a; '
        f'font-family:monospace;">{_summary["range_bound"]}</div>'
        f'</div>'

        f'<div style="border-left:1px solid #0d2137; padding-left:24px;">'
        f'<div style="font-size:9px; color:#ffa726; letter-spacing:1px; '
        f'text-transform:uppercase; margin-bottom:2px;">⚠ False Break Rate</div>'
        f'<div style="font-size:22px; font-weight:800; color:#ffa726; '
        f'font-family:monospace;">{_summary.get("false_break_rate", 0.0):.1f}%</div>'
        f'<div style="font-size:9px; color:#37474f;">'
        f'↑{_summary.get("fb_up_count", 0)} ↓{_summary.get("fb_down_count", 0)} traps</div>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Walk-forward train / test comparison (range only) ───────────────────────
    _wf_train = _summary.get("train", {})
    _wf_test  = _summary.get("test", {})
    if _sim_is_range and _wf_train and _wf_test and _wf_test.get("total", 0) > 0:
        st.markdown(
            '<div style="font-size:10px; color:#7c4dff; text-transform:uppercase; '
            'letter-spacing:1.5px; margin-bottom:8px; font-weight:700;">'
            '🔬 Walk-Forward Validation — Train (70%) vs Test (30%)</div>',
            unsafe_allow_html=True,
        )
        _wf_cols = st.columns(2)
        for _wf_d, _wf_col in [(_wf_train, _wf_cols[0]), (_wf_test, _wf_cols[1])]:
            _wfn  = _wf_d.get("total", 0)
            _wfwr = _wf_d.get("win_rate", 0.0)
            _wflb = _wf_d.get("label", "?")
            _wfc  = "#4caf50" if _wfwr >= 60 else "#ffa726" if _wfwr >= 45 else "#ef5350"
            _is_test = "out-of-sample" in _wflb.lower()
            _bg   = "#0d1b0d" if not _is_test else "#1a0d26"
            _bd   = "#4caf50" if not _is_test else "#7c4dff"
            _wf_col.markdown(
                f'<div style="background:{_bg}; border:1px solid {_bd}44; border-left:4px solid {_bd}; '
                f'border-radius:8px; padding:14px 18px;">'
                f'<div style="font-size:10px; color:{_bd}; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:6px; font-weight:700;">{_wflb}</div>'
                f'<div style="font-size:32px; font-weight:900; color:{_wfc}; font-family:monospace;">'
                f'{_wfwr:.1f}%</div>'
                f'<div style="font-size:12px; color:#546e7a; margin-top:4px;">'
                f'{_wf_d.get("wins",0)}W / {_wf_d.get("losses",0)}L · {_wfn} setups · '
                f'Avg TCS {_wf_d.get("avg_tcs",0):.0f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        _oos_wr = _wf_test.get("win_rate", 0.0)
        _is_wr  = _wf_train.get("win_rate", 0.0)
        _drift  = _oos_wr - _is_wr
        _drift_msg = (
            f"Out-of-sample win rate is {abs(_drift):.1f}% {'better' if _drift >= 0 else 'worse'} "
            f"than in-sample ({'good — model generalises' if abs(_drift) <= 10 else 'large gap — check overfitting'})."
        )
        st.caption(f"📐 {_drift_msg}")
        st.markdown("<div style='margin:8px 0;'></div>", unsafe_allow_html=True)

    # ── Monte Carlo equity simulation ────────────────────────────────────────────
    if _results and len(_results) >= 3:
        _mc_risk_frac = st.session_state.get("bt_mc_risk", 2.0) / 100.0
        _mc_eq        = float(st.session_state.get("bt_mc_equity", 10_000))
        _mc_slip      = st.session_state.get("bt_slippage_pct", 0.5) / 100.0
        _mc = monte_carlo_equity_curves(
            _results,
            starting_equity=float(_mc_eq),
            n_simulations=1000,
            risk_pct=_mc_risk_frac,
            slippage_drag_pct=_mc_slip,
        )
        if _mc:
            st.markdown(
                '<div style="font-size:10px; color:#00bcd4; text-transform:uppercase; '
                'letter-spacing:1.5px; margin-bottom:8px; font-weight:700;">'
                '🎲 Monte Carlo Equity Simulation — 1,000 Trade Sequence Shuffles</div>',
                unsafe_allow_html=True,
            )
            _mc_x = list(range(len(_mc["p50"])))
            fig_mc = go.Figure()
            fig_mc.add_trace(go.Scatter(
                x=_mc_x + _mc_x[::-1],
                y=_mc["p90"] + _mc["p10"][::-1],
                fill="toself", fillcolor="rgba(0,188,212,0.08)",
                line=dict(color="rgba(0,0,0,0)"),
                name="P10–P90 range", showlegend=True,
            ))
            fig_mc.add_trace(go.Scatter(
                x=_mc_x, y=_mc["p90"],
                line=dict(color="#4caf50", dash="dot", width=1),
                name="P90 (best 10%)", showlegend=True,
            ))
            fig_mc.add_trace(go.Scatter(
                x=_mc_x, y=_mc["p50"],
                line=dict(color="#00bcd4", width=2.5),
                name="Median outcome", showlegend=True,
            ))
            fig_mc.add_trace(go.Scatter(
                x=_mc_x, y=_mc["p10"],
                line=dict(color="#ef5350", dash="dot", width=1),
                name="P10 (worst 10%)", showlegend=True,
            ))
            fig_mc.add_hline(
                y=float(_mc_eq), line_dash="dash",
                line_color="#546e7a", opacity=0.6,
                annotation_text="Starting equity",
                annotation_font_color="#546e7a",
            )
            fig_mc.update_layout(
                paper_bgcolor="#0a0a1a", plot_bgcolor="#0d1117",
                font=dict(color="#e0e0e0"), height=300,
                margin=dict(t=20, b=40, l=60, r=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                xaxis=dict(title="Trade #", gridcolor="#1a1a2e", zeroline=False),
                yaxis=dict(title="Equity ($)", gridcolor="#1a1a2e", zeroline=False,
                           tickformat="$,.0f"),
            )
            st.plotly_chart(fig_mc, use_container_width=True)
            _mc_kpi_cols = st.columns(4)
            _mc_kpi_cols[0].metric("Median Final", f"${_mc['median_final']:,.0f}",
                                   f"{(_mc['median_final']/_mc_eq - 1)*100:+.1f}%")
            _mc_kpi_cols[1].metric("P90 Final (best)", f"${_mc['p90_final']:,.0f}")
            _mc_kpi_cols[2].metric("P10 Final (worst)", f"${_mc['p10_final']:,.0f}")
            _mc_kpi_cols[3].metric("% Simulations Profitable",
                                   f"{_mc['pct_profitable']:.1f}%")
            st.markdown("<div style='margin:10px 0;'></div>", unsafe_allow_html=True)

    # ── Day-by-day breakdown (range only) ───────────────────────────────────────
    if _sim_is_range and _sim_daily:
        st.markdown(
            '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
            'letter-spacing:1.5px; margin-bottom:8px; font-weight:700;">📅 Day-by-Day Breakdown</div>',
            unsafe_allow_html=True,
        )
        _d_cols = st.columns([0.9, 0.6, 0.6, 0.6, 0.7, 0.7, 0.7, 0.8, 0.8])
        _d_hdrs = ["Date", "Setups", "Wins", "Losses", "Win %",
                   "Long Hit %", "Bull FT", "Bear FT", "False Brk%"]
        for _c, _h in zip(_d_cols, _d_hdrs):
            _c.markdown(
                f'<div style="font-size:9px; font-weight:700; color:#1565c0; '
                f'text-transform:uppercase; letter-spacing:1px; padding:4px 6px;">{_h}</div>',
                unsafe_allow_html=True,
            )
        for _dd, _dr, _ds in _sim_daily:
            _dwr   = _ds.get("win_rate", 0)
            _dlhr  = _ds.get("long_win_rate", 0)
            _dbft  = _ds.get("avg_bull_ft", 0)
            _dbear = _ds.get("avg_bear_ft", 0)
            _dfbr  = _ds.get("false_break_rate", 0)
            _dwr_c = "#4caf50" if _dwr >= 60 else "#ffa726" if _dwr >= 40 else "#ef5350"
            _dlr_c = "#4caf50" if _dlhr >= 50 else "#ffa726" if _dlhr >= 35 else "#ef5350"
            _row_d = st.columns([0.9, 0.6, 0.6, 0.6, 0.7, 0.7, 0.7, 0.8, 0.8])
            _row_d[0].markdown(
                f'<div style="font-size:11px; font-weight:700; color:#e0e0e0; '
                f'padding:6px 6px; font-family:monospace;">'
                f'{_dd.strftime("%a %b %d")}</div>', unsafe_allow_html=True)
            _row_d[1].markdown(
                f'<div style="font-size:11px; color:#90a4ae; padding:6px 6px;">'
                f'{_ds.get("total", 0)}</div>', unsafe_allow_html=True)
            _row_d[2].markdown(
                f'<div style="font-size:11px; color:#4caf50; padding:6px 6px;">'
                f'{_ds.get("wins", 0)}</div>', unsafe_allow_html=True)
            _row_d[3].markdown(
                f'<div style="font-size:11px; color:#ef5350; padding:6px 6px;">'
                f'{_ds.get("losses", 0)}</div>', unsafe_allow_html=True)
            _row_d[4].markdown(
                f'<div style="font-size:12px; font-weight:800; color:{_dwr_c}; '
                f'padding:6px 6px; font-family:monospace;">{_dwr:.1f}%</div>',
                unsafe_allow_html=True)
            _row_d[5].markdown(
                f'<div style="font-size:12px; font-weight:800; color:{_dlr_c}; '
                f'padding:6px 6px; font-family:monospace;">{_dlhr:.1f}%</div>',
                unsafe_allow_html=True)
            _row_d[6].markdown(
                f'<div style="font-size:11px; color:#4caf50; padding:6px 6px; '
                f'font-family:monospace;">+{_dbft:.1f}%</div>', unsafe_allow_html=True)
            _row_d[7].markdown(
                f'<div style="font-size:11px; color:#ef5350; padding:6px 6px; '
                f'font-family:monospace;">-{_dbear:.1f}%</div>', unsafe_allow_html=True)
            _row_d[8].markdown(
                f'<div style="font-size:11px; color:#ffa726; padding:6px 6px; '
                f'font-family:monospace;">{_dfbr:.1f}%</div>', unsafe_allow_html=True)
        st.markdown("---")

    # ── Column headers ──────────────────────────────────────────────────────────
    # ── Deduplicate by (ticker, sim_date) ────────────────────────────────────────
    _seen_keys: set = set()
    _results_deduped = []
    for _r in _results:
        _dedup_key = (
            _r.get("ticker", ""),
            str(_r.get("sim_date", _r.get("trade_date", ""))),
        )
        if _dedup_key not in _seen_keys:
            _seen_keys.add(_dedup_key)
            _results_deduped.append(_r)
    _dupes_removed = len(_results) - len(_results_deduped)
    if _dupes_removed > 0:
        st.caption(f"ℹ️ {_dupes_removed} duplicate row(s) removed from log.")
    _results = _results_deduped

    # ── Per-Ticker Breakdown ──────────────────────────────────────────────────
    import pandas as _pd_bt
    _bt_df = _pd_bt.DataFrame(_results)
    if not _bt_df.empty and "ticker" in _bt_df.columns:
        with st.expander(
            f"📊 Per-Ticker Breakdown — {_bt_df['ticker'].nunique()} tickers across all dates",
            expanded=True,
        ):
            _tkr_rows = []
            _tkr_sweep_data = {}
            _best_tcs_options = []
            _tk_pos_size = float(st.session_state.get("rp_pos_size", 500))

            # ── Min-trade-count slider ─────────────────────────────────────────
            _min_tcs_col, _ = st.columns([1, 3])
            with _min_tcs_col:
                _min_tcs_seed = max(5, min(20, st.session_state.get("min_tcs_trades", 8)))
                _MIN_TCS_TRADES = st.slider(
                    "Min trades required for Best TCS",
                    min_value=5,
                    max_value=20,
                    value=_min_tcs_seed,
                    step=1,
                    key="min_tcs_trades",
                    help=(
                        "Only TCS floors with at least this many trades are "
                        "eligible for the Best TCS recommendation. "
                        "Lower values allow more floors to qualify (less data needed); "
                        "higher values require more trades before trusting a floor."
                    ),
                )

            # Persist slider value to user prefs whenever it changes
            if _AUTH_USER_ID:
                _mtcs_cached = st.session_state.get("_cached_prefs", {})
                if _mtcs_cached.get("min_tcs_trades") != _MIN_TCS_TRADES:
                    _mtcs_new_prefs = {**_mtcs_cached, "min_tcs_trades": _MIN_TCS_TRADES}
                    save_user_prefs(_AUTH_USER_ID, _mtcs_new_prefs)
                    st.session_state["_cached_prefs"] = _mtcs_new_prefs

            def _fmt_avg_r(vals):
                if not vals:
                    return "—"
                avg = sum(vals) / len(vals)
                sign = "+" if avg >= 0 else ""
                return f"{sign}{avg:.3f}R"

            for _tk, _tgrp in _bt_df.groupby("ticker"):
                _tw   = (_tgrp["win_loss"] == "Win").sum()
                _tl   = (_tgrp["win_loss"] == "Loss").sum()
                _twr  = round(_tw / len(_tgrp) * 100, 1) if len(_tgrp) > 0 else 0
                _tavg_tcs = round(_tgrp["tcs"].mean(), 0) if "tcs" in _tgrp else 0
                _tft  = round(_tgrp["aft_move_pct"].mean(), 1) if "aft_move_pct" in _tgrp else 0
                _top_struct = (
                    _tgrp["predicted"].value_counts().index[0]
                    if "predicted" in _tgrp.columns and not _tgrp["predicted"].empty
                    else "—"
                )
                _top_struct = _clean_structure_label(_top_struct)
                _fb_count = (
                    _tgrp.get("false_break_up", _pd_bt.Series(dtype=bool)).sum()
                    + _tgrp.get("false_break_down", _pd_bt.Series(dtype=bool)).sum()
                ) if "false_break_up" in _tgrp else 0
                _fb_rate = round(_fb_count / len(_tgrp) * 100) if len(_tgrp) > 0 else 0
                _dates = sorted(_tgrp["sim_date"].astype(str).unique()) if "sim_date" in _tgrp else []

                # ── Per-ticker TCS Optimizer sweep ────────────────────────────
                _best_tcs_label = "—"
                _best_tcs_pnl_val = None
                _tk_sweep_rows = []
                _any_sweep_rows = False  # track if we had data but insufficient trades
                if "tcs" in _tgrp.columns and "win_loss" in _tgrp.columns:
                    for _sw_tcs in range(40, 85, 5):
                        try:
                            _sw_mask = (
                                _tgrp["actual_outcome"].str.lower().str.contains("bullish|bearish", na=False)
                                & _tgrp["win_loss"].str.strip().isin(["Win", "Loss"])
                                & (_tgrp["tcs"].astype(float) >= _sw_tcs)
                            ) if "actual_outcome" in _tgrp.columns else (
                                _tgrp["win_loss"].str.strip().isin(["Win", "Loss"])
                                & (_tgrp["tcs"].astype(float) >= _sw_tcs)
                            )
                            _sw_sub = _tgrp[_sw_mask]
                            if len(_sw_sub) == 0:
                                continue
                            _sw_wins_n = (_sw_sub["win_loss"] == "Win").sum()
                            _sw_wr_n   = _sw_wins_n / len(_sw_sub) * 100
                            _ft_col    = "follow_thru_pct" if "follow_thru_pct" in _sw_sub.columns else "aft_move_pct"
                            _sw_pnl_n  = sum(
                                _tk_pos_size * abs(float(ft) if ft == ft else 0) / 100
                                * (1 if wl == "Win" else -1)
                                for ft, wl in zip(
                                    _sw_sub[_ft_col].fillna(0) if _ft_col in _sw_sub.columns else [0] * len(_sw_sub),
                                    _sw_sub["win_loss"]
                                )
                            )
                            _sw_exp_n = _sw_pnl_n / len(_sw_sub)
                            _sufficient = len(_sw_sub) >= _MIN_TCS_TRADES
                            _any_sweep_rows = True
                            _tk_sweep_rows.append({
                                "TCS Floor":      _sw_tcs,
                                "Trades":         len(_sw_sub),
                                "Win Rate":       round(_sw_wr_n, 1),
                                "Net P&L ($)":    round(_sw_pnl_n, 0),
                                "Expectancy ($)": round(_sw_exp_n, 2),
                                "Sufficient":     "✓" if _sufficient else f"✗ (<{_MIN_TCS_TRADES})",
                            })
                            # only qualify this floor for Best TCS if it has enough trades
                            if _sufficient and (
                                _best_tcs_pnl_val is None or _sw_pnl_n > _best_tcs_pnl_val
                            ):
                                _best_tcs_pnl_val   = _sw_pnl_n
                                _best_tcs_floor     = _sw_tcs
                                _best_tcs_cnt       = len(_sw_sub)
                                _best_tcs_wr_final  = _sw_wr_n
                        except (ValueError, TypeError, AttributeError):
                            continue
                if _tk_sweep_rows:
                    _tkr_sweep_data[_tk] = _tk_sweep_rows
                if _best_tcs_pnl_val is not None:
                    _pnl_sign = "+" if _best_tcs_pnl_val >= 0 else ""
                    _best_tcs_label = (
                        f"TCS {_best_tcs_floor} "
                        f"({_best_tcs_cnt} trades, "
                        f"{_best_tcs_wr_final:.0f}% WR, "
                        f"{_pnl_sign}${_best_tcs_pnl_val:,.0f})"
                    )
                    _best_tcs_options.append((_tk, int(_best_tcs_floor)))
                elif _any_sweep_rows:
                    # had trades but no floor met the minimum count threshold
                    _best_tcs_label = f"— (insufficient data, <{_MIN_TCS_TRADES} trades per floor)"

                # ── Per-ticker EOD vs Tiered R averages ───────────────────────
                _tk_eod_vals = (
                    _tgrp["eod_pnl_r"].dropna().tolist()
                    if "eod_pnl_r" in _tgrp.columns else []
                )
                _tk_tiered_vals = (
                    _tgrp["tiered_pnl_r"].dropna().tolist()
                    if "tiered_pnl_r" in _tgrp.columns else []
                )
                _tk_eod_str    = _fmt_avg_r(_tk_eod_vals)
                _tk_tiered_str = _fmt_avg_r(_tk_tiered_vals)
                _tk_eod_num    = (sum(_tk_eod_vals) / len(_tk_eod_vals)) if _tk_eod_vals else float("-inf")
                _tk_tiered_num = (sum(_tk_tiered_vals) / len(_tk_tiered_vals)) if _tk_tiered_vals else float("-inf")

                _tkr_persist_key   = f"_drill_tcs_persist_{_tk}"
                _tkr_persisted_val = st.session_state.get(_tkr_persist_key)
                if _best_tcs_pnl_val is not None:
                    _tkr_has_override = (
                        _tkr_persisted_val is not None
                        and _tkr_persisted_val != _best_tcs_floor
                    )
                else:
                    _tkr_has_override = _tkr_persisted_val is not None
                _tkr_rows.append({
                    "Ticker":         f"{_tk}  ✱" if _tkr_has_override else _tk,
                    "Setups":         len(_tgrp),
                    "Win %":          f"{'🟢' if _twr >= 60 else '🟡' if _twr >= 45 else '🔴'} {_twr}%",
                    "W/L":            f"{_tw}/{_tl}",
                    "Avg TCS":        int(_tavg_tcs),
                    "Best TCS":       _best_tcs_label,
                    "Top Structure":  _top_struct,
                    "Avg Follow-Thru": f"{'+' if _tft >= 0 else ''}{_tft}%",
                    "EOD Hold R":     _tk_eod_str,
                    "Tiered Exit R":  _tk_tiered_str,
                    "False Brk %":    f"{'🔴' if _fb_rate > 35 else '🟡' if _fb_rate > 20 else '🟢'} {_fb_rate}%",
                    "Dates Seen":     ", ".join(d[:5] for d in _dates[-3:]) + ("…" if len(_dates) > 3 else ""),
                    "_sort_win_pct":  _twr,
                    "_sort_eod_r":    _tk_eod_num,
                    "_sort_tiered_r": _tk_tiered_num,
                })
            _sort_col_map = {
                "Win %":         ("_sort_win_pct",  False),
                "EOD Hold R":    ("_sort_eod_r",    False),
                "Tiered Exit R": ("_sort_tiered_r", False),
            }
            _r_filter_col_map = {
                "EOD Hold R":    "_sort_eod_r",
                "Tiered Exit R": "_sort_tiered_r",
            }
            _ctrl_col_sort, _ctrl_col_filter = st.columns([3, 2])
            with _ctrl_col_sort:
                _sort_choice = st.radio(
                    "Sort table by",
                    list(_sort_col_map.keys()),
                    index=0,
                    horizontal=True,
                    key="tkr_summary_sort_radio",
                )
            with _ctrl_col_filter:
                _r_filter_col = st.radio(
                    "Min-R filter column",
                    list(_r_filter_col_map.keys()),
                    index=0,
                    horizontal=True,
                    key="tkr_summary_r_filter_col",
                )
                _r_filter_min = st.number_input(
                    f"Min {_r_filter_col}",
                    value=None,
                    placeholder="No filter (show all)",
                    step=0.1,
                    format="%.2f",
                    key="tkr_summary_r_filter_min",
                    help=(
                        "Hide tickers whose selected R column is below this value. "
                        "Leave blank (or clear the field) to show all tickers. "
                        "Example: enter 0.5 to see only tickers averaging ≥ 0.5R."
                    ),
                )
                if _r_filter_min is None:
                    _r_filter_min = float("-inf")
            _sort_key, _sort_asc = _sort_col_map[_sort_choice]
            _r_filter_key = _r_filter_col_map[_r_filter_col]
            _tkr_summary_df = _pd_bt.DataFrame(_tkr_rows).sort_values(_sort_key, ascending=_sort_asc)
            _r_filter_mask = _tkr_summary_df[_r_filter_key].isna() | (_tkr_summary_df[_r_filter_key] >= _r_filter_min)
            _tkr_summary_df = _tkr_summary_df[_r_filter_mask]
            _filtered_count = int((~_r_filter_mask).sum())
            if _filtered_count > 0:
                st.info(
                    f"ℹ️ **{_filtered_count} ticker{'s' if _filtered_count != 1 else ''} hidden** — "
                    f"{_r_filter_col} below {_r_filter_min:.2f}R. "
                    "Lower the threshold to show more tickers."
                )
            _insuff_mask = _tkr_summary_df["Best TCS"].str.contains("insufficient", na=False)
            _insuff_count = int(_insuff_mask.sum())
            if _insuff_count > 0:
                st.warning(
                    f"⚠️ **{_insuff_count} ticker{'s' if _insuff_count != 1 else ''} "
                    f"{'have' if _insuff_count != 1 else 'has'} insufficient data for Best TCS optimization** — "
                    f"fewer than {_MIN_TCS_TRADES} trades exist at any single TCS floor. "
                    f"These rows are highlighted in grey below. Collect more trade data or broaden the replay date range."
                )
            _tkr_display_df = _tkr_summary_df.drop(
                columns=["_sort_win_pct", "_sort_eod_r", "_sort_tiered_r"],
                errors="ignore",
            )
            # Add a gold medal badge to the best-performing ticker's cell
            if len(_tkr_display_df) > 0 and "Ticker" in _tkr_display_df.columns:
                _tkr_display_df = _tkr_display_df.copy()
                _best_row_idx = _tkr_display_df.index[0]
                _tkr_col_pos = _tkr_display_df.columns.get_loc("Ticker")
                _tkr_display_df.iloc[0, _tkr_col_pos] = (
                    "🥇 " + str(_tkr_display_df.iloc[0, _tkr_col_pos])
                )
            else:
                _best_row_idx = None

            def _style_rows(row):
                if "insufficient" in str(row.get("Best TCS", "")):
                    return ["background-color: #e8e8e8; color: #666666"] * len(row)
                if _best_row_idx is not None and row.name == _best_row_idx:
                    return ["background-color: #fffbcc; font-weight: bold"] * len(row)
                return [""] * len(row)

            _styled_summary = _tkr_display_df.style.apply(_style_rows, axis=1)
            st.dataframe(
                _styled_summary,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Best TCS": st.column_config.TextColumn(
                        "Best TCS",
                        help=(
                            "The TCS cutoff (40–80) that produced the highest net P&L "
                            "for this specific ticker across all trades in the replay. "
                            f"Only floors with at least {_MIN_TCS_TRADES} trades are considered — floors "
                            "with fewer trades are statistically unreliable and are "
                            "excluded. Tickers with too few trades show "
                            "'— (insufficient data)'. "
                            "Format: TCS floor · trade count · win rate · net P&L "
                            "(using the same position size as the replay above). "
                            "Different tickers may thrive at different TCS levels."
                        ),
                    ),
                },
            )
            st.caption(
                "🟢 Win % ≥ 60% — model reads this ticker well  · "
                "🟡 45–60% — mixed signal, paper trade first  · "
                "🔴 < 45% — IB framework doesn't fit this ticker · "
                "False Brk % = how often IB breakouts reversed within 30 min  · "
                f"Best TCS = the TCS cutoff (≥{_MIN_TCS_TRADES} trades) that produced the highest net P&L for that ticker "
                "(hover the column header for details)"
            )
            if _best_tcs_options:
                if _rp_bot_mode:
                    st.markdown("**🎯 Apply Best TCS to replay filter (Bot mode — adjusts TCS offset):**")

                    # Pre-compute which tickers are clamped (ideal offset outside −20..+20)
                    _all_clamped = []
                    for _ckr, _cfloor in _best_tcs_options:
                        _cr_off = _cfloor - 50
                        _ca_off = max(-20, min(20, _cr_off))
                        if _cr_off != _ca_off:
                            _all_clamped.append({
                                "ticker": _ckr,
                                "floor": _cfloor,
                                "raw": _cr_off,
                                "applied": _ca_off,
                                "effective": 50 + _ca_off,
                            })

                    if len(_all_clamped) >= 2:
                        # Consolidated warning: list all affected tickers in one banner
                        _cw_lines = []
                        for _cw in _all_clamped:
                            _cw_rs = "+" if _cw["raw"] >= 0 else ""
                            _cw_as = "+" if _cw["applied"] >= 0 else ""
                            _cw_lines.append(
                                f"• **{_cw['ticker']}**: ideal floor TCS {_cw['floor']} "
                                f"(offset {_cw_rs}{_cw['raw']}) → clamped to {_cw_as}{_cw['applied']} "
                                f"≈ effective TCS {_cw['effective']}"
                            )
                        st.warning(
                            f"⚠️ **{len(_all_clamped)} tickers have ideal TCS floors outside the "
                            f"slider range (−20 to +20).** Their offsets have been clamped — "
                            f"the replay will filter at the effective TCS shown, not the ideal floor.\n\n"
                            + "\n\n".join(_cw_lines)
                        )
                        # Discard any stale single-ticker warning since the consolidated one covers it
                        st.session_state.pop("_tcs_clamp_warning", None)
                    else:
                        # 0 or 1 clamped ticker — keep the existing per-click session state warning
                        _clamp_warn = st.session_state.pop("_tcs_clamp_warning", None)
                        if _clamp_warn:
                            _cw_eff = 50 + _clamp_warn["applied"]
                            _cw_raw_sign = "+" if _clamp_warn["raw"] >= 0 else ""
                            _cw_off_sign = "+" if _clamp_warn["applied"] >= 0 else ""
                            st.warning(
                                f"⚠️ Best floor TCS {_clamp_warn['floor']} for **{_clamp_warn['ticker']}** "
                                f"requires offset {_cw_raw_sign}{_clamp_warn['raw']}, which is outside the "
                                f"slider range (−20 to +20). "
                                f"Applied offset {_cw_off_sign}{_clamp_warn['applied']} instead "
                                f"(≈ effective TCS {_cw_eff}). "
                                f"The replay is filtered at TCS ≥ {_cw_eff}, "
                                f"not the ideal TCS ≥ {_clamp_warn['floor']}."
                            )
                else:
                    st.markdown("**🎯 Apply Best TCS to replay filter:**")
                _btn_cols = st.columns(min(len(_best_tcs_options), 6))
                for _bi, (_btkr, _bfloor) in enumerate(_best_tcs_options):
                    with _btn_cols[_bi % 6]:
                        if _rp_bot_mode:
                            _raw_offset = _bfloor - 50
                            _bot_offset = max(-20, min(20, _raw_offset))
                            _clamped = _raw_offset != _bot_offset
                            _off_sign = "+" if _bot_offset >= 0 else ""
                            _raw_sign = "+" if _raw_offset >= 0 else ""
                            _help_txt = (
                                f"Set TCS Adjustment to {_off_sign}{_bot_offset} "
                                f"(≈ TCS {_bfloor} floor for {_btkr}) and re-run replay. "
                                f"Offset = best floor {_bfloor} − base 50. "
                                f"Based on min {_MIN_TCS_TRADES} trades per floor."
                            )
                            if _clamped:
                                _help_txt += (
                                    f" Note: ideal offset {_raw_sign}{_raw_offset} "
                                    f"exceeds slider range and is clamped to {_off_sign}{_bot_offset}."
                                )
                            if st.button(
                                f"{_btkr}: TCS {_bfloor} (≥{_MIN_TCS_TRADES} trades)",
                                key=f"use_best_tcs_{_btkr}",
                                help=_help_txt,
                            ):
                                if _clamped and len(_all_clamped) < 2:
                                    # Only store the per-click warning when there's no consolidated banner
                                    st.session_state["_tcs_clamp_warning"] = {
                                        "ticker": _btkr,
                                        "floor": _bfloor,
                                        "raw": _raw_offset,
                                        "applied": _bot_offset,
                                    }
                                st.session_state["rp_tcs_offset"] = _bot_offset
                                st.rerun()
                        else:
                            if st.button(
                                f"{_btkr}: TCS {_bfloor} (≥{_MIN_TCS_TRADES} trades)",
                                key=f"use_best_tcs_{_btkr}",
                                help=f"Set Min TCS filter to {_bfloor} (best floor for {_btkr}) and re-run replay. Based on min {_MIN_TCS_TRADES} trades per floor.",
                            ):
                                st.session_state["rp_min_tcs_slider"] = _bfloor
                                st.session_state["rp_best_tcs_source"] = {"ticker": _btkr, "floor": _bfloor}
                                st.rerun()

            # ── Per-Ticker TCS Sweep Charts ───────────────────────────────────
            if _tkr_sweep_data:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(
                    f'<div style="font-size:13px;font-weight:700;color:#90caf9;'
                    f'letter-spacing:0.5px;margin-bottom:6px;">📈 TCS Sweep Charts — P&L curve per ticker'
                    f' (min {_MIN_TCS_TRADES} trades per floor)</div>',
                    unsafe_allow_html=True,
                )
                import altair as _alt_tk

                # ── Bulk reset button ─────────────────────────────────────────
                _persist_keys_all = [
                    k for k in st.session_state if k.startswith("_drill_tcs_persist_")
                ]
                if _persist_keys_all:
                    _bulk_reset_col, _ = st.columns([2, 5])
                    with _bulk_reset_col:
                        if st.button(
                            f"↩ Reset all to best ({len(_persist_keys_all)} ticker{'s' if len(_persist_keys_all) != 1 else ''})",
                            key="_bulk_reset_all_tickers",
                            use_container_width=True,
                            help="Clear every manual TCS floor override and restore the recommended floor for all tickers.",
                        ):
                            for _prst_k in list(st.session_state.keys()):
                                if _prst_k.startswith("_drill_tcs_persist_") or _prst_k.startswith("drill_tcs_"):
                                    del st.session_state[_prst_k]
                            st.rerun()

                _SWEEP_SORT_OPTIONS = ["P&L (default)", "Win Rate", "Trade Count", "Alphabetical"]

                # ── Restore sort preference from localStorage (cross-session) ──
                import streamlit.components.v1 as _cmp_sort
                _cmp_sort.html("""
<script>
(function() {
    var _LS_KEY = 'sweep_chart_sort';
    var url = new URL(window.parent.location.href);
    // Only restore from localStorage when no explicit sweep_sort param is in the
    // URL — this preserves deep-link / shared-URL intent over personal preference.
    if (url.searchParams.has('sweep_sort')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('sweep_sort', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

                # Seed session state from URL query param (survives browser refresh)
                if "sweep_chart_sort" not in st.session_state:
                    _qp_sweep = st.query_params.get("sweep_sort", "P&L (default)")
                    st.session_state["sweep_chart_sort"] = (
                        _qp_sweep if _qp_sweep in _SWEEP_SORT_OPTIONS else "P&L (default)"
                    )

                _sweep_sort_sel = st.selectbox(
                    "Sort sweep charts by:",
                    options=_SWEEP_SORT_OPTIONS,
                    index=_SWEEP_SORT_OPTIONS.index(
                        st.session_state.get("sweep_chart_sort", "P&L (default)")
                        if st.session_state.get("sweep_chart_sort", "P&L (default)") in _SWEEP_SORT_OPTIONS
                        else "P&L (default)"
                    ),
                    key="sweep_chart_sort",
                    help=(
                        "P&L — highest net P&L (at the best sufficient TCS floor) first · "
                        "Win Rate — highest win rate (at the best sufficient floor) first · "
                        "Trade Count — most trades at the best sufficient floor first · "
                        "Alphabetical — A → Z by ticker"
                    ),
                )

                # Persist the selected value — URL query param (survives refresh) +
                # localStorage (survives new sessions via the JS restore above)
                _qp_current = st.query_params.get("sweep_sort")
                if _qp_current != _sweep_sort_sel:
                    st.query_params["sweep_sort"] = _sweep_sort_sel
                _cmp_sort.html(
                    f"<script>localStorage.setItem('sweep_chart_sort',"
                    f" {repr(_sweep_sort_sel)});</script>",
                    height=0,
                )
                _sort_desc = {
                    "P&L (default)": "sorted by highest net P&L at the best TCS floor — highest first",
                    "Win Rate":      "sorted by highest win rate at the best TCS floor — highest first",
                    "Trade Count":   "sorted by most trades at the best TCS floor — highest first",
                    "Alphabetical":  "sorted A → Z by ticker",
                }
                st.caption(
                    f"Currently {_sort_desc.get(_sweep_sort_sel, '')}. "
                    "Tickers with insufficient data fall to the bottom (except in Alphabetical mode). "
                    "Each chart sweeps TCS 40 → 80 for that ticker only. "
                    f"Bold row = highest net P&L among floors with ≥{_MIN_TCS_TRADES} trades. "
                    "Floors marked ✗ have too few trades and are excluded from the Best TCS pick. "
                    "Expand a ticker to see the full profit curve."
                )

                def _tk_sort_key(_name):
                    _rows = _tkr_sweep_data[_name]
                    _df = _pd_bt.DataFrame(_rows)
                    _suff = _df[_df["Sufficient"] == "✓"]
                    if _sweep_sort_sel == "Alphabetical":
                        return (0, _name)
                    if _suff.empty:
                        if _sweep_sort_sel == "Trade Count":
                            return (1, -_df["Trades"].max(), _name)
                        return (1, 0, _name)
                    if _sweep_sort_sel == "Win Rate":
                        _best_wr = _suff["Win Rate"].max()
                        return (0, -_best_wr, _name)
                    if _sweep_sort_sel == "Trade Count":
                        _best_cnt = _suff["Trades"].max()
                        return (0, -_best_cnt, _name)
                    _best_pnl = _suff["Net P&L ($)"].max()
                    return (0, -_best_pnl, _name)

                # ── Restore CSV column preference from localStorage (cross-session) ──
                import streamlit.components.v1 as _cmp_csv_pref
                _cmp_csv_pref.html("""
<script>
(function() {
    var _LS_KEY = 'csv_cols_pref';
    var url = new URL(window.parent.location.href);
    if (url.searchParams.has('csv_cols')) return;
    var saved = localStorage.getItem(_LS_KEY);
    if (!saved) return;
    url.searchParams.set('csv_cols', saved);
    window.parent.location.replace(url.toString());
})();
</script>
""", height=0)

                _CSV_PREF_DEFAULTS = [
                    "Date", "TCS", "Prediction",
                    "EOD Reality", "Follow-Thru %", "Result",
                ]
                if "_csv_cols_pref" not in st.session_state:
                    _qp_csv = st.query_params.get("csv_cols", "")
                    if _qp_csv:
                        _csv_pref_list = [c.strip() for c in _qp_csv.split(",") if c.strip()]
                        st.session_state["_csv_cols_pref"] = _csv_pref_list if _csv_pref_list else _CSV_PREF_DEFAULTS
                    else:
                        st.session_state["_csv_cols_pref"] = _CSV_PREF_DEFAULTS

                # ── Global CSV column reset (triggered by any expander's reset button) ──
                _csv_just_reset = False
                if st.session_state.pop("_csv_global_reset", False):
                    _csv_stale_keys = [k for k in list(st.session_state) if k.startswith("csv_cols_")]
                    for _csk in _csv_stale_keys:
                        del st.session_state[_csk]
                    st.session_state.pop("_csv_cols_pref", None)
                    if "csv_cols" in st.query_params:
                        del st.query_params["csv_cols"]
                    import streamlit.components.v1 as _cmp_csv_global_rst
                    _cmp_csv_global_rst.html(
                        "<script>localStorage.removeItem('csv_cols_pref');</script>",
                        height=0,
                    )
                    _csv_just_reset = True

                # ── Global "Reset all CSV columns" button (above all ticker expanders) ──
                _gcr_col, _ = st.columns([3, 7])
                with _gcr_col:
                    if st.button(
                        "↩ Reset all CSV columns to defaults",
                        key="csv_global_reset_top_btn",
                        help="Clears the saved column preference for every ticker at once and reverts all multiselects to the default 6 columns.",
                    ):
                        st.session_state["_csv_global_reset"] = True
                        st.rerun()

                for _tk_name in sorted(_tkr_sweep_data.keys(), key=_tk_sort_key):
                    _tk_rows = _tkr_sweep_data[_tk_name]
                    _tk_sw_df = _pd_bt.DataFrame(_tk_rows)
                    # only consider floors with sufficient trades for the highlighted best
                    _tk_sw_suff = _tk_sw_df[_tk_sw_df["Sufficient"] == "✓"]
                    _tk_persist_key_hdr = f"_drill_tcs_persist_{_tk_name}"
                    _tk_persisted_hdr   = st.session_state.get(_tk_persist_key_hdr)
                    if not _tk_sw_suff.empty:
                        _tk_best_pnl = _tk_sw_suff["Net P&L ($)"].max()
                        _tk_best_row = _tk_sw_suff[_tk_sw_suff["Net P&L ($)"] == _tk_best_pnl].iloc[0]
                        _tk_auto_floor = int(_tk_best_row["TCS Floor"])
                        _tk_has_override = (
                            _tk_persisted_hdr is not None
                            and _tk_persisted_hdr != _tk_auto_floor
                        )
                        _tk_override_active = _tk_has_override
                        _tk_override_badge = (
                            f" · 🔵 custom floor (TCS ≥ {_tk_persisted_hdr})"
                            if _tk_has_override else ""
                        )
                        _tk_pnl_sign = "+" if _tk_best_pnl >= 0 else "-"
                        _tk_expander_label = (
                            f"📊 {_tk_name}{'  ✱' if _tk_override_active else ''} — Best: TCS ≥ {_tk_auto_floor} "
                            f"({int(_tk_best_row['Trades'])}/{_MIN_TCS_TRADES} trades "
                            f"· {_tk_best_row['Win Rate']:.0f}% WR "
                            f"· {_tk_pnl_sign}${abs(_tk_best_pnl):,.0f} net P&L)"
                            f"{_tk_override_badge}"
                        )
                        _tk_has_best = True
                    else:
                        _tk_best_pnl = None
                        _tk_best_row = None
                        _tk_max_trades = int(_tk_sw_df["Trades"].max()) if not _tk_sw_df.empty else 0
                        _tk_has_override = _tk_persisted_hdr is not None
                        _tk_override_active = _tk_has_override
                        _tk_override_badge = (
                            f" · 🔵 custom floor (TCS ≥ {_tk_persisted_hdr})"
                            if _tk_has_override else ""
                        )
                        _tk_expander_label = (
                            f"📊 {_tk_name}{'  ✱' if _tk_override_active else ''} — insufficient data "
                            f"({_tk_max_trades} of {_MIN_TCS_TRADES} trades at best floor)"
                            f"{_tk_override_badge}"
                        )
                        _tk_has_best = False
                    with st.expander(_tk_expander_label, expanded=False):
                        if _tk_has_best:
                            st.markdown(
                                f'<div style="background:#1e3a2a;border-radius:8px;padding:8px 14px;'
                                f'margin-bottom:10px;font-size:13px;color:#a5d6a7;">'
                                f'📈 <b>Optimal for Max Profit (≥{_MIN_TCS_TRADES} trades):</b> TCS ≥ {int(_tk_best_row["TCS Floor"])} '
                                f'→ {int(_tk_best_row["Trades"])} trades · {_tk_best_row["Win Rate"]:.1f}% WR · '
                                f'${_tk_best_pnl:,.0f} net P&L</div>',
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                f'<div style="background:#3a2a0a;border-left:4px solid #f9a825;border-radius:8px;'
                                f'padding:8px 14px;margin-bottom:10px;font-size:13px;color:#ffe082;">'
                                f'⚠️ <b>Insufficient data for Best TCS</b> — best floor: {_tk_max_trades} trade{"s" if _tk_max_trades != 1 else ""} '
                                f'— need {_MIN_TCS_TRADES}. Collect more data or broaden the replay date range.</div>',
                                unsafe_allow_html=True,
                            )

                        _tk_chart_df = _tk_sw_df.copy()
                        _tk_chart_df["_is_best"] = (
                            (_tk_chart_df["Net P&L ($)"] == _tk_best_pnl) & _tk_has_best
                        )
                        _tk_chart_df["Color"] = _tk_chart_df.apply(
                            lambda r: "#555555" if r["Sufficient"] != "✓" else (
                                "#4caf50" if r["_is_best"] else (
                                    "#ef5350" if r["Net P&L ($)"] < 0 else "#42a5f5"
                                )
                            ),
                            axis=1,
                        )
                        _tk_chart_df["TCS Floor Label"] = _tk_chart_df["TCS Floor"].astype(str)

                        _tk_bar = (
                            _alt_tk.Chart(_tk_chart_df)
                            .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                            .encode(
                                x=_alt_tk.X(
                                    "TCS Floor Label:O",
                                    axis=_alt_tk.Axis(title="TCS Floor", labelColor="#b0bec5", titleColor="#90caf9"),
                                    sort=[str(v) for v in range(40, 85, 5)],
                                ),
                                y=_alt_tk.Y(
                                    "Net P&L ($):Q",
                                    axis=_alt_tk.Axis(title="Net P&L ($)", labelColor="#b0bec5", titleColor="#90caf9", format="$,.0f"),
                                ),
                                color=_alt_tk.Color("Color:N", scale=None, legend=None),
                                tooltip=[
                                    _alt_tk.Tooltip("TCS Floor:Q", title="TCS Floor"),
                                    _alt_tk.Tooltip("Trades:Q", title="Trades"),
                                    _alt_tk.Tooltip("Win Rate:Q", title="Win Rate", format=".1f"),
                                    _alt_tk.Tooltip("Net P&L ($):Q", title="Net P&L ($)", format="$,.0f"),
                                    _alt_tk.Tooltip("Expectancy ($):Q", title="Expectancy ($)", format="$,.2f"),
                                ],
                            )
                            .properties(height=220)
                            .configure_view(strokeWidth=0, fill="#0e1117")
                            .configure_axis(gridColor="#2a2a3a", domainColor="#444")
                        )
                        st.altair_chart(_tk_bar, use_container_width=True)

                        st.markdown(
                            f'<div style="display:flex;gap:18px;flex-wrap:wrap;margin:-6px 0 10px 0;font-size:11px;color:#90a4ae;">'
                            f'<span><span style="display:inline-block;width:10px;height:10px;background:#4caf50;border-radius:2px;margin-right:4px;"></span>Best floor</span>'
                            f'<span><span style="display:inline-block;width:10px;height:10px;background:#42a5f5;border-radius:2px;margin-right:4px;"></span>Profitable</span>'
                            f'<span><span style="display:inline-block;width:10px;height:10px;background:#ef5350;border-radius:2px;margin-right:4px;"></span>Unprofitable</span>'
                            f'<span><span style="display:inline-block;width:10px;height:10px;background:#555555;border-radius:2px;margin-right:4px;"></span>'
                            f'Insufficient data (&lt;{_MIN_TCS_TRADES} trades)</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                        def _tk_sw_style(row):
                            if _tk_has_best and row["Net P&L ($)"] == _tk_best_pnl and row["Sufficient"] == "✓":
                                return ["background:#1e3a2a;font-weight:bold"] * len(row)
                            return [""] * len(row)

                        st.dataframe(
                            _tk_sw_df.style.apply(_tk_sw_style, axis=1),
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Net P&L ($)":    st.column_config.NumberColumn(format="$%.0f"),
                                "Expectancy ($)": st.column_config.NumberColumn(format="$%.2f"),
                                "Win Rate":       st.column_config.NumberColumn(format="%.1f%%"),
                                "Sufficient":     st.column_config.TextColumn(
                                    "Sufficient",
                                    help=f"✓ = at least {_MIN_TCS_TRADES} trades at this floor (eligible for Best TCS); ✗ = fewer than {_MIN_TCS_TRADES} trades (excluded from Best TCS pick)",
                                ),
                            },
                        )
                        # ── R-stats summary block for sweep summary CSV ──────────
                        _sw_sum_cols   = list(_tk_sw_df.columns)
                        _sw_lbl_col    = _sw_sum_cols[0]
                        _sw_val_col    = _sw_sum_cols[1] if len(_sw_sum_cols) > 1 else _sw_sum_cols[0]
                        # Filter to only valid Win/Loss trades, matching the sweep eligibility logic
                        _sw_tgrp       = _bt_df[_bt_df["ticker"] == _tk_name]
                        if "actual_outcome" in _sw_tgrp.columns:
                            _sw_valid_mask = (
                                _sw_tgrp["actual_outcome"].str.lower().str.contains("bullish|bearish", na=False)
                                & _sw_tgrp["win_loss"].str.strip().isin(["Win", "Loss"])
                            )
                        elif "win_loss" in _sw_tgrp.columns:
                            _sw_valid_mask = _sw_tgrp["win_loss"].str.strip().isin(["Win", "Loss"])
                        else:
                            _sw_valid_mask = _pd_bt.Series([True] * len(_sw_tgrp), index=_sw_tgrp.index)
                        _sw_tgrp       = _sw_tgrp[_sw_valid_mask]
                        _sw_r_col      = (
                            "eod_pnl_r" if "eod_pnl_r" in _sw_tgrp.columns
                            else ("tiered_pnl_r" if "tiered_pnl_r" in _sw_tgrp.columns else None)
                        )
                        if _sw_r_col is not None:
                            _sw_r_ser       = _pd_bt.to_numeric(_sw_tgrp[_sw_r_col], errors="coerce").dropna()
                            _sw_n           = len(_sw_r_ser)
                            _sw_fb_n        = 0
                            if "false_break_up" in _sw_tgrp.columns:
                                _sw_fb_n = (
                                    _sw_tgrp["false_break_up"].fillna(False).astype(bool).sum()
                                    + _sw_tgrp.get("false_break_down", _pd_bt.Series(dtype=bool)).fillna(False).astype(bool).sum()
                                )
                            _sw_fb_rate     = round(_sw_fb_n / _sw_n * 100, 1) if _sw_n else 0
                            _sw_avg_win_r   = round(_sw_r_ser[_sw_r_ser > 0].mean(), 2) if (_sw_r_ser > 0).any() else 0
                            _sw_avg_loss_r  = round(_sw_r_ser[_sw_r_ser < 0].mean(), 2) if (_sw_r_ser < 0).any() else 0
                            _sw_exp_r       = round(_sw_r_ser.mean(), 3) if _sw_n else 0
                            _sw_cum_r_vals  = _sw_r_ser.cumsum().reset_index(drop=True)
                            _sw_peak_r      = _sw_cum_r_vals.cummax()
                            _sw_max_dd_r    = round((_sw_cum_r_vals - _sw_peak_r).min(), 2) if _sw_n else 0

                            # ── Cumulative R chart with drawdown highlight ──────────
                            _sw_dd_series = _sw_cum_r_vals - _sw_peak_r
                            if _sw_n and _sw_max_dd_r < 0:
                                _sw_dd_trough_idx = int(_sw_dd_series.idxmin())
                                _sw_dd_peak_idx   = int(_sw_cum_r_vals.iloc[:_sw_dd_trough_idx + 1].idxmax())
                            else:
                                _sw_dd_trough_idx = None
                                _sw_dd_peak_idx   = None

                            _sw_fig_cum_r = go.Figure()
                            _sw_fig_cum_r.add_trace(go.Scatter(
                                x=list(range(len(_sw_cum_r_vals))),
                                y=_sw_cum_r_vals.tolist(),
                                mode="lines",
                                name="Cumulative R",
                                line=dict(color="#1f77b4", width=2),
                            ))

                            if _sw_dd_trough_idx is not None and _sw_dd_peak_idx is not None:
                                _sw_fig_cum_r.add_vrect(
                                    x0=_sw_dd_peak_idx,
                                    x1=_sw_dd_trough_idx,
                                    fillcolor="rgba(220, 50, 50, 0.15)",
                                    layer="below",
                                    line_width=0,
                                )
                                _sw_fig_cum_r.add_trace(go.Scatter(
                                    x=[_sw_dd_peak_idx],
                                    y=[float(_sw_cum_r_vals.iloc[_sw_dd_peak_idx])],
                                    mode="markers",
                                    marker=dict(color="#2ca02c", size=10, symbol="triangle-down"),
                                    name=f"DD Start (trade #{_sw_dd_peak_idx})",
                                ))
                                _sw_fig_cum_r.add_trace(go.Scatter(
                                    x=[_sw_dd_trough_idx],
                                    y=[float(_sw_cum_r_vals.iloc[_sw_dd_trough_idx])],
                                    mode="markers",
                                    marker=dict(color="#d62728", size=10, symbol="triangle-up"),
                                    name=f"DD End (trade #{_sw_dd_trough_idx})",
                                ))

                            _sw_fig_cum_r.update_layout(
                                height=240,
                                margin=dict(l=0, r=0, t=10, b=30),
                                xaxis_title="Trade #",
                                yaxis_title="Cumulative R",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                                plot_bgcolor="rgba(0,0,0,0)",
                                paper_bgcolor="rgba(0,0,0,0)",
                            )
                            _sw_fig_cum_r.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            _sw_fig_cum_r.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.15)")
                            st.caption("**Cumulative R (raw edge)** — sum of R multiples trade by trade across all TCS floors")
                            st.plotly_chart(_sw_fig_cum_r, use_container_width=True)

                            if _sw_dd_trough_idx is not None and _sw_dd_peak_idx is not None:
                                st.caption(
                                    f"🔴 Max drawdown period: trade\u00a0**#{_sw_dd_peak_idx}** → **#{_sw_dd_trough_idx}**"
                                    f"\u2002({_sw_dd_trough_idx - _sw_dd_peak_idx} trades)\u2002|\u2002magnitude\u00a0**{abs(_sw_max_dd_r)}R**"
                                )

                            def _sw_stat_r(lbl, val):
                                _rx = {c: "" for c in _sw_sum_cols}
                                _rx[_sw_lbl_col] = lbl
                                _rx[_sw_val_col] = val
                                return _rx
                            _sw_summ_rows = [
                                {c: "" for c in _sw_sum_cols},
                                _sw_stat_r("--- R-STATS SUMMARY ---", ""),
                                _sw_stat_r("Stop-Out Rate", f"{_sw_fb_rate}%"),
                                _sw_stat_r("Avg Win (R)", f"+{_sw_avg_win_r}R"),
                                _sw_stat_r("Avg Loss (R)", f"{_sw_avg_loss_r}R"),
                                _sw_stat_r("Expectancy", f"{_sw_exp_r:+.3f}R/trade"),
                                _sw_stat_r("Max Drawdown (R)", f"{abs(_sw_max_dd_r)}R"),
                            ]
                            _tk_sw_csv_export = _pd_bt.concat(
                                [_tk_sw_df, _pd_bt.DataFrame(_sw_summ_rows)],
                                ignore_index=True,
                            )
                        else:
                            _tk_sw_csv_export = _tk_sw_df
                        st.download_button(
                            label="⬇️ Download Sweep Summary CSV",
                            data=_tk_sw_csv_export.to_csv(index=False),
                            file_name=f"{_tk_name}_sweep_summary.csv",
                            mime="text/csv",
                            key=f"_dl_sweep_{_tk_name}",
                        )

                        # ── Drill-down: trades at a selected TCS cutoff ──────────
                        st.markdown(
                            '<div style="border-top:1px solid #1e2a3a;margin:16px 0 10px 0;"></div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            '<div style="font-size:12px;font-weight:700;color:#90caf9;'
                            'margin-bottom:6px;">🔍 Drill into trades at a TCS cutoff</div>',
                            unsafe_allow_html=True,
                        )
                        _tk_drill_floors = sorted(
                            _tk_sw_df["TCS Floor"].astype(int).unique().tolist()
                        )
                        _tk_drill_default = (
                            int(_tk_best_row["TCS Floor"])
                            if _tk_has_best and _tk_best_row is not None
                            else _tk_drill_floors[0]
                        )
                        _tk_persist_key = f"_drill_tcs_persist_{_tk_name}"
                        _tk_persisted_val = st.session_state.get(_tk_persist_key)
                        if _tk_persisted_val is not None and _tk_persisted_val in _tk_drill_floors:
                            _tk_drill_default_idx = _tk_drill_floors.index(_tk_persisted_val)
                        else:
                            _tk_drill_default_idx = (
                                _tk_drill_floors.index(_tk_drill_default)
                                if _tk_drill_default in _tk_drill_floors
                                else 0
                            )

                        def _make_drill_tcs_on_change(_widget_key, _pkey):
                            def _on_change():
                                st.session_state[_pkey] = st.session_state[_widget_key]
                            return _on_change

                        _show_reset_btn = (
                            _tk_has_best
                            and _tk_persisted_val is not None
                            and _tk_persisted_val in _tk_drill_floors
                            and _tk_persisted_val != _tk_drill_default
                        )
                        if _show_reset_btn:
                            _drill_sel_col, _drill_btn_col = st.columns([3, 1])
                        else:
                            _drill_sel_col = st.container()
                            _drill_btn_col = None
                        with _drill_sel_col:
                            _tk_drill_floor = st.selectbox(
                                "Show trades with TCS ≥",
                                options=_tk_drill_floors,
                                index=_tk_drill_default_idx,
                                key=f"drill_tcs_{_tk_name}",
                                on_change=_make_drill_tcs_on_change(
                                    f"drill_tcs_{_tk_name}", _tk_persist_key
                                ),
                                help=(
                                    "Select a TCS floor to view all individual trades for this "
                                    "ticker where TCS is at or above that cutoff."
                                ),
                            )
                        if _drill_btn_col is not None and _tk_drill_floor != _tk_drill_default:
                            with _drill_btn_col:
                                st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
                                _reset_key = f"_reset_best_tcs_{_tk_name}"
                                if st.button(
                                    "↩ Reset to best",
                                    key=_reset_key,
                                    use_container_width=True,
                                    help=f"Restore the recommended floor (TCS ≥ {_tk_drill_default}) for {_tk_name}.",
                                ):
                                    if _tk_persist_key in st.session_state:
                                        del st.session_state[_tk_persist_key]
                                    _drill_widget_key = f"drill_tcs_{_tk_name}"
                                    if _drill_widget_key in st.session_state:
                                        del st.session_state[_drill_widget_key]
                                    st.rerun()
                        _tk_drill_mask = (
                            (_bt_df["ticker"] == _tk_name)
                            & (_bt_df["tcs"].astype(float) >= _tk_drill_floor)
                        )
                        if "actual_outcome" in _bt_df.columns:
                            _tk_drill_mask = (
                                _tk_drill_mask
                                & _bt_df["actual_outcome"].str.lower().str.contains(
                                    "bullish|bearish", na=False
                                )
                                & _bt_df["win_loss"].str.strip().isin(["Win", "Loss"])
                            )
                        _tk_drill_df = _bt_df[_tk_drill_mask].copy()
                        if _tk_drill_df.empty:
                            st.info(
                                f"No trades found for {_tk_name} with TCS ≥ {_tk_drill_floor}.",
                                icon="ℹ️",
                            )
                        else:
                            _tk_d_wins  = (_tk_drill_df["win_loss"] == "Win").sum()
                            _tk_d_total = len(_tk_drill_df)
                            _tk_d_wr    = _tk_d_wins / _tk_d_total * 100
                            _tk_d_ft_col = (
                                "follow_thru_pct"
                                if "follow_thru_pct" in _tk_drill_df.columns
                                else "aft_move_pct"
                            )
                            _tk_d_pnl = sum(
                                _tk_pos_size
                                * abs(float(ft) if ft == ft else 0)
                                / 100
                                * (1 if wl == "Win" else -1)
                                for ft, wl in zip(
                                    _tk_drill_df[_tk_d_ft_col].fillna(0),
                                    _tk_drill_df["win_loss"],
                                )
                            )
                            _tk_d_sign = "+" if _tk_d_pnl >= 0 else ""
                            st.caption(
                                f"**{_tk_d_total}** trades · "
                                f"**{_tk_d_wins}W / {_tk_d_total - _tk_d_wins}L** · "
                                f"**{_tk_d_wr:.1f}% WR** · "
                                f"Net P&L: **{_tk_d_sign}${_tk_d_pnl:,.0f}**"
                            )
                            _tk_date_col = (
                                "sim_date"
                                if "sim_date" in _tk_drill_df.columns
                                else "trade_date"
                            )
                            _tk_drill_display = _tk_drill_df[
                                [_tk_date_col, "tcs", "predicted", "actual_outcome",
                                 _tk_d_ft_col, "win_loss"]
                            ].copy()
                            _tk_drill_display.columns = [
                                "Date", "TCS", "Prediction", "EOD Reality",
                                "Follow-Thru %", "Result",
                            ]
                            # Attach optional extra columns from the raw drill frame
                            _extra_col_map = {
                                "ticker":          "Ticker",
                                "open_price":      "Open Price",
                                "ib_high":         "IB High",
                                "ib_low":          "IB Low",
                                "false_break_up":  "False Break Up",
                                "false_break_down":"False Break Down",
                                "eod_pnl_r":       "EOD Hold R",
                                "tiered_pnl_r":    "Tiered Exit R",
                            }
                            for _raw_col, _display_col in _extra_col_map.items():
                                if _raw_col in _tk_drill_df.columns:
                                    _tk_drill_display[_display_col] = _tk_drill_df[_raw_col].values
                            _tk_drill_display["Date"] = (
                                _tk_drill_display["Date"].astype(str).str[:10]
                            )
                            _tk_drill_display["TCS"] = (
                                _tk_drill_display["TCS"].astype(float).astype(int)
                            )
                            _tk_drill_display["Prediction"] = (
                                _tk_drill_display["Prediction"].apply(_clean_structure_label)
                            )
                            _tk_drill_display = _tk_drill_display.sort_values(
                                "Date", ascending=False
                            )
                            # Build an HTML table so the "View in Log" anchor links
                            # work as true same-page navigation (LinkColumn opens new tabs).
                            _dd_rows_html = ""
                            for _, _dd_row in _tk_drill_display.iterrows():
                                _dd_date   = str(_dd_row["Date"])
                                _dd_tcs    = int(_dd_row["TCS"])
                                _dd_pred   = str(_dd_row["Prediction"])
                                _dd_eod    = str(_dd_row["EOD Reality"])
                                _dd_ft     = _dd_row["Follow-Thru %"]
                                _dd_ft_str = f"{float(_dd_ft):.1f}%" if _dd_ft == _dd_ft else "—"
                                _dd_wl     = str(_dd_row["Result"])
                                _dd_wl_clr = "#4caf50" if _dd_wl == "Win" else "#ef5350"
                                _dd_anchor = f"trade-{_tk_name.lower()}-{_dd_date}"
                                _dd_rows_html += (
                                    f'<tr style="border-bottom:1px solid #0d2137;">'
                                    f'<td style="padding:6px 8px;">{_html.escape(_dd_date)}</td>'
                                    f'<td style="padding:6px 8px;">{_dd_tcs}</td>'
                                    f'<td style="padding:6px 8px;">{_html.escape(_dd_pred)}</td>'
                                    f'<td style="padding:6px 8px;">{_html.escape(_dd_eod)}</td>'
                                    f'<td style="padding:6px 8px;">{_html.escape(_dd_ft_str)}</td>'
                                    f'<td style="padding:6px 8px;color:{_dd_wl_clr};font-weight:700;">{_html.escape(_dd_wl)}</td>'
                                    f'<td style="padding:6px 8px;">'
                                    f'<a href="#{_dd_anchor}" '
                                    f'style="color:#90caf9;font-size:11px;text-decoration:none;" '
                                    f'title="Scroll to this trade in the main log below">'
                                    f'&#8595; Jump</a></td>'
                                    f'</tr>'
                                )
                            st.markdown(
                                f'<div style="overflow-x:auto;">'
                                f'<table style="width:100%;border-collapse:collapse;'
                                f'font-size:12px;font-family:monospace;color:#cfd8dc;">'
                                f'<thead><tr style="color:#1565c0;font-size:10px;'
                                f'text-transform:uppercase;letter-spacing:0.8px;'
                                f'border-bottom:1px solid #0d2137;">'
                                f'<th style="padding:4px 8px;text-align:left;">Date</th>'
                                f'<th style="padding:4px 8px;text-align:left;">TCS</th>'
                                f'<th style="padding:4px 8px;text-align:left;">Prediction</th>'
                                f'<th style="padding:4px 8px;text-align:left;">EOD Reality</th>'
                                f'<th style="padding:4px 8px;text-align:left;">Follow-Thru %</th>'
                                f'<th style="padding:4px 8px;text-align:left;">Result</th>'
                                f'<th style="padding:4px 8px;text-align:left;">View in Log</th>'
                                f'</tr></thead>'
                                f'<tbody>{_dd_rows_html}</tbody>'
                                f'</table></div>',
                                unsafe_allow_html=True,
                            )
                            _csv_default_cols = [
                                "Date", "TCS", "Prediction",
                                "EOD Reality", "Follow-Thru %", "Result",
                            ]
                            _csv_all_cols = [
                                c for c in _tk_drill_display.columns
                                if c in _csv_default_cols
                                or c in ["Ticker", "Open Price", "IB High",
                                         "IB Low", "False Break Up", "False Break Down"]
                            ]
                            _csv_ms_key = f"csv_cols_{_tk_name}_{_tk_drill_floor}"
                            import streamlit.components.v1 as _cmp_csv_write
                            if _csv_ms_key not in st.session_state:
                                _saved_pref = st.session_state.get("_csv_cols_pref", _csv_default_cols)
                                _restored = [c for c in _saved_pref if c in _csv_all_cols]
                                st.session_state[_csv_ms_key] = _restored if _restored else _csv_default_cols
                            _csv_col_ms, _csv_col_rst = st.columns([5, 1])
                            with _csv_col_ms:
                                _csv_sel_cols = st.multiselect(
                                    "Columns to include in CSV export",
                                    options=_csv_all_cols,
                                    default=_csv_default_cols,
                                    key=_csv_ms_key,
                                    help="Choose which columns appear in the downloaded CSV file.",
                                )
                            with _csv_col_rst:
                                st.write("")
                                if st.button(
                                    "↩ Reset to defaults",
                                    key=f"csv_reset_btn_{_tk_name}_{_tk_drill_floor}",
                                    help="Clear the saved column preference and reset to the default 6 columns: Date, TCS, Prediction, EOD Reality, Follow-Thru %, Result.",
                                ):
                                    st.session_state["_csv_global_reset"] = True
                                    st.rerun()
                            if not _csv_just_reset:
                                st.session_state["_csv_cols_pref"] = _csv_sel_cols
                                _csv_cols_joined = ",".join(_csv_sel_cols)
                                if st.query_params.get("csv_cols") != _csv_cols_joined:
                                    st.query_params["csv_cols"] = _csv_cols_joined
                                _cmp_csv_write.html(
                                    f"<script>localStorage.setItem('csv_cols_pref',"
                                    f" {repr(_csv_cols_joined)});</script>",
                                    height=0,
                                )
                            # ── R-stats: compute once, always shown ──────────────
                            _dd_r_col = (
                                "eod_pnl_r" if "eod_pnl_r" in _tk_drill_df.columns
                                else ("tiered_pnl_r" if "tiered_pnl_r" in _tk_drill_df.columns else None)
                            )
                            if _dd_r_col is not None:
                                _dd_r_ser      = pd.to_numeric(_tk_drill_df[_dd_r_col], errors="coerce").dropna()
                                _dd_n          = len(_dd_r_ser)
                                _dd_fb_n       = 0
                                if "false_break_up" in _tk_drill_df.columns:
                                    _dd_fb_n = (
                                        _tk_drill_df["false_break_up"].fillna(False).astype(bool).sum()
                                        + _tk_drill_df.get("false_break_down", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()
                                    )
                                _dd_fb_rate    = round(_dd_fb_n / _dd_n * 100, 1) if _dd_n else 0
                                _dd_avg_win_r  = round(_dd_r_ser[_dd_r_ser > 0].mean(), 2) if (_dd_r_ser > 0).any() else 0
                                _dd_avg_loss_r = round(_dd_r_ser[_dd_r_ser < 0].mean(), 2) if (_dd_r_ser < 0).any() else 0
                                _dd_exp_r      = round(_dd_r_ser.mean(), 3) if _dd_n else 0
                                _dd_cum_r_v    = _dd_r_ser.cumsum().reset_index(drop=True)
                                _dd_peak_r     = _dd_cum_r_v.cummax()
                                _dd_max_dd_r   = round((_dd_cum_r_v - _dd_peak_r).min(), 2) if _dd_n else 0
                                # ── Visual R-stats metric chips ───────────────────
                                _dd_exp_clr  = "#66bb6a" if _dd_exp_r >= 0 else "#ef5350"
                                _dd_sor_clr  = "#ffb300" if _dd_fb_rate >= 30 else "#90a4ae"
                                _dd_r_label  = "EOD Hold R" if _dd_r_col == "eod_pnl_r" else "Tiered Exit R"
                                st.markdown(
                                    f'<div style="margin:10px 0 6px 0;">'
                                    f'<span style="font-size:10px;font-weight:700;'
                                    f'color:#90caf9;letter-spacing:0.8px;'
                                    f'text-transform:uppercase;">R-Stats ({_dd_r_label})</span>'
                                    f'</div>'
                                    f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">'
                                    f'<div style="background:rgba(255,179,0,0.10);border:1px solid rgba(255,179,0,0.25);'
                                    f'border-radius:6px;padding:7px 13px;min-width:110px;text-align:center;">'
                                    f'<div style="font-size:9px;color:#90a4ae;letter-spacing:0.7px;'
                                    f'text-transform:uppercase;margin-bottom:3px;">Stop-Out Rate</div>'
                                    f'<div style="font-size:18px;font-weight:700;color:{_dd_sor_clr};">'
                                    f'{_dd_fb_rate}%</div></div>'
                                    f'<div style="background:rgba(76,175,80,0.10);border:1px solid rgba(76,175,80,0.25);'
                                    f'border-radius:6px;padding:7px 13px;min-width:110px;text-align:center;">'
                                    f'<div style="font-size:9px;color:#90a4ae;letter-spacing:0.7px;'
                                    f'text-transform:uppercase;margin-bottom:3px;">Avg Win R</div>'
                                    f'<div style="font-size:18px;font-weight:700;color:#66bb6a;">'
                                    f'+{_dd_avg_win_r}R</div></div>'
                                    f'<div style="background:rgba(239,83,80,0.10);border:1px solid rgba(239,83,80,0.25);'
                                    f'border-radius:6px;padding:7px 13px;min-width:110px;text-align:center;">'
                                    f'<div style="font-size:9px;color:#90a4ae;letter-spacing:0.7px;'
                                    f'text-transform:uppercase;margin-bottom:3px;">Avg Loss R</div>'
                                    f'<div style="font-size:18px;font-weight:700;color:#ef5350;">'
                                    f'{_dd_avg_loss_r}R</div></div>'
                                    f'<div style="background:rgba(144,164,174,0.08);border:1px solid rgba(144,164,174,0.20);'
                                    f'border-radius:6px;padding:7px 13px;min-width:110px;text-align:center;">'
                                    f'<div style="font-size:9px;color:#90a4ae;letter-spacing:0.7px;'
                                    f'text-transform:uppercase;margin-bottom:3px;">Expectancy</div>'
                                    f'<div style="font-size:18px;font-weight:700;color:{_dd_exp_clr};">'
                                    f'{_dd_exp_r:+.3f}R</div></div>'
                                    f'<div style="background:rgba(239,83,80,0.08);border:1px solid rgba(239,83,80,0.18);'
                                    f'border-radius:6px;padding:7px 13px;min-width:110px;text-align:center;">'
                                    f'<div style="font-size:9px;color:#90a4ae;letter-spacing:0.7px;'
                                    f'text-transform:uppercase;margin-bottom:3px;">Max Drawdown R</div>'
                                    f'<div style="font-size:18px;font-weight:700;color:#ef5350;">'
                                    f'{abs(_dd_max_dd_r)}R</div></div>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                            if not _csv_sel_cols:
                                st.warning("Select at least one column to enable the download.", icon="⚠️")
                            else:
                                _tk_csv_export = _tk_drill_display[
                                    [c for c in _csv_sel_cols if c in _tk_drill_display.columns]
                                ].copy()
                                _csv_round_1dp = {"Follow-Thru %"}
                                _csv_round_2dp = {"Open Price", "IB High", "IB Low", "EOD Hold R", "Tiered Exit R"}
                                for _ccol in _tk_csv_export.columns:
                                    if _ccol in _csv_round_1dp:
                                        _tk_csv_export[_ccol] = (
                                            pd.to_numeric(_tk_csv_export[_ccol], errors="coerce")
                                            .round(1)
                                        )
                                    elif _ccol in _csv_round_2dp:
                                        _tk_csv_export[_ccol] = (
                                            pd.to_numeric(_tk_csv_export[_ccol], errors="coerce")
                                            .round(2)
                                        )
                                    elif _ccol == "TCS":
                                        _tk_csv_export[_ccol] = (
                                            pd.to_numeric(_tk_csv_export[_ccol], errors="coerce")
                                            .astype("Int64")
                                        )
                                # ── R-stats summary block for drill-down CSV ─────────
                                _dd_csv_cols  = list(_tk_csv_export.columns)
                                _dd_lbl_col   = _dd_csv_cols[0]
                                _dd_val_col   = _dd_csv_cols[1] if len(_dd_csv_cols) > 1 else _dd_csv_cols[0]
                                if _dd_r_col is not None:
                                    def _dd_stat_r(lbl, val):
                                        _rx = {c: "" for c in _dd_csv_cols}
                                        _rx[_dd_lbl_col] = lbl
                                        _rx[_dd_val_col] = val
                                        return _rx
                                    _dd_summ_rows = [
                                        {c: "" for c in _dd_csv_cols},
                                        _dd_stat_r("--- R-STATS SUMMARY ---", ""),
                                        _dd_stat_r("Stop-Out Rate", f"{_dd_fb_rate}%"),
                                        _dd_stat_r("Avg Win (R)", f"+{_dd_avg_win_r}R"),
                                        _dd_stat_r("Avg Loss (R)", f"{_dd_avg_loss_r}R"),
                                        _dd_stat_r("Expectancy", f"{_dd_exp_r:+.3f}R/trade"),
                                        _dd_stat_r("Max Drawdown (R)", f"{abs(_dd_max_dd_r)}R"),
                                    ]
                                    _dd_csv_final  = pd.concat(
                                        [_tk_csv_export, pd.DataFrame(_dd_summ_rows)],
                                        ignore_index=True,
                                    )
                                    _tk_csv_bytes  = _dd_csv_final.to_csv(index=False).encode("utf-8")
                                else:
                                    _tk_csv_bytes  = _tk_csv_export.to_csv(index=False).encode("utf-8")
                                st.download_button(
                                    label="⬇ Download CSV",
                                    data=_tk_csv_bytes,
                                    file_name=f"{_tk_name}_TCS{_tk_drill_floor}_trades.csv",
                                    mime="text/csv",
                                    key=f"dl_csv_{_tk_name}_{_tk_drill_floor}",
                                    help=f"Download all {_tk_d_total} filtered trades for {_tk_name} (TCS ≥ {_tk_drill_floor}) as a CSV file — includes R-stats summary block at the bottom",
                                )

                # ── Export All Tickers Sweep CSV ──────────────────────────────────
                # Build a ticker → best TCS floor lookup from the earlier sweep
                _best_tcs_map = {_btk: _bfloor for _btk, _bfloor in _best_tcs_options}
                _all_sweep_frames = []
                for _exp_tk, _exp_rows in _tkr_sweep_data.items():
                    _exp_df = _pd_bt.DataFrame(_exp_rows)
                    _exp_df.insert(0, "Ticker", _exp_tk)
                    _exp_best_floor = _best_tcs_map.get(_exp_tk)
                    if _exp_best_floor is not None:
                        _exp_df["Best TCS Floor"] = _exp_best_floor
                        _exp_df["Recommended"] = _exp_df["TCS Floor"].apply(
                            lambda _f: "✓" if int(_f) == _exp_best_floor else ""
                        )
                    else:
                        _exp_df["Best TCS Floor"] = ""
                        _exp_df["Recommended"] = ""
                    _all_sweep_frames.append(_exp_df)
                if _all_sweep_frames:
                    _all_sweep_df = _pd_bt.concat(_all_sweep_frames, ignore_index=True)
                    st.markdown("<br>", unsafe_allow_html=True)
                    if "_sweep_export_sufficient_only" not in st.session_state:
                        st.session_state["_sweep_export_sufficient_only"] = (
                            st.query_params.get("sweep_suf_only", "0") == "1"
                        )
                    _sweep_suf_only = st.checkbox(
                        "Sufficient floors only",
                        key="_sweep_export_sufficient_only",
                        help=(
                            "When checked, the exported CSV only includes rows marked ✓ "
                            "(enough trades to be statistically actionable). "
                            "Rows marked ✗ (insufficient trades) are excluded."
                        ),
                    )
                    if st.query_params.get("sweep_suf_only") != ("1" if _sweep_suf_only else "0"):
                        st.query_params["sweep_suf_only"] = "1" if _sweep_suf_only else "0"
                    if _sweep_suf_only:
                        _all_sweep_export_df = _all_sweep_df[
                            _all_sweep_df["Sufficient"] == "✓"
                        ].reset_index(drop=True)
                        _sweep_btn_label = "⬇️ Download All Tickers Sweep CSV (sufficient only)"
                        _sweep_fname = "sweep_summary_all_sufficient.csv"
                    else:
                        _all_sweep_export_df = _all_sweep_df
                        _sweep_btn_label = "⬇️ Download All Tickers Sweep CSV"
                        _sweep_fname = "sweep_summary_all.csv"
                    # ── Build combined CSV with per-ticker R-stats blocks ─────────
                    _all_exp_cols = list(_all_sweep_export_df.columns)
                    _all_lbl_col  = _all_exp_cols[0]
                    _all_val_col  = _all_exp_cols[1] if len(_all_exp_cols) > 1 else _all_exp_cols[0]
                    def _all_stat_r(_lbl, _val):
                        _rx = {_c: "" for _c in _all_exp_cols}
                        _rx[_all_lbl_col] = _lbl
                        _rx[_all_val_col] = _val
                        return _rx
                    _all_csv_parts = []
                    for _exp_tk in _all_sweep_export_df["Ticker"].unique():
                        _tk_part = _all_sweep_export_df[_all_sweep_export_df["Ticker"] == _exp_tk]
                        _all_csv_parts.append(_tk_part)
                        _sw_tgrp2 = _bt_df[_bt_df["ticker"] == _exp_tk]
                        if "actual_outcome" in _sw_tgrp2.columns:
                            _sw2_mask = (
                                _sw_tgrp2["actual_outcome"].str.lower().str.contains("bullish|bearish", na=False)
                                & _sw_tgrp2["win_loss"].str.strip().isin(["Win", "Loss"])
                            )
                        elif "win_loss" in _sw_tgrp2.columns:
                            _sw2_mask = _sw_tgrp2["win_loss"].str.strip().isin(["Win", "Loss"])
                        else:
                            _sw2_mask = _pd_bt.Series([True] * len(_sw_tgrp2), index=_sw_tgrp2.index)
                        _sw_tgrp2 = _sw_tgrp2[_sw2_mask]
                        _sw2_r_col = (
                            "eod_pnl_r" if "eod_pnl_r" in _sw_tgrp2.columns
                            else ("tiered_pnl_r" if "tiered_pnl_r" in _sw_tgrp2.columns else None)
                        )
                        if _sw2_r_col is not None:
                            _sw2_r_ser      = _pd_bt.to_numeric(_sw_tgrp2[_sw2_r_col], errors="coerce").dropna()
                            _sw2_n          = len(_sw2_r_ser)
                            _sw2_fb_n       = 0
                            if "false_break_up" in _sw_tgrp2.columns:
                                _sw2_fb_n = (
                                    _sw_tgrp2["false_break_up"].fillna(False).astype(bool).sum()
                                    + _sw_tgrp2.get("false_break_down", _pd_bt.Series(dtype=bool)).fillna(False).astype(bool).sum()
                                )
                            _sw2_fb_rate    = round(_sw2_fb_n / _sw2_n * 100, 1) if _sw2_n else 0
                            _sw2_avg_win_r  = round(_sw2_r_ser[_sw2_r_ser > 0].mean(), 2) if (_sw2_r_ser > 0).any() else 0
                            _sw2_avg_loss_r = round(_sw2_r_ser[_sw2_r_ser < 0].mean(), 2) if (_sw2_r_ser < 0).any() else 0
                            _sw2_exp_r      = round(_sw2_r_ser.mean(), 3) if _sw2_n else 0
                            _sw2_cum_r      = _sw2_r_ser.cumsum().reset_index(drop=True)
                            _sw2_peak_r     = _sw2_cum_r.cummax()
                            _sw2_max_dd_r   = round((_sw2_cum_r - _sw2_peak_r).min(), 2) if _sw2_n else 0
                            _sw2_summ_rows  = [
                                {_c: "" for _c in _all_exp_cols},
                                _all_stat_r(f"--- R-STATS SUMMARY ({_exp_tk}) ---", ""),
                                _all_stat_r("Stop-Out Rate",    f"{_sw2_fb_rate}%"),
                                _all_stat_r("Avg Win (R)",      f"+{_sw2_avg_win_r}R"),
                                _all_stat_r("Avg Loss (R)",     f"{_sw2_avg_loss_r}R"),
                                _all_stat_r("Expectancy",       f"{_sw2_exp_r:+.3f}R/trade"),
                                _all_stat_r("Max Drawdown (R)", f"{abs(_sw2_max_dd_r)}R"),
                            ]
                            _all_csv_parts.append(_pd_bt.DataFrame(_sw2_summ_rows))
                        _all_csv_parts.append(_pd_bt.DataFrame([{_c: "" for _c in _all_exp_cols}]))
                    _all_sweep_csv_data = (
                        _pd_bt.concat(_all_csv_parts, ignore_index=True).to_csv(index=False)
                        if _all_csv_parts
                        else _all_sweep_export_df.to_csv(index=False)
                    )
                    st.download_button(
                        label=_sweep_btn_label,
                        data=_all_sweep_csv_data,
                        file_name=_sweep_fname,
                        mime="text/csv",
                        key="_dl_sweep_all_tickers",
                        help=(
                            "Download a single combined CSV with every ticker's TCS sweep results. "
                            "Includes 'Best TCS Floor' and 'Recommended' columns, plus a per-ticker "
                            "R-stats summary block (Stop-Out Rate, Avg Win R, Avg Loss R, Expectancy, "
                            "Max Drawdown R) appended after each ticker's rows."
                        ),
                    )

    st.markdown("---")
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; margin-bottom:10px; font-weight:700;">'
        '📋 Trade-by-Trade Simulation Log</div>',
        unsafe_allow_html=True,
    )

    _BT_COLS  = [0.55, 0.6, 0.65, 0.75, 0.55, 1.1, 1.2, 1.0, 0.75, 0.45, 0.55]
    _BT_HDRS  = ["Date", "Ticker", "Open", "IB Range", "TCS", "Morning Prediction",
                 "EOD Reality", "Close", "Follow-Thru", "⚠", "Result"]

    _hdr_row = st.columns(_BT_COLS)
    for _col, _lbl in zip(_hdr_row, _BT_HDRS):
        _hdr_clr = "#1565c0"
        _col.markdown(
            f'<div style="font-size:10px; font-weight:700; color:{_hdr_clr}; '
            f'text-transform:uppercase; letter-spacing:0.8px; padding:4px 0 8px 0; '
            f'border-bottom:1px solid #0d2137; font-family:monospace;">{_lbl}</div>',
            unsafe_allow_html=True,
        )

    # ── Data rows ───────────────────────────────────────────────────────────────
    for _r in _results:
        _wl         = _r["win_loss"]
        _tcs        = _r["tcs"]
        _tcs_clr    = _bt_tcs_color(_tcs)
        _out_clr    = _bt_outcome_color(_r["actual_outcome"])
        _wl_clr     = _BT_WIN_CLR if _wl == "Win" else _BT_LOSS_CLR
        _move       = _r["aft_move_pct"]
        _move_sign  = "+" if _move >= 0 else ""
        _move_clr   = "#4caf50" if _move >= 0 else "#ef5350"
        _pred_short = (_r["predicted"][:18] + "…") if len(_r["predicted"]) > 18 else _r["predicted"]
        _pred_clean = _clean_structure_label(_r["predicted"])
        _sim_date_raw = str(_r.get("sim_date", _r.get("trade_date", "")))
        _sim_date_disp = _sim_date_raw[:10] if len(_sim_date_raw) >= 10 else _sim_date_raw

        _row_bg = "#051015" if _wl == "Win" else "#120508"

        _row_anchor = f"trade-{_r['ticker'].lower()}-{_sim_date_disp}"
        _row = st.columns(_BT_COLS)
        _row[0].markdown(
            f'<div id="{_row_anchor}" style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:10px; color:#546e7a; font-family:monospace;">'
            f'{_sim_date_disp}</div>',
            unsafe_allow_html=True,
        )
        _row[1].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:14px; font-weight:900; color:#e0e0e0; '
            f'font-family:monospace;">{_r["ticker"]}</div>',
            unsafe_allow_html=True,
        )
        _row[2].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:12px; color:#90a4ae; font-family:monospace;">'
            f'${_r["open_price"]:.2f}</div>',
            unsafe_allow_html=True,
        )
        _row[3].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:11px; color:#607d8b; font-family:monospace;">'
            f'${_r["ib_low"]:.2f}–${_r["ib_high"]:.2f}</div>',
            unsafe_allow_html=True,
        )
        _row[4].markdown(
            f'<div style="background:{_row_bg}; padding:8px 6px;">'
            f'<span style="background:{_tcs_clr}22; color:{_tcs_clr}; '
            f'font-size:13px; font-weight:800; padding:2px 8px; border-radius:10px; '
            f'border:1px solid {_tcs_clr}55; font-family:monospace;">'
            f'{_tcs:.0f}</span></div>',
            unsafe_allow_html=True,
        )
        _row[5].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:11px; color:#ce93d8; font-weight:600;">'
            f'{_pred_clean}</div>',
            unsafe_allow_html=True,
        )
        _row[6].markdown(
            f'<div style="background:{_row_bg}; padding:8px 6px;">'
            f'<span style="background:{_out_clr}22; color:{_out_clr}; '
            f'font-size:11px; font-weight:700; padding:3px 10px; border-radius:10px; '
            f'border:1px solid {_out_clr}55;">'
            f'{_r["actual_icon"]} {_r["actual_outcome"]}</span></div>',
            unsafe_allow_html=True,
        )
        _row[7].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:12px; color:#90a4ae; font-family:monospace;">'
            f'${_r["close_price"]:.2f}</div>',
            unsafe_allow_html=True,
        )
        _row[8].markdown(
            f'<div style="background:{_row_bg}; padding:10px 6px; '
            f'font-size:12px; font-weight:700; color:{_move_clr}; font-family:monospace;">'
            f'{_move_sign}{_move:.1f}%</div>',
            unsafe_allow_html=True,
        )
        _fb_icon = ""
        if _r.get("false_break_up"):
            _fb_icon = '<span title="False bullish break — reversed within 30 min" ' \
                       'style="color:#ffa726; font-size:14px;">⚠↑</span>'
        elif _r.get("false_break_down"):
            _fb_icon = '<span title="False bearish break — reversed within 30 min" ' \
                       'style="color:#ffa726; font-size:14px;">⚠↓</span>'
        _row[9].markdown(
            f'<div style="background:{_row_bg}; padding:10px 4px; text-align:center;">'
            f'{_fb_icon}</div>',
            unsafe_allow_html=True,
        )
        _row[10].markdown(
            f'<div style="background:{_row_bg}; padding:8px 6px;">'
            f'<span style="background:{_wl_clr}22; color:{_wl_clr}; '
            f'font-size:11px; font-weight:800; padding:3px 10px; border-radius:10px; '
            f'border:1px solid {_wl_clr}55; text-transform:uppercase;">'
            f'{_wl}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="border-bottom:1px solid #060f18; margin:0;"></div>',
            unsafe_allow_html=True,
        )

    # ── Footer legend ────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-top:20px; font-size:10px; color:#263238; font-family:monospace;">'
        'WIN LOGIC: Trend/Nrml Var → one IB side breaks · '
        'Ntrl Extreme → any IB break (high-vol) · '
        'Non-Trend/Normal → stays inside IB · '
        'Neutral/Dbl Dist → any break · '
        'Follow-Thru = (Best post-IB point − IB boundary broken) / IB boundary · IB = 9:30–10:30 AM ET'
        '</div>',
        unsafe_allow_html=True,
    )


_BATCH_DEFAULT = """\
3/30: ANNA, SST, ASTC, BFRG, UGRO, JCSE, EEIQ, ELAB
3/27: VSA, ARTL, GVH, GCTK
3/26: AIFF, EEIQ, GLND, FCHL, VSA
3/25: VCX, RMSG, UGRO, SATL, CODX, QNRX, FEED
3/24: SATL, FEED, RBNE, PAVS, VCX, UGRO, ANNA
3/23: AHMA, UGRO, VCX, BIAF, PTLE
3/20: ARTL, ANNA, CODX
3/19: LNKS, SWMR, ACXP, GOAI, SER, VCX, CHNR
3/18: ARTL, AIM, SWMR, MTVA
3/17: UCAR, LNAI, BIAF, CREG, EDSA, SWMR
3/16: WNW, HCWB"""


def render_analytics_tab():
    """Render the 📊 Analytics & Edge dashboard tab."""
    import plotly.graph_objects as _go
    from plotly.subplots import make_subplots as _make_subplots

    st.markdown("## 📊 Analytics & Edge")
    st.caption("Live edge stats computed from your trade journal and accuracy tracker.")

    with st.expander("📋 What this tab shows and how to read it", expanded=False):
        st.markdown(
            """
**This tab is read-only. It shows your personal edge data derived from your real trades.**

Nothing here requires any input from you. All numbers update automatically as you add trades in the Journal tab.

---

**What you'll find here:**

| Section | What it shows |
|---|---|
| **KPI cards** (top) | Total trades, win rate, avg P&L, avg TCS — your headline stats |
| **Pattern Correlation** | How your win rate varies by structure type, TCS range, and IB entry position |
| **Saved Predictions** | Your Predict All outputs grouped by date — what the model said vs what happened |
| **Trade Timing & Ticker Edge** | Win rate by entry hour, day of week, and ticker — tells you WHEN and WHERE you perform best |
| **Win Rate by Hold Type** | Intraday vs Overnight vs Multi-day — tells you which style you're actually good at |
| **Hold-Type Signal Profile** | For each hold style, the TCS/RVOL/structure fingerprint of your winning vs losing trades |

---

**When should you act on this data?**
- With fewer than 20 trades, treat it as directional only — not statistically reliable yet
- At 30+ trades, the timing and structure breakdowns start to be genuinely useful
- At 50+ trades, the hold-type fingerprint becomes an actual decision rule

**How to get more data in here:**
→ Go to the **📖 Journal** tab → import your Webull CSV → click "🔄 Sync Journal → Analytics" if prompted
            """,
        )

    _uid = st.session_state.get("auth_user_id", "")
    journal_df = _cached_load_journal(user_id=_uid)
    tracker_df = _cached_load_accuracy_tracker(user_id=_uid)

    with st.spinner("Computing edge analytics…"):
        ana = compute_edge_analytics(journal_df, tracker_df)

    s = ana["summary"]
    no_data = s["total_trades"] == 0

    # ── KPI CARDS ──────────────────────────────────────────────────────────
    def _kpi(label, value, color="#e0e0e0", sub=""):
        _sub_html = (
            '<div style="font-size:10px;color:#666;margin-top:2px;">' + sub + '</div>'
            if sub else ""
        )
        return (
            f'<div style="background:#12122288; border:1px solid #2a2a4a; '
            f'border-radius:10px; padding:14px 18px; text-align:center;">'
            f'<div style="font-size:10px; color:#5c6bc0; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:4px;">{label}</div>'
            f'<div style="font-size:28px; font-weight:900; color:{color};">{value}</div>'
            f'{_sub_html}'
            f'</div>'
        )

    wr        = s["win_rate"]
    wr_color  = "#4caf50" if wr >= 55 else ("#ffa726" if wr >= 45 else "#ef5350")
    pnl_color = "#4caf50" if s["total_pnl"] >= 0 else "#ef5350"
    pf_color  = "#4caf50" if s["profit_factor"] >= 1.5 else ("#ffa726" if s["profit_factor"] >= 1.0 else "#ef5350")
    pnl_sign  = "+" if s["total_pnl"] >= 0 else ""

    kc1, kc2, kc3, kc4 = st.columns(4)
    with kc1:
        st.markdown(_kpi("Win Rate", f"{wr}%", wr_color,
                         f"{s['total_trades']} trades · {s['trade_days']} days"),
                    unsafe_allow_html=True)
    with kc2:
        st.markdown(_kpi("Total P&L",
                         f"{pnl_sign}${s['total_pnl']:.2f}", pnl_color,
                         f"avg win ${s['avg_win']:.2f} / avg loss ${s['avg_loss']:.2f}"),
                    unsafe_allow_html=True)
    with kc3:
        pf_str = f"{s['profit_factor']:.2f}" if s['profit_factor'] < 99 else "∞"
        st.markdown(_kpi("Profit Factor", pf_str, pf_color,
                         "gross win / gross loss"),
                    unsafe_allow_html=True)
    with kc4:
        j_count = len(journal_df) if not journal_df.empty else 0
        st.markdown(_kpi("Journal Entries", str(j_count), "#90caf9",
                         f"{s['total_trades']} synced to tracker"),
                    unsafe_allow_html=True)

    # ── JOURNAL × MODEL CROSS-REFERENCE (runs regardless of tracker state) ────
    st.markdown("---")
    st.markdown("### 🔬 Personal Trades × Model Predictions — Cross-Reference")
    st.caption(
        "Joins your journal trades to the model's structure call on that same day & ticker. "
        "Shows whether the model was warning you on days you lost."
    )

    @st.cache_data(ttl=300, show_spinner=False)
    def _load_xref_bt_hist(uid):
        return load_backtest_sim_history(user_id=uid)

    _xref_bt_df = _load_xref_bt_hist(uid=_uid)

    _xref = compute_journal_model_crossref(journal_df, _xref_bt_df)

    if not _xref["by_structure"] and _xref["unmatched_n"] == 0 and journal_df.empty:
        st.info("Import your trades and run Brain Calibration first — both datasets are needed.")
    elif not _xref["by_structure"]:
        _total_j = len(journal_df) if not journal_df.empty else 0
        st.warning(
            f"Your journal has {_total_j} entries but none matched to a model prediction yet. "
            f"Run Brain Calibration (22 days) so the model has predictions for the same "
            f"ticker/date combinations as your trades."
        )
        if _xref["unmatched_n"] > 0:
            st.caption(f"{_xref['unmatched_n']} journal trades have no matching model session.")
    else:
        _xr_al = _xref["alignment"]
        _al_color = "#4caf50" if _xr_al >= 70 else ("#ffa726" if _xr_al >= 40 else "#ef5350")
        _al_label = "Model was correctly warning you" if _xr_al >= 60 else (
            "Partial alignment" if _xr_al >= 40 else "Model missed these losses")
        _fs = _xref["filter_sim"]
        _blocked_n = _fs.get("blocked_n", 0)
        _allowed_n = _fs.get("allowed_n", 0)
        _total_matched = _blocked_n + _allowed_n
        _block_pct = round(_blocked_n / _total_matched * 100) if _total_matched else 0

        _xc1, _xc2, _xc3 = st.columns(3)
        with _xc1:
            st.markdown(
                f'<div style="background:#12122288; border:1px solid #2a2a4a; '
                f'border-radius:10px; padding:14px 18px; text-align:center;">'
                f'<div style="font-size:10px; color:#5c6bc0; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:4px;">Model Alignment on Losses</div>'
                f'<div style="font-size:32px; font-weight:900; color:{_al_color};">{_xr_al}%</div>'
                f'<div style="font-size:10px; color:#666; margin-top:2px;">{_al_label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _xc2:
            st.markdown(
                f'<div style="background:#12122288; border:1px solid #2a2a4a; '
                f'border-radius:10px; padding:14px 18px; text-align:center;">'
                f'<div style="font-size:10px; color:#5c6bc0; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:4px;">Trades Filter Would Block</div>'
                f'<div style="font-size:32px; font-weight:900; color:#ef5350;">'
                f'{_blocked_n}<span style="font-size:14px; color:#666;"> / {_total_matched}</span></div>'
                f'<div style="font-size:10px; color:#666; margin-top:2px;">'
                f'{_block_pct}% of matched trades · non-Neutral + TCS≥75 filter</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _xc3:
            _pnl_blocked = _fs.get("pnl_blocked", 0.0)
            _pnl_color = "#4caf50" if _pnl_blocked >= 0 else "#ef5350"
            _pnl_sign  = "+" if _pnl_blocked >= 0 else ""
            _df_blk = _fs.get("d_f_blocked_pct", 0.0)
            st.markdown(
                f'<div style="background:#12122288; border:1px solid #2a2a4a; '
                f'border-radius:10px; padding:14px 18px; text-align:center;">'
                f'<div style="font-size:10px; color:#5c6bc0; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:4px;">P&L of Blocked Trades</div>'
                f'<div style="font-size:32px; font-weight:900; color:{_pnl_color};">'
                f'{_pnl_sign}${abs(_pnl_blocked):.0f}</div>'
                f'<div style="font-size:10px; color:#666; margin-top:2px;">'
                f'{_df_blk}% of blocked were D/F grade</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        _xref_rows = []
        for _s in _xref["by_structure"]:
            _gc  = _s["grade_counts"]
            _grade_order = ["A", "B", "C", "D", "F", "?"]
            _grade_str = " · ".join(
                f'{g}: {_gc[g]}' for g in _grade_order if g in _gc
            )
            _pnl_disp = f"${_s['avg_pnl_est']:+.2f}" if _s["avg_pnl_est"] is not None else "N/A"
            _xref_rows.append({
                "Model Called":    _s["structure"],
                "Your Trades":     _s["trades"],
                "Grade Breakdown": _grade_str,
                "D/F Rate":        f"{_s['d_f_pct']}%",
                "Avg P&L":         _pnl_disp,
            })
        st.dataframe(pd.DataFrame(_xref_rows), use_container_width=True, hide_index=True)

        _df_allowed = _fs.get("d_f_allowed_pct", 0.0)
        _df_blk2 = _fs.get("d_f_blocked_pct", 0.0)
        st.markdown(
            f'<div style="background:#0a1628; border-left:4px solid #1565c0; '
            f'padding:12px 16px; border-radius:4px; margin-top:8px; font-size:12px; color:#b0bec5;">'
            f'<b style="color:#90caf9;">Filter Simulation:</b> '
            f'If the non-Neutral + TCS≥75 filter had been active — '
            f'<b>{_blocked_n} trades would have been blocked</b> ({_df_blk2}% D/F grade) · '
            f'<b>{_allowed_n} would have been allowed</b> ({_df_allowed}% D/F grade). '
            f'Model alignment on your losing trades: '
            f'<b style="color:{_al_color};">{_xr_al}%</b> — '
            f'{"it was correctly flagging Neutral on most of your worst sessions." if _xr_al >= 60 else "filter needs further tuning before autonomous use."}'
            f'</div>',
            unsafe_allow_html=True,
        )
        if _xref["unmatched_n"] > 0:
            st.caption(
                f"⚠️ {_xref['unmatched_n']} journal trades had no matching model prediction. "
                f"Re-run calibration with more days to close this gap."
            )

        # ── Within-Neutral Quality Filter ─────────────────────────────────
        _nq = _xref.get("neutral_quality", {})
        if _nq.get("tcs_buckets") or _nq.get("ib_position"):
            st.markdown("---")
            st.markdown("#### 🎯 Within-Neutral Quality Filter")
            st.caption(
                "Since your tickers live almost entirely in Neutral/Ntrl Extreme days, "
                "the edge isn't in the structure — it's in the conditions *within* that structure. "
                "This shows what actually separated your wins from your losses."
            )

            _nq_col1, _nq_col2 = st.columns(2)

            # Detect if TCS data is entirely missing (table schema gap)
            _all_no_tcs = (
                _nq.get("tcs_buckets")
                and len(_nq["tcs_buckets"]) == 1
                and _nq["tcs_buckets"][0]["bucket"] == "No TCS"
            )
            if _all_no_tcs:
                st.warning(
                    "All trades are landing in **No TCS** — this means your "
                    "`backtest_sim_runs` table is missing the `tcs` column. "
                    "Go to the **Backtest tab** for the exact SQL to run in your "
                    "Supabase SQL editor, then re-run calibration once.",
                    icon="⚠️",
                )

            with _nq_col1:
                st.markdown("**By TCS Score (session conviction)**")
                if _nq.get("tcs_buckets"):
                    _tcs_rows = []
                    for _b in _nq["tcs_buckets"]:
                        _gc = _b["grade_counts"]
                        _grade_order = ["A", "B", "C", "D", "F", "?"]
                        _gs = " · ".join(f'{g}:{_gc[g]}' for g in _grade_order if g in _gc)
                        _ab_color = (
                            "🟢" if _b["ab_pct"] >= 60 else
                            "🟡" if _b["ab_pct"] >= 40 else "🔴"
                        )
                        _tcs_rows.append({
                            "TCS Range":     _b["bucket"],
                            "Trades":        _b["trades"],
                            "A/B Rate":      f"{_ab_color} {_b['ab_pct']}%",
                            "D/F Rate":      f"{_b['df_pct']}%",
                            "Grades":        _gs,
                        })
                    st.dataframe(pd.DataFrame(_tcs_rows),
                                 use_container_width=True, hide_index=True)
                else:
                    st.caption("No TCS data available for matched trades.")

            with _nq_col2:
                st.markdown("**By Entry Position vs IB Range**")
                if _nq.get("ib_position"):
                    _ib_rows = []
                    for _p in _nq["ib_position"]:
                        _gc = _p["grade_counts"]
                        _grade_order = ["A", "B", "C", "D", "F", "?"]
                        _gs = " · ".join(f'{g}:{_gc[g]}' for g in _grade_order if g in _gc)
                        _ab_color = (
                            "🟢" if _p["ab_pct"] >= 60 else
                            "🟡" if _p["ab_pct"] >= 40 else "🔴"
                        )
                        _ib_rows.append({
                            "Entry Position": _p["position"],
                            "Trades":         _p["trades"],
                            "A/B Rate":       f"{_ab_color} {_p['ab_pct']}%",
                            "D/F Rate":       f"{_p['df_pct']}%",
                            "Grades":         _gs,
                        })
                    st.dataframe(pd.DataFrame(_ib_rows),
                                 use_container_width=True, hide_index=True)
                else:
                    st.caption(
                        "IB position data not available — re-run calibration to populate "
                        "IB high/low for your tickers."
                    )

            if _nq.get("recommendation"):
                st.markdown(
                    f'<div style="background:#0d2137; border-left:4px solid #00e5ff; '
                    f'padding:12px 16px; border-radius:4px; margin-top:10px; '
                    f'font-size:13px; color:#e0e0e0;">'
                    f'<b style="color:#00e5ff;">Derived Rule:</b> {_nq["recommendation"]}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    if no_data:
        # If journal has entries but tracker is empty, offer one-click backfill
        _j_count = len(journal_df) if not journal_df.empty else 0
        if _j_count > 0:
            st.warning(
                f"Your journal has **{_j_count} entries** from your Webull import, but they haven't "
                f"been linked to the analytics engine yet. Click below to sync them now — this is a "
                f"one-time step for previously imported trades."
            )
            if st.button("🔄 Sync Journal → Analytics", type="primary",
                         key="analytics_backfill_btn"):
                _backfill_uid = st.session_state.get("auth_user_id", "")
                _backfill_count = 0
                _backfill_fail  = 0
                for _, _jrow in journal_df.iterrows():
                    try:
                        _bp   = float(str(_jrow.get("price", 0)).replace("$","") or 0)
                        # Try to extract exit price from notes ("Exit: $X.XXXX")
                        _bnotes = str(_jrow.get("notes", ""))
                        import re as _re
                        _ex_m = _re.search(r"Exit:\s*\$([0-9.]+)", _bnotes)
                        _bex  = float(_ex_m.group(1)) if _ex_m else 0.0
                        # Try to extract P&L ("P&L: $+X.XX")
                        _pnl_m = _re.search(r"P&L:\s*\$([+-]?[0-9.]+)", _bnotes)
                        _bmfe  = float(_pnl_m.group(1)) if _pnl_m else 0.0
                        if _bp > 0 and _bex > 0:
                            log_accuracy_entry(
                                symbol=str(_jrow.get("ticker", "")),
                                predicted=str(_jrow.get("structure", "Unknown")),
                                actual=str(_jrow.get("structure", "Unknown")),
                                compare_key="webull_import",
                                entry_price=_bp,
                                exit_price=_bex,
                                mfe=_bmfe,
                                user_id=_backfill_uid,
                            )
                            _backfill_count += 1
                        else:
                            _backfill_fail += 1
                    except Exception:
                        _backfill_fail += 1
                st.success(
                    f"Synced **{_backfill_count} trades** to analytics."
                    + (f" {_backfill_fail} entries skipped (missing P&L data)." if _backfill_fail else "")
                    + " Refresh the page to see your stats."
                )
                st.rerun()
        else:
            st.info(
                "No synced trades yet. Import your Webull CSV in the **Journal** tab, "
                "then return here to see your edge stats. "
                "The Pattern Correlation section below is still available."
            )

    st.markdown("---")

    # ── EQUITY CURVE ──────────────────────────────────────────────────────
    eq = ana["equity_curve"]
    if not eq.empty:
        st.markdown("**Cumulative P&L — Equity Curve**")
        _colors_eq = ["#ef5350" if v < 0 else "#4caf50"
                      for v in eq["cumulative_pnl"]]
        fig_eq = _go.Figure()
        # Zero line fill area
        fig_eq.add_trace(_go.Scatter(
            x=eq["timestamp"], y=eq["cumulative_pnl"],
            mode="lines+markers",
            line=dict(color="#00e5ff", width=2.5),
            marker=dict(size=6, color=_colors_eq,
                        line=dict(color="#1a1a2e", width=1)),
            fill="tozeroy",
            fillcolor="rgba(0,229,255,0.08)",
            name="Cumulative P&L",
            hovertemplate=(
                "<b>%{customdata}</b><br>"
                "Trade P&L: $%{text}<br>"
                "Cumulative: $%{y:.2f}<extra></extra>"
            ),
            customdata=eq["symbol"],
            text=eq["mfe"].round(2).astype(str),
        ))
        fig_eq.add_hline(y=0, line=dict(color="rgba(255,255,255,0.15)", dash="dot"))
        fig_eq.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"), height=280,
            xaxis=dict(title="", gridcolor="#2a2a4a", showgrid=True),
            yaxis=dict(title="Cumulative P&L ($)", gridcolor="#2a2a4a"),
            margin=dict(l=10, r=10, t=20, b=30),
            showlegend=False,
            hoverlabel=dict(bgcolor="#1a1a2e", font_color="#e0e0e0"),
        )
        st.plotly_chart(fig_eq, use_container_width=True)

    # ── WIN RATE BY STRUCTURE + GRADE DONUT ──────────────────────────────
    col_a, col_b = st.columns([3, 2])

    with col_a:
        wr_s = ana["win_rate_by_struct"]
        if not wr_s.empty:
            st.markdown("**Win Rate by Predicted Structure**")
            _bar_colors = [
                "#4caf50" if r >= 60 else ("#ffa726" if r >= 45 else "#ef5350")
                for r in wr_s["win_rate"]
            ]
            fig_wr = _go.Figure()
            fig_wr.add_trace(_go.Bar(
                x=wr_s["structure"], y=wr_s["win_rate"],
                marker_color=_bar_colors,
                text=[f"{r}%<br>{t}T" for r, t in
                      zip(wr_s["win_rate"], wr_s["trades"])],
                textposition="outside",
                textfont=dict(size=10, color="#e0e0e0"),
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Win Rate: %{y:.1f}%<br>"
                    "Avg P&L: $%{customdata:.2f}<extra></extra>"
                ),
                customdata=wr_s["avg_pnl"],
                name="Win Rate",
            ))
            fig_wr.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"),
                             annotation_text="50%", annotation_font_color="#888")
            fig_wr.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=300,
                xaxis=dict(gridcolor="#2a2a4a"),
                yaxis=dict(title="Win Rate (%)", gridcolor="#2a2a4a",
                           range=[0, max(wr_s["win_rate"].max() + 15, 70)]),
                margin=dict(l=10, r=10, t=20, b=40),
                showlegend=False,
            )
            st.plotly_chart(fig_wr, use_container_width=True)

    with col_b:
        gd = ana["grade_distribution"]
        if gd:
            st.markdown("**Grade Distribution**")
            _gd_order  = [g for g in ["A", "B", "C", "F"] if g in gd]
            _gd_vals   = [gd[g] for g in _gd_order]
            _gd_colors = [_GRADE_COLORS.get(g, "#aaa") for g in _gd_order]
            fig_pie = _go.Figure(_go.Pie(
                labels=_gd_order, values=_gd_vals,
                marker=dict(colors=_gd_colors,
                            line=dict(color="#1a1a2e", width=2)),
                hole=0.55,
                textinfo="label+percent",
                textfont=dict(size=12, color="#e0e0e0"),
                hovertemplate="<b>Grade %{label}</b><br>Count: %{value}<extra></extra>",
            ))
            fig_pie.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#1a1a2e",
                font=dict(color="#e0e0e0"), height=300,
                margin=dict(l=0, r=0, t=20, b=0),
                showlegend=False,
                annotations=[dict(
                    text=f"<b>{sum(_gd_vals)}</b><br>trades",
                    x=0.5, y=0.5, font_size=14,
                    font_color="#e0e0e0", showarrow=False,
                )],
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    # ── DAILY P&L BAR + TCS EDGE ─────────────────────────────────────────
    col_c, col_d = st.columns([3, 2])

    with col_c:
        dp = ana["daily_pnl"]
        if not dp.empty:
            st.markdown("**Daily P&L**")
            _day_colors = ["#4caf50" if v >= 0 else "#ef5350" for v in dp["pnl"]]
            fig_day = _go.Figure()
            fig_day.add_trace(_go.Bar(
                x=dp["date"].astype(str), y=dp["pnl"],
                marker_color=_day_colors,
                text=[f"${v:+.2f}" for v in dp["pnl"]],
                textposition="outside",
                textfont=dict(size=9, color="#e0e0e0"),
                hovertemplate="<b>%{x}</b><br>P&L: $%{y:.2f}<extra></extra>",
                name="Daily P&L",
            ))
            fig_day.add_hline(y=0, line=dict(color="rgba(255,255,255,0.2)"))
            fig_day.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=250,
                xaxis=dict(gridcolor="#2a2a4a", tickangle=-30),
                yaxis=dict(title="P&L ($)", gridcolor="#2a2a4a"),
                margin=dict(l=10, r=10, t=20, b=50),
                showlegend=False,
            )
            st.plotly_chart(fig_day, use_container_width=True)

    with col_d:
        tcs_df = ana["tcs_edge"]
        if not tcs_df.empty:
            st.markdown("**Win Rate by TCS Score**")
            _tcs_colors = [
                "#4caf50" if r >= 60 else ("#ffa726" if r >= 45 else "#ef5350")
                for r in tcs_df["win_rate"]
            ]
            fig_tcs = _go.Figure(_go.Bar(
                x=tcs_df["tcs_bucket"], y=tcs_df["win_rate"],
                marker_color=_tcs_colors,
                text=[f"{r}%<br>{t}T" for r, t in
                      zip(tcs_df["win_rate"], tcs_df["trades"])],
                textposition="outside",
                textfont=dict(size=10, color="#e0e0e0"),
                hovertemplate="<b>TCS %{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
            ))
            fig_tcs.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"))
            fig_tcs.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=250,
                xaxis=dict(title="TCS Range", gridcolor="#2a2a4a"),
                yaxis=dict(title="Win Rate (%)", gridcolor="#2a2a4a",
                           range=[0, 100]),
                margin=dict(l=10, r=10, t=20, b=30),
                showlegend=False,
            )
            st.plotly_chart(fig_tcs, use_container_width=True)

    # ── BACKTEST STRUCTURE WIN RATE (from sim history) ────────────────────
    st.markdown("---")
    st.markdown("**📊 Backtest Structure Win Rates** *(from your saved simulation history)*")
    _bk_stats = compute_backtest_structure_stats(_uid)
    if _bk_stats.empty:
        st.caption("Run backtests in the Backtest tab to populate this chart. "
                   "Each unique ticker/date pair you simulate builds this dataset.")
    else:
        import plotly.graph_objects as _go2
        _bk_win_colors = [
            "#4caf50" if r >= 60 else ("#ffa726" if r >= 45 else "#ef5350")
            for r in _bk_stats["win_rate"]
        ]
        _bcols = st.columns([3, 2])
        with _bcols[0]:
            fig_bk = _go2.Figure()
            fig_bk.add_trace(_go2.Bar(
                x=_bk_stats["structure"], y=_bk_stats["win_rate"],
                marker_color=_bk_win_colors,
                text=[f"{r}%<br>{t}T · FT {ft:.1f}%"
                      for r, t, ft in zip(_bk_stats["win_rate"],
                                          _bk_stats["trades"],
                                          _bk_stats["avg_follow_thru"])],
                textposition="outside",
                textfont=dict(size=9, color="#e0e0e0"),
                hovertemplate=(
                    "<b>%{x}</b><br>Win Rate: %{y:.1f}%<br>"
                    "Trades: %{customdata[0]}<br>"
                    "Avg Follow-Thru: %{customdata[1]:.1f}%<br>"
                    "False Break Rate: %{customdata[2]:.1f}%<extra></extra>"
                ),
                customdata=list(zip(
                    _bk_stats["trades"],
                    _bk_stats["avg_follow_thru"],
                    _bk_stats["false_brk_rate"],
                )),
            ))
            fig_bk.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"),
                             annotation_text="50% baseline", annotation_font_color="#888")
            fig_bk.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=300,
                xaxis=dict(gridcolor="#2a2a4a", tickangle=-20),
                yaxis=dict(title="Win Rate (%)", gridcolor="#2a2a4a", range=[0, 100]),
                margin=dict(l=10, r=10, t=20, b=60),
                showlegend=False,
            )
            st.plotly_chart(fig_bk, use_container_width=True)

        with _bcols[1]:
            st.markdown("**False Break Rate by Structure**")
            _fb_colors = [
                "#4caf50" if r <= 15 else ("#ffa726" if r <= 30 else "#ef5350")
                for r in _bk_stats["false_brk_rate"]
            ]
            fig_fb = _go2.Figure(_go2.Bar(
                x=_bk_stats["structure"], y=_bk_stats["false_brk_rate"],
                marker_color=_fb_colors,
                text=[f"{r}%" for r in _bk_stats["false_brk_rate"]],
                textposition="outside",
                textfont=dict(size=10, color="#e0e0e0"),
                hovertemplate="<b>%{x}</b><br>False Break: %{y:.1f}%<extra></extra>",
            ))
            fig_fb.add_hline(y=20, line=dict(color="rgba(255,255,255,0.2)", dash="dot"),
                             annotation_text="20% danger", annotation_font_color="#ffa726")
            fig_fb.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=300,
                xaxis=dict(gridcolor="#2a2a4a", tickangle=-20),
                yaxis=dict(title="False Break (%)", gridcolor="#2a2a4a", range=[0, 80]),
                margin=dict(l=10, r=10, t=20, b=60),
                showlegend=False,
            )
            st.plotly_chart(fig_fb, use_container_width=True)

        total_unique = _bk_stats["trades"].sum()
        st.caption(
            f"Based on {total_unique} unique ticker/date simulations · "
            "Duplicates removed · Hover bars for full breakdown"
        )

    # ── RAW TRACKER TABLE ─────────────────────────────────────────────────
    with st.expander("🗂 Raw Synced Trades", expanded=False):
        eq_disp = ana["equity_curve"].copy()
        if not eq_disp.empty:
            eq_disp["timestamp"] = eq_disp["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
            eq_disp["mfe"]       = eq_disp["mfe"].map(lambda v: f"{'+'if v>=0 else''}{v:.2f}")
            eq_disp["cumulative_pnl"] = eq_disp["cumulative_pnl"].map(
                lambda v: f"{'+'if v>=0 else''}{v:.2f}")
            eq_disp.columns = ["Time", "Symbol", "Trade P&L", "Cumulative P&L"]
            st.dataframe(eq_disp, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION — Journal-Based Edge Analytics (time, day, ticker)
    # ══════════════════════════════════════════════════════════════════════════
    if not journal_df.empty:
        st.markdown("---")
        st.markdown("### 📅 Trade Timing & Ticker Edge")
        st.caption("Based on your full journal history — grades A/B = win, C/D/F = loss.")

        import pandas as _pd_an
        _jdf = journal_df.copy()

        # Coerce timestamp
        _jdf["_ts"] = _pd_an.to_datetime(_jdf.get("timestamp", _pd_an.NaT), errors="coerce")
        _jdf["_hour"] = _jdf["_ts"].dt.hour
        _jdf["_dow"]  = _jdf["_ts"].dt.dayofweek   # 0=Mon … 4=Fri
        _jdf["_win"]  = _jdf["grade"].isin(["A", "B"]).astype(int)

        # Drop rows with no timestamp
        _jdf = _jdf.dropna(subset=["_ts"])

        _timing_col1, _timing_col2 = st.columns(2)

        # ── Time-of-Day win rate ──────────────────────────────────────────────
        with _timing_col1:
            _hour_grp = (
                _jdf.groupby("_hour")["_win"]
                .agg(trades="count", wins="sum")
                .reset_index()
            )
            _hour_grp = _hour_grp[_hour_grp["trades"] >= 1].copy()
            _hour_grp["win_rate"] = (_hour_grp["wins"] / _hour_grp["trades"] * 100).round(1)
            _hour_grp["label"]    = _hour_grp["_hour"].apply(
                lambda h: f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
            )

            if not _hour_grp.empty:
                _hr_colors = [
                    "#4caf50" if r >= 55 else "#ffa726" if r >= 40 else "#ef5350"
                    for r in _hour_grp["win_rate"]
                ]
                fig_hr = _go.Figure()
                fig_hr.add_trace(_go.Bar(
                    x=_hour_grp["label"],
                    y=_hour_grp["win_rate"],
                    marker_color=_hr_colors,
                    text=[f"{r}%<br>({t}T)" for r, t in
                          zip(_hour_grp["win_rate"], _hour_grp["trades"])],
                    textposition="outside",
                    textfont=dict(size=9, color="#e0e0e0"),
                    hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
                ))
                fig_hr.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"))
                fig_hr.update_layout(
                    paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                    font=dict(color="#e0e0e0"), height=260,
                    title=dict(text="Win Rate by Entry Hour (EST)", font=dict(size=13)),
                    xaxis=dict(gridcolor="#2a2a4a"),
                    yaxis=dict(gridcolor="#2a2a4a", range=[0, 110], title="Win %"),
                    margin=dict(l=10, r=10, t=40, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_hr, use_container_width=True)
            else:
                st.info("Not enough timestamped entries for time-of-day analysis.")

        # ── Day-of-Week win rate ──────────────────────────────────────────────
        with _timing_col2:
            _dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
            _dow_grp = (
                _jdf[_jdf["_dow"].isin(range(5))]
                .groupby("_dow")["_win"]
                .agg(trades="count", wins="sum")
                .reset_index()
            )
            _dow_grp["win_rate"] = (_dow_grp["wins"] / _dow_grp["trades"] * 100).round(1)
            _dow_grp["label"]    = _dow_grp["_dow"].map(_dow_names)

            if not _dow_grp.empty:
                _dw_colors = [
                    "#4caf50" if r >= 55 else "#ffa726" if r >= 40 else "#ef5350"
                    for r in _dow_grp["win_rate"]
                ]
                fig_dw = _go.Figure()
                fig_dw.add_trace(_go.Bar(
                    x=_dow_grp["label"],
                    y=_dow_grp["win_rate"],
                    marker_color=_dw_colors,
                    text=[f"{r}%<br>({t}T)" for r, t in
                          zip(_dow_grp["win_rate"], _dow_grp["trades"])],
                    textposition="outside",
                    textfont=dict(size=9, color="#e0e0e0"),
                    hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
                ))
                fig_dw.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"))
                fig_dw.update_layout(
                    paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                    font=dict(color="#e0e0e0"), height=260,
                    title=dict(text="Win Rate by Day of Week", font=dict(size=13)),
                    xaxis=dict(gridcolor="#2a2a4a",
                               categoryorder="array",
                               categoryarray=["Mon","Tue","Wed","Thu","Fri"]),
                    yaxis=dict(gridcolor="#2a2a4a", range=[0, 110], title="Win %"),
                    margin=dict(l=10, r=10, t=40, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_dw, use_container_width=True)
            else:
                st.info("Not enough entries for day-of-week analysis.")

        # ── Intraday vs Multi-Day hold type win rate ─────────────────────────
        st.markdown("---")
        import re as _re_an
        def _hold_type(row):
            _notes = str(row.get("notes", "") or "")
            _entry_ts = str(row.get("timestamp", "") or "")
            _ets_m2 = _re_an.search(
                r"ExitTS:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[T\s][0-9:]+)"
                r"|Exit:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s[0-9:]+)", _notes
            )
            if not _ets_m2 or not _entry_ts:
                return None
            _exit_raw = (_ets_m2.group(1) or _ets_m2.group(2) or "").strip()
            try:
                import pandas as _pd_ht
                _edt = _pd_ht.to_datetime(_entry_ts, errors="coerce")
                _xdt = _pd_ht.to_datetime(_exit_raw, errors="coerce")
                if _pd_ht.isna(_edt) or _pd_ht.isna(_xdt):
                    return None
                _days = (_xdt.date() - _edt.date()).days
                if _days == 0:
                    return "Intraday"
                elif _days == 1:
                    return "Overnight"
                else:
                    return "Multi-day"
            except Exception:
                return None

        _jdf["_hold_type"] = _jdf.apply(_hold_type, axis=1)
        _hold_df = _jdf.dropna(subset=["_hold_type"])

        if not _hold_df.empty and len(_hold_df) >= 3:
            _hold_grp = (
                _hold_df.groupby("_hold_type")["_win"]
                .agg(trades="count", wins="sum")
                .reset_index()
            )
            _hold_grp["win_rate"] = (_hold_grp["wins"] / _hold_grp["trades"] * 100).round(1)
            _hold_order = ["Intraday", "Overnight", "Multi-day"]
            _hold_grp["_ord"] = _hold_grp["_hold_type"].map(
                {t: i for i, t in enumerate(_hold_order)}
            )
            _hold_grp = _hold_grp.sort_values("_ord")
            _hold_colors_map = {"Intraday": "#29b6f6", "Overnight": "#ffa726", "Multi-day": "#ce93d8"}
            _hold_colors_list = [_hold_colors_map.get(t, "#90caf9") for t in _hold_grp["_hold_type"]]

            _hold_left, _hold_right = st.columns([2, 1])
            with _hold_left:
                fig_hold = _go.Figure(_go.Bar(
                    x=_hold_grp["_hold_type"],
                    y=_hold_grp["win_rate"],
                    marker_color=_hold_colors_list,
                    text=[f"{r}%<br>({t} trades)" for r, t in
                          zip(_hold_grp["win_rate"], _hold_grp["trades"])],
                    textposition="outside",
                    textfont=dict(size=10, color="#e0e0e0"),
                    hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
                ))
                fig_hold.add_hline(y=50, line=dict(color="rgba(255,255,255,0.2)", dash="dot"))
                fig_hold.update_layout(
                    paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                    font=dict(color="#e0e0e0"), height=260,
                    title=dict(text="Win Rate by Hold Type (from your actual Webull exits)",
                               font=dict(size=13)),
                    xaxis=dict(gridcolor="#2a2a4a"),
                    yaxis=dict(gridcolor="#2a2a4a", range=[0, 120], title="Win %"),
                    margin=dict(l=10, r=10, t=40, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_hold, use_container_width=True)
            with _hold_right:
                st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
                for _, _hrow in _hold_grp.iterrows():
                    _hc = _hold_colors_map.get(_hrow["_hold_type"], "#90caf9")
                    st.markdown(
                        f'<div style="background:{_hc}11;border:1px solid {_hc}44;'
                        f'border-radius:8px;padding:10px 14px;margin:6px 0;">'
                        f'<div style="font-size:11px;color:{_hc};font-weight:700;">'
                        f'{_hrow["_hold_type"]}</div>'
                        f'<div style="font-size:22px;font-weight:900;color:#e0e0e0;">'
                        f'{_hrow["win_rate"]}%</div>'
                        f'<div style="font-size:10px;color:#666;">'
                        f'{int(_hrow["wins"])}/{int(_hrow["trades"])} trades</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # ── Multi-Day Setup Profile ───────────────────────────────────────────
        if not _hold_df.empty and len(_hold_df) >= 3:
            st.markdown("---")
            st.markdown("### 🔎 Hold-Type Signal Profile")
            st.caption(
                "For each hold type, what did your winning trades have in common? "
                "Use this to predict whether tomorrow's setup is an intraday trade or a multi-day hold."
            )

            _hold_types_present = [t for t in ["Intraday", "Overnight", "Multi-day"]
                                   if t in _hold_df["_hold_type"].values]

            for _ht in _hold_types_present:
                _ht_color = {"Intraday": "#29b6f6", "Overnight": "#ffa726",
                             "Multi-day": "#ce93d8"}.get(_ht, "#90caf9")
                _ht_df = _hold_df[_hold_df["_hold_type"] == _ht].copy()
                _ht_wins  = _ht_df[_ht_df["_win"] == 1]
                _ht_loss  = _ht_df[_ht_df["_win"] == 0]
                _ht_wr    = round(len(_ht_wins) / len(_ht_df) * 100, 1) if len(_ht_df) > 0 else 0

                with st.expander(
                    f"{_ht}  ·  {_ht_wr}% win rate  ·  {len(_ht_df)} trades",
                    expanded=(_ht == "Intraday"),
                ):
                    _sp_col1, _sp_col2 = st.columns(2)

                    # ── Winning trade fingerprint ──────────────────────────
                    with _sp_col1:
                        st.markdown(
                            f'<div style="color:{_ht_color};font-weight:700;'
                            f'font-size:12px;margin-bottom:8px;">✅ A/B WIN FINGERPRINT</div>',
                            unsafe_allow_html=True,
                        )
                        if _ht_wins.empty:
                            st.caption("Not enough winning trades yet.")
                        else:
                            # Avg TCS
                            _tcs_vals = _pd_an.to_numeric(
                                _ht_wins.get("tcs", _pd_an.Series()), errors="coerce"
                            ).dropna()
                            _rvol_vals = _pd_an.to_numeric(
                                _ht_wins.get("rvol", _pd_an.Series()), errors="coerce"
                            ).dropna()

                            _stat_rows = []
                            if not _tcs_vals.empty:
                                _stat_rows.append(("Avg TCS", f"{_tcs_vals.mean():.0f}",
                                                   f"range {_tcs_vals.min():.0f}–{_tcs_vals.max():.0f}"))
                            if not _rvol_vals.empty:
                                _stat_rows.append(("Avg RVOL", f"{_rvol_vals.mean():.1f}×",
                                                   f"min {_rvol_vals.min():.1f}×"))

                            # Most common entry hours
                            if "_hour" in _ht_wins.columns:
                                _hr_counts = _ht_wins["_hour"].value_counts().head(3)
                                _hr_labels = ", ".join(
                                    f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
                                    for h in _hr_counts.index
                                )
                                _stat_rows.append(("Best entry hours", _hr_labels, "most frequent"))

                            # Most common structures
                            if "structure" in _ht_wins.columns:
                                _struct_counts = (
                                    _ht_wins["structure"]
                                    .astype(str)
                                    .replace({"Unknown": None, "nan": None, "": None})
                                    .dropna()
                                    .value_counts()
                                    .head(3)
                                )
                                if not _struct_counts.empty:
                                    _struct_labels = ", ".join(
                                        f"{s} ({c})" for s, c in _struct_counts.items()
                                    )
                                    _stat_rows.append(("Top structures", _struct_labels, ""))

                            for _lbl, _val, _sub in _stat_rows:
                                _sub_html = (
                                    '<div style="font-size:9px;color:#555;text-align:right;">'
                                    + _sub + "</div>"
                                ) if _sub else ""
                                st.markdown(
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'align-items:center;padding:5px 0;'
                                    f'border-bottom:1px solid #1e1e3a;">'
                                    f'<span style="color:#888;font-size:11px;">{_lbl}</span>'
                                    f'<span style="color:#e0e0e0;font-weight:700;font-size:13px;">'
                                    f'{_val}</span>'
                                    f'</div>{_sub_html}',
                                    unsafe_allow_html=True,
                                )

                    # ── Losing trade fingerprint ───────────────────────────
                    with _sp_col2:
                        st.markdown(
                            '<div style="color:#ef5350;font-weight:700;'
                            'font-size:12px;margin-bottom:8px;">❌ C/D/F LOSS FINGERPRINT</div>',
                            unsafe_allow_html=True,
                        )
                        if _ht_loss.empty:
                            st.caption("No losing trades in this category — great!")
                        else:
                            _ltcs_vals = _pd_an.to_numeric(
                                _ht_loss.get("tcs", _pd_an.Series()), errors="coerce"
                            ).dropna()
                            _lrvol_vals = _pd_an.to_numeric(
                                _ht_loss.get("rvol", _pd_an.Series()), errors="coerce"
                            ).dropna()

                            _loss_rows = []
                            if not _ltcs_vals.empty:
                                _loss_rows.append(("Avg TCS", f"{_ltcs_vals.mean():.0f}",
                                                   f"range {_ltcs_vals.min():.0f}–{_ltcs_vals.max():.0f}"))
                            if not _lrvol_vals.empty:
                                _loss_rows.append(("Avg RVOL", f"{_lrvol_vals.mean():.1f}×", ""))

                            if "_hour" in _ht_loss.columns:
                                _lhr_counts = _ht_loss["_hour"].value_counts().head(3)
                                _lhr_labels = ", ".join(
                                    f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
                                    for h in _lhr_counts.index
                                )
                                _loss_rows.append(("Danger hours", _lhr_labels, "most losses"))

                            if "structure" in _ht_loss.columns:
                                _lstruct_counts = (
                                    _ht_loss["structure"]
                                    .astype(str)
                                    .replace({"Unknown": None, "nan": None, "": None})
                                    .dropna()
                                    .value_counts()
                                    .head(3)
                                )
                                if not _lstruct_counts.empty:
                                    _loss_rows.append(("Loss structures",
                                                       ", ".join(f"{s} ({c})"
                                                                 for s, c in _lstruct_counts.items()),
                                                       "avoid these in this hold style"))

                            for _lbl, _val, _sub in _loss_rows:
                                _lsub_html = (
                                    '<div style="font-size:9px;color:#555;text-align:right;">'
                                    + _sub + "</div>"
                                ) if _sub else ""
                                st.markdown(
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'align-items:center;padding:5px 0;'
                                    f'border-bottom:1px solid #1e1e3a;">'
                                    f'<span style="color:#888;font-size:11px;">{_lbl}</span>'
                                    f'<span style="color:#e0e0e0;font-weight:700;font-size:13px;">'
                                    f'{_val}</span>'
                                    f'</div>{_lsub_html}',
                                    unsafe_allow_html=True,
                                )

                    # ── Derived rule ───────────────────────────────────────
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    if not _ht_wins.empty and not _tcs_vals.empty:
                        _rule_tcs = f"TCS ≥ {_tcs_vals.mean():.0f}"
                        _rule_rvol = (f" · RVOL ≥ {_rvol_vals.mean():.1f}×"
                                      if not _rvol_vals.empty else "")
                        st.markdown(
                            f'<div style="background:#0d1f2d;border-left:3px solid {_ht_color};'
                            f'padding:8px 14px;border-radius:4px;font-size:12px;color:#cfd8dc;">'
                            f'<b style="color:{_ht_color};">Derived Rule:</b> '
                            f'For {_ht.lower()} trades, your wins cluster at {_rule_tcs}{_rule_rvol}. '
                            f'Entries below these thresholds in this hold style have historically underperformed.'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

        # ── Top / Bottom Tickers ──────────────────────────────────────────────
        st.markdown("---")
        _tkr_grp = (
            _jdf.groupby("ticker")["_win"]
            .agg(trades="count", wins="sum")
            .reset_index()
        )
        _tkr_grp["win_rate"] = (_tkr_grp["wins"] / _tkr_grp["trades"] * 100).round(1)
        _tkr_grp = _tkr_grp[_tkr_grp["trades"] >= 2].sort_values("win_rate", ascending=False)

        # Try to join P&L from equity curve
        _ec = ana.get("equity_curve", _pd_an.DataFrame())
        if not _ec.empty and "symbol" in _ec.columns and "mfe" in _ec.columns:
            _pnl_by_tkr = _ec.groupby("symbol")["mfe"].sum().reset_index()
            _pnl_by_tkr.columns = ["ticker", "total_pnl"]
            _tkr_grp = _tkr_grp.merge(_pnl_by_tkr, on="ticker", how="left")
            _tkr_grp["total_pnl"] = _tkr_grp["total_pnl"].fillna(0.0).round(2)
        else:
            _tkr_grp["total_pnl"] = 0.0

        if not _tkr_grp.empty:
            _tkr_col1, _tkr_col2 = st.columns(2)
            _best  = _tkr_grp.head(8)
            _worst = _tkr_grp.tail(8).sort_values("win_rate")

            with _tkr_col1:
                st.markdown("**🏆 Best Tickers (≥2 trades)**")
                _bc = ["#4caf50" if r >= 55 else "#ffa726" for r in _best["win_rate"]]
                fig_bt = _go.Figure(_go.Bar(
                    x=_best["ticker"], y=_best["win_rate"],
                    marker_color=_bc,
                    text=[f"{r}%<br>({t}T)" for r, t in
                          zip(_best["win_rate"], _best["trades"])],
                    textposition="outside",
                    textfont=dict(size=9, color="#e0e0e0"),
                ))
                fig_bt.update_layout(
                    paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                    font=dict(color="#e0e0e0"), height=240,
                    yaxis=dict(gridcolor="#2a2a4a", range=[0, 120], title="Win %"),
                    xaxis=dict(gridcolor="#2a2a4a"),
                    margin=dict(l=10, r=10, t=10, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_bt, use_container_width=True)

            with _tkr_col2:
                st.markdown("**📉 Worst Tickers (≥2 trades)**")
                _wc = ["#ef5350" if r < 40 else "#ffa726" for r in _worst["win_rate"]]
                fig_wt = _go.Figure(_go.Bar(
                    x=_worst["ticker"], y=_worst["win_rate"],
                    marker_color=_wc,
                    text=[f"{r}%<br>({t}T)" for r, t in
                          zip(_worst["win_rate"], _worst["trades"])],
                    textposition="outside",
                    textfont=dict(size=9, color="#e0e0e0"),
                ))
                fig_wt.update_layout(
                    paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                    font=dict(color="#e0e0e0"), height=240,
                    yaxis=dict(gridcolor="#2a2a4a", range=[0, 120], title="Win %"),
                    xaxis=dict(gridcolor="#2a2a4a"),
                    margin=dict(l=10, r=10, t=10, b=30),
                    showlegend=False,
                )
                st.plotly_chart(fig_wt, use_container_width=True)

            # Full ticker table
            with st.expander("📋 All Tickers — Full Breakdown", expanded=False):
                _tkr_disp = _tkr_grp.sort_values("win_rate", ascending=False).copy()
                _tkr_disp.columns = [c.replace("_", " ").title() for c in _tkr_disp.columns]
                if "Total Pnl" in _tkr_disp.columns:
                    _tkr_disp["Total Pnl"] = _tkr_disp["Total Pnl"].apply(
                        lambda v: f"${v:+.2f}" if v != 0 else "—"
                    )
                    _tkr_disp = _tkr_disp.rename(columns={"Total Pnl": "Total P&L"})
                st.dataframe(_tkr_disp, use_container_width=True, hide_index=True)
        else:
            st.info("Need at least 2 trades per ticker to compute ticker stats.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION — Webull Pattern Correlation
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🔬 Pattern Correlation — Your Trade History")
    st.caption(
        "Scans every trade session in your journal retroactively. "
        "Fetches the day's bar data and detects which chart patterns were present "
        "on your A/B wins vs your C/F losses. Identifies which setups actually work for you."
    )

    _pat_scan_api = st.session_state.get("_sb_api_key", "")
    _pat_scan_sec = st.session_state.get("_sb_secret_key", "")
    _pat_scan_uid = st.session_state.get("auth_user_id", "")
    _pat_result   = st.session_state.get("_pat_scan_result")

    _pat_col1, _pat_col2 = st.columns([3, 1])
    _pat_scan_feed = _pat_col2.selectbox(
        "Feed", ["iex", "sip"], key="pat_scan_feed",
        help="iex = free tier. sip = paid Alpaca subscription."
    )

    if _pat_col1.button("🔬 Scan Trade History for Patterns",
                        use_container_width=True, key="pat_scan_btn"):
        if not _pat_scan_api or not _pat_scan_sec:
            st.error("Add your Alpaca credentials in the sidebar first.")
        else:
            _pat_jdf = _cached_load_journal(user_id=_pat_scan_uid)
            if _pat_jdf.empty:
                st.warning("No journal entries found. Import your Webull CSV in the Journal tab first.")
            else:
                _n_uniq = _pat_jdf[["ticker", "timestamp"]].drop_duplicates().shape[0] if "timestamp" in _pat_jdf.columns else len(_pat_jdf)
                with st.spinner(f"Scanning {_n_uniq} trade sessions for patterns — may take 30–60 seconds…"):
                    _pat_result = scan_journal_patterns(
                        _pat_scan_api, _pat_scan_sec, _pat_jdf, feed=_pat_scan_feed
                    )
                st.session_state["_pat_scan_result"] = _pat_result

    if _pat_result and _pat_result.get("scanned", 0) > 0:
        import plotly.graph_objects as _go_pat

        _ps  = _pat_result["summary"]
        _sc  = _pat_result["scanned"]
        _tw  = _pat_result["total_wins"]
        _tl  = _pat_result["total_losses"]
        _err = _pat_result["errors"]

        _pk1, _pk2, _pk3, _pk4 = st.columns(4)
        _pk1.metric("Sessions Scanned", _sc)
        _pk2.metric("A/B Wins", _tw)
        _pk3.metric("C/F Losses", _tl)
        _pk4.metric("No Bar Data", _err, help="Too old or delisted on Alpaca")

        if not _ps:
            st.info("No patterns detected. Try switching to SIP feed or check that trades are recent enough for Alpaca history.")
        else:
            _sorted_pats = sorted(_ps.items(), key=lambda x: x[1]["total"], reverse=True)

            _pat_names  = [p[0] for p in _sorted_pats]
            _pat_wrates = [p[1]["win_rate"] for p in _sorted_pats]
            _pat_totals = [p[1]["total"] for p in _sorted_pats]
            _bar_clrs   = [
                "#4caf50" if wr >= 60 else "#ffa726" if wr >= 45 else "#ef5350"
                for wr in _pat_wrates
            ]
            _fig_pat = _go_pat.Figure(_go_pat.Bar(
                x=_pat_names,
                y=_pat_wrates,
                marker_color=_bar_clrs,
                text=[f"{wr:.0f}%<br>({t} trades)" for wr, t in zip(_pat_wrates, _pat_totals)],
                textposition="outside",
            ))
            _fig_pat.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"), height=320,
                title=dict(text="Win Rate When Pattern Present", font=dict(size=14), x=0.0),
                yaxis=dict(range=[0, 115], gridcolor="#2a2a4a", title="Win Rate %", ticksuffix="%"),
                xaxis=dict(gridcolor="#2a2a4a", tickangle=-20),
                margin=dict(t=40, b=80, l=50, r=20),
            )
            _fig_pat.add_hline(y=50, line_dash="dot", line_color="#555",
                               annotation_text="50% baseline", annotation_position="right")
            st.plotly_chart(_fig_pat, use_container_width=True)

            st.markdown("**Pattern breakdown:**")
            _tbl_rows = []
            for pat, data in _sorted_pats:
                _wr = data["win_rate"]
                _signal = "✅ Has Edge" if _wr >= 60 else "⚠️ Neutral" if _wr >= 45 else "🚫 No Edge"
                _tbl_rows.append({
                    "Pattern":         pat,
                    "Total Trades":    data["total"],
                    "On Wins (A/B)":   data["win"],
                    "On Losses (C/F)": data["loss"],
                    "Win Rate":        f"{_wr:.0f}%",
                    "Signal":          _signal,
                })
            st.dataframe(
                _tbl_rows,
                use_container_width=True,
                height=min(420, 40 + 36 * len(_tbl_rows)),
            )

            _win_pats  = _pat_result["by_outcome"]["win"]
            _loss_pats = _pat_result["by_outcome"]["loss"]
            _wp_sorted = sorted(_win_pats.items(), key=lambda x: x[1], reverse=True)[:5]
            _lp_sorted = sorted(_loss_pats.items(), key=lambda x: x[1], reverse=True)[:5]

            _pc1, _pc2 = st.columns(2)
            with _pc1:
                st.markdown("**Most common on your A/B wins:**")
                for _pn, _cnt in _wp_sorted:
                    _wr_v = _ps.get(_pn, {}).get("win_rate", 0)
                    st.markdown(
                        f'<div style="background:#0d2e1a;border:1px solid #2e5c3a;border-radius:6px;'
                        f'padding:6px 12px;margin:4px 0;display:flex;justify-content:space-between;'
                        f'align-items:center;">'
                        f'<span style="color:#4caf50;font-weight:600;">{_pn}</span>'
                        f'<span style="color:#888;font-size:11px;">{_cnt}x present &nbsp;·&nbsp; {_wr_v:.0f}% win rate</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            with _pc2:
                st.markdown("**Most common on your C/F losses:**")
                for _pn, _cnt in _lp_sorted:
                    _wr_v = _ps.get(_pn, {}).get("win_rate", 0)
                    st.markdown(
                        f'<div style="background:#2e0d0d;border:1px solid #5c2e2e;border-radius:6px;'
                        f'padding:6px 12px;margin:4px 0;display:flex;justify-content:space-between;'
                        f'align-items:center;">'
                        f'<span style="color:#ef5350;font-weight:600;">{_pn}</span>'
                        f'<span style="color:#888;font-size:11px;">{_cnt}x present &nbsp;·&nbsp; {_wr_v:.0f}% win rate</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            st.caption(
                f"Scan complete — {_sc} sessions analyzed, {_err} skipped (no bar data). "
                "Results cached until you re-run."
            )

    elif _pat_result and _pat_result.get("scanned", 0) == 0:
        st.warning("Scan ran but found no sessions with bar data. "
                   "Make sure your journal has entries and try switching feeds.")

    # ── Macro Regime History ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 🌡️ Macro Regime History")
    st.caption("Last 30 days of tape regime — track how breadth conditions have shifted over time.")
    try:
        _rh_uid = st.session_state.get("auth_user_id", "")
        _rh = get_breadth_regime_history(days=30, user_id=_rh_uid)
        if _rh:
            import plotly.graph_objects as _pgo
            _rh_sorted = sorted(_rh, key=lambda x: x.get("trade_date", ""))
            _rh_dates  = [r.get("trade_date", "") for r in _rh_sorted]
            _rh_scores = []
            _rh_labels = []
            _rh_colors = []
            _score_map  = {"hot_tape": 3, "warm": 2, "cold": 1, "unknown": 0}
            _color_map  = {"hot_tape": "#ff6b35", "warm": "#ffd700", "cold": "#5c9bd4", "unknown": "#555555"}
            for r in _rh_sorted:
                _tag = r.get("regime_tag", "unknown")
                _rh_scores.append(_score_map.get(_tag, 0))
                _rh_labels.append(r.get("label", "?"))
                _rh_colors.append(_color_map.get(_tag, "#555555"))

            _rh_fig = _pgo.Figure()
            _rh_fig.add_trace(_pgo.Bar(
                x=_rh_dates,
                y=_rh_scores,
                marker_color=_rh_colors,
                text=_rh_labels,
                textposition="inside",
                hovertemplate="%{x}<br>%{text}<extra></extra>",
            ))
            _rh_fig.update_layout(
                height=200,
                margin=dict(l=0, r=0, t=20, b=0),
                paper_bgcolor="#0e1117",
                plot_bgcolor="#0e1117",
                font_color="#e0e0e0",
                xaxis=dict(showgrid=False, tickformat="%b %d"),
                yaxis=dict(
                    showticklabels=True,
                    tickvals=[1, 2, 3],
                    ticktext=["❄️ Cold", "🟡 Warm", "🔥 Hot"],
                    gridcolor="#1e2a3a",
                    range=[0, 3.5],
                ),
                showlegend=False,
            )
            st.plotly_chart(_rh_fig, use_container_width=True)

            # Mini table
            _rh_df_data = [
                {
                    "Date": r.get("trade_date", ""),
                    "Regime": r.get("label", "?"),
                    "Mode": r.get("mode", "").replace("_", " ").title(),
                    "TCS Adj": (f"{r.get('tcs_floor_adj', 0):+d}" if r.get("tcs_floor_adj", 0) != 0 else "—"),
                }
                for r in reversed(_rh_sorted)
            ]
            st.dataframe(
                _rh_df_data,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Date":    st.column_config.TextColumn("Date", width="small"),
                    "Regime":  st.column_config.TextColumn("Regime", width="medium"),
                    "Mode":    st.column_config.TextColumn("Mode", width="medium"),
                    "TCS Adj": st.column_config.TextColumn("TCS Adj", width="small"),
                },
            )
        else:
            st.info(
                "No breadth regime history yet. Enter your first reading in the "
                "🌡️ Macro Regime panel in the sidebar."
            )
    except Exception as _rhe:
        st.caption(f"Could not load regime history: {_rhe}")

    # ── Brain Accuracy (formerly Tracker tab) ─────────────────────────────
    st.markdown("---")
    render_tracker_tab()


def render_tracker_tab():
    """Render the Accuracy Tracker tab — structure distribution + Predicted vs Actual history."""
    st.markdown("## 🧠 MarketBrain — Accuracy Tracker")
    st.caption("All-time structure distribution and brain prediction accuracy.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0 — Batch Backtest Runner
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("🔬 Batch Backtest — Feed Historical Tickers", expanded=False):
        st.caption(
            "Fetches each ticker/date pair via Alpaca, classifies structure, records Brain "
            "prediction vs actual. Uses credentials entered in the sidebar. "
            "Skips any pair already in the tracker. Add more lines in M/D: T1, T2 format."
        )
        _bt_pairs_text = st.text_area(
            "Ticker / Date pairs", value=_BATCH_DEFAULT, height=220, key="bt_pairs_input"
        )
        _bt_feed = st.selectbox(
            "Data feed", ["iex", "sip"], index=0, key="bt_feed_select",
            help="IEX = free tier (recommended for backtests). SIP = full tape."
        )
        _bt_run = st.button("▶ Run Batch Backtest", type="primary",
                            use_container_width=True, key="bt_run_btn")

        if _bt_run:
            # api_key and secret_key come from the outer sidebar scope
            if not api_key or not secret_key:
                st.error("Enter your Alpaca API Key and Secret Key in the sidebar first.")
            else:
                _pairs = _parse_batch_pairs(_bt_pairs_text)
                if not _pairs:
                    st.warning("No valid ticker/date pairs found. Use format: 3/30: ANNA, SST, BFRG")
                else:
                    st.info(f"Processing {len(_pairs)} pairs… this may take 1–2 minutes.")
                    _prog   = st.progress(0.0, text="Starting…")
                    _bt_results = []
                    for _i, (_tk, _dt) in enumerate(_pairs):
                        _prog.progress((_i + 1) / len(_pairs),
                                       text=f"Fetching {_tk} {_dt} ({_i+1}/{len(_pairs)})…")
                        _r = run_single_backtest(api_key, secret_key, _tk, _dt,
                                                 feed=_bt_feed, num_bins=100)
                        _bt_results.append(_r)
                    _prog.empty()
                    # Persist results so they survive the page rerun
                    st.session_state["bt_last_results"] = _bt_results

        # ── Always render results if available ────────────────────────────────
        if st.session_state.get("bt_last_results"):
            _rdf = pd.DataFrame(st.session_state["bt_last_results"])
            _ok      = (_rdf["status"] == "OK").sum()
            _dup     = (_rdf["status"] == "Already logged").sum()
            _no_data = _rdf["status"].str.startswith("No data").sum()
            _no_ib   = _rdf["status"].str.startswith("IB").sum()
            _errs    = len(_rdf) - _ok - _dup - _no_data - _no_ib
            _correct = (_rdf["correct"] == "✅").sum()
            _wrong   = (_rdf["correct"] == "❌").sum()
            _logged  = _ok + _dup

            _acc_str = (f"  •  Batch accuracy: **{_correct}/{_correct+_wrong}** "
                        f"({_correct/((_correct+_wrong) or 1)*100:.0f}%)"
                        if (_correct + _wrong) > 0 else "")
            st.success(
                f"✅ **{_ok}** new logged  •  "
                f"🔁 **{_dup}** already existed  •  "
                f"📭 **{_no_data}** no data  •  "
                f"⏱ **{_no_ib}** IB incomplete  •  "
                f"⚠️ **{_errs}** errors"
                + _acc_str
            )
            st.caption(
                "💡 'No data' means Alpaca IEX doesn't carry historical minute bars for that "
                "ticker on that date. Very small OTC stocks often only appear on SIP. "
                "Try switching the feed to **sip** if you have an Alpaca paid subscription."
            )

            # Color rows by result
            def _bt_style(row):
                s = row.get("correct", "—")
                if s == "✅":  return ["background-color:#4caf5022"] * len(row)
                if s == "❌":  return ["background-color:#ef535022"] * len(row)
                return [""] * len(row)

            try:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]]
                      .style.apply(_bt_style, axis=1),
                    use_container_width=True, hide_index=True, height="stretch"
                )
            except Exception:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]],
                    use_container_width=True, hide_index=True
                )
            if st.button("🗑 Clear batch results", key="bt_clear_btn"):
                st.session_state.pop("bt_last_results", None)
                st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0b — High Conviction Watchlist (top prob ≥ 80%)
    # ══════════════════════════════════════════════════════════════════════════
    _STRUCT_COLORS_HC = {
        "Trend":        "#ff9800",
        "Dbl Dist":     "#00bcd4",
        "Non-Trend":    "#78909c",
        "Normal":       "#66bb6a",
        "Nrml Var":     "#aed581",
        "Neutral":      "#80cbc4",
        "Ntrl Extreme": "#7e57c2",
    }

    st.markdown("### 🎯 High Conviction Calls  <span style='font-size:13px;color:#888;font-weight:400'>≥ 75% structure probability</span>",
                unsafe_allow_html=True)
    _hc_df = _cached_load_high_conviction_log()

    if _hc_df.empty:
        st.info(
            "No high-conviction calls logged yet. "
            "Run a historical or live analysis on any ticker — any day where the "
            "top structure probability ≥ 75% is automatically captured here."
        )
    else:
        # ── Summary pills row ─────────────────────────────────────────────────
        _hc_total   = len(_hc_df)
        _hc_structs = _hc_df["structure"].value_counts()
        _pill_html  = ""
        for _s, _cnt in _hc_structs.items():
            _c = _STRUCT_COLORS_HC.get(_s, "#888888")
            _pill_html += (
                f'<span style="background:{_c}33;color:{_c};border:1px solid {_c}66;'
                f'border-radius:12px;padding:3px 10px;margin:3px;'
                f'font-size:13px;font-weight:600;display:inline-block;">'
                f'{_s} ({_cnt})</span>'
            )
        st.markdown(
            f'<div style="margin-bottom:8px;">'
            f'<b style="color:#ccc;">{_hc_total} total</b> &nbsp;·&nbsp; '
            + _pill_html + "</div>",
            unsafe_allow_html=True,
        )

        # ── Table ─────────────────────────────────────────────────────────────
        def _hc_row_style(row):
            _c = _STRUCT_COLORS_HC.get(row.get("structure", ""), "#888888")
            return [f"background-color:{_c}18;"] * len(row)

        _hc_display = _hc_df[["ticker","date","structure","prob_pct",
                               "ib_high","ib_low","poc_price"]].copy()
        _hc_display.columns = ["Ticker","Date","Structure","Probability %",
                                "IB High","IB Low","POC"]
        _hc_display["Probability %"] = _hc_display["Probability %"].apply(
            lambda x: f"{x:.1f}%"
        )

        try:
            st.dataframe(
                _hc_display.style.apply(_hc_row_style, axis=1),
                use_container_width=True, hide_index=True, height="stretch"
            )
        except Exception:
            st.dataframe(_hc_display, use_container_width=True, hide_index=True, height="stretch")

        c1, c2 = st.columns([3, 1])
        with c2:
            if st.button("🗑 Clear list", key="hc_clear_btn"):
                try:
                    os.remove(HICONS_FILE)
                except Exception:
                    pass
                st.rerun()

    st.markdown("---")
    df = _cached_load_accuracy_tracker()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — All-Time Structure Distribution
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 All-Time Structure Distribution")
    st.caption("Every structure classified since you started using the dashboard (from accuracy tracker log).")

    if df.empty or "actual" not in df.columns:
        st.info("No data yet — run analyses to start populating the distribution.")
    else:
        _dist = df["actual"].dropna()
        _dist = _dist[_dist.str.strip() != ""]

        if _dist.empty:
            st.info("No actual structure data logged yet.")
        else:
            # Count + percent
            _counts = _dist.value_counts().reset_index()
            _counts.columns = ["structure", "count"]
            _counts["pct"] = (_counts["count"] / _counts["count"].sum() * 100).round(1)
            _counts["label_clean"] = _counts["structure"].apply(_clean_structure_label)
            _counts["color"] = _counts["structure"].apply(_structure_color)
            _counts = _counts.sort_values("pct", ascending=True)   # horizontal bar → ascending

            # ── Pill badges row ────────────────────────────────────────────────
            pills_html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px 0;">'
            for _, row in _counts.sort_values("pct", ascending=False).iterrows():
                c = row["color"]
                pills_html += (
                    f'<span style="background:{c}22; border:1px solid {c}55; border-radius:20px; '
                    f'padding:4px 12px; font-size:12px; color:{c}; white-space:nowrap;">'
                    f'<b>{row["pct"]:.0f}%</b> {row["label_clean"]} '
                    f'<span style="color:#555; font-size:10px;">({int(row["count"])})</span></span>'
                )
            pills_html += "</div>"
            st.markdown(pills_html, unsafe_allow_html=True)

            # ── Horizontal bar chart ──────────────────────────────────────────
            fig_dist = go.Figure(go.Bar(
                x=_counts["pct"],
                y=_counts["label_clean"],
                orientation="h",
                marker_color=_counts["color"].tolist(),
                text=[f"  {p:.1f}%  ({n})" for p, n in zip(_counts["pct"], _counts["count"])],
                textposition="outside",
                cliponaxis=False,
            ))
            fig_dist.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"),
                height=max(240, len(_counts) * 44 + 60),
                xaxis=dict(range=[0, min(100, _counts["pct"].max() * 1.25)],
                           gridcolor="#2a2a4a", title="% of all sessions",
                           ticksuffix="%"),
                yaxis=dict(gridcolor="#2a2a4a", tickfont=dict(size=12)),
                margin=dict(l=10, r=80, t=20, b=40),
            )
            st.plotly_chart(fig_dist, use_container_width=True)

            # ── Trend vs Balance split ────────────────────────────────────────
            _directional_keys = ["trend", "bear", "double", "variation"]
            _balanced_keys    = ["normal", "neutral", "non", "balanced"]

            def _classify_side(s):
                sl = s.lower()
                if any(k in sl for k in _directional_keys):
                    return "Directional"
                if any(k in sl for k in _balanced_keys):
                    return "Balanced"
                return "Other"

            _sides = _dist.apply(_classify_side).value_counts()
            _total_sides = _sides.sum()
            dir_pct = _sides.get("Directional", 0) / _total_sides * 100
            bal_pct = _sides.get("Balanced", 0) / _total_sides * 100

            d1, d2, d3 = st.columns(3)
            d1.metric("📈 Directional Days", f"{dir_pct:.0f}%",
                      help="Trend Day, Trend Bear, Double Distribution, Normal Variation")
            d2.metric("⚖️ Balanced Days",    f"{bal_pct:.0f}%",
                      help="Normal, Neutral, Neutral Extreme, Non-Trend")
            d3.metric("📋 Total Sessions",   int(_total_sides))

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Brain Accuracy
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 Brain Prediction Accuracy")

    if df.empty:
        st.info("No accuracy data yet — run analyses after 10:30 ET to start logging brain predictions.")
        return

    total    = len(df)
    correct  = (df["correct"] == "✅").sum()
    acc_rate = correct / total * 100 if total > 0 else 0
    wrong    = total - correct

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Predictions", total)
    c2.metric("Correct", f"{correct}  ({acc_rate:.0f}%)")
    c3.metric("Wrong",   wrong)
    c4.metric("Accuracy Rate", f"{acc_rate:.1f}%")

    # ── Accuracy by predicted structure ──────────────────────────────────────
    if "predicted" in df.columns and "correct" in df.columns:
        grouped = df.groupby("predicted").apply(
            lambda g: pd.Series({
                "total":   len(g),
                "correct": (g["correct"] == "✅").sum(),
                "acc":     (g["correct"] == "✅").sum() / len(g) * 100,
            })
        ).reset_index()
        grouped = grouped.sort_values("acc", ascending=False)

        st.markdown("**Accuracy by Predicted Structure**")
        bar_colors = [
            "#4caf50" if a >= 60 else "#ffa726" if a >= 40 else "#ef5350"
            for a in grouped["acc"]
        ]
        fig_acc = go.Figure(go.Bar(
            x=grouped["predicted"].apply(_clean_structure_label),
            y=grouped["acc"].round(1),
            marker_color=bar_colors,
            text=[f"{a:.0f}%<br>({int(c)}/{int(t)})"
                  for a, c, t in zip(grouped["acc"], grouped["correct"], grouped["total"])],
            textposition="outside",
        ))
        fig_acc.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"), height=320,
            yaxis=dict(range=[0, 115], gridcolor="#2a2a4a", title="Accuracy %"),
            xaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(t=20, b=60, l=50, r=20),
        )
        st.plotly_chart(fig_acc, use_container_width=True)

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2.5 — Predictive Probability Engine
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 Your Predictive Win Rates")
    st.caption(
        "Based on your logged signal outcomes. Every time you verify an EOD result, "
        "the conditions (Edge Score, RVOL, Structure) are paired with the outcome and "
        "stored here. After 3+ samples per setup, the system shows you your actual "
        "historical win rate for each condition cluster."
    )
    _pred_rates = compute_win_rates(_AUTH_USER_ID, min_samples=1)
    if not _pred_rates:
        st.info(
            "No outcome data yet. Verify predictions in the EOD review panel (Journal tab) "
            "to start building your personal win rate database. Each verification adds a data point."
        )
    else:
        _pred_total = _pred_rates.get("_total", {})
        _pred_by_struct = _pred_rates.get("_by_struct", {})
        _pred_by_edge   = _pred_rates.get("_by_edge", {})

        # Overall stat
        if _pred_total.get("n", 0) > 0:
            _pt_wr = _pred_total["win_rate"] * 100
            _pt_n  = _pred_total["n"]
            _pt_c  = "#4caf50" if _pt_wr >= 60 else "#ffa726" if _pt_wr >= 45 else "#ef5350"
            st.markdown(
                f'<div style="background:{_pt_c}11; border:1px solid {_pt_c}44; '
                f'border-radius:8px; padding:12px 18px; margin-bottom:12px;">'
                f'<span style="font-size:13px; color:#888;">Overall (all setups logged):</span> '
                f'<span style="font-size:22px; font-weight:900; color:{_pt_c};">{_pt_wr:.0f}%</span>'
                f'<span style="font-size:12px; color:#555;"> win rate — {_pt_n} outcomes recorded</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # By structure
        if _pred_by_struct:
            st.markdown("**Win Rate by Structure**")
            _struct_rows = sorted(
                [(k, v) for k, v in _pred_by_struct.items() if v.get("n", 0) >= 1],
                key=lambda x: x[1]["win_rate"], reverse=True
            )
            for _sk, _sv in _struct_rows:
                _swr = _sv["win_rate"] * 100
                _sn  = _sv["n"]
                _sc  = "#4caf50" if _swr >= 60 else "#ffa726" if _swr >= 45 else "#ef5350"
                _bar = int(_swr)
                st.markdown(
                    f'<div style="display:flex; align-items:center; gap:10px; '
                    f'margin:3px 0; font-size:12px;">'
                    f'<span style="min-width:160px; color:#ccc;">{_sk}</span>'
                    f'<div style="flex:1; background:#1a1a2e; border-radius:4px; height:12px; '
                    f'position:relative; overflow:hidden;">'
                    f'<div style="width:{_bar}%; background:{_sc}; height:100%; '
                    f'border-radius:4px;"></div></div>'
                    f'<span style="min-width:60px; color:{_sc}; font-weight:700;">'
                    f'{_swr:.0f}% ({_sn})</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # By edge band
        if _pred_by_edge:
            st.markdown("**Win Rate by Edge Score Band**")
            _edge_order = ["75+", "65-75", "50-65", "<50"]
            _ec_cols = st.columns(len(_edge_order))
            for _ec_i, _eb in enumerate(_edge_order):
                _ev = _pred_by_edge.get(_eb, {})
                if _ev and _ev.get("n", 0) > 0:
                    _ewr = _ev["win_rate"] * 100
                    _en  = _ev["n"]
                    _ec  = "#4caf50" if _ewr >= 60 else "#ffa726" if _ewr >= 45 else "#ef5350"
                    _ec_cols[_ec_i].markdown(
                        f'<div style="background:#12122288; border:1px solid {_ec}44; '
                        f'border-radius:6px; padding:10px; text-align:center;">'
                        f'<div style="font-size:10px; color:#666; text-transform:uppercase; '
                        f'letter-spacing:1px; margin-bottom:4px;">Edge {_eb}</div>'
                        f'<div style="font-size:22px; font-weight:900; color:{_ec};">'
                        f'{_ewr:.0f}%</div>'
                        f'<div style="font-size:11px; color:#555;">{_en} trades</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    _ec_cols[_ec_i].markdown(
                        f'<div style="background:#12122244; border:1px solid #1a1a2e; '
                        f'border-radius:6px; padding:10px; text-align:center; opacity:0.4;">'
                        f'<div style="font-size:10px; color:#555; text-transform:uppercase; '
                        f'letter-spacing:1px;">Edge {_eb}</div>'
                        f'<div style="font-size:16px; color:#333;">—</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Adaptive Learning Status
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🔬 Adaptive Learning Status")
    st.caption(
        f"Brain recalibrates its probability weights every {_RECALIBRATE_EVERY} comparisons. "
        f"Structures with ≥5 samples are eligible. Multiplier > 1.0 = trusted; < 1.0 = confidence reduced."
    )

    _ws_rows = brain_weights_summary(_AUTH_USER_ID)
    _raw_w   = _cached_load_brain_weights(user_id=_AUTH_USER_ID)

    if not _ws_rows:
        st.info(f"Learning begins once at least 5 comparisons are logged for any structure. "
                f"Full recalibration fires every {_RECALIBRATE_EVERY} entries.")
    else:
        # ── Weight bar chart ──────────────────────────────────────────────────
        _wdf = pd.DataFrame(_ws_rows)
        _bar_colors = [
            "#4caf50" if m >= 1.3 else "#26a69a" if m >= 1.0 else
            "#ffa726" if m >= 0.7 else "#ef5350"
            for m in _wdf["Multiplier"]
        ]
        fig_w = go.Figure(go.Bar(
            x=_wdf["Multiplier"],
            y=_wdf["Structure"].apply(_clean_structure_label),
            orientation="h",
            marker_color=_bar_colors,
            text=[f"  {m:.2f}×  ({a:.0f}% acc / {n} samples)"
                  for m, a, n in zip(_wdf["Multiplier"], _wdf["Accuracy"], _wdf["Samples"])],
            textposition="outside",
            cliponaxis=False,
        ))
        fig_w.add_vline(x=1.0, line_dash="dash", line_color="#5c6bc0",
                        annotation_text="Baseline", annotation_position="top")
        fig_w.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
            height=max(220, len(_wdf) * 44 + 60),
            xaxis=dict(range=[0.0, 1.8], gridcolor="#2a2a4a",
                       title="Probability Multiplier"),
            yaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(l=10, r=120, t=20, b=40),
        )
        st.plotly_chart(fig_w, use_container_width=True)

        # ── Summary table ──────────────────────────────────────────────────────
        _wdf_disp = _wdf[["Structure", "Samples", "Accuracy", "Multiplier", "Status"]].copy()
        _wdf_disp["Accuracy"] = _wdf_disp["Accuracy"].apply(lambda x: f"{x:.1f}%")
        _wdf_disp["Multiplier"] = _wdf_disp["Multiplier"].apply(lambda x: f"{x:.3f}×")
        st.dataframe(_wdf_disp, use_container_width=True, hide_index=True)

        # ── Next recalibration countdown ──────────────────────────────────────
        _current_n = len(df) if not df.empty else 0
        _next_recal = _RECALIBRATE_EVERY - (_current_n % _RECALIBRATE_EVERY)
        if _next_recal == _RECALIBRATE_EVERY:
            st.success(f"✅ Weights just recalibrated at {_current_n} entries.")
        else:
            _entry_word = "entry" if _next_recal == 1 else "entries"
            st.info(f"🔄 Next recalibration in **{_next_recal}** more {_entry_word} "
                    f"({_current_n} logged so far).")

        # ── Manual recalibrate button — LOCKED ───────────────────────────────
        st.markdown(
            '<div style="background:#1a0a00; border:1px solid #e65100; border-radius:8px; '
            'padding:12px 16px; margin-top:8px;">'
            '<span style="font-size:13px; font-weight:700; color:#ff6d00;">🔒 Auto-Managed — Do Not Touch</span><br>'
            '<span style="font-size:12px; color:#bf360c;">'
            'Brain weights are automatically recalibrated by the bot at <b>4:10 PM ET</b> every trading day. '
            'Manual recalibration is disabled to protect the learning model. '
            'If you believe a manual recalibration is needed, ask in Replit chat first.'
            '</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — Full history table
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📋 Full History")
    display_cols = ["timestamp", "symbol", "predicted", "actual", "correct",
                    "entry_price", "exit_price", "mfe"]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
    if "timestamp" in disp.columns:
        disp = disp.sort_values("timestamp", ascending=False)

    def _style_row(row):
        color = "#4caf5022" if row.get("correct") == "✅" else "#ef535022"
        return [f"background-color: {color}"] * len(row)

    try:
        styled = disp.style.apply(_style_row, axis=1)
        st.dataframe(styled, use_container_width=True, height=320)
    except Exception:
        st.dataframe(disp, use_container_width=True, height=320)

    csv_str = df.to_csv(index=False)
    st.download_button(
        "⬇ Download Tracker CSV", data=csv_str,
        file_name="accuracy_tracker.csv", mime="text/csv"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SMALL ACCOUNT CHALLENGE TAB
# ══════════════════════════════════════════════════════════════════════════════

def render_sa_tab():
    """Small Account Challenge — Ross Cameron / Warrior Trading methodology.
    Five Pillars | Sniper Entry | Position Sizer | PDT Tracker | Recovery Ratio
    """
    now_et   = datetime.now(EASTERN)
    hour_et  = now_et.hour + now_et.minute / 60.0
    snap     = st.session_state.get("last_analysis_state") or {}
    bars     = st.session_state.get("last_bars")
    ticker   = snap.get("ticker", "")
    price    = snap.get("price", 0.0)
    rvol     = snap.get("rvol")
    ib_high  = snap.get("ib_high")
    ib_low   = snap.get("ib_low")
    poc_p    = snap.get("poc_price")
    val_p    = snap.get("val_price")
    vah_p    = snap.get("vah_price")
    pct_chg  = snap.get("pct_change", 0.0)

    # ── SECTION 1 — TRADING WINDOW + PDT TRACKER ─────────────────────────────
    window_open = 7.0 <= hour_et < 10.0
    mins_left   = max(0, int((10.0 - hour_et) * 60)) if window_open else 0
    if window_open:
        wc, wlbl = "#4caf50", f"✅  SNIPER WINDOW ACTIVE — {mins_left} min remaining"
        wbg = "#1b5e2022"
    elif hour_et < 7.0:
        wc, wlbl = "#ffa726", f"⏳  Pre-market — Window opens at 7:00 AM ET"
        wbg = "#e6510022"
    else:
        wc, wlbl = "#ef5350", "❌  WINDOW CLOSED — Trades after 10 AM carry lower win rate"
        wbg = "#4e111122"

    wcol1, wcol2 = st.columns([3, 1])
    with wcol1:
        st.markdown(
            f'<div style="background:{wbg};border:1px solid {wc};border-radius:8px;'
            f'padding:10px 20px;margin-bottom:8px;">'
            f'<span style="font-size:14px;font-weight:700;color:{wc};">{wlbl}</span>'
            f'<span style="font-size:12px;color:#666;margin-left:18px;">'
            f'{now_et.strftime("%H:%M:%S ET")}</span></div>',
            unsafe_allow_html=True,
        )
    with wcol2:
        pdt_used = st.number_input(
            "Day trades used (this week)", min_value=0, max_value=10,
            value=int(st.session_state.get("sa_pdt_used", 0)),
            step=1, key="sa_pdt_inp",
            help="PDT rule: ≤3 round-trip day trades in 5 days for accounts under $25k"
        )
        st.session_state.sa_pdt_used = pdt_used
        pdt_rem   = max(0, 3 - pdt_used)
        pdt_color = "#ef5350" if pdt_rem == 0 else "#ffa726" if pdt_rem == 1 else "#4caf50"
        st.markdown(
            f'<div style="font-size:18px;font-weight:800;color:{pdt_color};">'
            f'{pdt_rem}/3 trades left</div>'
            + ('<div style="font-size:11px;color:#ef5350;">⛔ PDT LIMIT — no more day trades</div>'
               if pdt_rem == 0 else ''),
            unsafe_allow_html=True,
        )

    # ── SECTION 2 — FIVE PILLARS EVALUATION ──────────────────────────────────
    st.markdown("---")
    st.markdown("### 🏛️ Five Pillars Evaluation")
    if not ticker:
        st.info("Run a Historical Analysis on the Main Chart tab first — "
                "the Five Pillars will auto-populate for the loaded ticker.")
    else:
        c_float, c_news = st.columns(2)
        with c_float:
            float_est = st.number_input(
                "Float estimate (M shares) — check Finviz / Benzinga",
                min_value=0.0, max_value=1000.0,
                value=float(st.session_state.get("sa_float_est", 0.0)),
                step=0.5, format="%.1f", key="sa_float_inp",
            )
            st.session_state.sa_float_est = float_est
        with c_news:
            news_ok = st.checkbox(
                "✅  Catalyst confirmed (FDA, 13D filing, PR, earnings beat, etc.)",
                value=st.session_state.get("sa_news_confirmed", False),
                key="sa_news_chk",
            )
            st.session_state.sa_news_confirmed = news_ok

        # Evaluate pillars
        p_price = 0.25 <= price <= 9.00
        p_chg   = pct_chg >= 50.0
        p_rvol  = (rvol is not None and rvol >= 100.0)
        p_news  = news_ok
        p_f5    = float_est > 0 and float_est <= 5.0
        p_f20   = float_est > 0 and float_est <= 20.0
        p_float = p_f5 or p_f20

        float_note = (
            f"<span style='color:#4caf50;'>Sub-5M ✅ Explosive potential</span> — {float_est:.1f}M" if p_f5 else
            f"<span style='color:#ffa726;'>Sub-20M ⚠️ Acceptable max</span> — {float_est:.1f}M" if p_f20 else
            f"<span style='color:#ef5350;'>&gt;20M ❌ Too big — Algos dominant</span> — {float_est:.1f}M" if float_est > 0 else
            "<span style='color:#555;'>Enter float estimate above</span>"
        )

        def _pill_row(icon, passed, name, note, warn=False):
            c = "#4caf50" if passed else ("#ffa726" if warn else "#ef5350")
            return (
                f'<tr style="border-top:1px solid #1c2040;">'
                f'<td style="padding:6px 10px;font-size:16px;">{icon}</td>'
                f'<td style="padding:6px 10px;font-weight:700;color:{c};white-space:nowrap;">{name}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#aaa;">{note}</td>'
                f'</tr>'
            )

        score = sum([p_price, p_chg, p_rvol, p_news, p_float])
        sc    = "#4caf50" if score == 5 else "#ffa726" if score >= 3 else "#ef5350"
        slbl  = ("⭐ A+ — ALL PILLARS MET" if score == 5
                 else "B — Borderline" if score >= 3 else "❌ AVOID — Sub-threshold")

        tbl = (
            '<table style="width:100%;border-collapse:collapse;">'
            + _pill_row("✅" if p_price else "❌", p_price, "Price",
                        f"${price:.2f}  &nbsp;·&nbsp; Target: $0.25 – $9.00")
            + _pill_row("✅" if p_chg else "❌", p_chg, "% Change",
                        f"{pct_chg:+.1f}%  &nbsp;·&nbsp; Need ≥ 50%")
            + _pill_row("✅" if p_rvol else "❌", p_rvol, "RVOL",
                        f"{f'{rvol:.0f}×' if rvol else '—'}  &nbsp;·&nbsp; Need ≥ 100× (institutional prints)")
            + _pill_row("✅" if p_news else "⚠️", p_news, "News Catalyst",
                        "FDA / 13D / PR / earnings beat required — momentum without news fades faster",
                        warn=True)
            + _pill_row("✅" if p_float else ("⚠️" if p_f20 else "❌"), p_float, "Float",
                        float_note, warn=(p_f20 and not p_f5))
            + '</table>'
        )
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:10px;'
            f'padding:14px 18px;margin:8px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
            f'<span style="font-size:17px;font-weight:900;color:#e0e0e0;letter-spacing:1px;">{ticker}</span>'
            f'<span style="background:{sc}22;border:1px solid {sc};border-radius:5px;'
            f'padding:4px 14px;font-size:13px;font-weight:800;color:{sc};">{slbl}</span>'
            f'</div>{tbl}</div>',
            unsafe_allow_html=True,
        )

        # Pre-market float turnover (bonus)
        if float_est > 0:
            pm_row = next(
                (r for r in st.session_state.scanner_results if r.get("ticker") == ticker), None)
            if pm_row and pm_row.get("pm_vol"):
                float_sh = float_est * 1_000_000
                pmv      = pm_row["pm_vol"]
                tover    = pmv / float_sh * 100.0 if float_sh > 0 else 0.0
                tc  = "#ef5350" if tover > 80 else "#ffa726" if tover > 40 else "#4caf50"
                tlb = ("EXTREME — Float nearly traded through" if tover > 80
                       else "HIGH — Supply/demand heavily imbalanced" if tover > 40
                       else "Normal pre-market activity")
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                    f'padding:8px 16px;margin:4px 0;font-size:12px;color:#aaa;">'
                    f'📊 PM Float Turnover: <b style="color:{tc};">{tover:.1f}%</b>'
                    f' <span style="color:{tc};">({tlb})</span>'
                    f' — {pmv:,} shares vs ~{float_sh:,.0f} float</div>',
                    unsafe_allow_html=True,
                )

    # ── SECTION 3 — SETUP QUALITY (if bars loaded) ────────────────────────────
    if bars is not None and len(bars) >= 5:
        st.markdown("---")
        st.markdown("### 📡 Setup Quality Analysis")

        atr    = compute_atr(bars)
        greens = count_consecutive_greens(bars)
        mline, sline, hist = compute_macd(bars["close"])
        macd_now  = float(mline.iloc[-1])
        sig_now   = float(sline.iloc[-1])
        hist_now  = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else hist_now

        # Front Side: MACD above signal AND histogram not yet shrinking
        macd_above  = macd_now > sig_now
        hist_open   = hist_now > 0 and hist_now >= hist_prev * 0.80
        front_side  = macd_above and hist_open

        # VA Breakout
        va_breakout = (vah_p is not None and price is not None
                       and price > vah_p and rvol is not None and rvol >= 100.0)

        # POC shift
        bin_c = st.session_state.get("sa_bin_centers")
        vap_c = st.session_state.get("sa_vap")
        poc_shift_lbl, poc_shift_col = (
            detect_poc_shift(bin_c, vap_c)
            if bin_c is not None and vap_c is not None and len(bin_c) > 0
            else ("—", "#aaa")
        )

        # Whole/Half dollar levels
        try:
            whl = get_whole_half_levels(float(bars["low"].min()), float(bars["high"].max()))
        except Exception:
            whl = []

        # Opening Range (first 5 bars)
        or_hi = float(bars["high"].iloc[:5].max()) if len(bars) >= 5 else None
        or_lo = float(bars["low"].iloc[:5].min())  if len(bars) >= 5 else None

        qa1, qa2, qa3 = st.columns(3)

        with qa1:
            fsc = "#4caf50" if front_side else "#ef5350"
            fsl = "✅ FRONT SIDE" if front_side else "⛔ BACK SIDE"
            fsb = "MACD open & diverging" if front_side else "MACD converging — fade risk"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid {fsc}55;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Trade Side</div>'
                f'<div style="font-size:19px;font-weight:800;color:{fsc};">{fsl}</div>'
                f'<div style="font-size:11px;color:#777;margin-top:3px;">{fsb}</div>'
                f'<div style="font-size:10px;color:#444;margin-top:4px;font-family:monospace;">'
                f'MACD {macd_now:+.4f} · Sig {sig_now:+.4f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with qa2:
            gc  = "#4caf50" if greens >= 3 else "#ffa726" if greens >= 1 else "#ef5350"
            glb = "🔥 SURGE" if greens >= 3 else "Building" if greens >= 1 else "No Surge"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid {gc}55;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Consecutive Greens</div>'
                f'<div style="font-size:34px;font-weight:900;color:{gc};line-height:1;">{greens}</div>'
                f'<div style="font-size:12px;color:{gc};margin-top:3px;">{glb}</div>'
                f'<div style="font-size:10px;color:#444;margin-top:4px;">Need 3+ for surge confirmation</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with qa3:
            vac = "#4caf50" if va_breakout else "#555"
            vat = "✅ BREAKING VA on RVOL ≥ 100" if va_breakout else "Inside Value Area"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">VA / POC</div>'
                f'<div style="font-size:13px;font-weight:700;color:{poc_shift_col};margin:4px 0;">{poc_shift_lbl}</div>'
                f'<div style="font-size:11px;color:{vac};">{vat}</div>'
                + (f'<div style="font-size:10px;color:#555;margin-top:3px;">VAH ${vah_p:.2f} · VAL ${val_p:.2f}</div>'
                   if vah_p and val_p else '')
                + f'</div>',
                unsafe_allow_html=True,
            )

        # Opening Range + ATR
        if or_hi and or_lo:
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:8px 16px;margin:8px 0;font-size:12px;color:#aaa;">'
                f'📐 Opening Range (first 5 bars): '
                f'<b style="color:#00e676;">Hi ${or_hi:.2f}</b> / '
                f'<b style="color:#ef5350;">Lo ${or_lo:.2f}</b> — '
                f'OR Range: <b style="color:#e0e0e0;">${or_hi - or_lo:.2f}</b>'
                f'&nbsp;&nbsp;|&nbsp;&nbsp;ATR(14): <b style="color:#5c6bc0;">${atr:.2f}</b>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Whole / Half dollar resistance levels
        if whl:
            whole_s = "  ".join(
                f"<b style='color:#ffa726;'>${l:.2f}</b>" if l == int(l)
                else f"<span style='color:#7986cb;'>${l:.2f}</span>"
                for l in whl
            )
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:8px 16px;margin:4px 0;font-size:12px;color:#aaa;">'
                f'🎯 Whole/Half-Dollar Levels: {whole_s}'
                f'&nbsp;&nbsp;<span style="color:#444;font-size:10px;">'
                f'(orange = whole $, blue = half $  — expect resistance, then support once broken)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── SECTION 4 — SNIPER ENTRY CHECKLIST ───────────────────────────────────
    st.markdown("---")
    st.markdown("### 🎯 Sniper Entry Checklist — First Candle New High")
    st.caption("All 7 boxes must be ✅ before pulling the trigger. One unchecked = wait.")

    ch1, ch2 = st.columns(2)
    with ch1:
        chk1 = st.checkbox("📈 Surge: 3–5 consecutive green candles on volume spike", key="sa_c1")
        chk2 = st.checkbox("🔽 Pullback: 1–2 red candles on notably lighter volume",   key="sa_c2")
        chk3 = st.checkbox("🔺 Apex: Price approaching high of the pullback candle",   key="sa_c3")
        chk4 = st.checkbox("✅ Trigger: Candle BROKE the apex (confirmed — not anticipated)", key="sa_c4")
    with ch2:
        chk5 = st.checkbox("📊 MACD: Lines still open / histogram not shrinking",       key="sa_c5")
        chk6 = st.checkbox("📋 Level 2: Surge of green orders visible on tape",         key="sa_c6")
        chk7 = st.checkbox("🛡️ PDT: Day trade slot available (not at 3-trade limit)",   key="sa_c7",
                            value=(st.session_state.get("sa_pdt_used", 0) < 3))
        entry_type = st.selectbox(
            "Entry Type",
            ["First Pullback", "ABCD Pattern", "High of Day Break", "Other"],
            key="sa_entry_sel",
        )

    checks_passed = sum([chk1, chk2, chk3, chk4, chk5, chk6, chk7])
    if checks_passed == 7:
        st.markdown(
            '<div style="background:#4caf5022;border:2px solid #4caf50;border-radius:8px;'
            'padding:12px 20px;font-weight:700;color:#4caf50;font-size:15px;margin:8px 0;">'
            '🟢 ALL 7 CHECKS PASSED — VALID SNIPER ENTRY</div>',
            unsafe_allow_html=True,
        )
    elif checks_passed >= 5:
        st.markdown(
            f'<div style="background:#ffa72622;border:1px solid #ffa726;border-radius:8px;'
            f'padding:12px 20px;font-weight:700;color:#ffa726;font-size:14px;margin:8px 0;">'
            f'⚠️ {checks_passed}/7 — Partial. Tighten your criteria before entry.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:#ef535022;border:1px solid #ef5350;border-radius:8px;'
            f'padding:12px 20px;font-weight:700;color:#ef5350;font-size:14px;margin:8px 0;">'
            f'🔴 {checks_passed}/7 — DO NOT ENTER. Be the sniper, not the machine gunner.</div>',
            unsafe_allow_html=True,
        )

    # ── SECTION 5 — POSITION SIZER ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 💰 Sniper Position Sizer")

    ps1, ps2, ps3 = st.columns(3)
    with ps1:
        acct = st.number_input(
            "Account Balance ($)", min_value=100.0, max_value=1_000_000.0,
            value=float(st.session_state.get("sa_account_bal", 5000.0)),
            step=500.0, format="%.0f", key="sa_acct",
        )
        st.session_state.sa_account_bal = acct
        risk_p = st.number_input(
            "Max Risk per Trade (%)", min_value=0.5, max_value=10.0,
            value=float(st.session_state.get("sa_risk_pct", 2.0)),
            step=0.5, format="%.1f", key="sa_riskp",
        )
        st.session_state.sa_risk_pct = risk_p
    with ps2:
        atr_v = compute_atr(bars) if bars is not None and len(bars) >= 5 else 0.20
        stop_d = st.number_input(
            "Stop Distance ($)",
            min_value=0.01, max_value=20.0,
            value=round(max(0.05, atr_v), 2),
            step=0.01, format="%.2f",
            help="ATR(14) auto-filled. Adjust to your actual stop placement.",
            key="sa_stopd",
        )
        st.markdown(
            f'<div style="font-size:11px;color:#5c6bc0;margin-top:4px;">'
            f'ATR(14) auto-fill: ${atr_v:.2f}</div>',
            unsafe_allow_html=True,
        )
    with ps3:
        risk_dol   = acct * (risk_p / 100.0)
        max_shares = int(risk_dol / stop_d) if stop_d > 0 else 0
        max_pos    = max_shares * price if price > 0 else 0.0
        daily_lim  = acct * 0.25
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #5c6bc0;border-radius:10px;'
            f'padding:14px;text-align:center;">'
            f'<div style="font-size:10px;color:#5c6bc0;text-transform:uppercase;letter-spacing:1px;">Risk Budget</div>'
            f'<div style="font-size:22px;font-weight:800;color:#e0e0e0;">${risk_dol:.0f}</div>'
            f'<div style="font-size:11px;color:#555;margin:4px 0;">Max Shares</div>'
            f'<div style="font-size:30px;font-weight:900;color:#4caf50;line-height:1;">{max_shares:,}</div>'
            f'<div style="font-size:11px;color:#555;">Position size: ${max_pos:,.0f}</div>'
            f'<div style="font-size:11px;color:#ef5350;margin-top:6px;">Daily loss limit: ${daily_lim:,.0f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
        f'padding:8px 16px;margin:6px 0;font-size:12px;color:#aaa;">'
        f'💡 <b style="color:#e0e0e0;">Cash Account Rule:</b> Treat every trade as your ONLY trade of the day. '
        f'Settle T+1 before sizing up. Sniper — not machine gunner. '
        f'<b style="color:#ef5350;">Stop trading if daily loss hits ${daily_lim:,.0f}.</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── SECTION 5b — GOD MODE LIVE EXECUTION ────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div style="background:linear-gradient(135deg,#1a0a2e,#0d1b2a);'
        'border:2px solid #7c4dff;border-radius:12px;padding:14px 18px;margin-bottom:8px;">'
        '<span style="font-size:18px;font-weight:900;color:#e040fb;letter-spacing:1px;">'
        '⚡ GOD MODE — Live Trade Execution</span>'
        '<span style="font-size:11px;color:#546e7a;margin-left:12px;">'
        'Routes directly to Alpaca broker</span></div>',
        unsafe_allow_html=True,
    )

    _gm_sa_ticker = st.session_state.get("sa_ticker_input", "").strip().upper() or ticker
    _gm_c1, _gm_c2, _gm_c3 = st.columns([1.2, 1.2, 1.6])

    with _gm_c1:
        _gm_ticker = st.text_input(
            "Ticker", value=_gm_sa_ticker, key="gm_ticker",
            placeholder="GME",
        ).strip().upper()
        _gm_side = st.selectbox(
            "Side", ["buy", "sell"], key="gm_side",
            format_func=lambda x: "🟢 BUY" if x == "buy" else "🔴 SELL",
        )

    with _gm_c2:
        _gm_default_idx = 0 if get_trading_mode() else 1
        _gm_env = st.selectbox(
            "Environment", ["Paper Trading", "Live Trading"],
            index=_gm_default_idx, key="gm_env",
        )
        _gm_is_paper = _gm_env == "Paper Trading"
        _gm_order_type = st.selectbox(
            "Order Type", ["Market", "Limit"], key="gm_order_type",
        )

    with _gm_c3:
        _gm_qty = st.number_input(
            "Shares", min_value=1, max_value=10_000,
            value=max(1, max_shares),
            step=1, key="gm_qty",
        )
        _gm_limit_px = None
        if _gm_order_type == "Limit":
            _gm_limit_px = st.number_input(
                "Limit Price ($)", min_value=0.01, max_value=10_000.0,
                value=round(float(price) if price else 1.00, 2),
                step=0.01, format="%.2f", key="gm_limit_px",
            )

    # Safety warning for live mode
    if not _gm_is_paper:
        st.warning(
            "⚠️ **LIVE trading is enabled.** This will send a REAL order with REAL money "
            "to your Alpaca brokerage account. Double-check everything before firing.",
            icon="⚠️",
        )

    _gm_env_badge  = "🔵 PAPER" if _gm_is_paper else "🔴 LIVE"
    _gm_btn_color  = "#5c6bc0" if _gm_is_paper else "#b71c1c"
    _gm_btn_label  = (
        f"🚀 EXECUTE {_gm_env_badge} ORDER — "
        f"{_gm_side.upper()} {_gm_qty} {_gm_ticker or '???'}"
    )

    st.markdown(
        f'<style>.gm-fire-btn button{{background:{_gm_btn_color}!important;'
        f'color:#fff!important;font-size:17px!important;font-weight:900!important;'
        f'border-radius:10px!important;padding:14px!important;'
        f'letter-spacing:1px!important;border:none!important;}}</style>',
        unsafe_allow_html=True,
    )
    with st.container():
        st.markdown('<div class="gm-fire-btn">', unsafe_allow_html=True)
        _gm_fire = st.button(_gm_btn_label, use_container_width=True, key="gm_fire_btn")
        st.markdown('</div>', unsafe_allow_html=True)

    if _gm_fire:
        if not _gm_ticker:
            st.error("Enter a ticker symbol first.")
        elif not api_key or not secret_key:
            st.error("No Alpaca credentials — enter your API Key and Secret in the sidebar.")
        else:
            with st.spinner(f"Sending {'paper' if _gm_is_paper else 'LIVE'} order to Alpaca…"):
                _gm_result = execute_alpaca_trade(
                    api_key=api_key,
                    secret_key=secret_key,
                    is_paper=_gm_is_paper,
                    ticker=_gm_ticker,
                    qty=int(_gm_qty),
                    side=_gm_side,
                    limit_price=_gm_limit_px,
                )
            if _gm_result["success"]:
                st.success(_gm_result["message"])
                st.caption(f"Order ID: `{_gm_result['order_id']}`")
            else:
                st.error(_gm_result["message"])

    # ── SECTION 6 — RECOVERY RATIO ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📉 Recovery Ratio — The Math of Drawdown")
    st.caption("A 25% loss requires a 33% gain just to break even. Protect capital first.")

    rr1, rr2 = st.columns([1, 2])
    with rr1:
        loss_in = st.slider("Simulate a loss of:", 1, 90, 25, format="%d%%", key="sa_rrs")
        rr_val  = compute_recovery_ratio(loss_in)
        rrc     = "#4caf50" if loss_in < 15 else "#ffa726" if loss_in < 35 else "#ef5350"
        st.markdown(
            f'<div style="background:#0c1030;border:2px solid {rrc};border-radius:10px;'
            f'padding:16px;text-align:center;margin:8px 0;">'
            f'<div style="font-size:12px;color:#aaa;">After a <b style="color:{rrc};">{loss_in}%</b> loss</div>'
            f'<div style="font-size:11px;color:#555;margin:4px 0;">you need to gain</div>'
            f'<div style="font-size:38px;font-weight:900;color:{rrc};">{rr_val:.1f}%</div>'
            f'<div style="font-size:11px;color:#aaa;">to return to break-even</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with rr2:
        rows_rr = [(10, "Manageable"), (20, "Manageable"), (25, "Dangerous"),
                   (33, "Dangerous"),  (50, "DEATH SPIRAL"), (75, "DEATH SPIRAL")]
        tbl = '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:monospace;">'
        tbl += ('<tr style="color:#5c6bc0;font-size:10px;text-transform:uppercase;">'
                '<th style="text-align:left;padding:5px 10px;">Loss %</th>'
                '<th style="text-align:left;padding:5px 10px;">Must Gain</th>'
                '<th style="text-align:left;padding:5px 10px;">Verdict</th></tr>')
        for lp, verdict in rows_rr:
            rr_v  = compute_recovery_ratio(lp)
            rc2   = "#4caf50" if lp < 15 else "#ffa726" if lp < 35 else "#ef5350"
            tbl  += (f'<tr style="border-top:1px solid #1c2040;">'
                     f'<td style="padding:5px 10px;color:{rc2};font-weight:700;">−{lp}%</td>'
                     f'<td style="padding:5px 10px;color:{rc2};">+{rr_v:.1f}%</td>'
                     f'<td style="padding:5px 10px;color:{rc2};">{verdict}</td></tr>')
        tbl += '</table>'
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:8px;'
            f'padding:12px;">{tbl}</div>',
            unsafe_allow_html=True,
        )

    # ── SECTION 7 — SNIPER TRADE LOG ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Sniper Trade Log — Cognitive Audit")
    st.caption(
        "The data doesn't lie: confirmed entries (waited for the break) vs. "
        "anticipated entries (jumped early). Track both — your win rate will show the difference."
    )

    with st.expander("➕ Log a Trade", expanded=False):
        lg1, lg2, lg3 = st.columns(3)
        with lg1:
            lg_tick  = st.text_input("Ticker", value=ticker or "", key="sa_lgt").upper()
            lg_ep    = st.number_input("Entry Price", min_value=0.01,
                                        value=float(price or 1.0), step=0.01,
                                        format="%.2f", key="sa_lgep")
            lg_xp    = st.number_input("Exit Price", min_value=0.01,
                                        value=float(price or 1.0), step=0.01,
                                        format="%.2f", key="sa_lgxp")
        with lg2:
            lg_et    = st.selectbox("Entry Type",
                                     ["First Pullback", "ABCD Pattern",
                                      "High of Day Break", "Other"],
                                     key="sa_lget")
            lg_conf  = st.radio("Entry Discipline",
                                 ["✅ Confirmed (waited for break)",
                                  "⚠️ Anticipated (jumped early)"],
                                 key="sa_lgconf")
            lg_news  = st.checkbox("Catalyst confirmed at entry time", key="sa_lgnews")
        with lg3:
            in_win   = window_open
            ts_str   = now_et.strftime("%H:%M ET")
            if not in_win:
                st.warning(f"⚠️ {ts_str} — Outside sniper window")
            else:
                st.success(f"✅ {ts_str} — Inside window")
            lg_notes = st.text_area("Notes / reason for entry", height=90,
                                     placeholder="Why you took this trade…",
                                     key="sa_lgnotes")

        if st.button("💾 Log Trade", key="sa_log_btn"):
            pnl_p = round((lg_xp - lg_ep) / lg_ep * 100, 2) if lg_ep > 0 else 0.0
            entry = {
                "date":        now_et.strftime("%Y-%m-%d"),
                "time":        ts_str,
                "ticker":      lg_tick,
                "entry_price": round(lg_ep, 2),
                "exit_price":  round(lg_xp, 2),
                "pnl_pct":     pnl_p,
                "entry_type":  lg_et,
                "confirmed":   "Confirmed" if "Confirmed" in lg_conf else "Anticipated",
                "news":        "✅" if lg_news else "❌",
                "in_window":   "✅" if in_win else "❌",
                "notes":       lg_notes,
            }
            journal = _cached_load_sa_journal()
            journal.append(entry)
            save_sa_journal(journal)
            st.success(f"Logged: {lg_tick} | {pnl_p:+.2f}%")
            st.rerun()

    # Display journal + cognitive audit split
    journal = _cached_load_sa_journal()
    if journal:
        dfj = pd.DataFrame(journal)

        # Win-rate by confirmed vs anticipated
        if "confirmed" in dfj.columns and "pnl_pct" in dfj.columns:
            conf_df = dfj[dfj["confirmed"] == "Confirmed"]
            anti_df = dfj[dfj["confirmed"] == "Anticipated"]
            cwr = (conf_df["pnl_pct"] > 0).mean() * 100 if len(conf_df) else None
            awr = (anti_df["pnl_pct"] > 0).mean() * 100 if len(anti_df) else None

            au1, au2 = st.columns(2)
            with au1:
                cwc = "#4caf50" if (cwr or 0) >= 50 else "#ef5350"
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid {cwc};border-radius:8px;'
                    f'padding:12px;text-align:center;">'
                    f'<div style="font-size:11px;color:#aaa;">✅ Confirmed Entry Win Rate</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{cwc};">'
                    f'{"%.0f%%" % cwr if cwr is not None else "—"}</div>'
                    f'<div style="font-size:10px;color:#555;">{len(conf_df)} trades</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with au2:
                awc = "#4caf50" if (awr or 0) >= 50 else "#ef5350"
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid {awc};border-radius:8px;'
                    f'padding:12px;text-align:center;">'
                    f'<div style="font-size:11px;color:#aaa;">⚠️ Anticipated Entry Win Rate</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{awc};">'
                    f'{"%.0f%%" % awr if awr is not None else "—"}</div>'
                    f'<div style="font-size:10px;color:#555;">{len(anti_df)} trades</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Recent trades table
        st.markdown("**Recent Trades**")
        for row in reversed(journal[-15:]):
            pnl = row.get("pnl_pct", 0)
            pc  = "#4caf50" if pnl > 0 else "#ef5350"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:7px 14px;margin:3px 0;font-size:12px;'
                f'display:flex;gap:14px;align-items:center;flex-wrap:wrap;">'
                f'<span style="color:#e0e0e0;font-weight:700;min-width:48px;">'
                f'{row.get("ticker","")}</span>'
                f'<span style="color:#666;">{row.get("date","")} {row.get("time","")}</span>'
                f'<span style="color:#888;">{row.get("entry_type","")}</span>'
                f'<span style="color:#888;">{row.get("confirmed","")}</span>'
                f'<span style="color:#555;">News: {row.get("news","")}</span>'
                f'<span style="color:#555;">Window: {row.get("in_window","")}</span>'
                f'<span style="font-weight:700;color:{pc};">{pnl:+.2f}%</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        sa_csv = dfj.to_csv(index=False)
        st.download_button(
            "⬇ Download SA Journal", data=sa_csv,
            file_name="sa_journal.csv", mime="text/csv"
        )
    else:
        st.info("No trades logged yet. Use the expander above to log your first sniper trade.")


def render_performance_tab():
    """Live performance dashboard — paper trades, structure win rates, brain weights."""
    import json as _json

    st.markdown(
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:2px; font-weight:700; margin-bottom:4px;">📊 LIVE PERFORMANCE TRACKER</div>'
        '<div style="font-size:12px; color:#546e7a; margin-bottom:18px;">'
        'Real-time stats pulled directly from the database. Refreshes every page load.</div>',
        unsafe_allow_html=True,
    )

    now_et = datetime.now(EASTERN)
    st.caption(f"Last loaded: {now_et.strftime('%b %d, %Y  %I:%M:%S %p')} ET")

    # ── Load paper trades ───────────────────────────────────────────────────────
    _pt_df = _cached_load_paper_trades(user_id=_AUTH_USER_ID, days=365)

    # ── Load backtest sim history (backtest_sim_runs) ────────────────────────
    @st.cache_data(ttl=300, show_spinner=False)
    def _load_bt_sim_history(uid):
        return load_backtest_sim_history(user_id=uid)

    _bt_sim_df = _load_bt_sim_history(uid=_AUTH_USER_ID)

    # ── Load accuracy_tracker (bot watchlist calls + ALL combined) ──
    _at_df = pd.DataFrame()
    _at_all_df = pd.DataFrame()
    if supabase:
        try:
            _at_raw = (
                supabase.table("accuracy_tracker")
                .select("predicted,correct,timestamp")
                .eq("user_id", _AUTH_USER_ID)
                .eq("compare_key", "watchlist_pred")
                .execute()
                .data
            )
            _at_df = pd.DataFrame(_at_raw) if _at_raw else pd.DataFrame()
            if not _at_df.empty and "predicted" in _at_df.columns:
                _at_df = _at_df[_at_df["predicted"].notna() & (_at_df["predicted"] != "—")]
        except Exception:
            _at_df = pd.DataFrame()
        try:
            _at_all_raw = (
                supabase.table("accuracy_tracker")
                .select("predicted,correct")
                .eq("user_id", _AUTH_USER_ID)
                .range(0, 9999)
                .execute()
                .data
            )
            _at_all_df = pd.DataFrame(_at_all_raw) if _at_all_raw else pd.DataFrame()
            if not _at_all_df.empty and "predicted" in _at_all_df.columns:
                _at_all_df = _at_all_df[_at_all_df["predicted"].notna() & (_at_all_df["predicted"] != "—")]
        except Exception:
            _at_all_df = pd.DataFrame()

    # ── Load brain weights ───────────────────────────────────────────────────────
    _bw = _cached_load_brain_weights(user_id=_AUTH_USER_ID)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 1 — KPI STRIP
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### Key Numbers")

    # Paper trade stats
    _total_trades = 0
    _wins = 0
    _losses = 0
    _net_pnl = 0.0
    _sim_per_trade = 500.0  # $500/trade simulation

    if not _pt_df.empty and "win_loss" in _pt_df.columns:
        _wl = _pt_df["win_loss"].dropna()
        _wins    = int((_wl == "Win").sum()) + int((_wl == "W").sum())
        _losses  = int((_wl == "Loss").sum()) + int((_wl == "L").sum())
        _total_trades = _wins + _losses
        _wl_map = {"Win": 1, "W": 1, "Loss": -1, "L": -1}
        if "follow_thru_pct" in _pt_df.columns:
            _ft = _pt_df["follow_thru_pct"].fillna(0).astype(float)
            _wl_num = _pt_df["win_loss"].map(_wl_map).fillna(0)
            _net_pnl = float((_wl_num * _ft / 100 * _sim_per_trade).sum())
        else:
            _net_pnl = (_wins - _losses) * _sim_per_trade * 0.06

    _pt_rate = (_wins / _total_trades * 100) if _total_trades else 0.0

    # Structure prediction stats (bot-only = watchlist_pred)
    _struct_total = 0
    _struct_wins  = 0
    if not _at_df.empty and "correct" in _at_df.columns:
        _struct_total = len(_at_df)
        _struct_wins  = int((_at_df["correct"] == "✅").sum())
    _struct_rate = (_struct_wins / _struct_total * 100) if _struct_total else 0.0

    # Combined accuracy (all sources — journal + bot + webull)
    _all_total = 0
    _all_wins  = 0
    if not _at_all_df.empty and "correct" in _at_all_df.columns:
        _all_total = len(_at_all_df)
        _all_wins  = int((_at_all_df["correct"] == "✅").sum())
    _all_rate = (_all_wins / _all_total * 100) if _all_total else 0.0

    # Brain weight — normal (most active signal)
    _bw_normal = _bw.get("normal", 1.0)

    k1, k2, k3, k3b, k4, k5 = st.columns(6)
    with k1:
        _color = "#2e7d32" if _pt_rate >= 60 else ("#ef6c00" if _pt_rate >= 50 else "#c62828")
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Paper W/L</div>'
            f'<div style="font-size:26px;font-weight:700;color:{_color};">{_wins}W / {_losses}L</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{_pt_rate:.1f}% win rate</div>'
            f'</div>', unsafe_allow_html=True
        )
    with k2:
        _pnl_color = "#2e7d32" if _net_pnl >= 0 else "#c62828"
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Sim P&L ($500/trade)</div>'
            f'<div style="font-size:26px;font-weight:700;color:{_pnl_color};">{"+" if _net_pnl >= 0 else ""}{_net_pnl:,.0f}</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{_total_trades} verified trades</div>'
            f'</div>', unsafe_allow_html=True
        )
    with k3:
        _sc = "#2e7d32" if _struct_rate >= 65 else ("#ef6c00" if _struct_rate >= 55 else "#c62828")
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Bot Pred Rate</div>'
            f'<div style="font-size:26px;font-weight:700;color:{_sc};">{_struct_rate:.1f}%</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{_struct_wins}/{_struct_total} correct</div>'
            f'<div style="font-size:10px;color:#607d8b;margin-top:2px;">Bot watchlist calls only</div>'
            f'</div>', unsafe_allow_html=True
        )
    with k3b:
        _ac = "#2e7d32" if _all_rate >= 65 else ("#ef6c00" if _all_rate >= 55 else "#c62828")
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Overall Pred Rate</div>'
            f'<div style="font-size:26px;font-weight:700;color:{_ac};">{_all_rate:.1f}%</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{_all_wins}/{_all_total} correct</div>'
            f'<div style="font-size:10px;color:#607d8b;margin-top:2px;">All sources combined</div>'
            f'</div>', unsafe_allow_html=True
        )
    with k4:
        _bw_delta = _bw_normal - 1.0
        _bwc = "#2e7d32" if _bw_delta > 0.05 else "#90a4ae"
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Normal Weight</div>'
            f'<div style="font-size:26px;font-weight:700;color:{_bwc};">{_bw_normal:.4f}</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{"▲" if _bw_delta >= 0 else "▼"}{abs(_bw_delta):.4f} vs baseline</div>'
            f'</div>', unsafe_allow_html=True
        )
    with k5:
        _active_bw = {k: v for k, v in _bw.items() if isinstance(v, float) and v != 1.0}
        _most_active = max(_active_bw, key=_active_bw.get) if _active_bw else "baseline"
        st.markdown(
            f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">Strongest Signal</div>'
            f'<div style="font-size:22px;font-weight:700;color:#80cbc4;">{_most_active}</div>'
            f'<div style="font-size:14px;color:#cfd8dc;">{_active_bw.get(_most_active, 1.0):.4f}</div>'
            f'</div>', unsafe_allow_html=True
        )

    # ── Trades/Day pace row ─────────────────────────────────────────────────────
    _TARGET_PER_DAY  = 0.81   # best-only target (P3→P1→P4→P2 priority, backtest)
    _TARGET_PER_YEAR = 202    # 0.81 × 250 trading days

    # Count actual trading days elapsed since first settled trade
    _td_first = None
    if not _pt_df.empty and "trade_date" in _pt_df.columns:
        _td_dates = _pt_df["trade_date"].dropna().astype(str)
        if not _td_dates.empty:
            try:
                _td_first = pd.to_datetime(_td_dates).min().date()
            except Exception:
                _td_first = None

    if _td_first:
        import pandas as _pd_biz
        _td_today = datetime.now(EASTERN).date()
        _td_bdays = max(1, len(_pd_biz.bdate_range(str(_td_first), str(_td_today))))
        _trades_per_day  = round(_total_trades / _td_bdays, 2) if _td_bdays else 0.0
        _annual_pace     = round(_trades_per_day * 250)
        _pace_color      = "#66bb6a" if _trades_per_day >= _TARGET_PER_DAY * 0.7 else (
                           "#ef6c00"  if _trades_per_day >= _TARGET_PER_DAY * 0.4 else "#90a4ae")
        _pace_label      = (
            f"{_total_trades} trades / {_td_bdays} trading days"
        )
        _pace_sub = f"~{_annual_pace}/yr pace  ·  target {_TARGET_PER_DAY}/day ({_TARGET_PER_YEAR}/yr)"
    else:
        _trades_per_day  = 0.0
        _annual_pace     = 0
        _pace_color      = "#90a4ae"
        _pace_label      = "no trades yet"
        _pace_sub        = f"target {_TARGET_PER_DAY}/day ({_TARGET_PER_YEAR}/yr)"

    st.markdown(
        f'<div style="background:#151f2e;border:1px solid #1e2a3a;border-radius:8px;'
        f'padding:10px 18px;margin-top:10px;display:flex;align-items:center;gap:24px;">'
        f'<div style="min-width:140px;">'
        f'<div style="font-size:10px;color:#546e7a;text-transform:uppercase;letter-spacing:1px;">Avg Trades / Day</div>'
        f'<div style="font-size:28px;font-weight:700;color:{_pace_color};line-height:1.1;">'
        f'{_trades_per_day:.2f}<span style="font-size:14px;color:#546e7a;font-weight:400;"> /day</span></div>'
        f'</div>'
        f'<div style="flex:1;">'
        f'<div style="font-size:13px;color:#cfd8dc;">{_pace_label}</div>'
        f'<div style="font-size:11px;color:#546e7a;margin-top:2px;">{_pace_sub}</div>'
        f'</div>'
        f'<div style="text-align:right;min-width:100px;">'
        f'<div style="font-size:10px;color:#546e7a;text-transform:uppercase;letter-spacing:1px;">Annual Pace</div>'
        f'<div style="font-size:22px;font-weight:700;color:{_pace_color};">{_annual_pace}'
        f'<span style="font-size:12px;color:#546e7a;font-weight:400;">/yr</span></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 1b — IB BREAKOUT SIMULATION P&L
    # Uses pnl_r_sim / sim_outcome columns from paper_trades
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 IB Breakout Simulation")
    st.caption(
        "Simulated entry/exit using Initial Balance breakout rules — "
        "long at IB high, short at IB low, stop at opposite IB level (1R), "
        "P&L measured in R multiples at EOD close."
    )

    # Safe defaults so P1–P4 block below never hits NameError if no sim data
    _sim_df    = pd.DataFrame()
    _s_total   = 0
    _s_total_r = 0.0
    _s_wr      = 0.0

    _sim_has_data = (
        not _pt_df.empty
        and "pnl_r_sim" in _pt_df.columns
        and _pt_df["pnl_r_sim"].notna().any()
    )

    if not _sim_has_data:
        st.info(
            "No simulation data yet.  \n"
            "1. Run the SQL migration below to add the sim columns.  \n"
            "2. Run `python run_sim_backfill.py` to backfill history.  \n"
            "The bot will populate new trades automatically going forward."
        )
        with st.expander("SQL — add sim columns to paper_trades & backtest_sim_runs", expanded=True):
            st.code(
                """ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS scan_type TEXT DEFAULT 'morning',
  ADD COLUMN IF NOT EXISTS sim_outcome TEXT,
  ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT,
  ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT,
  ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT,
  ADD COLUMN IF NOT EXISTS target_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS eod_pnl_r FLOAT,
  ADD COLUMN IF NOT EXISTS tiered_pnl_r FLOAT;

ALTER TABLE backtest_sim_runs
  ADD COLUMN IF NOT EXISTS sim_outcome TEXT,
  ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT,
  ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT,
  ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT,
  ADD COLUMN IF NOT EXISTS target_price_sim FLOAT,
  ADD COLUMN IF NOT EXISTS eod_pnl_r FLOAT,
  ADD COLUMN IF NOT EXISTS tiered_pnl_r FLOAT;""",
                language="sql",
            )
    else:
        _sim_df = _pt_df[_pt_df["pnl_r_sim"].notna()].copy()
        _sim_df["pnl_r_sim"] = _sim_df["pnl_r_sim"].astype(float)
        if "scan_type" not in _sim_df.columns:
            _sim_df["scan_type"] = "morning"
        _sim_df["scan_type"] = _sim_df["scan_type"].fillna("morning")

        # ── Task #83: tickers with trades logged but no sim data yet ─────────
        _missing_sim_tickers = (
            _pt_df[_pt_df["pnl_r_sim"].isna()]["ticker"].dropna().unique().tolist()
            if "ticker" in _pt_df.columns else []
        )
        if _missing_sim_tickers:
            st.warning(
                f"⚠️ **{len(_missing_sim_tickers)} ticker(s) have trades logged but no sim P&L yet:** "
                f"{', '.join(sorted(_missing_sim_tickers))}  \n"
                "Run `python run_sim_backfill.py` to populate them, or wait for the bot to fill them in during the next EOD update.",
                icon="🕐",
            )

        import altair as _alt

        _scen_defs = [
            ("pnl_r_sim",    "#4fc3f7", "📈 Best Possible (MFE)",
             "Max intraday excursion — theoretical ceiling, assumes perfect exit timing."),
            ("eod_pnl_r",    "#81c784", "📅 Held to Close (EOD)",
             "Full position held to EOD close — no partial exits, raw hold-to-close P&L."),
            ("tiered_pnl_r", "#ffb74d", "🪜 50 / 25 / 25 Ladder",
             "50% off at 1R → stop to BE → 25% at 2R → 25% runner to close. "
             "Populates as new batch backtests run (bar replay required)."),
        ]

        # ── Row 1 — Three scenario stat cards side by side ──────────────────
        _scen_stat_cols = st.columns(3)
        _scen_data_cache = {}   # store computed per-scenario data for overlay chart below

        for _ci, (_scol, _sclr, _slabel, _sdesc) in enumerate(_scen_defs):
            with _scen_stat_cols[_ci]:
                st.markdown(
                    f'<div style="border-left:3px solid {_sclr};padding-left:8px;margin-bottom:6px;">'
                    f'<span style="font-size:13px;font-weight:700;color:{_sclr};">{_slabel}</span><br>'
                    f'<span style="font-size:11px;color:#78909c;">{_sdesc}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _has_scen = _scol in _sim_df.columns and _sim_df[_scol].notna().any()
                if not _has_scen:
                    _missing_hint = {
                        "eod_pnl_r":    "Run the SQL migration then `python run_sim_backfill.py`.",
                        "tiered_pnl_r": "Populates automatically as the batch backtest runs on new days.",
                    }.get(_scol, "Run the SQL migration and backfill script.")
                    st.markdown(
                        f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
                        f'<div style="font-size:12px;color:#546e7a;">No data yet</div>'
                        f'<div style="font-size:11px;color:#455a64;margin-top:4px;">{_missing_hint}</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                    continue

                _sc_df  = _sim_df[_sim_df[_scol].notna()].copy()
                _sc_df[_scol] = _sc_df[_scol].astype(float)
                _sc_wins   = _sc_df[_sc_df[_scol] > 0]
                _sc_losses = _sc_df[_sc_df[_scol] <= 0]
                _sc_total  = len(_sc_df)
                _sc_wr     = len(_sc_wins) / _sc_total * 100 if _sc_total else 0.0
                _sc_avg_w  = _sc_wins[_scol].mean()   if len(_sc_wins)   else 0.0
                _sc_avg_l  = _sc_losses[_scol].mean() if len(_sc_losses) else 0.0
                _sc_exp    = _sc_df[_scol].mean() if _sc_total else 0.0
                _sc_total_r = _sc_df[_scol].sum()

                _sc_wr_c  = "#2e7d32" if _sc_wr >= 60 else ("#ef6c00" if _sc_wr >= 50 else "#c62828")
                _sc_ex_c  = "#2e7d32" if _sc_exp > 0 else "#c62828"
                _sc_tr_c  = "#2e7d32" if _sc_total_r > 0 else "#c62828"

                st.markdown(
                    f'<div style="background:#1e2a3a;border-radius:8px;padding:12px 10px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;text-transform:uppercase;">Win Rate</span>'
                    f'  <span style="font-size:18px;font-weight:700;color:{_sc_wr_c};">{_sc_wr:.1f}%</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Avg Winner</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:#2e7d32;">+{_sc_avg_w:.2f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Avg Loser</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:#c62828;">{_sc_avg_l:.2f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Expectancy</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:{_sc_ex_c};">{"+" if _sc_exp >= 0 else ""}{_sc_exp:.3f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Total R</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:{_sc_tr_c};">{"+" if _sc_total_r >= 0 else ""}{_sc_total_r:.1f}R</span>'
                    f'</div>'
                    f'<div style="font-size:10px;color:#546e7a;text-align:right;margin-top:4px;">{_sc_total} trades · {len(_sc_wins)}W / {len(_sc_losses)}L</div>'
                    f'</div>', unsafe_allow_html=True
                )
                _scen_data_cache[_scol] = (_sc_df, _sclr, _slabel.split(" ", 1)[-1].strip())

        # ── Row 2 — Combined overlay equity curve ────────────────────────────
        if _scen_data_cache:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                'text-transform:uppercase;margin-bottom:6px;">Cumulative R Equity Curves — All Scenarios</div>',
                unsafe_allow_html=True,
            )
            _overlay_frames = []
            for _oc, (_odf, _oclr, _olabel) in _scen_data_cache.items():
                _odf2 = _odf.sort_values("trade_date", ascending=True).copy()
                _odf2["cum_r"]   = _odf2[_oc].cumsum()
                _odf2["trade_date"] = pd.to_datetime(_odf2["trade_date"])
                _odf2["Scenario"] = _olabel
                _overlay_frames.append(_odf2[["trade_date", "cum_r", "Scenario"]].copy())
            if _overlay_frames:
                _ov_df = pd.concat(_overlay_frames, ignore_index=True)
                _ov_clr_domain = [_scen_data_cache[k][2] for k in _scen_data_cache]
                _ov_clr_range  = [_scen_data_cache[k][1] for k in _scen_data_cache]
                _ov_chart = (
                    _alt.Chart(_ov_df)
                    .mark_line(point=True, strokeWidth=2)
                    .encode(
                        x=_alt.X("trade_date:T", title="Date", axis=_alt.Axis(format="%b %d")),
                        y=_alt.Y("cum_r:Q", title="Cumulative R", scale=_alt.Scale(zero=False)),
                        color=_alt.Color(
                            "Scenario:N",
                            scale=_alt.Scale(domain=_ov_clr_domain, range=_ov_clr_range),
                            legend=_alt.Legend(orient="top", labelColor="#cfd8dc", titleColor="#cfd8dc"),
                        ),
                        tooltip=[
                            _alt.Tooltip("trade_date:T", title="Date"),
                            _alt.Tooltip("Scenario:N",   title="Scenario"),
                            _alt.Tooltip("cum_r:Q",      title="Cum R", format=".2f"),
                        ],
                    )
                    .properties(height=240)
                    .configure_view(fill="#141e2e", stroke=None)
                    .configure_axis(labelColor="#90a4ae", titleColor="#90a4ae", gridColor="#263248")
                )
                st.altair_chart(_ov_chart, use_container_width=True)

        # ── Row 3 — Structure vs Sim comparison + Scan-type breakdown (MFE) ─
        if "pnl_r_sim" in _scen_data_cache:
            _mfe_df, _, _ = _scen_data_cache["pnl_r_sim"]
            _mfe_wr = len(_mfe_df[_mfe_df["pnl_r_sim"] > 0]) / len(_mfe_df) * 100 if len(_mfe_df) else 0.0

            st.markdown("<br>", unsafe_allow_html=True)
            _cmp_col, _scan_col = st.columns([2, 3])

            with _cmp_col:
                st.markdown(
                    '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                    'text-transform:uppercase;margin-bottom:6px;">Structure Prediction vs Sim Win Rate (MFE)</div>',
                    unsafe_allow_html=True,
                )
                _cmp_rows = [{"Category": "Overall", "Structure": round(_pt_rate, 1), "Sim": round(_mfe_wr, 1)}]
                for _st_key in ["morning", "intraday", "eod"]:
                    _st_mask = _pt_df["scan_type"].fillna("morning") == _st_key if "scan_type" in _pt_df.columns else pd.Series(False, index=_pt_df.index)
                    _st_wl   = _pt_df.loc[_st_mask, "win_loss"].dropna() if not _pt_df.empty else pd.Series()
                    _st_sw   = int((_st_wl.isin(["Win", "W"])).sum())
                    _st_tot  = len(_st_wl[_st_wl.isin(["Win", "W", "Loss", "L"])])
                    _st_swr  = _st_sw / _st_tot * 100 if _st_tot else 0
                    _st_scn  = _mfe_df[_mfe_df["scan_type"] == _st_key] if "scan_type" in _mfe_df.columns else pd.DataFrame()
                    _st_simwr = len(_st_scn[_st_scn["pnl_r_sim"] > 0]) / len(_st_scn) * 100 if len(_st_scn) else 0
                    if _st_tot > 0 or len(_st_scn) > 0:
                        _cmp_rows.append({"Category": _st_key.capitalize(), "Structure": round(_st_swr, 1), "Sim": round(_st_simwr, 1)})
                _cmp_df = pd.DataFrame(_cmp_rows)
                if not _cmp_df.empty:
                    _cmp_melt = _cmp_df.melt("Category", var_name="Type", value_name="Win Rate %")
                    _cmp_bar = (
                        _alt.Chart(_cmp_melt)
                        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                        .encode(
                            x=_alt.X("Category:N", title=""),
                            y=_alt.Y("Win Rate %:Q", scale=_alt.Scale(domain=[0, 100])),
                            color=_alt.Color(
                                "Type:N",
                                scale=_alt.Scale(domain=["Structure", "Sim"], range=["#4fc3f7", "#81c784"]),
                                legend=_alt.Legend(orient="top", labelColor="#cfd8dc", titleColor="#cfd8dc"),
                            ),
                            xOffset="Type:N",
                            tooltip=[
                                _alt.Tooltip("Category:N",   title="Scan"),
                                _alt.Tooltip("Type:N",       title="Type"),
                                _alt.Tooltip("Win Rate %:Q", title="Win Rate", format=".1f"),
                            ],
                        )
                        .properties(height=220)
                        .configure_view(fill="#141e2e", stroke=None)
                        .configure_axis(labelColor="#90a4ae", titleColor="#90a4ae", gridColor="#263248")
                    )
                    st.altair_chart(_cmp_bar, use_container_width=True)

            with _scan_col:
                st.markdown(
                    '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                    'text-transform:uppercase;margin-bottom:6px;">Breakdown by Scan Type (MFE)</div>',
                    unsafe_allow_html=True,
                )
                _scan_cards = st.columns(3)
                for _idx3, _stk3 in enumerate(["morning", "intraday", "eod"]):
                    _stk3_df = _mfe_df[_mfe_df["scan_type"] == _stk3] if "scan_type" in _mfe_df.columns else pd.DataFrame()
                    with _scan_cards[_idx3]:
                        if _stk3_df.empty:
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">{_stk3.upper()}</div>'
                                f'<div style="font-size:13px;color:#546e7a;margin-top:6px;">No data</div>'
                                f'</div>', unsafe_allow_html=True
                            )
                        else:
                            _sk3_w   = len(_stk3_df[_stk3_df["pnl_r_sim"] > 0])
                            _sk3_l   = len(_stk3_df[_stk3_df["pnl_r_sim"] <= 0])
                            _sk3_wr  = _sk3_w / len(_stk3_df) * 100 if len(_stk3_df) else 0
                            _sk3_exp = _stk3_df["pnl_r_sim"].mean() if not _stk3_df.empty else 0
                            _sk3_tot = _stk3_df["pnl_r_sim"].sum()
                            _sk3_c   = "#2e7d32" if _sk3_wr >= 55 else ("#ef6c00" if _sk3_wr >= 45 else "#c62828")
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">{_stk3.upper()}</div>'
                                f'<div style="font-size:22px;font-weight:700;color:{_sk3_c};margin-top:4px;">{_sk3_wr:.1f}%</div>'
                                f'<div style="font-size:12px;color:#cfd8dc;">{_sk3_w}W / {_sk3_l}L  ·  '
                                f'Exp: {"+" if _sk3_exp >= 0 else ""}{_sk3_exp:.3f}R</div>'
                                f'<div style="font-size:12px;color:#90a4ae;">Total: {"+" if _sk3_tot >= 0 else ""}{_sk3_tot:.1f}R '
                                f'({len(_stk3_df)} trades)</div>'
                                f'</div>', unsafe_allow_html=True
                            )

        # Update summary vars for P1-P4 tier breakdown below (use MFE as primary)
        _s_total   = len(_sim_df)
        _s_total_r = _sim_df["pnl_r_sim"].sum() if _s_total else 0.0
        _s_wr      = len(_sim_df[_sim_df["pnl_r_sim"] > 0]) / _s_total * 100 if _s_total else 0.0

    st.markdown("<br>", unsafe_allow_html=True)

    # ── P1–P4 Priority Tier Breakdown ────────────────────────────────────────
    st.markdown(
        '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:8px;">Priority Tier Breakdown — P1 / P2 / P3 / P4</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "P1 🔴 = Intraday TCS 70+  ·  P2 🟠 = Intraday TCS 50–69  ·  "
        "P3 🟡 = Morning TCS 70+  ·  P4 🟢 = Morning TCS 50–69"
    )

    _tier_defs = [
        ("P1", "🔴", "intraday", 70, 999, "#c62828",  "Intraday 70+"),
        ("P2", "🟠", "intraday", 50,  69, "#ef6c00",  "Intraday 50–69"),
        ("P3", "🟡", "morning",  70, 999, "#f9a825",  "Morning 70+"),
        ("P4", "🟢", "morning",  50,  69, "#2e7d32",  "Morning 50–69"),
    ]

    _has_tcs = "tcs" in _sim_df.columns and _sim_df["tcs"].notna().any()
    _tier_cols = st.columns(4)

    # Pre-compute expectancy R per tier to find best edge
    _tier_exp_map: dict = {}
    if _has_tcs:
        for _ti2, (_tlabel2, _temoji2, _tst2, _tcs_lo2, _tcs_hi2, _tcolor2, _tdesc2) in enumerate(_tier_defs):
            _tdf2 = _sim_df[
                (_sim_df["scan_type"].fillna("morning") == _tst2) &
                (_sim_df["tcs"].fillna(0) >= _tcs_lo2) &
                (_sim_df["tcs"].fillna(0) <= _tcs_hi2)
            ]
            if not _tdf2.empty:
                _tier_exp_map[_ti2] = _tdf2["pnl_r_sim"].mean()
    # Only show best-edge highlight when more than one tier has trades
    _best_exp_r = max(_tier_exp_map.values()) if len(_tier_exp_map) > 1 else None

    # Sort tier render order by expectancy R (best edge first) when multiple tiers have trades
    if len(_tier_exp_map) > 1:
        _tier_render_order = sorted(
            range(len(_tier_defs)),
            key=lambda _i: _tier_exp_map.get(_i, float("-inf")),
            reverse=True,
        )
    else:
        _tier_render_order = list(range(len(_tier_defs)))

    for _ti_rpos, _ti in enumerate(_tier_render_order):
        (_tlabel, _temoji, _tst, _tcs_lo, _tcs_hi, _tcolor, _tdesc) = _tier_defs[_ti]
        with _tier_cols[_ti_rpos]:
            if not _has_tcs:
                st.markdown(
                    f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                    f'<div style="font-size:13px;font-weight:700;color:{_tcolor};">{_temoji} {_tlabel}</div>'
                    f'<div style="font-size:11px;color:#546e7a;margin-top:6px;">TCS data unavailable</div>'
                    f'</div>', unsafe_allow_html=True
                )
            else:
                _tdf = _sim_df[
                    (_sim_df["scan_type"].fillna("morning") == _tst) &
                    (_sim_df["tcs"].fillna(0) >= _tcs_lo) &
                    (_sim_df["tcs"].fillna(0) <= _tcs_hi)
                ]
                if _tdf.empty:
                    st.markdown(
                        f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                        f'<div style="font-size:13px;font-weight:700;color:{_tcolor};">{_temoji} {_tlabel}</div>'
                        f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">{_tdesc}</div>'
                        f'<div style="font-size:12px;color:#546e7a;margin-top:6px;">No sim trades yet</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                else:
                    _tw  = len(_tdf[_tdf["pnl_r_sim"] > 0])
                    _tl  = len(_tdf[_tdf["pnl_r_sim"] <= 0])
                    _twr = _tw / len(_tdf) * 100
                    _tavg_w = _tdf[_tdf["pnl_r_sim"] > 0]["pnl_r_sim"].mean() if _tw else 0
                    _tavg_l = _tdf[_tdf["pnl_r_sim"] < 0]["pnl_r_sim"].mean() if _tl and (_tdf["pnl_r_sim"] < 0).any() else 0
                    _texp = _tdf["pnl_r_sim"].mean()
                    _ttot = _tdf["pnl_r_sim"].sum()
                    _pct_trades = len(_tdf) / _s_total * 100 if _s_total else 0
                    _pct_r      = _ttot / _s_total_r * 100 if _s_total_r else 0
                    _is_best = _best_exp_r is not None and abs(_texp - _best_exp_r) < 1e-9
                    _card_border = f"2px solid #ffd54f" if _is_best else f"3px solid {_tcolor}"
                    _best_badge  = (
                        '<div style="font-size:10px;font-weight:700;color:#ffd54f;'
                        'background:rgba(255,213,79,0.12);border-radius:4px;'
                        'padding:1px 6px;display:inline-block;margin-bottom:4px;">'
                        'Best Edge ⭐</div>'
                    ) if _is_best else ""
                    st.markdown(
                        f'<div style="background:#1e2a3a;border-left:{_card_border};'
                        f'border-radius:8px;padding:12px;text-align:center;">'
                        f'{_best_badge}'
                        f'<div style="font-size:13px;font-weight:700;color:{_tcolor};">{_temoji} {_tlabel}</div>'
                        f'<div style="font-size:10px;color:#90a4ae;margin-bottom:6px;">{_tdesc}</div>'
                        f'<div style="font-size:22px;font-weight:700;color:{"#2e7d32" if _twr >= 55 else "#ef6c00"};">'
                        f'{_twr:.1f}%</div>'
                        f'<div style="font-size:11px;color:#cfd8dc;">{_tw}W / {_tl}L</div>'
                        f'<div style="font-size:11px;color:#cfd8dc;margin-top:4px;">'
                        f'Avg Win: <b style="color:#4fc3f7;">+{_tavg_w:.2f}R</b>  ·  '
                        f'Avg Loss: <b style="color:#ef9a9a;">{_tavg_l:.2f}R</b></div>'
                        f'<div style="font-size:11px;color:#cfd8dc;">'
                        f'Exp: <b>{"+" if _texp >= 0 else ""}{_texp:.3f}R</b></div>'
                        f'<div style="font-size:11px;color:#90a4ae;margin-top:4px;">'
                        f'Total: {"+" if _ttot >= 0 else ""}{_ttot:.1f}R · {len(_tdf)} trades</div>'
                        f'<div style="font-size:10px;color:#546e7a;margin-top:3px;">'
                        f'{_pct_trades:.1f}% of trades · {_pct_r:.1f}% of R</div>'
                        f'</div>', unsafe_allow_html=True
                    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 1b2 — BACKTEST SIM P&L (from backtest_sim_runs)
    # Shows the same three-scenario layout as SECTION 1b (paper trades) but
    # sourced from historical batch-backtest data.
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 Backtest Sim P&L — Historical")
    st.caption(
        "P&L scenarios across all saved batch-backtest runs (backtest_sim_runs). "
        "Use this for large-sample edge validation — thousands of historical setups."
    )

    # ── Tiered P&L backfill warning ─────────────────────────────────────────────
    _tiered_pending_count = count_backtest_tiered_pending(user_id=_AUTH_USER_ID)
    if _tiered_pending_count > 0:
        _tp_col_warn, _tp_col_btn = st.columns([3, 1])
        with _tp_col_warn:
            st.warning(
                f"**{_tiered_pending_count:,} backtest rows are missing tiered P&L** "
                f"(50/25/25 ladder exit). Tiered P&L requires intraday bar data from Alpaca "
                f"and cannot be computed at save time. Click **Run Backfill (25 rows)** to "
                f"process a batch, or run `python run_tiered_pnl_backfill.py --backtest-only` "
                f"from the shell for the full backlog."
            )
        with _tp_col_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("▶ Run Backfill (25 rows)", key="bt_tiered_backfill_btn",
                         use_container_width=True):
                with st.spinner("Running tiered P&L backfill — fetching Alpaca bars…"):
                    _bf_result = run_backtest_tiered_backfill_batch(
                        batch_size=25, user_id=_AUTH_USER_ID)
                _bf_updated  = _bf_result.get("updated", 0)
                _bf_fetched  = _bf_result.get("fetched", 0)
                _bf_no_bars  = _bf_result.get("skipped_no_bars", 0)
                _bf_no_cross = _bf_result.get("skipped_no_tiered", 0)
                _bf_errors   = _bf_result.get("errors", 0)
                _bf_remain   = _bf_result.get("remaining", 0)
                if _bf_errors and not _bf_fetched:
                    st.error(
                        "Backfill failed — Alpaca credentials may be missing. "
                        "Check that ALPACA_API_KEY and ALPACA_SECRET_KEY are set."
                    )
                else:
                    st.success(
                        f"Batch complete — {_bf_fetched} rows processed, "
                        f"{_bf_updated} updated, {_bf_no_bars} deferred (no bars), "
                        f"{_bf_no_cross} skipped (no entry cross). "
                        f"{_bf_remain:,} rows still pending."
                    )
                    st.rerun()

    # ── Missing EOD close price summary ─────────────────────────────────────────
    _missing_cp = get_missing_close_price_stats(user_id=_AUTH_USER_ID)
    _missing_cp_total = _missing_cp.get("total_missing", 0)
    if _missing_cp_total > 0:
        _top_tickers         = _missing_cp.get("top_tickers", [])
        _total_tickers       = _missing_cp.get("total_tickers", len(_top_tickers))
        _ticker_list_complete = _missing_cp.get("ticker_list_complete", True)
        _ticker_badges = "".join(
            f'<span style="display:inline-block; background:#1a2744; border:1px solid #263260; '
            f'border-radius:4px; padding:2px 8px; margin:2px 4px 2px 0; font-family:monospace; '
            f'font-size:11px; color:#90a4ae;">'
            f'{t["ticker"]} <span style="color:#546e7a;">×{t["count"]}</span></span>'
            for t in _top_tickers
        )
        # Show "+ N more" only when there are genuinely more distinct tickers than shown.
        _shown_count = len(_top_tickers)
        _more_hidden = _total_tickers - _shown_count
        if _more_hidden > 0:
            _more_note = (
                f'<span style="font-size:10px; color:#37474f;">'
                f' + {_more_hidden} more ticker{"s" if _more_hidden != 1 else ""} not shown</span>'
            )
        elif not _ticker_list_complete:
            _more_note = (
                f'<span style="font-size:10px; color:#37474f;"> + additional tickers not shown</span>'
            )
        else:
            _more_note = ""
        # When pagination cap was hit, present the ticker count as a lower bound.
        if _ticker_list_complete:
            _tickers_label = (
                f'{_total_tickers} ticker{"s" if _total_tickers != 1 else ""} affected'
            )
        else:
            _tickers_label = (
                f'at least {_total_tickers} ticker{"s" if _total_tickers != 1 else ""} affected'
            )
        st.markdown(
            f'<div style="background:#020813; border:1px solid #263260; border-radius:8px; '
            f'padding:12px 20px; margin-bottom:16px;">'
            f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
            f'letter-spacing:1.5px; font-weight:700; font-family:monospace; margin-bottom:8px;">'
            f'⚠ EOD Close Price — Missing Data</div>'
            f'<div style="display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start;">'
            f'<div>'
            f'<div style="font-size:9px; color:#546e7a; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:2px;">Rows without close price</div>'
            f'<div style="font-size:28px; font-weight:800; color:#ffa726; '
            f'font-family:monospace;">{_missing_cp_total:,}</div>'
            f'<div style="font-size:9px; color:#37474f; margin-top:2px;">'
            f'no EOD P&amp;L possible · {_tickers_label}</div>'
            f'</div>'
            f'<div style="border-left:1px solid #1a2744; padding-left:20px; flex:1;">'
            f'<div style="font-size:9px; color:#546e7a; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:6px;">Tickers most affected</div>'
            f'<div>{_ticker_badges}{_more_note}</div>'
            f'<div style="font-size:9px; color:#37474f; margin-top:6px;">'
            f'Typically delisted stocks, OTC tickers, or names with no IEX/Alpaca coverage. '
            f'Run <code style="font-size:9px;">python backfill_close_prices.py</code> to retry.</div>'
            f'</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    _bt_sim_has_data = (
        not _bt_sim_df.empty
        and "pnl_r_sim" in _bt_sim_df.columns
        and _bt_sim_df["pnl_r_sim"].notna().any()
    )

    if not _bt_sim_has_data:
        st.info(
            "No backtest simulation data yet.  \n"
            "Run the Batch Backtest in the Backtest tab to populate this section."
        )
    else:
        _bts_df = _bt_sim_df[_bt_sim_df["pnl_r_sim"].notna()].copy()
        _bts_df["pnl_r_sim"] = _bts_df["pnl_r_sim"].astype(float)
        if "scan_type" not in _bts_df.columns:
            _bts_df["scan_type"] = "morning"
        _bts_df["scan_type"] = _bts_df["scan_type"].fillna("morning")
        # Normalise date column — backtest uses sim_date
        if "sim_date" in _bts_df.columns and "trade_date" not in _bts_df.columns:
            _bts_df = _bts_df.rename(columns={"sim_date": "trade_date"})
        if "trade_date" not in _bts_df.columns:
            _bts_df["trade_date"] = pd.NaT  # safe fallback; equity curve will skip gracefully

        # ── Date-range filter ────────────────────────────────────────────────
        _bts_td_col = pd.to_datetime(_bts_df["trade_date"], errors="coerce")
        _bts_valid_dates = _bts_td_col.dropna()
        _bts_min_date = _bts_valid_dates.min().date() if not _bts_valid_dates.empty else None
        _bts_max_date = _bts_valid_dates.max().date() if not _bts_valid_dates.empty else None

        _bts_dr_cols = st.columns([1, 1, 4])
        with _bts_dr_cols[0]:
            _bts_start = st.date_input(
                "From",
                value=None,
                min_value=_bts_min_date,
                max_value=_bts_max_date,
                key="bts_dr_start",
                help="Show backtest runs from this date (inclusive). Leave blank for all time.",
            )
        with _bts_dr_cols[1]:
            _bts_end = st.date_input(
                "To",
                value=None,
                min_value=_bts_min_date,
                max_value=_bts_max_date,
                key="bts_dr_end",
                help="Show backtest runs up to this date (inclusive). Leave blank for all time.",
            )

        _bts_date_filter_active = bool(_bts_start or _bts_end)

        # Persist bts date-range filter to user prefs so it survives page reloads
        if _AUTH_USER_ID:
            _bts_dr_cached = st.session_state.get("_cached_prefs", {})
            _bts_start_str = _bts_start.isoformat() if _bts_start else None
            _bts_end_str   = _bts_end.isoformat()   if _bts_end   else None
            if (
                _bts_dr_cached.get("bts_dr_start") != _bts_start_str
                or _bts_dr_cached.get("bts_dr_end") != _bts_end_str
            ):
                _bts_dr_new_prefs = {
                    **_bts_dr_cached,
                    "bts_dr_start": _bts_start_str,
                    "bts_dr_end":   _bts_end_str,
                }
                save_user_prefs(_AUTH_USER_ID, _bts_dr_new_prefs)
                st.session_state["_cached_prefs"] = _bts_dr_new_prefs

        if _bts_start and _bts_end and _bts_start > _bts_end:
            st.error("'From' date must be on or before the 'To' date — no results shown.")
            _bts_df = _bts_df.iloc[0:0].copy()  # empty but preserve columns
        elif _bts_date_filter_active:
            _bts_td_mask = pd.to_datetime(_bts_df["trade_date"], errors="coerce")
            if _bts_start:
                _bts_df = _bts_df[_bts_td_mask >= pd.Timestamp(_bts_start)]
            if _bts_end:
                _bts_df = _bts_df[_bts_td_mask <= pd.Timestamp(_bts_end)]
            _bts_df = _bts_df.reset_index(drop=True)
            if _bts_df.empty:
                st.warning("No backtest runs found in the selected date range — try widening the filter.")

        import altair as _alt_bt

        _bts_scen_defs = [
            ("pnl_r_sim",    "#4fc3f7", "📈 Best Possible (MFE)",
             "Max intraday excursion — theoretical ceiling, assumes perfect exit timing."),
            ("eod_pnl_r",    "#81c784", "📅 Held to Close (EOD)",
             "Full position held to EOD close — no partial exits, raw hold-to-close P&L."),
            ("tiered_pnl_r", "#ffb74d", "🪜 50 / 25 / 25 Ladder",
             "50% off at 1R → stop to BE → 25% at 2R → 25% runner to close. "
             "Populates as new backtest runs."),
        ]

        # ── Row 1 — Three scenario stat cards side by side ───────────────────
        _bts_stat_cols = st.columns(3)
        _bts_cache = {}

        for _bci, (_bscol, _bsclr, _bslabel, _bsdesc) in enumerate(_bts_scen_defs):
            with _bts_stat_cols[_bci]:
                st.markdown(
                    f'<div style="border-left:3px solid {_bsclr};padding-left:8px;margin-bottom:6px;">'
                    f'<span style="font-size:13px;font-weight:700;color:{_bsclr};">{_bslabel}</span><br>'
                    f'<span style="font-size:11px;color:#78909c;">{_bsdesc}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _bts_has_scen = _bscol in _bts_df.columns and _bts_df[_bscol].notna().any()
                if not _bts_has_scen:
                    if _bts_date_filter_active:
                        _bts_hint = "No trades in the selected date range — try widening the filter."
                        _bts_empty_label = "No data in range"
                    else:
                        _bts_hint = {
                            "eod_pnl_r":    "Run the SQL migration then re-run backtests.",
                            "tiered_pnl_r": "Populates automatically as new batch backtests run.",
                        }.get(_bscol, "Run a batch backtest to populate this scenario.")
                        _bts_empty_label = "No data yet"
                    st.markdown(
                        f'<div style="background:#1e2a3a;border-radius:8px;padding:14px 10px;text-align:center;">'
                        f'<div style="font-size:12px;color:#546e7a;">{_bts_empty_label}</div>'
                        f'<div style="font-size:11px;color:#455a64;margin-top:4px;">{_bts_hint}</div>'
                        f'</div>', unsafe_allow_html=True
                    )
                    continue

                _bsc_df  = _bts_df[_bts_df[_bscol].notna()].copy()
                _bsc_df[_bscol] = _bsc_df[_bscol].astype(float)
                _bsc_wins   = _bsc_df[_bsc_df[_bscol] > 0]
                _bsc_losses = _bsc_df[_bsc_df[_bscol] <= 0]
                _bsc_total  = len(_bsc_df)
                _bsc_wr     = len(_bsc_wins) / _bsc_total * 100 if _bsc_total else 0.0
                _bsc_avg_w  = _bsc_wins[_bscol].mean()   if len(_bsc_wins)   else 0.0
                _bsc_avg_l  = _bsc_losses[_bscol].mean() if len(_bsc_losses) else 0.0
                _bsc_exp    = _bsc_df[_bscol].mean() if _bsc_total else 0.0
                _bsc_total_r = _bsc_df[_bscol].sum()

                _bsc_wr_c  = "#2e7d32" if _bsc_wr >= 60 else ("#ef6c00" if _bsc_wr >= 50 else "#c62828")
                _bsc_ex_c  = "#2e7d32" if _bsc_exp > 0 else "#c62828"
                _bsc_tr_c  = "#2e7d32" if _bsc_total_r > 0 else "#c62828"

                st.markdown(
                    f'<div style="background:#1e2a3a;border-radius:8px;padding:12px 10px;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;text-transform:uppercase;">Win Rate</span>'
                    f'  <span style="font-size:18px;font-weight:700;color:{_bsc_wr_c};">{_bsc_wr:.1f}%</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Avg Winner</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:#2e7d32;">+{_bsc_avg_w:.2f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Avg Loser</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:#c62828;">{_bsc_avg_l:.2f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Expectancy</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:{_bsc_ex_c};">{"+" if _bsc_exp >= 0 else ""}{_bsc_exp:.3f}R</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'  <span style="font-size:10px;color:#90a4ae;">Total R</span>'
                    f'  <span style="font-size:13px;font-weight:600;color:{_bsc_tr_c};">{"+" if _bsc_total_r >= 0 else ""}{_bsc_total_r:.1f}R</span>'
                    f'</div>'
                    f'<div style="font-size:10px;color:#546e7a;text-align:right;margin-top:4px;">{_bsc_total} trades · {len(_bsc_wins)}W / {len(_bsc_losses)}L</div>'
                    f'</div>', unsafe_allow_html=True
                )
                _bts_cache[_bscol] = (_bsc_df, _bsclr, _bslabel.split(" ", 1)[-1].strip())

        # ── Row 2 — Overlay equity curve ─────────────────────────────────────
        if _bts_cache:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                'text-transform:uppercase;margin-bottom:6px;">Cumulative R Equity Curves — All Scenarios (Backtest)</div>',
                unsafe_allow_html=True,
            )
            _bts_overlay = []
            for _boc, (_bodf, _boclr, _bolabel) in _bts_cache.items():
                _bodf2 = _bodf.sort_values("trade_date", ascending=True).copy()
                _bodf2["cum_r"]     = _bodf2[_boc].cumsum()
                _bodf2["trade_date"] = pd.to_datetime(_bodf2["trade_date"])
                _bodf2["Scenario"]  = _bolabel
                _bts_overlay.append(_bodf2[["trade_date", "cum_r", "Scenario"]].copy())
            if _bts_overlay:
                _bov_df = pd.concat(_bts_overlay, ignore_index=True)
                _bov_domain = [_bts_cache[k][2] for k in _bts_cache]
                _bov_range  = [_bts_cache[k][1] for k in _bts_cache]
                _bov_chart = (
                    _alt_bt.Chart(_bov_df)
                    .mark_line(point=True, strokeWidth=2)
                    .encode(
                        x=_alt_bt.X("trade_date:T", title="Date", axis=_alt_bt.Axis(format="%b %d")),
                        y=_alt_bt.Y("cum_r:Q", title="Cumulative R", scale=_alt_bt.Scale(zero=False)),
                        color=_alt_bt.Color(
                            "Scenario:N",
                            scale=_alt_bt.Scale(domain=_bov_domain, range=_bov_range),
                            legend=_alt_bt.Legend(orient="top", labelColor="#cfd8dc", titleColor="#cfd8dc"),
                        ),
                        tooltip=[
                            _alt_bt.Tooltip("trade_date:T", title="Date"),
                            _alt_bt.Tooltip("Scenario:N",   title="Scenario"),
                            _alt_bt.Tooltip("cum_r:Q",      title="Cum R", format=".2f"),
                        ],
                    )
                    .properties(height=240)
                    .configure_view(fill="#141e2e", stroke=None)
                    .configure_axis(labelColor="#90a4ae", titleColor="#90a4ae", gridColor="#263248")
                )
                st.altair_chart(_bov_chart, use_container_width=True)

            # ── Row 3 — Scan-type breakdown (MFE + EOD) ─────────────────────
            if "pnl_r_sim" in _bts_cache:
                _bts_mfe_df, _, _ = _bts_cache["pnl_r_sim"]
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(
                    '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                    'text-transform:uppercase;margin-bottom:6px;">Breakdown by Scan Type — MFE Exit · EOD Hold</div>',
                    unsafe_allow_html=True,
                )
                _bts_scan_cols = st.columns(4)
                for _bts_idx, _bts_stk in enumerate(["morning", "intraday"]):
                    _bts_stk_df = (
                        _bts_mfe_df[_bts_mfe_df["scan_type"] == _bts_stk]
                        if "scan_type" in _bts_mfe_df.columns
                        else pd.DataFrame()
                    )
                    with _bts_scan_cols[_bts_idx]:
                        if _bts_stk_df.empty:
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">{_bts_stk.upper()}</div>'
                                f'<div style="font-size:13px;color:#546e7a;margin-top:6px;">No data</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        else:
                            _bts_sk_w   = len(_bts_stk_df[_bts_stk_df["pnl_r_sim"] > 0])
                            _bts_sk_l   = len(_bts_stk_df[_bts_stk_df["pnl_r_sim"] <= 0])
                            _bts_sk_wr  = _bts_sk_w / len(_bts_stk_df) * 100 if len(_bts_stk_df) else 0
                            _bts_sk_exp = _bts_stk_df["pnl_r_sim"].mean() if not _bts_stk_df.empty else 0
                            _bts_sk_tot = _bts_stk_df["pnl_r_sim"].sum()
                            _bts_sk_c   = "#2e7d32" if _bts_sk_wr >= 55 else ("#ef6c00" if _bts_sk_wr >= 45 else "#c62828")
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">{_bts_stk.upper()}</div>'
                                f'<div style="font-size:22px;font-weight:700;color:{_bts_sk_c};margin-top:4px;">{_bts_sk_wr:.1f}%</div>'
                                f'<div style="font-size:12px;color:#cfd8dc;">{_bts_sk_w}W / {_bts_sk_l}L  ·  '
                                f'Exp: {"+" if _bts_sk_exp >= 0 else ""}{_bts_sk_exp:.3f}R</div>'
                                f'<div style="font-size:12px;color:#90a4ae;">Total: {"+" if _bts_sk_tot >= 0 else ""}{_bts_sk_tot:.1f}R '
                                f'({len(_bts_stk_df)} trades)</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                for _bts_eod_idx, _bts_eod_stk in enumerate(["morning", "intraday"]):
                    with _bts_scan_cols[2 + _bts_eod_idx]:
                        _bts_eod_df = pd.DataFrame()
                        if "eod_pnl_r" in _bts_df.columns:
                            _bts_eod_df = (
                                _bts_df[
                                    _bts_df["eod_pnl_r"].notna() &
                                    (_bts_df["scan_type"] == _bts_eod_stk)
                                ].copy()
                            )
                        _eod_label = f"{_bts_eod_stk.upper()} EOD"
                        if _bts_eod_df.empty:
                            st.markdown(
                                f'<div style="background:#1a2535;border:1px solid #263248;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#7986cb;letter-spacing:1px;text-transform:uppercase;">{_eod_label}</div>'
                                f'<div style="font-size:13px;color:#546e7a;margin-top:6px;">No data</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        else:
                            _bts_eod_df["eod_pnl_r"] = _bts_eod_df["eod_pnl_r"].astype(float)
                            _bts_eod_w   = len(_bts_eod_df[_bts_eod_df["eod_pnl_r"] > 0])
                            _bts_eod_l   = len(_bts_eod_df[_bts_eod_df["eod_pnl_r"] <= 0])
                            _bts_eod_wr  = _bts_eod_w / len(_bts_eod_df) * 100 if len(_bts_eod_df) else 0
                            _bts_eod_exp = _bts_eod_df["eod_pnl_r"].mean()
                            _bts_eod_tot = _bts_eod_df["eod_pnl_r"].sum()
                            _bts_eod_c   = "#2e7d32" if _bts_eod_wr >= 55 else ("#ef6c00" if _bts_eod_wr >= 45 else "#c62828")
                            st.markdown(
                                f'<div style="background:#1a2535;border:1px solid #263248;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:11px;color:#7986cb;letter-spacing:1px;text-transform:uppercase;">{_eod_label}</div>'
                                f'<div style="font-size:22px;font-weight:700;color:{_bts_eod_c};margin-top:4px;">{_bts_eod_wr:.1f}%</div>'
                                f'<div style="font-size:12px;color:#cfd8dc;">{_bts_eod_w}W / {_bts_eod_l}L  ·  '
                                f'Exp: {"+" if _bts_eod_exp >= 0 else ""}{_bts_eod_exp:.3f}R</div>'
                                f'<div style="font-size:12px;color:#90a4ae;">Total: {"+" if _bts_eod_tot >= 0 else ""}{_bts_eod_tot:.1f}R '
                                f'({len(_bts_eod_df)} trades)</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

            # ── Row 3b — P1–P4 Priority Tier Breakdown ───────────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                'text-transform:uppercase;margin-bottom:6px;">'
                'P1–P4 Priority Tier Breakdown — MFE Exit</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "P1 🔴 = Intraday TCS 70+  ·  P2 🟠 = Intraday TCS 50–69  ·  "
                "P3 🟡 = Morning TCS 70+  ·  P4 🟢 = Morning TCS 50–69"
            )
            _bts_tier_defs2 = [
                ("P1", "🔴", "intraday", 70, 999, "#c62828", "Intraday 70+"),
                ("P2", "🟠", "intraday", 50,  69, "#ef6c00", "Intraday 50–69"),
                ("P3", "🟡", "morning",  70, 999, "#f9a825", "Morning 70+"),
                ("P4", "🟢", "morning",  50,  69, "#2e7d32", "Morning 50–69"),
            ]
            _bts_has_tcs2 = "tcs" in _bts_df.columns and _bts_df["tcs"].notna().any()
            _bts_tier_cols2 = st.columns(4)
            # Pre-compute expectancy R per tier for best-edge highlight
            _bts_tier_exp_map2: dict = {}
            if _bts_has_tcs2:
                for _btsii, (_btsl2x, _btse2x, _btsst2x, _btslo2x, _btshi2x, _btsc2x, _btsd2x) in enumerate(_bts_tier_defs2):
                    _bts_td2 = _bts_df[
                        (_bts_df["scan_type"].fillna("morning") == _btsst2x) &
                        (_bts_df["tcs"].fillna(0) >= _btslo2x) &
                        (_bts_df["tcs"].fillna(0) <= _btshi2x) &
                        _bts_df["pnl_r_sim"].notna()
                    ]
                    if not _bts_td2.empty:
                        _bts_tier_exp_map2[_btsii] = float(_bts_td2["pnl_r_sim"].astype(float).mean())
            _bts_best_exp_r2 = max(_bts_tier_exp_map2.values()) if len(_bts_tier_exp_map2) > 1 else None
            for _btsi2, (_btsl, _btse, _btsst, _btslo, _btshi, _btsc, _btsd) in enumerate(_bts_tier_defs2):
                with _bts_tier_cols2[_btsi2]:
                    if not _bts_has_tcs2:
                        st.markdown(
                            f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                            f'<div style="font-size:13px;font-weight:700;color:{_btsc};">{_btse} {_btsl}</div>'
                            f'<div style="font-size:11px;color:#546e7a;margin-top:6px;">TCS data unavailable</div>'
                            f'</div>', unsafe_allow_html=True
                        )
                    else:
                        _bts_tdf = _bts_df[
                            (_bts_df["scan_type"].fillna("morning") == _btsst) &
                            (_bts_df["tcs"].fillna(0) >= _btslo) &
                            (_bts_df["tcs"].fillna(0) <= _btshi) &
                            _bts_df["pnl_r_sim"].notna()
                        ].copy()
                        _bts_tdf["pnl_r_sim"] = _bts_tdf["pnl_r_sim"].astype(float)
                        if _bts_tdf.empty:
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-radius:8px;padding:12px;text-align:center;">'
                                f'<div style="font-size:13px;font-weight:700;color:{_btsc};">{_btse} {_btsl}</div>'
                                f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">{_btsd}</div>'
                                f'<div style="font-size:12px;color:#546e7a;margin-top:6px;">No trades</div>'
                                f'</div>', unsafe_allow_html=True
                            )
                        else:
                            _bts_tw2  = (_bts_tdf["pnl_r_sim"] > 0).sum()
                            _bts_tl2  = (_bts_tdf["pnl_r_sim"] <= 0).sum()
                            _bts_twr2 = _bts_tw2 / len(_bts_tdf) * 100
                            _bts_texp2 = _bts_tdf["pnl_r_sim"].mean()
                            _bts_ttot2 = _bts_tdf["pnl_r_sim"].sum()
                            _bts_avg_w2 = _bts_tdf[_bts_tdf["pnl_r_sim"] > 0]["pnl_r_sim"].mean() if _bts_tw2 else 0.0
                            _bts_avg_l2 = _bts_tdf[_bts_tdf["pnl_r_sim"] < 0]["pnl_r_sim"].mean() if (_bts_tdf["pnl_r_sim"] < 0).any() else 0.0
                            _bts_twr_c2 = "#2e7d32" if _bts_twr2 >= 60 else ("#ef6c00" if _bts_twr2 >= 50 else "#c62828")
                            _bts_texp_str2 = f'{"+" if _bts_texp2 >= 0 else ""}{_bts_texp2:.3f}R'
                            _bts_texp_col2 = "#2e7d32" if _bts_texp2 > 0 else ("#ef6c00" if _bts_texp2 == 0 else "#c62828")
                            _bts_is_best2 = _bts_best_exp_r2 is not None and abs(_bts_texp2 - _bts_best_exp_r2) < 1e-9
                            _bts_card_border2 = "2px solid #ffd54f" if _bts_is_best2 else f"3px solid {_btsc}"
                            _bts_best_badge2 = (
                                '<div style="font-size:10px;font-weight:700;color:#ffd54f;'
                                'background:rgba(255,213,79,0.12);border-radius:4px;'
                                'padding:1px 6px;display:inline-block;margin-bottom:4px;">'
                                'Best Edge ⭐</div>'
                            ) if _bts_is_best2 else ""
                            st.markdown(
                                f'<div style="background:#1e2a3a;border-left:{_bts_card_border2};'
                                f'border-radius:8px;padding:12px;text-align:center;">'
                                f'{_bts_best_badge2}'
                                f'<div style="font-size:13px;font-weight:700;color:{_btsc};">{_btse} {_btsl}</div>'
                                f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">{_btsd}</div>'
                                f'<div style="font-size:22px;font-weight:700;color:{_bts_twr_c2};margin-top:4px;">{_bts_twr2:.1f}%</div>'
                                f'<div style="font-size:12px;color:#cfd8dc;">{_bts_tw2}W / {_bts_tl2}L  ·  {len(_bts_tdf)} trades</div>'
                                f'<div style="font-size:13px;font-weight:600;color:{_bts_texp_col2};margin-top:4px;">'
                                f'Exp: {_bts_texp_str2} / trade</div>'
                                f'<div style="font-size:11px;color:#90a4ae;margin-top:2px;">'
                                f'Avg Win: +{_bts_avg_w2:.2f}R  ·  Avg Loss: {_bts_avg_l2:.2f}R</div>'
                                f'<div style="font-size:11px;color:#90a4ae;">'
                                f'Total: {"+" if _bts_ttot2 >= 0 else ""}{_bts_ttot2:.1f}R</div>'
                                f'</div>', unsafe_allow_html=True
                            )

            # ── Row 4 — Tiered vs EOD Head-to-Head Comparison ────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:12px;color:#90a4ae;letter-spacing:1px;'
                'text-transform:uppercase;margin-bottom:8px;">'
                '📊 Tiered vs EOD Hold — Head-to-Head Comparison</div>',
                unsafe_allow_html=True,
            )
            _bts_cmp_has_eod    = "eod_pnl_r"   in _bts_df.columns and _bts_df["eod_pnl_r"].notna().any()
            _bts_cmp_has_tiered = "tiered_pnl_r" in _bts_df.columns and _bts_df["tiered_pnl_r"].notna().any()

            if not _bts_cmp_has_eod or not _bts_cmp_has_tiered:
                st.info(
                    "Both EOD and tiered R values are needed for this comparison.  \n"
                    "EOD data requires a close-price backfill; tiered data populates as new backtests run."
                )
            else:
                _bts_cmp_scan_raw  = (
                    sorted(_bts_df["scan_type"].dropna().unique().tolist())
                    if "scan_type" in _bts_df.columns else []
                )
                _bts_cmp_scan_opts = ["All"] + _bts_cmp_scan_raw
                _bts_cmp_scan = st.selectbox(
                    "Scan type",
                    options=_bts_cmp_scan_opts,
                    index=0,
                    key="bts_cmp_scan_filter",
                    help="Filter comparison to a specific scan type, or 'All' for combined.",
                )
                _bts_cmp_df = _bts_df.copy()
                if _bts_cmp_scan != "All" and "scan_type" in _bts_cmp_df.columns:
                    _bts_cmp_df = _bts_cmp_df[_bts_cmp_df["scan_type"] == _bts_cmp_scan].copy()

                # Matched rows — both metrics present on the same trade (true apples-to-apples)
                _bts_both = _bts_cmp_df[
                    _bts_cmp_df["eod_pnl_r"].notna() & _bts_cmp_df["tiered_pnl_r"].notna()
                ].copy()

                # Individual coverage counts for display
                _bts_eod_n    = len(_bts_cmp_df[_bts_cmp_df["eod_pnl_r"].notna()])
                _bts_tie_n    = len(_bts_cmp_df[_bts_cmp_df["tiered_pnl_r"].notna()])
                _bts_both_n   = len(_bts_both)

                # Head-to-head averages computed on matched rows only
                _bts_avg_eod    = _bts_both["eod_pnl_r"].astype(float).mean()    if not _bts_both.empty else None
                _bts_avg_tiered = _bts_both["tiered_pnl_r"].astype(float).mean() if not _bts_both.empty else None

                def _bts_r_str(val, n):
                    if val is None:
                        return "—"
                    sign = "+" if val >= 0 else ""
                    return f"{sign}{val:.3f}R ({n} trades)"

                def _bts_r_color(val):
                    if val is None:
                        return "#546e7a"
                    return "#4caf50" if val >= 0 else "#ef5350"

                _bts_eod_str    = _bts_r_str(_bts_avg_eod,    _bts_both_n)
                _bts_tiered_str = _bts_r_str(_bts_avg_tiered, _bts_both_n)
                _bts_eod_clr    = _bts_r_color(_bts_avg_eod)
                _bts_tiered_clr = _bts_r_color(_bts_avg_tiered)

                if _bts_avg_eod is not None and _bts_avg_tiered is not None:
                    _bts_diff      = _bts_avg_tiered - _bts_avg_eod
                    _bts_diff_sign = "+" if _bts_diff >= 0 else ""
                    # % improvement always uses EOD as the baseline denominator
                    if _bts_avg_eod != 0:
                        _bts_pct       = _bts_diff / abs(_bts_avg_eod) * 100
                        _bts_pct_valid = True
                    else:
                        _bts_pct       = 0.0
                        _bts_pct_valid = False
                    if abs(_bts_diff) < 0.001:
                        _bts_verdict     = "Strategies tied"
                        _bts_verdict_clr = "#90a4ae"
                        _bts_pct_label   = ""
                    elif _bts_diff > 0:
                        _bts_verdict     = (
                            f"Tiered exits outperform EOD hold by "
                            f"{_bts_diff_sign}{_bts_diff:.3f}R per trade"
                        )
                        _bts_verdict_clr = "#ffb74d"
                        _bts_pct_label   = (
                            f"+{_bts_pct:.1f}% vs EOD baseline" if _bts_pct_valid else "N/A (EOD avg = 0)"
                        )
                    else:
                        _bts_verdict     = (
                            f"EOD hold outperforms tiered exits by "
                            f"{abs(_bts_diff):.3f}R per trade"
                        )
                        _bts_verdict_clr = "#81c784"
                        _bts_pct_label   = (
                            f"{_bts_pct:.1f}% vs EOD baseline" if _bts_pct_valid else "N/A (EOD avg = 0)"
                        )
                elif _bts_both_n == 0 and (_bts_eod_n > 0 or _bts_tie_n > 0):
                    # Data exists in the full set but no overlap for current filter
                    _bts_verdict     = "No trades match both metrics for the current filter — try widening the date range or changing scan type"
                    _bts_verdict_clr = "#78909c"
                    _bts_pct_label   = ""
                else:
                    _bts_verdict     = "Run close-price backfill to populate both metrics"
                    _bts_verdict_clr = "#546e7a"
                    _bts_pct_label   = ""

                st.markdown(
                    f'<div style="background:#020813; border:1px solid #1a2744; border-radius:8px; '
                    f'padding:14px 24px; margin-bottom:12px;">'
                    f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
                    f'letter-spacing:1.5px; margin-bottom:10px; font-weight:700; font-family:monospace;">'
                    f'📊 Strategy Comparison — EOD Hold vs Tiered Exits (avg R per trade)'
                    f'<span style="font-size:9px; font-weight:400; color:#37474f; margin-left:10px;">'
                    f'matched trades: {_bts_both_n} · EOD coverage: {_bts_eod_n} · Tiered coverage: {_bts_tie_n}</span>'
                    f'</div>'
                    f'<div style="display:flex; gap:32px; flex-wrap:wrap; align-items:center;">'

                    f'<div>'
                    f'<div style="font-size:9px; color:#81c784; text-transform:uppercase; '
                    f'letter-spacing:1px; margin-bottom:2px;">📅 Held to Close (EOD)</div>'
                    f'<div style="font-size:26px; font-weight:800; color:{_bts_eod_clr}; '
                    f'font-family:monospace;">{_bts_eod_str}</div>'
                    f'</div>'

                    f'<div style="font-size:20px; color:#37474f; align-self:center;">vs</div>'

                    f'<div>'
                    f'<div style="font-size:9px; color:#ffb74d; text-transform:uppercase; '
                    f'letter-spacing:1px; margin-bottom:2px;">🪜 50/25/25 Ladder (Tiered)</div>'
                    f'<div style="font-size:26px; font-weight:800; color:{_bts_tiered_clr}; '
                    f'font-family:monospace;">{_bts_tiered_str}</div>'
                    f'</div>'

                    f'<div style="border-left:1px solid #1a2744; padding-left:24px; align-self:center;">'
                    f'<div style="font-size:12px; font-weight:700; color:{_bts_verdict_clr};">{_bts_verdict}</div>'
                    + (f'<div style="font-size:11px; color:#ffb74d; margin-top:2px;">{_bts_pct_label}</div>' if _bts_pct_label else '')
                    + f'<div style="font-size:10px; color:#37474f; margin-top:3px;">'
                    f'Avg R computed on matched rows only · % vs EOD baseline</div>'
                    f'</div>'

                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 1c — 5-YEAR BACKTEST INTELLIGENCE GRID (Scan Type × TCS)
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 🗺️ 5-Year Edge Map — Scan Type × TCS")
    st.caption(
        "True expectancy per setup = P(breakout) × Avg R earned on breaks. "
        "Excludes Pending records. Only Bullish/Bearish Breaks counted as entries. "
        "All other outcomes (Range-Bound, Neutral, etc.) reduce P(breakout). "
        "Use the date filter below to narrow to a specific window, or leave blank for all-time data."
    )

    @st.cache_data(ttl=3600, show_spinner=False)
    def _load_backtest_grid(uid, start_date=None, end_date=None):
        RESOLVED = [
            "Bullish Break", "Bearish Break", "Range-Bound", "Both Sides",
            "Neutral", "Ntrl Extreme", "Normal Var", "Nrml Var",
        ]
        grid = []
        for st_name in ["morning", "intraday"]:
            for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]:
                try:
                    rows, offset = [], 0
                    while True:
                        q = (
                            supabase.table("backtest_sim_runs")
                            .select("actual_outcome,pnl_r_sim")
                            .eq("user_id", uid)
                            .eq("scan_type", st_name)
                            .gte("tcs", lo)
                            .lt("tcs", min(hi, 100) if hi < 101 else 200)
                            .in_("actual_outcome", RESOLVED)
                        )
                        if start_date:
                            q = q.gte("sim_date", str(start_date))
                        if end_date:
                            q = q.lte("sim_date", str(end_date))
                        batch = (
                            q.range(offset, offset + 999)
                            .execute()
                            .data or []
                        )
                        if not batch:
                            break
                        rows += batch
                        if len(batch) < 1000:
                            break
                        offset += 1000
                    if not rows:
                        continue
                    breaks = [
                        r for r in rows
                        if r.get("actual_outcome") in ("Bullish Break", "Bearish Break")
                        and r.get("pnl_r_sim") is not None
                    ]
                    p_break = len(breaks) / len(rows) if rows else 0
                    avg_r   = sum(float(r["pnl_r_sim"]) for r in breaks) / len(breaks) if breaks else 0
                    true_e  = p_break * avg_r
                    label   = f"{lo}–{min(hi-1,99)}+" if hi > 99 else f"{lo}–{hi-1}"
                    grid.append({
                        "scan_type":   st_name,
                        "tcs_label":   label,
                        "tcs_lo":      lo,
                        "tcs_hi":      hi,
                        "setups":      len(rows),
                        "breaks":      len(breaks),
                        "p_break_pct": round(p_break * 100, 1),
                        "avg_r":       round(avg_r, 3),
                        "true_exp":    round(true_e, 3),
                    })
                except Exception:
                    continue
        return grid

    # ── Date-range filter for SECTION 1c ─────────────────────────────────────
    _grid_dr_cols = st.columns([1, 1, 4])
    with _grid_dr_cols[0]:
        _grid_start = st.date_input(
            "From",
            value=None,
            key="grid_dr_start",
            help="Filter edge map to runs from this date (inclusive). Leave blank for all time.",
        )
    with _grid_dr_cols[1]:
        _grid_end = st.date_input(
            "To",
            value=None,
            key="grid_dr_end",
            help="Filter edge map to runs up to this date (inclusive). Leave blank for all time.",
        )

    _grid_date_invalid = bool(_grid_start and _grid_end and _grid_start > _grid_end)

    if _grid_date_invalid:
        st.error("'From' date must be on or before the 'To' date — no results shown.")
    elif _grid_start or _grid_end:
        _date_label = []
        if _grid_start:
            _date_label.append(f"from {_grid_start}")
        if _grid_end:
            _date_label.append(f"to {_grid_end}")
        st.caption(f"Edge map filtered {' '.join(_date_label)} — win rates and expectancy reflect this window only.")

    if _grid_date_invalid:
        _bt_grid = []
    else:
        _bt_grid = _load_backtest_grid(
            st.session_state.get("auth_user_id", ""),
            start_date=_grid_start,
            end_date=_grid_end,
        )

    if not _bt_grid:
        if not _grid_date_invalid:
            if _grid_start or _grid_end:
                st.warning("No edge map data found in the selected date range — try widening the filter or clearing the dates.")
            else:
                st.info("Backtest grid data unavailable — batch backtest may still be running.")
    else:
        # ── Priority tier assignment ─────────────────────────────────────────
        def _get_tier(row):
            st_name = row["scan_type"]
            lo      = row["tcs_lo"]
            if st_name == "intraday" and lo >= 70:
                return "🔴 P1", "#c62828", "Intraday 70+ — act on every one"
            if st_name == "intraday" and lo >= 50:
                return "🟠 P2", "#ef6c00", "Intraday 50+ — core bread & butter"
            if st_name == "morning" and lo >= 70:
                return "🟡 P3", "#f9a825", "Morning 70+ — high R, small sample"
            if st_name == "morning" and lo >= 50:
                return "🟢 P4", "#2e7d32", "Morning 50+ — solid, current system"
            return "⚪ Low", "#546e7a", "Below threshold — skip"

        # ── Best combos summary ──────────────────────────────────────────────
        _ranked = sorted(
            [r for r in _bt_grid if r["setups"] >= 50],
            key=lambda x: x["true_exp"], reverse=True
        )

        st.markdown("**Priority Tiers — ranked by true expectancy per setup (5-yr backtest)**")
        _tier_cols = st.columns(4)
        _tier_defs = [
            ("🔴 P1", "Intraday TCS 70+", "#c62828",
             next((r for r in _ranked if r["scan_type"] == "intraday" and r["tcs_lo"] == 70), None)),
            ("🟠 P2", "Intraday TCS 50–69", "#ef6c00",
             next((r for r in _bt_grid if r["scan_type"] == "intraday" and r["tcs_lo"] == 50), None)),
            ("🟡 P3", "Morning TCS 70+", "#f9a825",
             next((r for r in _bt_grid if r["scan_type"] == "morning" and r["tcs_lo"] == 70), None)),
            ("🟢 P4", "Morning TCS 50–69", "#2e7d32",
             next((r for r in _bt_grid if r["scan_type"] == "morning" and r["tcs_lo"] == 50), None)),
        ]
        for _col, (_badge, _label, _color, _rd) in zip(_tier_cols, _tier_defs):
            if _rd:
                with _col:
                    st.markdown(
                        f'<div style="background:#1e2a3a;border-radius:10px;padding:14px 12px;">'
                        f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1px;text-transform:uppercase;">{_badge} {_label}</div>'
                        f'<div style="font-size:24px;font-weight:700;color:{_color};margin:6px 0;">+{_rd["true_exp"]:.2f}R</div>'
                        f'<div style="font-size:12px;color:#cfd8dc;">per setup</div>'
                        f'<div style="font-size:11px;color:#90a4ae;margin-top:4px;">'
                        f'{_rd["p_break_pct"]}% break rate · +{_rd["avg_r"]:.2f}R avg · {_rd["setups"]:,} setups</div>'
                        f'</div>', unsafe_allow_html=True
                    )

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Full grid tables side by side ────────────────────────────────────
        _gc_morning, _gc_intraday = st.columns(2)

        for _gcol, _stype, _stitle in [
            (_gc_morning, "morning", "🌅 Morning Scan (10:47 AM)"),
            (_gc_intraday, "intraday", "🔄 Intraday Scan (2:00 PM)"),
        ]:
            with _gcol:
                st.markdown(f"**{_stitle}**")
                _rows = [r for r in _bt_grid if r["scan_type"] == _stype]
                _rows_sorted = sorted(_rows, key=lambda x: x["tcs_lo"])

                _tbl_rows = []
                for _r in _rows_sorted:
                    _tier_badge, _tier_color, _ = _get_tier(_r)
                    _bar_len = min(int(_r["true_exp"] * 4), 20)
                    _bar_fill = "█" * _bar_len
                    _tbl_rows.append({
                        "TCS": _r["tcs_label"],
                        "Setups": f"{_r['setups']:,}",
                        "Breakout %": f"{_r['p_break_pct']}%",
                        "Avg R": f"+{_r['avg_r']:.2f}R",
                        "True Exp": f"+{_r['true_exp']:.3f}R",
                        "Tier": _tier_badge,
                    })
                _grid_df = pd.DataFrame(_tbl_rows)

                def _color_tier(val):
                    if "P1" in str(val):
                        return "color:#c62828;font-weight:700"
                    if "P2" in str(val):
                        return "color:#ef6c00;font-weight:700"
                    if "P3" in str(val):
                        return "color:#f9a825;font-weight:700"
                    if "P4" in str(val):
                        return "color:#2e7d32;font-weight:700"
                    return "color:#546e7a"

                def _color_exp(val):
                    try:
                        v = float(str(val).replace("+", "").replace("R", ""))
                        if v >= 2.0:
                            return "color:#c62828;font-weight:700"
                        if v >= 1.0:
                            return "color:#ef6c00;font-weight:600"
                        if v >= 0.5:
                            return "color:#2e7d32"
                        return "color:#546e7a"
                    except Exception:
                        return ""

                st.dataframe(
                    _grid_df.style
                    .map(_color_tier, subset=["Tier"])
                    .map(_color_exp, subset=["True Exp"]),
                    use_container_width=True,
                    hide_index=True,
                    height=310,
                )

        # ── Live paper trade breakdown (small sample warning) ─────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("**Your Live Paper Trades — Win Rate by Scan & TCS** *(small sample)*")

        _live_resolved = _pt_df[_pt_df["win_loss"].isin(["W", "L", "Win", "Loss"])].copy() if not _pt_df.empty else pd.DataFrame()
        if _live_resolved.empty:
            st.info("No resolved live paper trades yet.")
        else:
            if "scan_type" not in _live_resolved.columns:
                _live_resolved["scan_type"] = "morning"
            _live_resolved["scan_type"] = _live_resolved["scan_type"].fillna("morning")
            if "tcs" not in _live_resolved.columns:
                _live_resolved["tcs"] = 0
            _live_resolved["tcs"] = pd.to_numeric(_live_resolved["tcs"], errors="coerce").fillna(0)
            _live_resolved["is_win"] = _live_resolved["win_loss"].isin(["W", "Win"])

            _live_rows = []
            for _st in ["morning", "intraday"]:
                for _lo, _hi, _lbl in [(0, 50, "<50"), (50, 60, "50–59"), (60, 70, "60–69"), (70, 101, "70+")]:
                    _sub = _live_resolved[
                        (_live_resolved["scan_type"] == _st) &
                        (_live_resolved["tcs"] >= _lo) &
                        (_live_resolved["tcs"] < _hi)
                    ]
                    if _sub.empty:
                        continue
                    _w = _sub["is_win"].sum()
                    _n = len(_sub)
                    _wr = _w / _n * 100
                    _tier_badge, _, _ = _get_tier({"scan_type": _st, "tcs_lo": _lo})
                    _live_rows.append({
                        "Scan": _st.capitalize(),
                        "TCS": _lbl,
                        "W": int(_w),
                        "L": int(_n - _w),
                        "Win Rate": f"{_wr:.0f}%",
                        "Tier": _tier_badge,
                        "Sample": f"n={_n}",
                    })

            if _live_rows:
                _live_tbl = pd.DataFrame(_live_rows)
                st.caption(
                    f"⚠️  Live data is {len(_live_resolved)} trades — use 5-year backtest above for decision-making."
                )
                st.dataframe(
                    _live_tbl.style.map(_color_tier, subset=["Tier"]),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No resolved live paper trades to break down.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 2 — PAPER TRADE HISTORY + RUNNING P&L CHART
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 📄 Paper Trade History")

    _verified = pd.DataFrame()
    if _pt_df.empty or _total_trades == 0:
        st.info("No verified paper trades yet. Trades are logged and auto-verified by the bot each day.")
    else:
        _verified = _pt_df[_pt_df["win_loss"].isin(["W", "L", "Win", "Loss"])].copy()

        if "follow_thru_pct" in _verified.columns:
            _verified["_sim_pnl"] = _verified["win_loss"].map({"W": 1, "Win": 1, "L": -1, "Loss": -1}) * \
                                    _verified["follow_thru_pct"].fillna(0).astype(float) / 100 * _sim_per_trade
            _verified = _verified.sort_values("trade_date", ascending=True)
            _verified["_running_pnl"] = _verified["_sim_pnl"].cumsum()

            # Chart
            _fig_pnl = go.Figure()
            _fig_pnl.add_trace(go.Scatter(
                x=_verified["trade_date"].astype(str),
                y=_verified["_running_pnl"],
                mode="lines+markers",
                line=dict(color="#26c6da", width=2),
                marker=dict(color=_verified["win_loss"].map({"W": "#66bb6a", "Win": "#66bb6a", "L": "#ef5350", "Loss": "#ef5350"}).tolist(),
                            size=8),
                name="Running P&L"
            ))
            _fig_pnl.add_hline(y=0, line_dash="dash", line_color="#546e7a", line_width=1)
            _fig_pnl.update_layout(
                height=220, margin=dict(l=0, r=0, t=10, b=10),
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="#cfd8dc", size=11),
                xaxis=dict(gridcolor="#1e2a3a", tickfont=dict(size=10)),
                yaxis=dict(gridcolor="#1e2a3a", tickprefix="$", tickfont=dict(size=10)),
                showlegend=False,
            )
            st.plotly_chart(_fig_pnl, use_container_width=True, config={"displayModeBar": False})

        # Table
        _display_cols = [c for c in ["trade_date", "ticker", "tcs", "predicted_structure",
                                      "actual_outcome", "win_loss", "follow_thru_pct",
                                      "mae", "mfe",
                                      "sim_pnl_100sh",
                                      "eod_pnl_r", "tiered_pnl_r"] if c in _verified.columns]
        _show = _verified[_display_cols].rename(columns={
            "trade_date": "Date", "ticker": "Ticker", "tcs": "TCS",
            "predicted_structure": "Predicted", "actual_outcome": "Actual",
            "win_loss": "W/L", "follow_thru_pct": "FT %",
            "mae": "MAE %", "mfe": "MFE %",
            "sim_pnl_100sh": "P&L (100sh)",
            "eod_pnl_r": "EOD Hold R", "tiered_pnl_r": "Tiered Exit R",
        }).reset_index(drop=True)

        def _color_wl(val):
            if val in ("W", "Win"):   return "color: #66bb6a; font-weight:700"
            if val in ("L", "Loss"):  return "color: #ef5350; font-weight:700"
            return ""

        def _color_mae(val):
            try:
                v = float(val)
                if v > 5:   return "color: #ef5350; font-weight:700"
                if v > 2:   return "color: #ff9800"
                return "color: #66bb6a"
            except (ValueError, TypeError):
                return ""

        def _color_mfe(val):
            try:
                v = float(val)
                if v > 5:   return "color: #66bb6a; font-weight:700"
                if v > 2:   return "color: #81c784"
                return "color: #90a4ae"
            except (ValueError, TypeError):
                return ""

        def _color_r(val):
            try:
                v = float(val)
                if v > 0:  return "color: #66bb6a; font-weight:700"
                if v < 0:  return "color: #ef5350; font-weight:700"
                return ""
            except (ValueError, TypeError):
                return ""

        _style_map = {}
        if "W/L" in _show.columns:
            _style_map["W/L"] = _color_wl
        if "MAE %" in _show.columns:
            _style_map["MAE %"] = _color_mae
        if "MFE %" in _show.columns:
            _style_map["MFE %"] = _color_mfe
        if "EOD Hold R" in _show.columns:
            _style_map["EOD Hold R"] = _color_r
        if "Tiered Exit R" in _show.columns:
            _style_map["Tiered Exit R"] = _color_r

        _styled = _show.style
        for _col_name, _fn in _style_map.items():
            _styled = _styled.map(_fn, subset=[_col_name])

        st.dataframe(_styled, use_container_width=True, height=280)

        # ── EOD Hold vs Tiered Exit Strategy Comparison ─────────────────────
        _pt_has_eod    = "eod_pnl_r"    in _verified.columns
        _pt_has_tiered = "tiered_pnl_r" in _verified.columns
        _pt_eod_vals    = _verified["eod_pnl_r"].dropna().tolist()    if _pt_has_eod    else []
        _pt_tiered_vals = _verified["tiered_pnl_r"].dropna().tolist() if _pt_has_tiered else []
        _pt_avg_eod    = round(sum(_pt_eod_vals)    / len(_pt_eod_vals),    3) if _pt_eod_vals    else None
        _pt_avg_tiered = round(sum(_pt_tiered_vals) / len(_pt_tiered_vals), 3) if _pt_tiered_vals else None

        # For the verdict use only rows where BOTH metrics are populated so the
        # comparison is apples-to-apples (partial backfill won't skew the diff).
        if _pt_has_eod and _pt_has_tiered:
            _pt_paired = _verified[_verified["eod_pnl_r"].notna() & _verified["tiered_pnl_r"].notna()]
        else:
            _pt_paired = pd.DataFrame()
        _pt_paired_n = len(_pt_paired)

        if _pt_avg_eod is not None or _pt_avg_tiered is not None:
            def _pt_r_str(val, n):
                if val is None:
                    return "—"
                sign = "+" if val >= 0 else ""
                return f"{sign}{val:.3f}R ({n} trades)"

            def _pt_r_color(val):
                if val is None:
                    return "#546e7a"
                return "#4caf50" if val >= 0 else "#ef5350"

            _pt_eod_str    = _pt_r_str(_pt_avg_eod,    len(_pt_eod_vals))
            _pt_tiered_str = _pt_r_str(_pt_avg_tiered, len(_pt_tiered_vals))
            _pt_eod_clr    = _pt_r_color(_pt_avg_eod)
            _pt_tiered_clr = _pt_r_color(_pt_avg_tiered)

            if _pt_paired_n > 0:
                _pt_pair_eod    = _pt_paired["eod_pnl_r"].astype(float).mean()
                _pt_pair_tiered = _pt_paired["tiered_pnl_r"].astype(float).mean()
                _pt_diff        = _pt_pair_tiered - _pt_pair_eod
                _pt_diff_sign   = "+" if _pt_diff >= 0 else ""
                _pt_paired_note = f"({_pt_paired_n} matched trades)"
                if abs(_pt_diff) < 0.001:
                    _pt_verdict     = f"Strategies tied on your paper trades {_pt_paired_note}"
                    _pt_verdict_clr = "#90a4ae"
                elif _pt_diff > 0:
                    _pt_verdict     = f"Tiered exits outperform EOD hold by {_pt_diff_sign}{_pt_diff:.3f}R per trade {_pt_paired_note}"
                    _pt_verdict_clr = "#ffb74d"
                else:
                    _pt_verdict     = f"EOD hold outperforms tiered exits by {abs(_pt_diff):.3f}R per trade {_pt_paired_note}"
                    _pt_verdict_clr = "#81c784"
            else:
                _pt_verdict     = "Both metrics needed for a comparison"
                _pt_verdict_clr = "#546e7a"

            st.markdown(
                f'<div style="background:#020813; border:1px solid #1a2744; border-radius:8px; '
                f'padding:14px 24px; margin-top:14px; margin-bottom:6px;">'
                f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
                f'letter-spacing:1.5px; margin-bottom:10px; font-weight:700; font-family:monospace;">'
                f'📊 Strategy Comparison — EOD Hold vs Tiered Exits (avg R per trade, paper trades)</div>'
                f'<div style="display:flex; gap:32px; flex-wrap:wrap; align-items:center;">'

                f'<div>'
                f'<div style="font-size:9px; color:#81c784; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:2px;">📅 Held to Close (EOD)</div>'
                f'<div style="font-size:26px; font-weight:800; color:{_pt_eod_clr}; '
                f'font-family:monospace;">{_pt_eod_str}</div>'
                f'</div>'

                f'<div style="font-size:20px; color:#37474f; align-self:center;">vs</div>'

                f'<div>'
                f'<div style="font-size:9px; color:#ffb74d; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:2px;">🪜 50/25/25 Ladder (Tiered)</div>'
                f'<div style="font-size:26px; font-weight:800; color:{_pt_tiered_clr}; '
                f'font-family:monospace;">{_pt_tiered_str}</div>'
                f'</div>'

                f'<div style="border-left:1px solid #1a2744; padding-left:24px; align-self:center;">'
                f'<div style="font-size:12px; font-weight:700; color:{_pt_verdict_clr};">{_pt_verdict}</div>'
                f'<div style="font-size:10px; color:#37474f; margin-top:3px;">'
                f'Positive = strategy added value vs a simple hold-to-close</div>'
                f'</div>'

                f'</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 3 — STRUCTURE WIN RATE BREAKDOWN
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 🧠 Structure Prediction Win Rate")

    if _at_all_df.empty:
        st.info("No structure predictions logged yet.")
    else:
        _at_all_df["_correct_bool"] = _at_all_df["correct"] == "✅"
        _by_struct = (
            _at_all_df.groupby("predicted")
            .agg(Total=("_correct_bool", "count"), Correct=("_correct_bool", "sum"))
            .assign(WinRate=lambda d: (d["Correct"].astype(float) / d["Total"].astype(float) * 100).round(1))
            .sort_values("Total", ascending=False)
            .reset_index()
        )
        _by_struct.columns = ["Structure", "Samples", "Correct", "Win Rate %"]

        # Overall row
        _overall_row = pd.DataFrame([{
            "Structure": "⭐ ALL",
            "Samples": _all_total,
            "Correct": _all_wins,
            "Win Rate %": round(_all_rate, 1)
        }])
        _by_struct_display = pd.concat([_overall_row, _by_struct], ignore_index=True)

        # Bar chart
        if len(_by_struct) > 0:
            _bar_fig = go.Figure(go.Bar(
                x=_by_struct["Structure"],
                y=_by_struct["Win Rate %"],
                text=_by_struct.apply(lambda r: f'{r["Win Rate %"]:.0f}%<br>({r["Samples"]})', axis=1),
                textposition="outside",
                marker_color=[
                    "#66bb6a" if v >= 65 else ("#ffa726" if v >= 50 else "#ef5350")
                    for v in _by_struct["Win Rate %"]
                ],
                marker_line_width=0,
            ))
            _bar_fig.add_hline(y=50, line_dash="dash", line_color="#546e7a", line_width=1,
                               annotation_text="50% baseline", annotation_font_size=10)
            _bar_fig.update_layout(
                height=280, margin=dict(l=0, r=0, t=30, b=10),
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="#cfd8dc", size=11),
                xaxis=dict(gridcolor="#1e2a3a"),
                yaxis=dict(gridcolor="#1e2a3a", range=[0, 110], ticksuffix="%"),
                showlegend=False,
            )
            st.plotly_chart(_bar_fig, use_container_width=True, config={"displayModeBar": False})

        def _color_rate(val):
            if isinstance(val, (int, float)):
                if val >= 65:   return "color: #66bb6a; font-weight:700"
                if val >= 50:   return "color: #ffa726"
                return "color: #ef5350"
            return ""

        st.dataframe(
            _by_struct_display.style.map(_color_rate, subset=["Win Rate %"]),
            use_container_width=True, hide_index=True, height="stretch"
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 3B — MAE / MFE EXECUTION DEPTH
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 📐 MAE / MFE Execution Depth")
    st.caption("Maximum Adverse Excursion (worst drawdown) vs Maximum Favorable Excursion (best unrealized gain) per trade")

    if not _verified.empty and "mae" in _verified.columns and "mfe" in _verified.columns:
        _mae_mfe = _verified.dropna(subset=["mae", "mfe"])
        if len(_mae_mfe) >= 1:
            _avg_mae = _mae_mfe["mae"].mean()
            _avg_mfe = _mae_mfe["mfe"].mean()
            _mfe_mae_ratio = round(_avg_mfe / _avg_mae, 2) if _avg_mae > 0 else 0

            _med_mae = _mae_mfe["mae"].median()
            _med_mfe = _mae_mfe["mfe"].median()

            _mc1, _mc2, _mc3, _mc4 = st.columns(4)
            _mc1.metric("Avg MAE", f"{_avg_mae:.1f}%", delta=f"med {_med_mae:.1f}%", delta_color="off")
            _mc2.metric("Avg MFE", f"{_avg_mfe:.1f}%", delta=f"med {_med_mfe:.1f}%", delta_color="off")
            _mc3.metric("MFE:MAE Ratio", f"{_mfe_mae_ratio:.2f}x")

            _actual_ft = _mae_mfe["follow_thru_pct"].abs().mean() if "follow_thru_pct" in _mae_mfe.columns else 0
            _capture_pct = round(_actual_ft / _avg_mfe * 100, 1) if _avg_mfe > 0 else 0
            _mc4.metric("Move Captured", f"{_capture_pct}%",
                        help="How much of the available MFE you actually captured at close")

            _left_on_table = round(_avg_mfe - _actual_ft, 2) if _avg_mfe > _actual_ft else 0
            if _left_on_table > 0:
                st.info(f"💡 Avg money left on table: **{_left_on_table:.1f}%** per trade — potential optimization target for exit timing")

            if "predicted_structure" in _mae_mfe.columns and len(_mae_mfe) >= 3:
                _mae_by_struct = (
                    _mae_mfe.groupby("predicted_structure")
                    .agg(
                        Trades=("mae", "count"),
                        AvgMAE=("mae", "mean"),
                        AvgMFE=("mfe", "mean"),
                    )
                    .assign(
                        Ratio=lambda d: (d["AvgMFE"] / d["AvgMAE"].replace(0, float("nan"))).round(2),
                    )
                    .sort_values("Trades", ascending=False)
                    .reset_index()
                )
                _mae_by_struct.columns = ["Structure", "Trades", "Avg MAE %", "Avg MFE %", "MFE:MAE"]

                def _color_ratio(val):
                    try:
                        v = float(val)
                        if v >= 2:   return "color: #66bb6a; font-weight:700"
                        if v >= 1:   return "color: #81c784"
                        return "color: #ef5350"
                    except (ValueError, TypeError):
                        return ""

                st.dataframe(
                    _mae_by_struct.style.map(_color_ratio, subset=["MFE:MAE"]).format(
                        {"Avg MAE %": "{:.1f}", "Avg MFE %": "{:.1f}", "MFE:MAE": "{:.2f}"}
                    ),
                    use_container_width=True, hide_index=True
                )

            if "exit_trigger" in _mae_mfe.columns:
                _exit_counts = _mae_mfe["exit_trigger"].value_counts()
                if len(_exit_counts) > 0:
                    _exit_labels = {"target_hit": "🎯 Target Hit", "stop_hit": "🛑 Stop Hit", "time_based": "⏰ Time-Based"}
                    _exit_rows = []
                    for _et, _cnt in _exit_counts.items():
                        _sub = _mae_mfe[_mae_mfe["exit_trigger"] == _et]
                        _exit_rows.append({
                            "Exit Type": _exit_labels.get(_et, _et),
                            "Count": _cnt,
                            "Avg MAE %": round(_sub["mae"].mean(), 1),
                            "Avg MFE %": round(_sub["mfe"].mean(), 1),
                            "Win Rate": f"{_sub['win_loss'].isin(['W', 'Win']).mean() * 100:.0f}%" if "win_loss" in _sub.columns else "—",
                        })
                    st.dataframe(pd.DataFrame(_exit_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No trades with MAE/MFE data yet. Data will populate as paper trades run with full-day bars.")
    else:
        st.info("MAE/MFE columns not yet available. Run the migration SQL and paper trades will start logging execution depth data.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 3C — PORTFOLIO RISK METRICS (Sharpe, Alpha, Drawdown)
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 📈 Portfolio Risk Metrics")

    _pt_uid = st.session_state.get("auth_user_id", "")
    _pt_df = _cached_load_paper_trades(user_id=_pt_uid, days=365)
    _pm = compute_portfolio_metrics(
        _pt_df,
        api_key=st.session_state.get("ALPACA_API_KEY", ""),
        secret_key=st.session_state.get("ALPACA_SECRET_KEY", ""),
    )

    if _pm["trade_count"] >= 3 and _pm["sharpe"] is not None:
        _pm_c1, _pm_c2, _pm_c3, _pm_c4, _pm_c5 = st.columns(5)
        with _pm_c1:
            _sh_color = "#66bb6a" if (_pm["sharpe_annual"] or 0) > 1.0 else "#ef5350" if (_pm["sharpe_annual"] or 0) < 0 else "#ffa726"
            st.markdown(
                f"<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                f"<div style='font-size:11px;color:#777'>Sharpe (Annual)</div>"
                f"<div style='font-size:24px;font-weight:800;color:{_sh_color}'>"
                f"{_pm['sharpe_annual']:.2f}</div></div>",
                unsafe_allow_html=True
            )
        with _pm_c2:
            _dd = _pm["max_drawdown_pct"] or 0
            _dd_color = "#66bb6a" if _dd > -5 else "#ef5350" if _dd < -20 else "#ffa726"
            st.markdown(
                f"<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                f"<div style='font-size:11px;color:#777'>Max Drawdown</div>"
                f"<div style='font-size:24px;font-weight:800;color:{_dd_color}'>"
                f"{_dd:.1f}%</div></div>",
                unsafe_allow_html=True
            )
        with _pm_c3:
            if _pm["alpha_vs_spy"] is not None:
                _al = _pm["alpha_vs_spy"]
                _al_color = "#66bb6a" if _al > 0 else "#ef5350"
                st.markdown(
                    f"<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                    f"<div style='font-size:11px;color:#777'>Alpha vs SPY</div>"
                    f"<div style='font-size:24px;font-weight:800;color:{_al_color}'>"
                    f"{'+' if _al > 0 else ''}{_al:.2f}%</div></div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                    "<div style='font-size:11px;color:#777'>Alpha vs SPY</div>"
                    "<div style='font-size:16px;color:#555'>Need API keys</div></div>",
                    unsafe_allow_html=True
                )
        with _pm_c4:
            if _pm.get("alpha_vs_iwm") is not None:
                _al_iwm = _pm["alpha_vs_iwm"]
                _al_iwm_color = "#66bb6a" if _al_iwm > 0 else "#ef5350"
                st.markdown(
                    f"<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                    f"<div style='font-size:11px;color:#777'>Alpha vs IWM</div>"
                    f"<div style='font-size:24px;font-weight:800;color:{_al_iwm_color}'>"
                    f"{'+' if _al_iwm > 0 else ''}{_al_iwm:.2f}%</div></div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                    "<div style='font-size:11px;color:#777'>Alpha vs IWM</div>"
                    "<div style='font-size:16px;color:#555'>Need API keys</div></div>",
                    unsafe_allow_html=True
                )
        with _pm_c5:
            _sh_d = _pm["sharpe"] or 0
            st.markdown(
                f"<div style='text-align:center;padding:12px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a'>"
                f"<div style='font-size:11px;color:#777'>Sharpe (Daily)</div>"
                f"<div style='font-size:24px;font-weight:800;color:#7986cb'>"
                f"{_sh_d:.3f}</div></div>",
                unsafe_allow_html=True
            )

        if not _pm["rolling_drawdown"].empty:
            import plotly.graph_objects as _go_pm
            _dd_fig = _go_pm.Figure()
            _dd_fig.add_trace(_go_pm.Scatter(
                x=[str(d) for d in _pm["rolling_drawdown"]["date"]],
                y=_pm["rolling_drawdown"]["drawdown_pct"],
                fill="tozeroy",
                fillcolor="rgba(239,83,80,0.15)",
                line=dict(color="#ef5350", width=1.5),
                name="Drawdown %",
            ))
            _dd_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0e0e1a", plot_bgcolor="#0e0e1a",
                height=220, margin=dict(l=40, r=20, t=30, b=30),
                title=dict(text="Rolling Drawdown", font=dict(size=13)),
                yaxis=dict(title="Drawdown %", ticksuffix="%"),
                xaxis=dict(title=""),
                showlegend=False,
            )
            st.plotly_chart(_dd_fig, use_container_width=True)

        st.caption(f"Based on {_pm['trade_count']} paper trades. "
                   f"Current drawdown: {_pm['current_drawdown_pct']:.1f}%")
    else:
        st.info(f"Need at least 3 trading days with P&L data for risk metrics. "
                f"Currently have {_pm['trade_count']} paper trade(s).")

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 4 — STRUCTURE TCS THRESHOLDS (per-structure priority)
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 Structure Priority — Adaptive TCS Thresholds")
    st.caption("Your hit rate per structure determines how aggressively the system should trade it. Higher hit rate → lower TCS required → take it more often.")

    _tcs_data = compute_structure_tcs_thresholds()
    if _tcs_data:
        for _t in _tcs_data:
            _hr = _t["hit_rate"]
            _rec = _t["recommended_tcs"]
            _n = _t["sample_count"]
            _conf = _t["confidence"]
            _status = _t["status"]
            _bw_val = _t["brain_weight"]

            if _hr is not None:
                if _hr >= 70:
                    _hr_color = "#66bb6a"
                elif _hr >= 55:
                    _hr_color = "#ffa726"
                elif _hr >= 40:
                    _hr_color = "#ff7043"
                else:
                    _hr_color = "#ef5350"

                _tcs_color = "#66bb6a" if _rec <= 55 else "#ffa726" if _rec <= 70 else "#ef5350"
                _action = "AGGRESSIVE — take with minimal confirmation" if _rec <= 55 else "STANDARD — require normal confluence" if _rec <= 65 else "CAUTIOUS — need strong supporting signals" if _rec <= 75 else "AVOID unless everything aligns"

                st.markdown(
                    f"<div style='padding:14px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a;margin-bottom:8px'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px'>"
                    f"<div style='flex:1;min-width:200px'>"
                    f"<span style='font-size:16px;font-weight:700;color:#e0e0e0'>{_status} {_t['structure']}</span>"
                    f"<div style='font-size:11px;color:#777;margin-top:2px'>{_n} samples ({_t['journal_n']} journal + {_t['bot_n']} bot) · Confidence: {_conf} · Weight: {_bw_val}</div>"
                    f"</div>"
                    f"<div style='display:flex;gap:20px;align-items:center'>"
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:11px;color:#777'>Hit Rate</div>"
                    f"<div style='font-size:22px;font-weight:800;color:{_hr_color}'>{_hr:.1f}%</div>"
                    f"</div>"
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:11px;color:#777'>Min TCS</div>"
                    f"<div style='font-size:22px;font-weight:800;color:{_tcs_color}'>{_rec}</div>"
                    f"</div>"
                    f"</div>"
                    f"</div>"
                    f"<div style='font-size:12px;color:#90a4ae;margin-top:6px'>{_action}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f"<div style='padding:14px;background:#1a1a2e;border-radius:8px;border:1px solid #2a2a4a;margin-bottom:8px;opacity:0.5'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                    f"<div>"
                    f"<span style='font-size:16px;font-weight:700;color:#555'>{_status} {_t['structure']}</span>"
                    f"<div style='font-size:11px;color:#555;margin-top:2px'>No verified predictions yet</div>"
                    f"</div>"
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:11px;color:#555'>Min TCS</div>"
                    f"<div style='font-size:22px;font-weight:800;color:#555'>{_rec}</div>"
                    f"</div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
    else:
        st.info("Connect to Supabase to see per-structure TCS thresholds.")

    st.markdown("<br>", unsafe_allow_html=True)

    # SECTION 5 — BRAIN WEIGHTS (raw learned values)
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 🧠 Current Brain Weights")

    # ── Recalibrate button ────────────────────────────────────────────────────
    _rc_col1, _rc_col2 = st.columns([2, 3])
    with _rc_col1:
        _do_recal = st.button("🔄 Recalibrate Both Brains Now", use_container_width=True,
                              help="Runs live recalibration (journal + bot) AND historical calibration (11k+ backtest rows)")
    if _do_recal:
        from paper_trader_bot import _alert_tcs_threshold_changes as _alert_tcs
        _old_tcs = _cached_load_tcs_thresholds()
        with st.spinner("Recalibrating live brain from journal + paper trades…"):
            _live_cal = recalibrate_from_supabase(user_id=_uid)
        with st.spinner("Calibrating historical brain from backtest data…"):
            _hist_cal = recalibrate_from_history(user_id=_uid)
        _new_tcs = _cached_load_tcs_thresholds()
        _alert_tcs(_old_tcs, _new_tcs)
        # Persist one clean history event using the true before/after snapshots
        # (individual save_tcs_thresholds calls do not record history so that
        # only a single, accurate entry is stored per full recalibration run).
        append_tcs_threshold_history(_old_tcs, _new_tcs)

        _live_src = _live_cal.get("sources", {})
        _hist_src = _hist_cal.get("sources", {})
        st.success(
            f"Done — Live: {_live_src.get('accuracy_tracker',0)} journal + "
            f"{_live_src.get('paper_trades',0)} bot trades | "
            f"Historical: {_hist_src.get('backtest_sim_runs',0):,} backtest rows"
        )

        # ── TCS threshold diff ────────────────────────────────────────────────────
        _tcs_rows = []
        for _k, _nv in _new_tcs.items():
            _ov = _old_tcs.get(_k)
            if _ov is None:
                continue
            try:
                _nv_i, _ov_i = int(_nv), int(_ov)
            except (TypeError, ValueError):
                continue
            _diff = _nv_i - _ov_i
            if abs(_diff) >= 3:
                _tcs_rows.append({
                    "Structure": WK_DISPLAY.get(_k, _k),
                    "Before":    _ov_i,
                    "After":     _nv_i,
                    "Change":    f"{'+'if _diff>0 else ''}{_diff}",
                })
        if _tcs_rows:
            st.markdown("#### 🎯 TCS Threshold Changes")

            def _color_tcs_change(val):
                try:
                    v = int(str(val).replace("+", ""))
                    if v > 0:  return "color: #ef5350; font-weight:700"
                    if v < 0:  return "color: #66bb6a; font-weight:700"
                except Exception:
                    pass
                return ""

            _tcs_df = pd.DataFrame(_tcs_rows)
            st.dataframe(
                _tcs_df.style.map(_color_tcs_change, subset=["Change"]),
                use_container_width=True, hide_index=True,
            )

        # Delta table
        _all_deltas = {}
        for _d in _live_cal.get("deltas", []):
            _all_deltas[_d["key"]] = {
                "Structure":    _d["key"],
                "Live Acc %":   _d.get("blended_acc", "—"),
                "Live n":       (_d.get("journal_n",0) or 0) + (_d.get("bot_n",0) or 0),
                "Hist Acc %":   "—",
                "Hist n":       0,
                "Old Weight":   _d["old"],
                "New Weight":   _d["new"],
                "Δ":            _d["delta"],
            }
        for _d in _hist_cal.get("deltas", []):
            _k = _d["key"]
            if _k in _all_deltas:
                _all_deltas[_k]["Hist Acc %"] = _d.get("hist_acc", "—")
                _all_deltas[_k]["Hist n"]     = _d.get("hist_n", 0)
            else:
                _all_deltas[_k] = {
                    "Structure":  _k,
                    "Live Acc %": "—",
                    "Live n":     0,
                    "Hist Acc %": _d.get("hist_acc", "—"),
                    "Hist n":     _d.get("hist_n", 0),
                    "Old Weight": _d["old"],
                    "New Weight": _d["new"],
                    "Δ":          _d["delta"],
                }

        if _all_deltas:
            _delta_df = pd.DataFrame(list(_all_deltas.values()))

            def _color_delta_cell(val):
                try:
                    v = float(val)
                    if v > 0.02:   return "color: #66bb6a; font-weight:700"
                    if v < -0.02:  return "color: #ef5350"
                except Exception:
                    pass
                return "color: #90a4ae"

            st.dataframe(
                _delta_df.style.map(_color_delta_cell, subset=["Δ"]),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No structures had enough data to update weights.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── TCS Threshold History (collapsible) ───────────────────────────────────
    _bw_hist_days = st.radio(
        "History window",
        options=[7, 30, 90],
        index=1,
        horizontal=True,
        format_func=lambda d: f"{d} days",
        key="tcs_hist_days",
    )
    _bw_tcs_hist   = _cached_load_tcs_threshold_history(days=_bw_hist_days)
    _bw_cur_thresh = _cached_load_tcs_thresholds()
    _bw_hist_90    = _cached_load_tcs_threshold_history(days=90)

    # ── Pre-compute stability map for use in the main table ───────────────────
    _bw_stability_map: dict = {}
    _bw_now_stab = datetime.utcnow()
    for _bw_sk, _bw_sv in _bw_cur_thresh.items():
        _bw_last_chg = None
        for _bw_rec in reversed(_bw_hist_90):
            _bw_rp = _bw_rec.get("previous", {})
            _bw_rn = _bw_rec.get("thresholds", {})
            _bw_ov = _bw_rp.get(_bw_sk)
            _bw_nv = _bw_rn.get(_bw_sk)
            if _bw_ov is not None and _bw_nv is not None:
                try:
                    if int(_bw_ov) != int(_bw_nv):
                        _bw_ts_s = _bw_rec.get("timestamp", "")
                        _bw_last_chg = datetime.fromisoformat(
                            _bw_ts_s.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        break
                except (TypeError, ValueError):
                    pass
        if _bw_last_chg is not None:
            _bw_stability_map[_bw_sk] = f"{(_bw_now_stab - _bw_last_chg).days}d"
        else:
            _bw_stability_map[_bw_sk] = "≥ 90d"

    _bw_expander_label = (
        f"📈 TCS Threshold Shift History — last {len(_bw_tcs_hist)} recalibrations ({_bw_hist_days} days)"
        if _bw_tcs_hist
        else "📈 TCS Threshold Stability"
    )
    if _bw_cur_thresh or _bw_tcs_hist:
        with st.expander(_bw_expander_label, expanded=False):

            # ── Stability summary ───────────────────────────────────────────────
            _bw_now       = datetime.utcnow()
            _bw_stab_rows = []
            for _bw_sk, _bw_sv in sorted(_bw_cur_thresh.items()):
                _bw_last_change = None
                for _bw_rec in reversed(_bw_hist_90):
                    _bw_rec_prev = _bw_rec.get("previous", {})
                    _bw_rec_new  = _bw_rec.get("thresholds", {})
                    _bw_ov = _bw_rec_prev.get(_bw_sk)
                    _bw_nv = _bw_rec_new.get(_bw_sk)
                    if _bw_ov is not None and _bw_nv is not None:
                        try:
                            if int(_bw_ov) != int(_bw_nv):
                                _bw_ts_str = _bw_rec.get("timestamp", "")
                                _bw_last_change = datetime.fromisoformat(_bw_ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                                break
                        except (TypeError, ValueError):
                            pass
                if _bw_last_change is not None:
                    _bw_days_stable = (_bw_now - _bw_last_change).days
                    _bw_stable_str  = f"{_bw_days_stable}d"
                else:
                    _bw_stable_str = "≥ 90d"
                _bw_stab_rows.append({
                    "Structure":         WK_DISPLAY.get(_bw_sk, _bw_sk),
                    "Current Threshold": int(_bw_sv),
                    "Stable For":        _bw_stable_str,
                })
            if _bw_stab_rows:
                st.markdown("**Current Threshold Stability**")

                def _bw_color_stable(val):
                    try:
                        _bw_d = int(str(val).replace("≥ ", "").replace("d", ""))
                        if _bw_d >= 30:   return "color:#66bb6a;font-weight:700"
                        if _bw_d >= 7:    return "color:#ffa726"
                        return "color:#ef5350"
                    except Exception:
                        return "color:#66bb6a;font-weight:700"

                st.dataframe(
                    pd.DataFrame(_bw_stab_rows).style.map(_bw_color_stable, subset=["Stable For"]),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("---")

            if _bw_tcs_hist:
                st.caption("Each row = one nightly recalibration where at least one structure's threshold moved by ≥ 3 pts.")
            _bw_hist_rows = []
            for _bhr in _bw_tcs_hist:
                _bh_ts    = _bhr.get("timestamp", "")[:16].replace("T", " ")
                _bh_old   = _bhr.get("previous", {})
                _bh_new   = _bhr.get("thresholds", {})
                _bh_changes = []
                for _bh_k, _bh_nv in _bh_new.items():
                    _bh_ov = _bh_old.get(_bh_k)
                    if _bh_ov is None:
                        continue
                    try:
                        _bh_d = int(_bh_nv) - int(_bh_ov)
                    except (TypeError, ValueError):
                        continue
                    if abs(_bh_d) >= 3:
                        _bh_changes.append({
                            "Date (UTC)": _bh_ts,
                            "Structure":  WK_DISPLAY.get(_bh_k, _bh_k),
                            "Before":     int(_bh_ov),
                            "After":      int(_bh_nv),
                            "Δ":          f"{'+'if _bh_d>0 else ''}{_bh_d}",
                        })
                _bw_hist_rows.extend(_bh_changes)
            if _bw_hist_rows:
                _bw_hist_df = pd.DataFrame(_bw_hist_rows[::-1])

                def _bw_color_delta(val):
                    try:
                        v = int(str(val).replace("+", ""))
                        if v > 0:  return "color:#ef5350;font-weight:700"
                        if v < 0:  return "color:#66bb6a;font-weight:700"
                    except Exception:
                        pass
                    return ""

                st.dataframe(
                    _bw_hist_df.style.map(_bw_color_delta, subset=["Δ"]),
                    use_container_width=True, hide_index=True,
                )

                # ── Line chart: threshold value over time per structure ──────
                _chart_rows = []
                for _row in _bw_hist_rows:
                    _chart_rows.append({
                        "date":      _row["Date (UTC)"][:10],
                        "structure": _row["Structure"],
                        "threshold": _row["After"],
                    })
                _chart_df = pd.DataFrame(_chart_rows)
                if not _chart_df.empty:
                    _chart_df["date"] = pd.to_datetime(_chart_df["date"])
                    _chart_pivot = _chart_df.pivot_table(
                        index="date", columns="structure", values="threshold", aggfunc="last"
                    )
                    _chart_pivot = _chart_pivot.sort_index()
                    _chart_pivot.columns.name = None
                    _chart_pivot.index.name = "Date"
                    _plottable = [c for c in _chart_pivot.columns if _chart_pivot[c].notna().sum() >= 2]
                    if _plottable:
                        st.caption("Threshold drift over time — structures with ≥ 2 changes shown · dotted line = current live threshold")
                        _live_thresh_by_name = {
                            WK_DISPLAY.get(_k, _k): int(_v)
                            for _k, _v in _bw_cur_thresh.items()
                            if isinstance(_v, (int, float))
                        }
                        _drift_palette = [
                            "#42a5f5", "#66bb6a", "#ffa726", "#ef5350",
                            "#ab47bc", "#26c6da", "#d4e157", "#ff7043",
                        ]
                        _drift_fig = go.Figure()
                        for _ci, _col in enumerate(_plottable):
                            _drift_color = _drift_palette[_ci % len(_drift_palette)]
                            _drift_series = _chart_pivot[_col].dropna()
                            _drift_fig.add_trace(go.Scatter(
                                x=_drift_series.index,
                                y=_drift_series.values,
                                mode="lines+markers",
                                name=_col,
                                line=dict(color=_drift_color, width=2),
                                marker=dict(size=5),
                            ))
                            _drift_live = _live_thresh_by_name.get(_col)
                            if _drift_live is not None:
                                _drift_fig.add_hline(
                                    y=_drift_live,
                                    line=dict(color=_drift_color, dash="dot", width=1.5),
                                    annotation_text=f"{_col}: {_drift_live}",
                                    annotation_position="right",
                                    annotation_font=dict(color=_drift_color, size=10),
                                )
                        _drift_fig.update_layout(
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="#0e1117",
                            font=dict(color="#cfd8dc"),
                            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#cfd8dc")),
                            margin=dict(l=0, r=130, t=10, b=0),
                            xaxis=dict(gridcolor="#1e2a3a"),
                            yaxis=dict(gridcolor="#1e2a3a", title="Threshold"),
                            height=350,
                        )
                        st.plotly_chart(_drift_fig, use_container_width=True)
            else:
                st.info(f"No threshold shifts ≥ 3 pts recorded in the last {_bw_hist_days} days.")

            # ── Per-structure sparklines (90-day threshold history) ──────────
            _spark_series: dict = {}
            _sp_today = _bw_now.strftime("%Y-%m-%d")
            for _sp_k, _sp_cur_val in _bw_cur_thresh.items():
                _sp_pts: list = []
                for _sp_rec in _bw_hist_90:
                    _sp_ts = _sp_rec.get("timestamp", "")[:10]
                    _sp_thresh = _sp_rec.get("thresholds", {}).get(_sp_k)
                    if _sp_thresh is not None and _sp_ts:
                        try:
                            _sp_pts.append((_sp_ts, int(_sp_thresh)))
                        except (TypeError, ValueError):
                            pass
                # Append today's current value
                if _sp_cur_val is not None:
                    try:
                        _sp_pts.append((_sp_today, int(_sp_cur_val)))
                    except (TypeError, ValueError):
                        pass
                # Deduplicate (keep last value per date) and sort chronologically
                _sp_by_date: dict = {}
                for _sp_d, _sp_v in _sp_pts:
                    _sp_by_date[_sp_d] = _sp_v
                _sp_sorted = sorted(_sp_by_date.items())
                if _sp_sorted:
                    _spark_series[_sp_k] = _sp_sorted

            if _spark_series:
                import plotly.graph_objects as _go
                st.markdown("---")
                st.markdown("**Per-Structure Threshold Sparklines — last 90 days**")
                _sp_all_keys = sorted(_spark_series.keys())
                _sp_n_cols = 3
                for _sp_row_start in range(0, len(_sp_all_keys), _sp_n_cols):
                    _sp_row_keys = _sp_all_keys[_sp_row_start: _sp_row_start + _sp_n_cols]
                    _sp_cols = st.columns(_sp_n_cols)
                    for _sp_col_i, _sp_k in enumerate(_sp_row_keys):
                        _sp_pts = _spark_series[_sp_k]
                        _sp_dates = [p[0] for p in _sp_pts]
                        _sp_vals  = [p[1] for p in _sp_pts]
                        _sp_label = WK_DISPLAY.get(_sp_k, _sp_k)
                        _sp_cur_v = _sp_vals[-1] if _sp_vals else None
                        _sp_min_v = min(_sp_vals) if _sp_vals else 0
                        _sp_max_v = max(_sp_vals) if _sp_vals else 100
                        # Colour by direction: trending up = red (stricter), down = green (looser), flat = blue
                        if len(_sp_vals) >= 2 and _sp_vals[-1] > _sp_vals[0]:
                            _sp_line_color = "#ef5350"
                            _sp_fill_color = "rgba(239,83,80,0.07)"
                        elif len(_sp_vals) >= 2 and _sp_vals[-1] < _sp_vals[0]:
                            _sp_line_color = "#66bb6a"
                            _sp_fill_color = "rgba(102,187,106,0.07)"
                        else:
                            _sp_line_color = "#42a5f5"
                            _sp_fill_color = "rgba(66,165,245,0.07)"
                        _sp_fig = _go.Figure()
                        _sp_fig.add_trace(_go.Scatter(
                            x=_sp_dates,
                            y=_sp_vals,
                            mode="lines+markers",
                            line=dict(color=_sp_line_color, width=2),
                            marker=dict(size=4, color=_sp_line_color),
                            fill="tozeroy",
                            fillcolor=_sp_fill_color,
                            showlegend=False,
                            hovertemplate="%{x}: <b>%{y}</b><extra></extra>",
                        ))
                        _sp_y_pad = max(2, (_sp_max_v - _sp_min_v) * 0.2) if _sp_max_v != _sp_min_v else 5
                        _sp_fig.update_layout(
                            title=dict(
                                text=f"{_sp_label} <span style='color:#90a4ae;font-size:11px'>({_sp_cur_v})</span>",
                                font=dict(size=11),
                                x=0,
                                xref="paper",
                            ),
                            height=130,
                            margin=dict(l=4, r=4, t=30, b=4),
                            xaxis=dict(visible=False),
                            yaxis=dict(
                                visible=True,
                                tickfont=dict(size=8),
                                tickformat="d",
                                range=[max(0, _sp_min_v - _sp_y_pad), _sp_max_v + _sp_y_pad],
                                gridcolor="rgba(128,128,128,0.1)",
                            ),
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                        )
                        with _sp_cols[_sp_col_i]:
                            st.plotly_chart(
                                _sp_fig,
                                use_container_width=True,
                                config={"displayModeBar": False},
                            )

    _bw_rows = []
    for _k, _v in _bw.items():
        if isinstance(_v, (int, float)):
            _delta = round(_v - 1.0, 4)
            _bw_rows.append({
                "Structure":  _k,
                "Weight":     round(_v, 4),
                "Δ Baseline": f"{'+'if _delta >= 0 else ''}{_delta:.4f}",
                "Signal":     "▲ Learning" if _delta > 0.02 else ("▼ Suppressed" if _delta < -0.02 else "— Flat"),
                "Stable For": _bw_stability_map.get(_k, "—"),
            })

    if _bw_rows:
        _bw_display = pd.DataFrame(_bw_rows)

        def _color_delta(val):
            try:
                v = float(str(val).replace("+", ""))
                if v > 0.02:   return "color: #66bb6a; font-weight:700"
                if v < -0.02:  return "color: #ef5350"
            except Exception:
                pass
            return "color: #90a4ae"

        def _color_stable(val):
            try:
                _sd = int(str(val).replace("≥", "").replace("d", "").strip())
                if _sd >= 30:  return "color: #66bb6a; font-weight:700"
                if _sd >= 7:   return "color: #ffa726; font-weight:700"
                return "color: #ef5350; font-weight:700"
            except Exception:
                if str(val).startswith("≥"):
                    return "color: #66bb6a; font-weight:700"
            return "color: #90a4ae"

        st.dataframe(
            _bw_display.style
                .map(_color_delta, subset=["Δ Baseline"])
                .map(_color_stable, subset=["Stable For"]),
            use_container_width=True, hide_index=True, height="stretch"
        )
    else:
        st.info("Brain weights not yet loaded.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 6 — DAILY SUMMARY TABLE
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("### 📅 Daily Breakdown")

    if not _pt_df.empty and "win_loss" in _pt_df.columns and "trade_date" in _pt_df.columns:
        _daily = (
            _pt_df[_pt_df["win_loss"].isin(["W", "L", "Win", "Loss"])]
            .groupby("trade_date")
            .agg(
                Trades=("win_loss", "count"),
                Wins=("win_loss", lambda x: x.isin(["W", "Win"]).sum()),
                Losses=("win_loss", lambda x: x.isin(["L", "Loss"]).sum()),
            )
            .assign(WinRate=lambda d: (d["Wins"].astype(float) / d["Trades"].astype(float) * 100).round(1))
            .sort_index(ascending=False)
            .reset_index()
            .rename(columns={"trade_date": "Date", "WinRate": "Win Rate %"})
        )

        if "follow_thru_pct" in _pt_df.columns:
            _pnl_by_day = (
                _pt_df[_pt_df["win_loss"].isin(["W", "L", "Win", "Loss"])].copy()
                .assign(_pnl=lambda d: d["win_loss"].map({"W": 1, "Win": 1, "L": -1, "Loss": -1}) *
                                       d["follow_thru_pct"].fillna(0).astype(float) / 100 * _sim_per_trade)
                .groupby("trade_date")["_pnl"].sum()
                .round(2)
                .reset_index()
                .rename(columns={"trade_date": "Date", "_pnl": "P&L ($500/trade)"})
            )
            _daily = _daily.merge(_pnl_by_day, on="Date", how="left")

        def _color_winrate(val):
            if isinstance(val, (int, float)):
                if val >= 65: return "color: #66bb6a; font-weight:700"
                if val >= 50: return "color: #ffa726"
                return "color: #ef5350"
            return ""

        st.dataframe(
            _daily.style.map(_color_winrate, subset=["Win Rate %"]),
            use_container_width=True, hide_index=True, height="stretch"
        )
    else:
        st.info("No daily breakdown available yet.")


def render_decision_log_tab():
    """Decision Log — track macro calls right vs wrong over time."""
    from backend import (
        ensure_decision_log_table, get_decisions,
        insert_decision, update_decision_outcome, seed_decisions_if_empty,
        delete_decision,
    )

    st.markdown(
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:2px; font-weight:700; margin-bottom:4px;">🧠 DECISION LOG</div>'
        '<div style="font-size:12px; color:#546e7a; margin-bottom:18px;">'
        'Track your macro calls — system design, market thesis, filters, sizing. '
        'Same discipline applied to trades, now applied to the builder.</div>',
        unsafe_allow_html=True,
    )

    if not supabase:
        st.warning("Supabase not connected. Decision Log unavailable.")
        return

    _dl_uid = _AUTH_USER_ID

    _dl_ok = ensure_decision_log_table()
    if not _dl_ok:
        st.error("Could not create decision_log table. Run `exec_sql` function in Supabase SQL Editor first.")
        return

    seed_decisions_if_empty(_dl_uid)

    _dl_rows = get_decisions(_dl_uid)

    _dl_confirmed = [r for r in _dl_rows if r.get("outcome") == "Confirmed"]
    _dl_refuted   = [r for r in _dl_rows if r.get("outcome") == "Refuted"]
    _dl_pending   = [r for r in _dl_rows if r.get("outcome") == "Pending"]
    _dl_partial   = [r for r in _dl_rows if r.get("outcome") == "Partial"]
    _dl_decided   = len(_dl_confirmed) + len(_dl_refuted)
    _dl_wr        = round(len(_dl_confirmed) / _dl_decided * 100, 1) if _dl_decided else 0.0

    _dl_c1, _dl_c2, _dl_c3, _dl_c4 = st.columns(4)

    def _dl_metric(col, label, value, color="#e0e0e0", sub=""):
        with col:
            st.markdown(
                f'<div style="background:#1a1a2e; border:1px solid #2a2a3e; border-radius:8px; '
                f'padding:14px 16px; text-align:center;">'
                f'<div style="font-size:11px; color:#78909c; text-transform:uppercase; '
                f'letter-spacing:1px; margin-bottom:4px;">{label}</div>'
                f'<div style="font-size:26px; font-weight:700; color:{color}; line-height:1.1;">{value}</div>'
                f'{"" if not sub else f"<div style=&quot;font-size:11px; color:#546e7a; margin-top:3px;&quot;>" + sub + "</div>"}'
                f'</div>',
                unsafe_allow_html=True,
            )

    _dl_metric(_dl_c1, "Total Decisions", len(_dl_rows), "#e0e0e0")
    _dl_metric(_dl_c2, "Confirmed ✅", len(_dl_confirmed), "#81c784")
    _dl_metric(_dl_c3, "Refuted ❌", len(_dl_refuted), "#ef9a9a")
    _dl_metric(_dl_c4, "Decision Win Rate", f"{_dl_wr:.0f}%",
               "#4fc3f7" if _dl_wr >= 70 else ("#ffd54f" if _dl_wr >= 50 else "#ef9a9a"),
               f"{_dl_decided} decided")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    _dl_fc1, _dl_fc2 = st.columns(2)
    with _dl_fc1:
        _dl_filter_cats = st.multiselect(
            "Filter by Category",
            options=["System Design", "Market Thesis", "Filter", "Sizing", "Timing", "Other"],
            key="dl_filter_cats",
            placeholder="All categories",
        )
    with _dl_fc2:
        _dl_filter_outcomes = st.multiselect(
            "Filter by Outcome",
            options=["Pending", "Confirmed", "Refuted", "Partial"],
            key="dl_filter_outcomes",
            placeholder="All outcomes",
        )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _dl_cat_colors = {
        "System Design": "#4fc3f7",
        "Market Thesis": "#ce93d8",
        "Filter":        "#80cbc4",
        "Sizing":        "#ffcc80",
        "Timing":        "#f48fb1",
        "Other":         "#90a4ae",
    }

    def _dl_cat_badge(cat: str) -> str:
        c = _dl_cat_colors.get(cat, "#90a4ae")
        return (
            f'<span style="background:{c}22; color:{c}; border:1px solid {c}55; '
            f'border-radius:4px; padding:1px 7px; font-size:10px; font-weight:700; '
            f'letter-spacing:0.5px;">{cat}</span>'
        )

    with st.expander("➕ Add New Decision", expanded=False):
        with st.form("dl_add_form", clear_on_submit=True):
            _dlf_c1, _dlf_c2 = st.columns([1, 2])
            with _dlf_c1:
                _dlf_date = st.date_input("Date", value=date.today(), key="dl_date")
                _dlf_cat  = st.selectbox(
                    "Category",
                    ["System Design", "Market Thesis", "Filter", "Sizing", "Timing", "Other"],
                    key="dl_cat",
                )
            with _dlf_c2:
                _dlf_call = st.text_area("The Call", placeholder="What are you predicting or deciding?", height=80, key="dl_call")
                _dlf_reason = st.text_area("Reasoning (optional)", placeholder="Why? What's the evidence?", height=80, key="dl_reason")
            _dlf_submit = st.form_submit_button("Log Decision", type="primary")
            if _dlf_submit:
                if not _dlf_call.strip():
                    st.error("The Call field is required.")
                else:
                    _ok = insert_decision(_dl_uid, _dlf_date, _dlf_cat, _dlf_call, _dlf_reason)
                    if _ok:
                        st.success("Decision logged.")
                        st.rerun()
                    else:
                        st.error("Failed to save. Check Supabase connection.")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if not _dl_rows:
        st.info("No decisions logged yet. Add your first call above.")
        return

    _dl_display = list(_dl_rows)
    if _dl_filter_cats:
        _dl_display = [r for r in _dl_display if r.get("category", "Other") in _dl_filter_cats]
    if _dl_filter_outcomes:
        _dl_display = [r for r in _dl_display if r.get("outcome", "Pending") in _dl_filter_outcomes]

    if not _dl_display:
        st.info("No decisions match the current filters.")
        return

    _dl_outcome_border = {
        "Confirmed": "#81c784",
        "Refuted":   "#ef9a9a",
        "Partial":   "#ffd54f",
        "Pending":   "#555",
    }
    _dl_outcome_badge = {
        "Confirmed": '<span style="color:#81c784; font-weight:700;">✅ Confirmed</span>',
        "Refuted":   '<span style="color:#ef9a9a; font-weight:700;">❌ Refuted</span>',
        "Partial":   '<span style="color:#ffd54f; font-weight:700;">⚡ Partial</span>',
        "Pending":   '<span style="color:#78909c; font-weight:700;">⏳ Pending</span>',
    }

    for _dlr in _dl_display:
        _outcome   = _dlr.get("outcome", "Pending")
        _border_c  = _dl_outcome_border.get(_outcome, "#555")
        _out_badge = _dl_outcome_badge.get(_outcome, "⏳ Pending")
        _cat       = _dlr.get("category", "Other")
        _call_txt  = _dlr.get("call", "")
        _reason    = _dlr.get("reasoning") or ""
        _out_date  = _dlr.get("outcome_date") or ""
        _out_notes = _dlr.get("outcome_notes") or ""
        _dec_date  = _dlr.get("decision_date", "")
        _dec_id    = _dlr.get("id", "")

        _header_html = (
            f'<div style="border-left:3px solid {_border_c}; background:#12121e; '
            f'border-radius:0 8px 8px 0; padding:12px 16px; margin-bottom:4px;">'
            f'<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">'
            f'<span style="font-size:11px; color:#546e7a;">{_dec_date}</span>'
            f'{_dl_cat_badge(_cat)}'
            f'{_out_badge}'
            f'{"" if not _out_date else f"<span style=&quot;font-size:11px; color:#546e7a;&quot;>→ {_out_date}</span>"}'
            f'</div>'
            f'<div style="font-size:14px; color:#e0e0e0; font-weight:600; line-height:1.4; margin-bottom:4px;">'
            f'{_call_txt}</div>'
        )
        if _reason:
            _header_html += (
                f'<div style="font-size:12px; color:#78909c; margin-top:4px;">'
                f'<em>{_reason}</em></div>'
            )
        if _out_notes:
            _header_html += (
                f'<div style="font-size:12px; color:#b0bec5; margin-top:6px; '
                f'background:#0d1117; border-radius:4px; padding:6px 10px;">'
                f'{_out_notes}</div>'
            )
        _header_html += '</div>'
        st.markdown(_header_html, unsafe_allow_html=True)

        if _outcome == "Pending" and _dec_id:
            with st.expander("Mark outcome", expanded=False):
                _oc1, _oc2, _oc3 = st.columns([1, 1, 2])
                with _oc1:
                    _new_oc = st.selectbox(
                        "Outcome",
                        ["Confirmed", "Refuted", "Partial"],
                        key=f"dl_oc_{_dec_id}",
                    )
                with _oc2:
                    _new_od = st.date_input("Outcome Date", value=date.today(), key=f"dl_od_{_dec_id}")
                with _oc3:
                    _new_on = st.text_input("Notes", placeholder="What actually happened?", key=f"dl_on_{_dec_id}")
                if st.button("Save Outcome", key=f"dl_save_{_dec_id}", type="primary"):
                    _upd_ok = update_decision_outcome(_dec_id, _dl_uid, _new_oc, _new_od, _new_on)
                    if _upd_ok:
                        st.success(f"Marked as {_new_oc}.")
                        st.rerun()
                    else:
                        st.error("Update failed.")

        if _dec_id:
            _confirm_key = f"dl_del_confirm_{_dec_id}"
            if not st.session_state.get(_confirm_key, False):
                if st.button("🗑 Delete", key=f"dl_del_{_dec_id}"):
                    st.session_state[_confirm_key] = True
                    st.rerun()
            else:
                st.warning("Are you sure you want to delete this decision? This cannot be undone.")
                _conf_c1, _conf_c2 = st.columns([1, 5])
                with _conf_c1:
                    if st.button("Yes, delete", key=f"dl_del_yes_{_dec_id}", type="primary"):
                        _del_ok = delete_decision(_dec_id, _dl_uid)
                        st.session_state[_confirm_key] = False
                        if _del_ok:
                            st.rerun()
                        else:
                            st.error("Delete failed. Check Supabase connection.")
                with _conf_c2:
                    if st.button("Cancel", key=f"dl_del_cancel_{_dec_id}"):
                        st.session_state[_confirm_key] = False
                        st.rerun()

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)


def render_paper_trade_tab(api_key: str = "", secret_key: str = ""):
    st.markdown(
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:2px; font-weight:700; margin-bottom:4px;">📄 AUTO PAPER TRADING</div>'
        '<div style="font-size:12px; color:#546e7a; margin-bottom:18px;">'
        'Run the IB engine on any date with a TCS ≥ filter. Results log automatically '
        'so you build 3 weeks of calibrated paper data without touching Alpaca again.</div>',
        unsafe_allow_html=True,
    )

    _pt_ready = ensure_paper_trades_table()
    if not _pt_ready:
        st.warning(
            "The paper_trades table doesn't exist yet in your Supabase database. "
            "Run the SQL below in your Supabase SQL editor, then reload the page.",
            icon="⚠️",
        )
        st.code(
            "CREATE TABLE IF NOT EXISTS paper_trades (\n"
            "  id SERIAL PRIMARY KEY,\n"
            "  user_id TEXT, trade_date DATE, ticker TEXT, tcs FLOAT,\n"
            "  predicted TEXT, ib_low FLOAT, ib_high FLOAT, open_price FLOAT,\n"
            "  actual_outcome TEXT, follow_thru_pct FLOAT, win_loss TEXT,\n"
            "  false_break_up BOOLEAN DEFAULT FALSE,\n"
            "  false_break_down BOOLEAN DEFAULT FALSE,\n"
            "  min_tcs_filter INT DEFAULT 50,\n"
            "  created_at TIMESTAMPTZ DEFAULT NOW()\n"
            ");",
            language="sql",
        )
        return

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 2 GATE TRACKER — Live Money Flip (target: ~May 6)
    # Gate: 30 settled trades · 60% WR · 30 days elapsed
    # ════════════════════════════════════════════════════════════════════════
    try:
        _gate_rows = []
        if supabase and _AUTH_USER_ID:
            _gate_resp = (
                supabase.table("paper_trades")
                .select("trade_date,win_loss")
                .eq("user_id", _AUTH_USER_ID)
                .in_("win_loss", ["Win", "Loss"])
                .execute()
            )
            _gate_rows = _gate_resp.data or []

        _gate_settled  = len(_gate_rows)
        _gate_wins     = sum(1 for r in _gate_rows if r.get("win_loss") == "Win")
        _gate_wr       = round(_gate_wins / _gate_settled * 100, 1) if _gate_settled else 0.0

        _gate_dates    = sorted(r["trade_date"] for r in _gate_rows if r.get("trade_date"))
        _gate_first    = _gate_dates[0] if _gate_dates else None
        if _gate_first:
            from datetime import date as _dt_date
            _gate_start   = _dt_date.fromisoformat(_gate_first)
            _gate_elapsed = (_dt_date.today() - _gate_start).days
        else:
            _gate_elapsed = 0

        _GATE_TRADES  = 30
        _GATE_WR      = 60.0
        _GATE_DAYS    = 30

        _g_t_ok  = _gate_settled >= _GATE_TRADES
        _g_wr_ok = _gate_wr      >= _GATE_WR
        _g_d_ok  = _gate_elapsed >= _GATE_DAYS
        _gate_all_clear = _g_t_ok and _g_wr_ok and _g_d_ok
        _gate_close     = sum([_g_t_ok, _g_wr_ok, _g_d_ok]) == 2

        if _gate_all_clear:
            _gate_border = "#2e7d32"; _gate_bg = "#0a1f0a"
            _gate_status = "🟢 ALL CLEAR — READY TO FLIP TO LIVE"
            _gate_status_color = "#66bb6a"
        elif _gate_close:
            _gate_border = "#f9a825"; _gate_bg = "#1a1500"
            _gate_status = "🟡 ALMOST — 2 of 3 GATES PASSED"
            _gate_status_color = "#ffee58"
        else:
            _gate_border = "#b71c1c"; _gate_bg = "#1a0000"
            _gate_status = "🔴 IN PROGRESS — building the track record"
            _gate_status_color = "#ef9a9a"

        def _gate_bar(val, mx, ok):
            pct  = min(100, round(val / mx * 100))
            col  = "#2e7d32" if ok else ("#ef6c00" if pct >= 70 else "#c62828")
            return (
                f'<div style="height:6px;background:#1e2a3a;border-radius:3px;margin-top:4px;">'
                f'<div style="width:{pct}%;height:6px;background:{col};border-radius:3px;"></div>'
                f'</div>'
            )

        _g_t_c  = "#66bb6a" if _g_t_ok  else "#ef9a9a"
        _g_wr_c = "#66bb6a" if _g_wr_ok else "#ef9a9a"
        _g_d_c  = "#66bb6a" if _g_d_ok  else "#ef9a9a"

        st.markdown(
            f'<div style="background:{_gate_bg};border:1px solid {_gate_border};'
            f'border-radius:10px;padding:14px 18px;margin-bottom:14px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
            f'<div style="font-size:11px;color:#90a4ae;letter-spacing:1.5px;'
            f'text-transform:uppercase;font-weight:700;">⚡ Phase 2 Gate — Live Money Flip</div>'
            f'<div style="font-size:12px;font-weight:700;color:{_gate_status_color};">{_gate_status}</div>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">'

            f'<div>'
            f'<div style="font-size:11px;color:#90a4ae;text-transform:uppercase;letter-spacing:1px;">Settled Trades</div>'
            f'<div style="font-size:22px;font-weight:700;color:{_g_t_c};">{_gate_settled}'
            f'<span style="font-size:13px;color:#546e7a;"> / {_GATE_TRADES}</span></div>'
            f'{_gate_bar(_gate_settled, _GATE_TRADES, _g_t_ok)}'
            f'<div style="font-size:10px;color:#546e7a;margin-top:3px;">'
            f'{"✓ Gate cleared" if _g_t_ok else f"{_GATE_TRADES - _gate_settled} more needed"}</div>'
            f'</div>'

            f'<div>'
            f'<div style="font-size:11px;color:#90a4ae;text-transform:uppercase;letter-spacing:1px;">Win Rate</div>'
            f'<div style="font-size:22px;font-weight:700;color:{_g_wr_c};">{_gate_wr:.1f}%'
            f'<span style="font-size:13px;color:#546e7a;"> / {_GATE_WR:.0f}%</span></div>'
            f'{_gate_bar(_gate_wr, _GATE_WR, _g_wr_ok)}'
            f'<div style="font-size:10px;color:#546e7a;margin-top:3px;">'
            f'{"✓ Gate cleared" if _g_wr_ok else f"{_gate_wins}W / {_gate_settled - _gate_wins}L"}</div>'
            f'</div>'

            f'<div>'
            f'<div style="font-size:11px;color:#90a4ae;text-transform:uppercase;letter-spacing:1px;">Days Elapsed</div>'
            f'<div style="font-size:22px;font-weight:700;color:{_g_d_c};">{_gate_elapsed}'
            f'<span style="font-size:13px;color:#546e7a;"> / {_GATE_DAYS}</span></div>'
            f'{_gate_bar(_gate_elapsed, _GATE_DAYS, _g_d_ok)}'
            f'<div style="font-size:10px;color:#546e7a;margin-top:3px;">'
            f'{"✓ Gate cleared" if _g_d_ok else (f"since {_gate_first}" if _gate_first else "no trades yet")}</div>'
            f'</div>'

            f'</div></div>',
            unsafe_allow_html=True,
        )
    except Exception as _gate_err:
        st.caption(f"Gate tracker unavailable: {_gate_err}")

    # ── Live Auto-Scan mode — LOCKED ────────────────────────────────────────
    _pt_live_on = False
    st.session_state["_pt_live_mode"] = False
    st.markdown(
        '<div style="background:#1a0a00; border:1px solid #e65100; border-radius:8px; '
        'padding:12px 16px; margin-bottom:12px;">'
        '<span style="font-size:13px; font-weight:700; color:#ff6d00;">🔒 Live Auto-Scan — Disabled</span><br>'
        '<span style="font-size:12px; color:#bf360c; line-height:1.6;">'
        'The standalone bot already scans your 45-ticker watchlist automatically at <b>10:46 AM ET</b> every trading day — '
        'no browser needs to be open. Turning this on would create duplicate paper trade entries on top of what the bot already logs. '
        'If you ever need to trigger a manual scan outside the bot schedule, ask in <b>Replit chat first</b>.'
        '</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    _now_et_pt = datetime.now(EASTERN)
    _pt_mkt_open  = _now_et_pt.replace(hour=9,  minute=30, second=0, microsecond=0)
    _pt_mkt_close = _now_et_pt.replace(hour=16, minute=0,  second=0, microsecond=0)
    _pt_in_market = (
        _pt_mkt_open <= _now_et_pt <= _pt_mkt_close
        and _now_et_pt.weekday() < 5
    )


    st.markdown("---")

    # ── Section 1: Scan & Log ────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; font-weight:700; margin:12px 0 8px 0;">'
        '🔍 SECTION 1 — SCAN & LOG PAPER TRADES</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Pick a date, paste your tickers, set a TCS minimum. "
        "Hit Scan — the engine fetches bars, runs the full IB analysis, "
        "filters to TCS ≥ your minimum, and saves qualifying setups automatically. "
        "Best run after 10:30 AM ET so the full IB window is captured."
    )

    _pt_c1, _pt_c2, _pt_c3 = st.columns([1, 1, 1])
    with _pt_c1:
        _pt_date = st.date_input(
            "Trade Date",
            value=date.today(),
            key="pt_scan_date",
            help="Set to today for live paper trades, or any past date to backfill.",
        )
    with _pt_c2:
        _pt_feed = st.radio(
            "Bar Data Feed",
            ["SIP (paid — accurate)", "IEX (free — limited)"],
            key="pt_feed",
            horizontal=True,
        )
        _pt_feed_str = "sip" if "SIP" in _pt_feed else "iex"
    with _pt_c3:
        _pt_min_tcs = st.slider(
            "Min TCS Filter",
            min_value=0, max_value=80, value=50, step=5,
            key="pt_min_tcs",
            help="Only setups with TCS ≥ this value get logged. 50 is the recommended minimum.",
        )
        # Persist pt_min_tcs to user prefs when it changes
        if _AUTH_USER_ID:
            _pt_cached = st.session_state.get("_cached_prefs", {})
            if _pt_cached.get("pt_min_tcs") != _pt_min_tcs:
                _pt_new_prefs = {**_pt_cached, "pt_min_tcs": _pt_min_tcs}
                save_user_prefs(_AUTH_USER_ID, _pt_new_prefs)
                st.session_state["_cached_prefs"] = _pt_new_prefs

    _pt_price_range = st.slider(
        "Price Range ($)",
        min_value=0.5, max_value=30.0,
        value=(1.0, 20.0), step=0.5,
        key="pt_price_range",
    )
    # Persist pt_price_range to user prefs when it changes
    if _AUTH_USER_ID:
        _pt_pr_cached = st.session_state.get("_cached_prefs", {})
        _pt_pr_saved = _pt_pr_cached.get("pt_price_range")
        if _pt_pr_saved is None or tuple(_pt_pr_saved) != tuple(_pt_price_range):
            _pt_pr_new_prefs = {**_pt_pr_cached, "pt_price_range": list(_pt_price_range)}
            save_user_prefs(_AUTH_USER_ID, _pt_pr_new_prefs)
            st.session_state["_cached_prefs"] = _pt_pr_new_prefs

    # ── Auto-watchlist (Finviz) + optional extras ────────────────────────────
    _pt_auto_wl = _cached_load_watchlist(user_id=_AUTH_USER_ID) or []
    _pt_auto_str = ", ".join(_pt_auto_wl) if _pt_auto_wl else ""

    st.markdown(
        f'<div style="background:#0a1a0a; border:1px solid #2e7d32; border-radius:8px; '
        f'padding:12px 16px; margin-bottom:10px;">'
        f'<span style="font-size:13px; font-weight:700; color:#66bb6a;">🤖 Auto-Watchlist — {len(_pt_auto_wl)} tickers</span><br>'
        f'<span style="font-size:11px; color:#388e3c; line-height:1.6;">'
        f'Populated automatically from Finviz at <b>9:15 AM ET</b> every trading day '
        f'using your exact filter settings (% Change ≥ 3% · Float ≤ 100M · Vol ≥ 1M · US only).<br>'
        f'<span style="color:#81c784;">{_pt_auto_str[:120]}{"…" if len(_pt_auto_str) > 120 else ""}</span>'
        f'</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    _pt_extra_raw = st.text_input(
        "➕ Extra tickers to add (optional — leave blank to use auto-watchlist only)",
        value="",
        key="pt_extra_tickers",
        placeholder="e.g. RENX, PFSA, VRAX",
    )
    # Persist pt_extra_tickers to user prefs when it changes
    if _AUTH_USER_ID:
        _pt_et_cached = st.session_state.get("_cached_prefs", {})
        if _pt_et_cached.get("pt_extra_tickers") != _pt_extra_raw:
            _pt_et_new_prefs = {**_pt_et_cached, "pt_extra_tickers": _pt_extra_raw}
            save_user_prefs(_AUTH_USER_ID, _pt_et_new_prefs)
            st.session_state["_cached_prefs"] = _pt_et_new_prefs
    _pt_extra = [t.strip().upper() for t in _pt_extra_raw.split(",") if t.strip() and t.strip().isalpha()]
    _pt_combined = _pt_auto_wl + [t for t in _pt_extra if t not in _pt_auto_wl]
    _pt_tickers_raw = ", ".join(_pt_combined)
    _pt_default_tickers = _pt_tickers_raw

    _pt_scan_btn = st.button(
        "🔍 Scan & Log Paper Trades",
        use_container_width=True,
        key="pt_scan_btn",
        type="primary",
    )

    if _pt_scan_btn:
        if not api_key or not secret_key:
            st.error("Add your Alpaca credentials in the sidebar first.")
        else:
            _pt_tickers = [
                t.strip().upper()
                for t in _pt_tickers_raw.replace("\n", ",").split(",")
                if t.strip() and t.strip().isalpha()
            ]
            if not _pt_tickers:
                st.error("No valid tickers found.")
            else:
                _pt_pmin, _pt_pmax = float(_pt_price_range[0]), float(_pt_price_range[1])
                with st.spinner(
                    f"Fetching bars for {len(_pt_tickers)} tickers on {_pt_date} · "
                    f"filtering TCS ≥ {_pt_min_tcs}…"
                ):
                    _pt_results, _pt_summary = run_historical_backtest(
                        api_key, secret_key,
                        trade_date=_pt_date,
                        tickers=_pt_tickers,
                        feed=_pt_feed_str,
                        price_min=_pt_pmin,
                        price_max=_pt_pmax,
                        slippage_pct=0.75,
                    )
                if _pt_summary.get("error"):
                    st.error(_pt_summary["error"])
                elif not _pt_results:
                    st.warning("No setups found for that date / ticker list.")
                else:
                    _pt_qualified = [
                        dict(r, sim_date=str(_pt_date))
                        for r in _pt_results
                        if float(r.get("tcs", 0)) >= _pt_min_tcs
                    ]
                    _pt_total_scanned = len(_pt_results)
                    _sim_failed = []  # populated after logging; used by preview table below
                    if not _pt_qualified:
                        st.warning(
                            f"Scanned {_pt_total_scanned} setups — none passed TCS ≥ {_pt_min_tcs}. "
                            f"Try a lower TCS filter or add more tickers."
                        )
                    else:
                        _regime_tag_ui = (st.session_state.get("breadth_regime") or {}).get("regime_tag")
                        _pt_qualified_tagged = [
                            dict(r, regime_tag=_regime_tag_ui) if _regime_tag_ui else r
                            for r in _pt_qualified
                        ]
                        _pt_log_result = log_paper_trades(
                            _pt_qualified_tagged,
                            user_id=_AUTH_USER_ID,
                            min_tcs=_pt_min_tcs,
                        )
                        st.session_state["_pt_last_scan"] = _pt_qualified
                        if _pt_log_result.get("error"):
                            st.error(f"Save error: {_pt_log_result['error']}")
                        else:
                            _sv = _pt_log_result["saved"]
                            _sk = _pt_log_result["skipped"]
                            st.success(
                                f"✅ {_sv} paper trade(s) logged for {_pt_date} "
                                f"(TCS ≥ {_pt_min_tcs}) · "
                                f"{_pt_total_scanned - len(_pt_qualified)} below-TCS setups filtered out"
                                + (f" · {_sk} already logged (skipped)" if _sk else "")
                            )
                            _sim_rows = _pt_log_result.get("sim_rows") or []
                            _sim_failed = _pt_log_result.get("sim_failed") or []
                            if _sim_rows or _sim_failed:
                                _all_already_logged = bool(_sim_rows) and all(r.get("already_logged") for r in _sim_rows)
                                _expander_label = (
                                    "📈 Sim P&L (already logged — shown from existing records)"
                                    if _all_already_logged
                                    else "📈 Sim P&L for logged trades"
                                )
                                with st.expander(_expander_label, expanded=True):
                                    if _sim_failed:
                                        _failed_lines = ", ".join(
                                            f"**{f['ticker']}** ({f['reason']})" for f in _sim_failed
                                        )
                                        st.warning(
                                            f"⚠️ Sim data could not be computed for "
                                            f"{len(_sim_failed)} ticker(s) — excluded from the table below: "
                                            f"{_failed_lines}",
                                            icon=None,
                                        )
                                    if not _sim_rows:
                                        st.info("No sim P&L data available for these trades.")
                                    else:
                                        _sim_df = pd.DataFrame(_sim_rows)
                                        _sim_df = _sim_df.rename(columns={
                                            "ticker":           "Ticker",
                                            "sim_outcome":      "Outcome",
                                            "pnl_r_sim":        "P&L (R)",
                                            "entry_price_sim":  "Entry",
                                            "stop_price_sim":   "Stop",
                                            "target_price_sim": "Target",
                                        })
                                        _col_order = [c for c in ["Ticker", "Outcome", "P&L (R)", "Entry", "Stop", "Target"] if c in _sim_df.columns]

                                        def _sim_row_color(row):
                                            import math as _math
                                            pnl = row.get("P&L (R)")
                                            try:
                                                pnl_f = float(pnl)
                                                if _math.isnan(pnl_f):
                                                    pnl_f = None
                                            except (TypeError, ValueError):
                                                pnl_f = None
                                            if pnl_f is None or pnl_f == 0:
                                                bg = "background-color: rgba(158,158,158,0.12)"
                                                fg_key = "color: #9e9e9e; font-weight:600"
                                            elif pnl_f > 0:
                                                bg = "background-color: rgba(102,187,106,0.12)"
                                                fg_key = "color: #66bb6a; font-weight:600"
                                            else:
                                                bg = "background-color: rgba(239,83,80,0.12)"
                                                fg_key = "color: #ef5350; font-weight:600"
                                            return [
                                                fg_key if col in ("Outcome", "P&L (R)") else bg
                                                for col in row.index
                                            ]

                                        st.dataframe(
                                            _sim_df[_col_order].style
                                                .format(
                                                    {
                                                        "P&L (R)": lambda v: f"{v:+.2f}R" if pd.notna(v) else "—",
                                                        "Entry":   lambda v: f"${v:.2f}" if pd.notna(v) else "—",
                                                        "Stop":    lambda v: f"${v:.2f}" if pd.notna(v) else "—",
                                                        "Target":  lambda v: f"${v:.2f}" if pd.notna(v) else "—",
                                                        "Outcome": lambda v: v if v else "—",
                                                    }
                                                )
                                                .apply(_sim_row_color, axis=1),
                                            use_container_width=True,
                                            hide_index=True,
                                        )

                    _pt_preview = _pt_qualified or _pt_results
                    _pt_preview_df = pd.DataFrame(_pt_preview)[[
                        c for c in ["ticker", "tcs", "predicted", "actual_outcome",
                                    "win_loss", "aft_move_pct", "ib_low", "ib_high"]
                        if c in pd.DataFrame(_pt_preview).columns
                    ]].copy()
                    if not _pt_preview_df.empty and "aft_move_pct" in _pt_preview_df.columns:
                        _pt_preview_df = _pt_preview_df.rename(columns={"aft_move_pct": "follow_thru_%"})

                    # Mark rows whose ticker was excluded from the sim due to missing price data
                    _sim_failed_reasons = {f["ticker"]: f.get("reason", "unknown reason") for f in _sim_failed} if _sim_failed else {}
                    _sim_failed_tickers = set(_sim_failed_reasons.keys())
                    if _sim_failed_tickers and not _pt_preview_df.empty:
                        _pt_preview_df.insert(
                            1, "Sim",
                            _pt_preview_df["ticker"].apply(
                                lambda t: f"⚠ No sim — {_sim_failed_reasons[t]}" if t in _sim_failed_tickers else ""
                            ),
                        )

                    # Persist preview table and sim_failed in session_state so the table
                    # survives reruns (e.g. sidebar interactions) without re-scanning.
                    st.session_state["_pt_sim_failed"] = _sim_failed
                    st.session_state["_pt_preview_df"] = _pt_preview_df

    # Render the scan preview table from session_state so it persists across reruns
    # (sidebar interactions, widget changes, etc.) without requiring a re-scan.
    if "_pt_preview_df" in st.session_state and not st.session_state["_pt_preview_df"].empty:
        _ss_preview_df = st.session_state["_pt_preview_df"]
        if "Sim" in _ss_preview_df.columns:
            def _preview_row_style(row):
                if str(row.get("Sim", "")).startswith("⚠ No sim"):
                    return ["background-color: rgba(255,152,0,0.15)"] * len(row)
                return [""] * len(row)
            st.dataframe(
                _ss_preview_df.style.apply(_preview_row_style, axis=1)
                    .map(
                        lambda v: "color: #e65100; font-weight:700" if str(v).startswith("⚠ No sim") else "",
                        subset=["Sim"],
                    ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.dataframe(_ss_preview_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Section 2: 3-Week Performance Tracker ──────────────────────────────
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; font-weight:700; margin:12px 0 8px 0;">'
        '📊 SECTION 2 — 3-WEEK PAPER TRADE TRACKER</div>',
        unsafe_allow_html=True,
    )

    _pt_reload = st.button("🔄 Refresh Tracker", key="pt_reload_btn")
    if _pt_reload or "pt_tracker_df" not in st.session_state:
        _pt_df = _cached_load_paper_trades(user_id=_AUTH_USER_ID, days=21)
        st.session_state["pt_tracker_df"] = _pt_df
    else:
        _pt_df = st.session_state.get("pt_tracker_df", pd.DataFrame())

    if _pt_df.empty:
        st.info(
            "No paper trades logged yet. Run Section 1 on a few days to build your history.",
            icon="📋",
        )
        return

    _pt_wins   = (_pt_df["win_loss"] == "Win").sum()
    _pt_losses = (_pt_df["win_loss"] == "Loss").sum()
    _pt_total  = len(_pt_df)
    _pt_wr     = round(_pt_wins / _pt_total * 100, 1) if _pt_total > 0 else 0
    _pt_dates  = _pt_df["trade_date"].nunique() if "trade_date" in _pt_df.columns else 0
    _pt_avg_ft = round(_pt_df["follow_thru_pct"].mean(), 1) if "follow_thru_pct" in _pt_df.columns else 0
    _pt_avg_tcs = round(_pt_df["tcs"].mean(), 0) if "tcs" in _pt_df.columns else 0
    _pt_wr_clr = "#4caf50" if _pt_wr >= 60 else "#ff9800" if _pt_wr >= 50 else "#ef5350"

    _pt_kpi_html = (
        f'<div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:20px;">'
        f'<div style="background:#0a1929; border:1px solid #1565c055; border-radius:10px; '
        f'padding:14px 22px; text-align:center; min-width:120px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Win Rate</div>'
        f'<div style="font-size:30px; font-weight:900; color:{_pt_wr_clr}; font-family:monospace;">{_pt_wr}%</div>'
        f'<div style="font-size:10px; color:#37474f;">{_pt_wins}W / {_pt_losses}L</div></div>'
        f'<div style="background:#0a1929; border:1px solid #1565c055; border-radius:10px; '
        f'padding:14px 22px; text-align:center; min-width:120px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Total Setups</div>'
        f'<div style="font-size:30px; font-weight:900; color:#e0e0e0; font-family:monospace;">{_pt_total}</div>'
        f'<div style="font-size:10px; color:#37474f;">across {_pt_dates} day(s)</div></div>'
        f'<div style="background:#0a1929; border:1px solid #1565c055; border-radius:10px; '
        f'padding:14px 22px; text-align:center; min-width:120px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Avg TCS</div>'
        f'<div style="font-size:30px; font-weight:900; color:#ce93d8; font-family:monospace;">{int(_pt_avg_tcs)}</div>'
        f'<div style="font-size:10px; color:#37474f;">min filter applied</div></div>'
        f'<div style="background:#0a1929; border:1px solid #1565c055; border-radius:10px; '
        f'padding:14px 22px; text-align:center; min-width:120px;">'
        f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Avg Follow-Thru</div>'
        f'<div style="font-size:30px; font-weight:900; color:{"#4caf50" if _pt_avg_ft >= 0 else "#ef5350"}; font-family:monospace;">'
        f'{("+" if _pt_avg_ft >= 0 else "")}{_pt_avg_ft}%</div>'
        f'<div style="font-size:10px; color:#37474f;">best post-IB point</div></div>'
        f'</div>'
    )
    st.markdown(_pt_kpi_html, unsafe_allow_html=True)

    # Daily win rate trend
    if "trade_date" in _pt_df.columns and "win_loss" in _pt_df.columns:
        _pt_daily = (
            _pt_df.groupby("trade_date")
            .apply(lambda g: pd.Series({
                "Win %":   round(g["win_loss"].isin(["Win", "W"]).sum() / len(g) * 100, 1),
                "Setups":  len(g),
                "Avg TCS": round(g["tcs"].mean(), 0) if "tcs" in g.columns else 0,
            }))
            .reset_index()
            .sort_values("trade_date")
        )
        import plotly.graph_objects as _pt_go
        _pt_fig = _pt_go.Figure()
        _pt_fig.add_trace(_pt_go.Scatter(
            x=_pt_daily["trade_date"].astype(str),
            y=_pt_daily["Win %"],
            mode="lines+markers+text",
            text=_pt_daily["Win %"].apply(lambda v: f"{v:.0f}%"),
            textposition="top center",
            line=dict(color="#1565c0", width=2),
            marker=dict(size=8, color="#1565c0"),
            name="Daily Win %",
        ))
        _pt_fig.add_hline(
            y=55, line_dash="dot", line_color="#ff9800",
            annotation_text="55% target", annotation_font_color="#ff9800",
        )
        _pt_fig.update_layout(
            paper_bgcolor="#050d18", plot_bgcolor="#050d18",
            font=dict(color="#90a4ae", size=11),
            height=280, margin=dict(l=10, r=10, t=30, b=10),
            xaxis=dict(showgrid=False, title=""),
            yaxis=dict(gridcolor="#0d2137", range=[0, 105], title="Win %"),
            title=dict(text="Daily Win Rate (Paper Trades)", font=dict(color="#1565c0", size=12)),
            showlegend=False,
        )
        st.plotly_chart(_pt_fig, use_container_width=True)

    # Per-ticker summary
    st.markdown(
        '<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
        'letter-spacing:1px; margin:16px 0 8px 0; font-weight:700;">Per-Ticker Stats</div>',
        unsafe_allow_html=True,
    )
    _pt_tkr_rows = []
    for _ptk, _ptg in _pt_df.groupby("ticker"):
        _ptw = (_ptg["win_loss"] == "Win").sum()
        _ptl = (_ptg["win_loss"] == "Loss").sum()
        _ptwr = round(_ptw / len(_ptg) * 100, 1) if len(_ptg) > 0 else 0
        _pt_top_struct = (
            _ptg["predicted"].value_counts().index[0]
            if "predicted" in _ptg.columns and not _ptg["predicted"].empty
            else "—"
        )
        _pt_tkr_rows.append({
            "Ticker":        _ptk,
            "Setups":        len(_ptg),
            "W/L":           f"{_ptw}/{_ptl}",
            "Win %":         f"{'🟢' if _ptwr >= 60 else '🟡' if _ptwr >= 50 else '🔴'} {_ptwr}%",
            "Avg TCS":       round(_ptg["tcs"].mean(), 0) if "tcs" in _ptg.columns else 0,
            "Top Structure": _pt_top_struct,
            "Avg FT %":      round(_ptg["follow_thru_pct"].mean(), 1) if "follow_thru_pct" in _ptg.columns else 0,
            "Days Seen":     _ptg["trade_date"].nunique() if "trade_date" in _ptg.columns else 0,
        })
    _pt_tkr_df = pd.DataFrame(_pt_tkr_rows).sort_values("Win %", ascending=False)
    st.dataframe(_pt_tkr_df, use_container_width=True, hide_index=True)

    # ── EOD Hold vs Tiered Exit Strategy Comparison ──────────────────────────
    _ptt_has_eod    = "eod_pnl_r"    in _pt_df.columns
    _ptt_has_tiered = "tiered_pnl_r" in _pt_df.columns
    _ptt_eod_vals    = _pt_df["eod_pnl_r"].dropna().tolist()    if _ptt_has_eod    else []
    _ptt_tiered_vals = _pt_df["tiered_pnl_r"].dropna().tolist() if _ptt_has_tiered else []
    _ptt_avg_eod    = round(sum(_ptt_eod_vals)    / len(_ptt_eod_vals),    3) if _ptt_eod_vals    else None
    _ptt_avg_tiered = round(sum(_ptt_tiered_vals) / len(_ptt_tiered_vals), 3) if _ptt_tiered_vals else None

    if _ptt_has_eod and _ptt_has_tiered:
        _ptt_paired = _pt_df[_pt_df["eod_pnl_r"].notna() & _pt_df["tiered_pnl_r"].notna()]
    else:
        _ptt_paired = pd.DataFrame()
    _ptt_paired_n = len(_ptt_paired)

    if _ptt_avg_eod is not None or _ptt_avg_tiered is not None:
        def _ptt_r_str(val, n):
            if val is None:
                return "—"
            sign = "+" if val >= 0 else ""
            return f"{sign}{val:.3f}R ({n} trades)"

        def _ptt_r_color(val):
            if val is None:
                return "#546e7a"
            return "#4caf50" if val >= 0 else "#ef5350"

        _ptt_eod_str    = _ptt_r_str(_ptt_avg_eod,    len(_ptt_eod_vals))
        _ptt_tiered_str = _ptt_r_str(_ptt_avg_tiered, len(_ptt_tiered_vals))
        _ptt_eod_clr    = _ptt_r_color(_ptt_avg_eod)
        _ptt_tiered_clr = _ptt_r_color(_ptt_avg_tiered)

        if _ptt_paired_n > 0:
            _ptt_pair_eod    = _ptt_paired["eod_pnl_r"].astype(float).mean()
            _ptt_pair_tiered = _ptt_paired["tiered_pnl_r"].astype(float).mean()
            _ptt_diff        = _ptt_pair_tiered - _ptt_pair_eod
            _ptt_diff_sign   = "+" if _ptt_diff >= 0 else ""
            _ptt_paired_note = f"({_ptt_paired_n} matched trades)"
            if abs(_ptt_diff) < 0.001:
                _ptt_verdict     = f"Strategies tied on your paper trades {_ptt_paired_note}"
                _ptt_verdict_clr = "#90a4ae"
            elif _ptt_diff > 0:
                _ptt_verdict     = f"Tiered exits outperform EOD hold by {_ptt_diff_sign}{_ptt_diff:.3f}R per trade {_ptt_paired_note}"
                _ptt_verdict_clr = "#ffb74d"
            else:
                _ptt_verdict     = f"EOD hold outperforms tiered exits by {abs(_ptt_diff):.3f}R per trade {_ptt_paired_note}"
                _ptt_verdict_clr = "#81c784"
        else:
            _ptt_verdict     = "Both metrics needed for a comparison"
            _ptt_verdict_clr = "#546e7a"

        st.markdown(
            f'<div style="background:#020813; border:1px solid #1a2744; border-radius:8px; '
            f'padding:14px 24px; margin-top:14px; margin-bottom:6px;">'
            f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
            f'letter-spacing:1.5px; margin-bottom:10px; font-weight:700; font-family:monospace;">'
            f'📊 Strategy Comparison — EOD Hold vs Tiered Exits (avg R per trade, paper trades)</div>'
            f'<div style="display:flex; gap:32px; flex-wrap:wrap; align-items:center;">'

            f'<div>'
            f'<div style="font-size:9px; color:#81c784; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:2px;">📅 Held to Close (EOD)</div>'
            f'<div style="font-size:26px; font-weight:800; color:{_ptt_eod_clr}; '
            f'font-family:monospace;">{_ptt_eod_str}</div>'
            f'</div>'

            f'<div style="font-size:20px; color:#37474f; align-self:center;">vs</div>'

            f'<div>'
            f'<div style="font-size:9px; color:#ffb74d; text-transform:uppercase; '
            f'letter-spacing:1px; margin-bottom:2px;">🪜 50/25/25 Ladder (Tiered)</div>'
            f'<div style="font-size:26px; font-weight:800; color:{_ptt_tiered_clr}; '
            f'font-family:monospace;">{_ptt_tiered_str}</div>'
            f'</div>'

            f'<div style="border-left:1px solid #1a2744; padding-left:24px; align-self:center;">'
            f'<div style="font-size:12px; font-weight:700; color:{_ptt_verdict_clr};">{_ptt_verdict}</div>'
            f'<div style="font-size:10px; color:#37474f; margin-top:3px;">'
            f'Positive = strategy added value vs a simple hold-to-close</div>'
            f'</div>'

            f'</div></div>',
            unsafe_allow_html=True,
        )

    # Full trade log
    with st.expander("📋 Full Paper Trade Log", expanded=False):
        _pt_log_cols = [
            c for c in ["trade_date", "ticker", "tcs", "predicted",
                         "actual_outcome", "follow_thru_pct", "win_loss",
                         "mae", "mfe", "entry_time", "exit_trigger",
                         "false_break_up", "false_break_down", "min_tcs_filter"]
            if c in _pt_df.columns
        ]
        _pt_log_show = _pt_df[_pt_log_cols].sort_values(
            "trade_date", ascending=False
        ).reset_index(drop=True)

        def _pt_row_color(row):
            wl = str(row.get("win_loss", "")).strip()
            if wl in ("W", "Win"):
                base = "background-color: rgba(76,175,80,0.08)"
                hi   = "background-color: rgba(76,175,80,0.18); color:#66bb6a; font-weight:700"
            elif wl in ("L", "Loss"):
                base = "background-color: rgba(239,83,80,0.08)"
                hi   = "background-color: rgba(239,83,80,0.18); color:#ef5350; font-weight:700"
            else:
                grey = "background-color: rgba(144,164,174,0.08); color:#90a4ae"
                return [grey] * len(row)
            return [
                hi if col == "win_loss" else base
                for col in row.index
            ]

        _pt_log_styled = _pt_log_show.style.apply(_pt_row_color, axis=1)
        st.dataframe(_pt_log_styled, use_container_width=True, hide_index=True)
        st.caption(
            f"Showing {len(_pt_log_show)} paper trades from last 21 days · "
            "Only TCS-filtered qualifying setups are stored here"
        )

    # ── Exit Observation Logger ──────────────────────────────────────────────
    with st.expander("✍️ Log Exit Observation", expanded=False):
        st.caption(
            "What were you seeing when you exited? Volume drying, wick rejection, "
            "VWAP stall, structure broke, emotional? This feeds exit strategy automation."
        )
        _recent_trades = _pt_df[["trade_date", "ticker"]].dropna().drop_duplicates()
        _trade_options = [
            f"{row['ticker']} — {row['trade_date']}"
            for _, row in _recent_trades.sort_values("trade_date", ascending=False).iterrows()
        ]
        if _trade_options:
            _obs_sel = st.selectbox("Select trade", options=_trade_options, key="exit_obs_sel")
            _obs_text = st.text_area(
                "What did you see at exit?",
                placeholder="e.g. volume dried at VWAP, wick through IB high with no follow, "
                            "L2 thinned out, held past target — got greedy, cut early on red candle...",
                height=100,
                key="exit_obs_text",
            )
            if st.button("💾 Save Exit Note", key="exit_obs_save"):
                if _obs_text.strip():
                    _obs_ticker = _obs_sel.split(" — ")[0].strip()
                    _obs_date   = _obs_sel.split(" — ")[1].strip()
                    _ok = patch_exit_obs(_obs_ticker, _obs_date, _obs_text, user_id=_AUTH_USER_ID)
                    if _ok:
                        st.success(f"✅ Exit note saved for {_obs_ticker} on {_obs_date}")
                    else:
                        st.error("Save failed — run the DB migration first (exit_obs column).")
                else:
                    st.warning("Write something first.")
        else:
            st.info("No paper trades loaded yet.")

    st.markdown("---")

    # ── Section 3: Manual EOD Outcome Update ────────────────────────────────
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; font-weight:700; margin:12px 0 8px 0;">'
        '🔒 SECTION 3 — MANUAL EOD OUTCOME UPDATE</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "The bot auto-updates outcomes at 4:05 PM ET. Use this if you want to "
        "force-refresh outcomes for a specific date right now (e.g. after market close)."
    )
    _eod_c1, _eod_c2 = st.columns([1, 2])
    with _eod_c1:
        _eod_date = st.date_input(
            "Date to update", value=date.today(), key="pt_eod_date"
        )
    with _eod_c2:
        _eod_tickers_raw = st.text_input(
            "Tickers (leave blank to use same as Section 1)",
            value="",
            key="pt_eod_tickers",
            placeholder="e.g. SATL, UGRO, ANNA — or leave blank",
        )
    if st.button("🔒 Update Outcomes for Selected Date", key="pt_eod_btn", use_container_width=True):
        _eod_tickers = (
            [t.strip().upper() for t in _eod_tickers_raw.split(",") if t.strip()]
            or [t.strip().upper() for t in _pt_tickers_raw.replace("\n", ",").split(",") if t.strip()]
        )
        with st.spinner(f"Fetching full-day bars for {_eod_date} and updating outcomes…"):
            _eod_res, _eod_sum = run_historical_backtest(
                api_key, secret_key,
                trade_date=_eod_date,
                tickers=_eod_tickers,
                feed=_pt_feed_str,
                price_min=float(_pt_price_range[0]),
                price_max=float(_pt_price_range[1]),
                slippage_pct=0.75,
            )
        if _eod_sum.get("error"):
            st.error(_eod_sum["error"])
        elif _eod_res:
            _upd = update_paper_trade_outcomes(str(_eod_date), _eod_res, user_id=_AUTH_USER_ID)
            st.success(f"✅ Updated {_upd.get('updated', 0)} paper trade outcome(s) for {_eod_date}")
            st.session_state.pop("pt_tracker_df", None)
        else:
            st.warning("No data returned for that date.")

    st.markdown("---")

    # ── Section 4: Time Window Comparison ───────────────────────────────────
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; font-weight:700; margin:12px 0 8px 0;">'
        '🕐 SECTION 4 — IB WINDOW vs FULL-DAY COMPARISON</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Run the same ticker list with three different cutoff windows and compare win rates side by side. "
        "Window A = standard IB (9:30–10:30). Window B = extended morning (to 12:00 PM). "
        "Window C = midday (to 2:00 PM). "
        "Each window uses its bars as the signal, then evaluates what happened after."
    )
    _wc_date = st.date_input(
        "Comparison Date", value=date.today(), key="pt_wc_date"
    )
    _wc_tickers_raw = st.text_input(
        "Tickers for comparison",
        value=_pt_default_tickers,
        key="pt_wc_tickers",
    )
    _wc_run = st.button(
        "▶ Run Window Comparison", key="pt_wc_run", use_container_width=True, type="primary"
    )
    if _wc_run:
        if not api_key or not secret_key:
            st.error("Add Alpaca credentials in the sidebar first.")
        else:
            _wc_tickers = [
                t.strip().upper()
                for t in _wc_tickers_raw.replace("\n", ",").split(",")
                if t.strip() and t.strip().isalpha()
            ]
            _wc_windows = [
                ("A — IB Only (10:30)",      10, 30),
                ("B — Extended AM (12:00)",  12,  0),
                ("C — Midday (14:00)",       14,  0),
            ]
            _wc_results = {}
            with st.spinner("Running 3 time windows in parallel…"):
                from concurrent.futures import ThreadPoolExecutor as _WCTP, as_completed as _wcac
                def _run_window(label, h, m):
                    res, summ = run_historical_backtest(
                        api_key, secret_key,
                        trade_date=_wc_date,
                        tickers=_wc_tickers,
                        feed=_pt_feed_str,
                        price_min=float(_pt_price_range[0]),
                        price_max=float(_pt_price_range[1]),
                        cutoff_hour=h,
                        cutoff_minute=m,
                        slippage_pct=0.75,
                    )
                    return label, res, summ
                with _WCTP(max_workers=3) as _wc_ex:
                    _wc_futs = {
                        _wc_ex.submit(_run_window, label, h, m): label
                        for label, h, m in _wc_windows
                    }
                    for _wc_f in _wcac(_wc_futs):
                        _lbl, _res, _summ = _wc_f.result()
                        _wc_results[_lbl] = (_res, _summ)

            st.session_state["_pt_wc_results"] = _wc_results

    _wc_stored = st.session_state.get("_pt_wc_results", {})
    if _wc_stored:
        _wc_cols = st.columns(3)
        _wc_summary_rows = []
        for _wi, (_wlabel, (_wres, _wsumm)) in enumerate(sorted(_wc_stored.items())):
            _wwr  = _wsumm.get("win_rate", 0)
            _wtot = _wsumm.get("total", 0)
            _wavg = _wsumm.get("avg_tcs", 0)
            _wfb  = _wsumm.get("false_break_rate", 0)
            _wbull_ft = _wsumm.get("avg_bull_ft", 0)
            _wr_clr = "#4caf50" if _wwr >= 60 else "#ff9800" if _wwr >= 50 else "#ef5350"
            _wc_summary_rows.append({
                "Window":         _wlabel,
                "Win Rate":       f"{_wwr}%",
                "Setups":         _wtot,
                "Avg TCS":        int(_wavg),
                "Avg Bull FT":    f"+{_wbull_ft:.1f}%",
                "False Brk %":    f"{_wfb:.1f}%",
            })
            with _wc_cols[_wi]:
                st.markdown(
                    f'<div style="background:#0a1929; border:1px solid #1565c055; '
                    f'border-radius:10px; padding:16px; text-align:center;">'
                    f'<div style="font-size:10px; color:#546e7a; text-transform:uppercase; '
                    f'letter-spacing:1px; margin-bottom:6px;">{_wlabel}</div>'
                    f'<div style="font-size:34px; font-weight:900; color:{_wr_clr}; '
                    f'font-family:monospace;">{_wwr}%</div>'
                    f'<div style="font-size:11px; color:#37474f; margin-top:4px;">'
                    f'{_wsumm.get("wins",0)}W / {_wsumm.get("losses",0)}L · {_wtot} setups</div>'
                    f'<div style="font-size:11px; color:#546e7a; margin-top:8px;">'
                    f'Avg TCS {int(_wavg)} · Bull FT +{_wbull_ft:.1f}% · False Brk {_wfb:.1f}%</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.markdown("<br>", unsafe_allow_html=True)
        _best_window = max(_wc_stored.items(), key=lambda x: x[1][1].get("win_rate", 0))
        st.info(
            f"**Best window for {_wc_date}: {_best_window[0]}** with "
            f"{_best_window[1][1].get('win_rate', 0)}% win rate. "
            f"This tells you whether giving the engine more bar data before calling structure "
            f"improves prediction accuracy for your ticker universe."
        )

    st.markdown("---")

    # ── Section 5: Brain Health ──────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:10px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:1.5px; font-weight:700; margin:12px 0 8px 0;">'
        '🧠 SECTION 5 — BRAIN HEALTH & LIVE WEIGHT CALIBRATION</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "The bot recalibrates brain weights automatically at 4:10 PM ET every trading day. "
        "It reads all verified journal trades (accuracy_tracker) + all paper trade outcomes "
        "and nudges each structure's weight up or down using a 30% learning rate."
    )

    st.markdown(
        '<div style="background:#1a0a00; border:1px solid #e65100; border-radius:8px; '
        'padding:14px 16px; margin:10px 0;">'
        '<span style="font-size:14px; font-weight:700; color:#ff6d00;">🔒 Brain Weight Controls — Locked</span><br>'
        '<span style="font-size:12px; color:#bf360c; line-height:1.6;">'
        'The <b>"Recalibrate Now"</b> and <b>"Reset to Neutral"</b> buttons have been disabled to protect the learning model.<br>'
        'The bot handles both automatically: recalibration at <b>4:10 PM ET</b> daily.<br>'
        'If you believe a manual recalibration is needed, ask in <b>Replit chat first</b>.'
        '</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    _bh_run   = False
    _bh_reset = False

    _bh_stored = st.session_state.get("_pt_bh_result")

    # Always show current weights
    _cur_weights = _cached_load_brain_weights(user_id=_AUTH_USER_ID)
    _w_default   = 1.0

    # Build display table with delta if calibration was just run
    _bh_deltas_map = {}
    if _bh_stored:
        for d in _bh_stored.get("deltas", []):
            _bh_deltas_map[d["key"]] = d

    _bh_rows = []
    for wk, label in WK_DISPLAY.items():
        cur = _cur_weights.get(wk, 1.0)
        row = {
            "Structure":    label,
            "Weight":       f"{cur:.4f}",
            "vs Default":   f"{cur - _w_default:+.4f}",
            "Status":       "🟢 Boosted" if cur > 1.1 else ("🔴 Penalized" if cur < 0.9 else "⚪ Neutral"),
            "Journal Acc":  "—",
            "Bot Acc":      "—",
            "Blended Acc":  "—",
            "Last Δ":       "—",
        }
        if wk in _bh_deltas_map:
            d = _bh_deltas_map[wk]
            row["Last Δ"]      = f"{d['delta']:+.4f}"
            row["Blended Acc"] = f"{d['blended_acc']}%"
            row["Journal Acc"] = f"{d['journal_acc']}% ({d['journal_n']})" if d.get("journal_acc") is not None else f"< 5 samples ({d.get('journal_n', 0)})"
            row["Bot Acc"]     = f"{d['bot_acc']}% ({d['bot_n']})"        if d.get("bot_acc")     is not None else f"< 5 samples ({d.get('bot_n', 0)})"
        _bh_rows.append(row)

    st.dataframe(
        pd.DataFrame(_bh_rows), use_container_width=True, hide_index=True
    )

    if _bh_stored:
        _src = _bh_stored.get("sources", {})
        _ts  = _bh_stored.get("timestamp", "")[:16].replace("T", " ")
        if _bh_stored.get("calibrated"):
            _n_adj = len(_bh_stored.get("deltas", []))
            st.success(
                f"✅ Calibration complete as of {_ts} — "
                f"{_n_adj} structure(s) adjusted · "
                f"Data: {_src.get('accuracy_tracker', 0)} journal entries + "
                f"{_src.get('paper_trades', 0)} paper trades = "
                f"{_src.get('total', 0)} total outcomes"
            )
        else:
            st.info(
                f"Not enough data yet to update weights (need ≥5 samples per structure). "
                f"Read {_src.get('total', 0)} outcomes so far — keep trading and journaling. "
                f"Weights unchanged."
            )

    # ════════════════════════════════════════════════════════════════════════════
    # SECTION 6 — NIGHTLY TICKER RANKINGS
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown(
        '<div style="font-size:11px; color:#1565c0; text-transform:uppercase; '
        'letter-spacing:2px; font-weight:700; margin-bottom:4px;">🎯 NIGHTLY TICKER RANKINGS</div>'
        '<div style="font-size:12px; color:#546e7a; margin-bottom:16px;">'
        'Rate each watchlist ticker 0–5 every night. Outcomes are verified automatically '
        'the next day. Over time this builds your personal chart-read accuracy by confidence tier.</div>',
        unsafe_allow_html=True,
    )

    _rk_table_ready = ensure_ticker_rankings_table()
    if not _rk_table_ready:
        st.warning("Create the ticker_rankings table in Supabase first:", icon="⚠️")
        st.code(
            "CREATE TABLE IF NOT EXISTS ticker_rankings (\n"
            "  id           SERIAL PRIMARY KEY,\n"
            "  user_id      TEXT NOT NULL,\n"
            "  rating_date  DATE NOT NULL,\n"
            "  ticker       TEXT NOT NULL,\n"
            "  rank         INTEGER NOT NULL CHECK (rank >= 0 AND rank <= 5),\n"
            "  notes        TEXT DEFAULT '',\n"
            "  actual_open  FLOAT,\n"
            "  actual_close FLOAT,\n"
            "  actual_chg_pct FLOAT,\n"
            "  verified     BOOLEAN DEFAULT FALSE,\n"
            "  created_at   TIMESTAMPTZ DEFAULT NOW(),\n"
            "  UNIQUE(user_id, rating_date, ticker)\n"
            ");",
            language="sql",
        )
    else:
        _rk_uid = st.session_state.get("auth_user_id", "")
        _rk_col1, _rk_col2 = st.columns([1, 1])

        # ── Draft persistence helpers ────────────────────────────────────────
        import json as _json, pathlib as _pl
        def _rk_draft_path(uid, dt):
            safe = str(dt).replace("-", "")
            safe_uid = (uid or "anon")[:16].replace("/", "")
            return _pl.Path(f"/tmp/rk_draft_{safe_uid}_{safe}.json")

        def _save_rk_draft(uid, dt, tickers):
            try:
                payload = {
                    t: {"rank": st.session_state.get(f"rk_sel_{t}", 0),
                        "notes": st.session_state.get(f"rk_note_{t}", "")}
                    for t in tickers
                }
                _rk_draft_path(uid, dt).write_text(_json.dumps(payload))
            except Exception:
                pass

        def _load_rk_draft(uid, dt):
            try:
                p = _rk_draft_path(uid, dt)
                if p.exists():
                    return _json.loads(p.read_text())
            except Exception:
                pass
            return {}

        def _clear_rk_draft(uid, dt):
            try:
                _rk_draft_path(uid, dt).unlink(missing_ok=True)
            except Exception:
                pass

        with _rk_col1:
            st.markdown("**📝 Log Tonight's Rankings**")
            _rk_date = st.date_input("Rating date", value=datetime.now(EASTERN).date(),
                                     key="rk_date_input")
            _wl_tickers = _cached_load_watchlist(user_id=_rk_uid)
            if not _wl_tickers:
                _wl_tickers = list(_DEFAULT_RANKING_TICKERS)

            _extra_rk = st.text_input("Add tickers (comma-separated)",
                                      placeholder="ARAI, SKYQ, CUE",
                                      key="rk_extra_tickers")
            if _rk_uid:
                _rk_et_cached = st.session_state.get("_cached_prefs", {})
                if _rk_et_cached.get("rk_extra_tickers") != _extra_rk:
                    _rk_et_new_prefs = {**_rk_et_cached, "rk_extra_tickers": _extra_rk}
                    save_user_prefs(_rk_uid, _rk_et_new_prefs)
                    st.session_state["_cached_prefs"] = _rk_et_new_prefs
            if _extra_rk:
                for t in _extra_rk.upper().split(","):
                    t = t.strip()
                    if t and t not in _wl_tickers:
                        _wl_tickers.append(t)

            if _wl_tickers:
                # Restore draft into session state before widgets render
                _draft = _load_rk_draft(_rk_uid, _rk_date)
                if _draft:
                    for _sym, _dv in _draft.items():
                        if f"rk_sel_{_sym}" not in st.session_state:
                            st.session_state[f"rk_sel_{_sym}"] = _dv.get("rank", 0)
                        if f"rk_note_{_sym}" not in st.session_state:
                            st.session_state[f"rk_note_{_sym}"] = _dv.get("notes", "")

                _rank_labels = {0: "0 – Skip", 1: "1 – Weak", 2: "2 – Low",
                                3: "3 – OK", 4: "4 – Strong", 5: "5 – Best"}

                _rankings_input = []
                _hdr = st.columns([2, 3, 4])
                _hdr[0].markdown("**Ticker**")
                _hdr[1].markdown("**Rank**")
                _hdr[2].markdown("**Notes**")
                for _rk_sym in _wl_tickers:
                    _rc = st.columns([2, 3, 4])
                    _rc[0].markdown(f"`{_rk_sym}`")
                    _sel = _rc[1].selectbox("Rank", options=[0,1,2,3,4,5],
                                            format_func=lambda x, _rl=_rank_labels: _rl[x],
                                            key=f"rk_sel_{_rk_sym}", label_visibility="collapsed")
                    _note = _rc[2].text_input("Notes", placeholder="optional note",
                                              key=f"rk_note_{_rk_sym}", label_visibility="collapsed")
                    _rankings_input.append({"ticker": _rk_sym, "rank": _sel, "notes": _note})

                # Auto-save draft on every rerun
                _save_rk_draft(_rk_uid, _rk_date, _wl_tickers)

                _draft_path = _rk_draft_path(_rk_uid, _rk_date)
                if _draft_path.exists():
                    st.caption("🟡 Draft auto-saved — your ratings are safe even if the app restarts.")

                _btn_c1, _btn_c2 = st.columns([3, 1])
                with _btn_c1:
                    if st.button("💾 Save Rankings to Database", type="primary",
                                 use_container_width=True, key="rk_save_btn"):
                        _wp_ctx = _cached_load_watchlist_predictions(user_id=_rk_uid, pred_date=_rk_date)
                        _wp_lookup = {}
                        if not _wp_ctx.empty:
                            for _, _wp_row in _wp_ctx.iterrows():
                                _tk = str(_wp_row.get("ticker", "")).upper().strip()
                                _wp_lookup[_tk] = {
                                    "tcs": _wp_row.get("tcs"),
                                    "edge_score": _wp_row.get("edge_score"),
                                    "predicted_structure": _wp_row.get("predicted_structure"),
                                    "confidence_label": _wp_row.get("confidence_label"),
                                    "rvol": _wp_row.get("rvol"),
                                }
                        for _ri in _rankings_input:
                            _ctx = _wp_lookup.get(_ri["ticker"], {})
                            _ri.update({k: v for k, v in _ctx.items() if v is not None and str(v).strip()})
                        _rk_res = save_ticker_rankings(_rk_uid, _rk_date, _rankings_input)
                        if _rk_res["saved"] > 0:
                            _clear_rk_draft(_rk_uid, _rk_date)
                            st.success(f"✅ Saved {_rk_res['saved']} rankings for {_rk_date}")
                        else:
                            st.error("Failed to save — check Supabase connection. Your draft is still preserved.")
                with _btn_c2:
                    if st.button("🗑 Clear Draft", use_container_width=True, key="rk_clear_draft_btn"):
                        _clear_rk_draft(_rk_uid, _rk_date)
                        for _sym in _wl_tickers:
                            st.session_state.pop(f"rk_sel_{_sym}", None)
                            st.session_state.pop(f"rk_note_{_sym}", None)
                        st.rerun()
            else:
                st.info("No watchlist tickers loaded. Add tickers above or load your watchlist.")

        with _rk_col2:
            st.markdown("**📊 Accuracy by Rank Tier**")

            _verify_date = st.date_input("Verify rankings from date",
                                         value=(datetime.now(EASTERN).date() -
                                                __import__('datetime').timedelta(days=1)),
                                         key="rk_verify_date")
            if st.button("🔍 Verify Outcomes", key="rk_verify_btn", use_container_width=True):
                with st.spinner("Pulling next-day outcomes..."):
                    _vres = verify_ticker_rankings(api_key, secret_key, _rk_uid, _verify_date)
                if _vres["verified"] > 0:
                    st.success(f"✅ Verified {_vres['verified']} tickers")
                elif _vres["errors"] > 0:
                    st.warning(f"Verified 0, errors on {_vres['errors']} tickers")
                else:
                    st.info("No rankings found for that date.")

            st.markdown("")
            _acc_df = _cached_load_ranking_accuracy(user_id=_rk_uid)
            if not _acc_df.empty:
                _acc_cols = ["rank", "trades", "win_rate", "avg_chg"]
                _acc_rename = {"rank": "Rank", "trades": "Trades", "win_rate": "Win Rate %", "avg_chg": "Avg Chg %"}
                if "avg_tcs" in _acc_df.columns:
                    _acc_cols.append("avg_tcs")
                    _acc_rename["avg_tcs"] = "Avg TCS"
                if "avg_rvol" in _acc_df.columns:
                    _acc_cols.append("avg_rvol")
                    _acc_rename["avg_rvol"] = "Avg RVOL"
                _acc_display = _acc_df[[c for c in _acc_cols if c in _acc_df.columns]].copy()
                _acc_display.columns = [_acc_rename.get(c, c) for c in _acc_display.columns]

                def _color_rank_wr(val):
                    if isinstance(val, (int, float)):
                        if val >= 60: return "color: #66bb6a; font-weight:700"
                        if val >= 45: return "color: #ffa726"
                        return "color: #ef5350"
                    return ""

                st.dataframe(
                    _acc_display.style.map(_color_rank_wr, subset=["Win Rate %"]),
                    use_container_width=True, hide_index=True, height="stretch"
                )
                _best = _acc_df[_acc_df["win_rate"] == _acc_df["win_rate"].max()]
                if not _best.empty:
                    _br = int(_best.iloc[0]["rank"])
                    _bwr = _best.iloc[0]["win_rate"]
                    st.caption(f"Your rank-{_br} picks win {_bwr}% of the time "
                               f"({int(_best.iloc[0]['trades'])} verified trades)")
            else:
                st.info("No verified rankings yet. Save tonight's rankings and hit "
                        "Verify Outcomes tomorrow after market close.")

            st.markdown("")
            st.markdown("**📋 Recent Rankings**")
            _recent_rk = _cached_load_ticker_rankings(user_id=_rk_uid)
            if not _recent_rk.empty:
                _show_cols = [c for c in ["rating_date", "ticker", "rank", "tcs", "rvol",
                                          "predicted_structure", "actual_chg_pct", "verified"]
                              if c in _recent_rk.columns]
                _recent_rk_disp = _recent_rk[_show_cols].head(30).copy()
                _recent_rk_disp.columns = [c.replace("_", " ").title() for c in _show_cols]
                st.dataframe(_recent_rk_disp, use_container_width=True, hide_index=True)
            else:
                st.caption("No rankings saved yet.")

    # ── Section 7: Cognitive Delta Log ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🧠 Cognitive Delta Log")
    st.caption(
        "Track when you deviate from the system's calls. "
        "Over time, this reveals whether your intuition adds edge or costs you."
    )
    _cd_table_ready = ensure_cognitive_delta_table()
    if not _cd_table_ready:
        st.warning("Run this in Supabase SQL Editor to enable the Cognitive Delta Log:")
        st.code(_COGNITIVE_DELTA_SQL, language="sql")
    else:
        _cd_uid       = _AUTH_USER_ID
        _cd_date      = date.today()
        _cd_preds     = _cached_load_watchlist_predictions(user_id=_cd_uid, pred_date=_cd_date)
        _cd_today     = _cached_load_cognitive_delta_today(user_id=_cd_uid, trade_date=_cd_date)
        _cd_logged    = set(_cd_today["ticker"].tolist()) if not _cd_today.empty else set()

        with st.expander("📋 Log Today's Decisions", expanded=False):
            if _cd_preds.empty:
                st.info("No system alerts found for today. The bot fires alerts at 10:47 AM ET — check back after the morning scan.")
            else:
                st.markdown("**System fired these alerts today. Mark each one:**")
                _cd_entries = []
                _sys_tickers = _cd_preds["ticker"].tolist() if "ticker" in _cd_preds.columns else []
                for _cd_sym in _sys_tickers:
                    _pred_row  = _cd_preds[_cd_preds["ticker"] == _cd_sym].iloc[0]
                    _cd_tcs    = float(_pred_row.get("tcs", 0) or 0)
                    _cd_struct = str(_pred_row.get("predicted_structure", "") or "")
                    _cd_rank   = int(_pred_row.get("rank", 0) or 0) if "rank" in _pred_row else None
                    _prev_action = _cd_today[_cd_today["ticker"] == _cd_sym]["user_action"].values[0] \
                                   if _cd_sym in _cd_logged else "followed"
                    _action_idx = {"followed": 0, "skipped": 1}.get(_prev_action, 0)
                    c1, c2, c3 = st.columns([2, 2, 3])
                    with c1:
                        st.markdown(f"**{_cd_sym}** · TCS {_cd_tcs:.0f}")
                        if _cd_struct:
                            st.caption(_cd_struct)
                    with c2:
                        _cd_action = st.radio(
                            f"action_{_cd_sym}",
                            ["followed", "skipped"],
                            index=_action_idx,
                            horizontal=True,
                            label_visibility="collapsed",
                            key=f"cd_action_{_cd_sym}",
                        )
                    with c3:
                        _cd_note = st.text_input(
                            f"note_{_cd_sym}",
                            placeholder="Why? (optional)",
                            label_visibility="collapsed",
                            key=f"cd_note_{_cd_sym}",
                        )
                    _cd_entries.append({
                        "ticker": _cd_sym,
                        "system_rank": _cd_rank,
                        "system_tcs": _cd_tcs,
                        "system_structure": _cd_struct,
                        "user_action": _cd_action,
                        "notes": _cd_note,
                    })

                st.markdown("**Overrides — tickers you traded that weren't on the system list:**")
                _cd_override_raw = st.text_input(
                    "Override tickers (comma-separated)",
                    placeholder="e.g. SIDU, CREG",
                    key="cd_override_tickers",
                )
                for _ov_sym in [t.strip().upper() for t in _cd_override_raw.split(",") if t.strip()]:
                    if _ov_sym not in _sys_tickers:
                        _cd_entries.append({
                            "ticker": _ov_sym,
                            "system_rank": None,
                            "system_tcs": None,
                            "system_structure": None,
                            "user_action": "override",
                            "notes": "Not on system list",
                        })

                if st.button("💾 Save Delta Log", key="cd_save_btn"):
                    _cd_res = save_cognitive_delta_entries(_cd_uid, _cd_date, _cd_entries)
                    if _cd_res["saved"] > 0:
                        st.success(f"✅ Logged {_cd_res['saved']} decisions for {_cd_date}")
                    if _cd_res["errors"]:
                        st.warning(f"Some errors: {_cd_res['errors']}")

        with st.expander("📊 Deviance Analysis", expanded=False):
            _cd_analysis = _cached_load_cognitive_delta_analysis(user_id=_cd_uid)
            if _cd_analysis.empty or len(_cd_analysis) < 5:
                st.info(f"Need at least 5 verified entries to show analysis. "
                        f"Currently have {len(_cd_analysis)} verified.")
            else:
                _n_followed = _cd_analysis[_cd_analysis["user_action"] == "followed"]
                _n_skipped  = _cd_analysis[_cd_analysis["user_action"] == "skipped"]
                _n_override = _cd_analysis[_cd_analysis["user_action"] == "override"]
                _c1, _c2, _c3 = st.columns(3)
                with _c1:
                    _f_wr = (_n_followed["user_won"].sum() / len(_n_followed) * 100) if len(_n_followed) > 0 else 0
                    st.metric("Followed System", f"{_f_wr:.0f}%",
                              help=f"Win rate when you traded what the system flagged ({len(_n_followed)} trades)")
                with _c2:
                    _s_wr = (_n_skipped["user_won"].sum() / len(_n_skipped) * 100) if len(_n_skipped) > 0 else 0
                    st.metric("Skipped System", f"{_s_wr:.0f}%",
                              help=f"% of skipped calls that would have won ({len(_n_skipped)} skips) — if high, your skip instinct costs you")
                with _c3:
                    _o_wr = (_n_override["user_won"].sum() / len(_n_override) * 100) if len(_n_override) > 0 else 0
                    st.metric("Override (Off-List)", f"{_o_wr:.0f}%",
                              help=f"Win rate on tickers you traded that weren't on the system list ({len(_n_override)} trades)")
                st.caption(
                    f"**Reading this:** Followed > Skipped = system adds edge. "
                    f"Override > Followed = your tape-reading exceeds the algo. "
                    f"Skipped > Followed = your skip instinct is costing you."
                )


# ── Macro Regime Banner ─────────────────────────────────────────────────────────
_active_regime = st.session_state.get("breadth_regime")
if _active_regime and _active_regime.get("regime_tag", "unknown") != "unknown":
    _rc    = _active_regime["color"]
    _rl    = _active_regime["label"]
    _rm    = {"home_run": "Home Run Mode 🏠", "singles": "Singles Mode ⚾", "caution": "Caution Mode ⚠️"}.get(
                _active_regime.get("mode", ""), ""
             )
    _rtcs  = _active_regime.get("tcs_floor_adj", 0)
    _rtcs_str = (f"TCS floor +{_rtcs}" if _rtcs > 0 else f"TCS floor {_rtcs}") if _rtcs != 0 else "TCS floor unchanged"
    _rd    = _active_regime.get("trade_date", "")
    st.markdown(
        f'<div style="background:#0d1117; border:1px solid {_rc}; border-radius:8px; '
        f'padding:10px 16px; margin-bottom:12px; display:flex; align-items:center; gap:16px;">'
        f'<span style="font-size:15px; font-weight:700; color:{_rc};">{_rl}</span>'
        f'<span style="font-size:13px; color:#ccc;">{_rm}</span>'
        f'<span style="font-size:12px; color:#888;">· {_rtcs_str}</span>'
        + (f'<span style="font-size:11px; color:#555; margin-left:auto;">{_rd}</span>' if _rd else "")
        + '</div>',
        unsafe_allow_html=True,
    )

tab_chart, tab_scan, tab_playbook, tab_backtest, tab_journal, tab_analytics, tab_sa, tab_paper, tab_perf, tab_decision = st.tabs(
    ["📈 Main Chart", "🔍 Scanner", "📋 Playbook", "🔬 Backtest",
     "📖 Journal", "📊 Analytics", "⚡ Small Account", "📄 Paper Trade", "📊 Performance", "🧠 Decision Log"]
)

# ── Scanner tab ────────────────────────────────────────────────────────────────
with tab_scan:
    # ── Run scanner if button clicked ──────────────────────────────────────────
    if scan_button:
        if not api_key or not secret_key:
            st.error("Enter your Alpaca credentials in the sidebar first.")
        else:
            watchlist = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]
            if not watchlist:
                st.warning("Watchlist is empty — add some tickers and try again.")
            else:
                with st.spinner(f"Scanning {len(watchlist)} tickers for pre-market gaps…"):
                    try:
                        _scan_out = run_gap_scanner(
                            api_key, secret_key, watchlist, date.today(),
                            feed=scan_feed,
                            min_price=scan_min_price,
                            max_price=scan_max_price,
                            min_rvol=scan_min_rvol,
                        )
                        results = _scan_out["rows"]
                        _filtered_out = _scan_out.get("filtered_out", [])
                        _rvol_filtered = _scan_out.get("rvol_filtered", [])
                        st.session_state.scanner_results = results
                        st.session_state.scanner_filtered_out = _filtered_out
                        st.session_state.scanner_rvol_filtered = _rvol_filtered
                        st.session_state.scanner_last_run = datetime.now(EASTERN)
                        # Pre-trade quality + pattern detection — run in parallel for all tickers
                        if results:
                            from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
                            _quality = {}
                            _patterns_map = {}
                            with ThreadPoolExecutor(max_workers=min(8, len(results))) as _ex:
                                _q_futs = {
                                    _ex.submit(
                                        compute_pretrade_quality,
                                        api_key, secret_key,
                                        r["ticker"], date.today(), scan_feed
                                    ): r["ticker"]
                                    for r in results
                                }
                                _p_futs = {
                                    _ex.submit(
                                        scan_ticker_patterns,
                                        api_key, secret_key,
                                        r["ticker"], date.today(), scan_feed
                                    ): r["ticker"]
                                    for r in results
                                }
                                for _f in _asc(_q_futs):
                                    _quality[_q_futs[_f]] = _f.result()
                                for _f in _asc(_p_futs):
                                    _patterns_map[_p_futs[_f]] = _f.result()
                            st.session_state.scanner_quality = _quality
                            st.session_state.scanner_patterns = _patterns_map
                        if not results:
                            st.warning(
                                "Scan ran but returned no results. "
                                f"Possible reasons: all tickers outside ${scan_min_price:.0f}–${scan_max_price:.0f} range, "
                                "no gap data available (market closed / weekend), "
                                "or IEX feed selected (no pre-market data). "
                                "Try adjusting the price range filter or switching to SIP feed."
                            )
                    except Exception as e:
                        st.error(f"Scanner error: {e}")

    # ── Display results ────────────────────────────────────────────────────────
    results = st.session_state.scanner_results
    last_run = st.session_state.scanner_last_run

    _scan_filtered_out = st.session_state.get("scanner_filtered_out", [])
    if last_run:
        _pm_ok = results[0].get("pm_data_available", True) if results else True
        _sort_by = "Pre-Market RVOL" if _pm_ok else "Gap %"
        _p_min = st.session_state.get("scan_min_price", 1.0)
        _p_max = st.session_state.get("scan_max_price", 50.0)
        st.caption(f"Last scan: {last_run.strftime('%H:%M:%S')} EST  ·  "
                   f"${_p_min:.0f}–${_p_max:.0f} filter · {len(results)} showing · sorted by {_sort_by}")
        if not _pm_ok:
            st.info("📊 **Gap-Only Mode** — Pre-market volume unavailable on free IEX tier. "
                    "Results are sorted by largest gap %. PM Vol / RVOL columns will be blank. "
                    "Upgrade to Alpaca SIP subscription to unlock pre-market RVOL.")
        if _scan_filtered_out:
            st.warning(
                f"⚠️ **{len(_scan_filtered_out)} ticker(s) excluded** by price filter "
                f"(${_p_min:.0f}–${_p_max:.0f}): "
                + ", ".join(_scan_filtered_out)
                + " — adjust Min/Max Price in the scanner settings to include them."
            )
        _rvol_filt = st.session_state.get("scanner_rvol_filtered", [])
        if _rvol_filt:
            st.info("📊 " + " · ".join(_rvol_filt))

    if not results:
        st.info("👈 Click **🔍 Scan Gap Plays** in the sidebar to populate this panel.\n\n"
                "The scanner checks every ticker in your watchlist, applies the price range filter, "
                "and ranks results by gap % (free IEX tier) or pre-market RVOL (SIP).")
    else:
        _gap_colors = {
            "up":   ("#4caf50", "#1b5e20"),   # (text, bg-tint)
            "down": ("#ef5350", "#4e1111"),
            "flat": ("#aaaaaa", "#1a1a2e"),
        }
        _rvol_color = lambda r: (
            "#FFD700" if r is not None and r > 5.5 else
            "#FF6B35" if r is not None and r > 4.0 else
            "#FF9500" if r is not None and r > 3.0 else
            "#26a69a" if r is not None and r >= 1.2 else
            "#ef5350"
        )

        for row in results:
            sym   = row["ticker"]
            price = row["price"]
            gap   = row["gap_pct"]
            rvol  = row["pm_rvol"]
            pm_v  = row["pm_vol"]
            avg_v = row["avg_pm_vol"]

            gap_dir    = "up" if gap > 0.2 else "down" if gap < -0.2 else "flat"
            gap_txt    = f"+{gap:.2f}%" if gap >= 0 else f"{gap:.2f}%"
            gap_clr, gap_bg = _gap_colors[gap_dir]
            rc         = _rvol_color(rvol)
            rvol_str   = f"{rvol:.1f}×" if rvol is not None else "N/A"
            pm_str     = f"{pm_v:,}"
            avg_str    = f"{avg_v:,.0f}" if avg_v else "—"

            # ── Pre-trade quality data (computed in parallel during scan) ────
            _sq = st.session_state.get("scanner_quality", {}).get(sym)
            _tcs_v = _ib_pos = _tcs_bkt = _ib_h = _ib_l = _ib_src = None
            _tcs_ok = _ib_ok = _go = False
            _tcs_clr = _ib_clr = "#888"
            _go_txt = _go_clr = _go_bg = _go_brd = ""
            _ib_warn = _src_tag = ""
            _ov_key_h = f"_ib_ov_high_{sym}"
            _ov_key_l = f"_ib_ov_low_{sym}"
            _ov_h = st.session_state.get(_ov_key_h)
            _ov_l = st.session_state.get(_ov_key_l)
            _has_quality = bool(_sq and not _sq.get("error"))

            if _has_quality:
                _tcs_v     = _sq["tcs"]
                _tcs_bkt   = _sq["tcs_bucket"]
                _tcs_ok    = _sq["tcs_ok"]
                _ib_formed = _sq.get("ib_formed", True)
                if _ov_h and _ov_l and _ov_h > _ov_l:
                    _ib_h, _ib_l, _ib_src = _ov_h, _ov_l, "Webull"
                else:
                    _ib_h, _ib_l, _ib_src = _sq["ib_high"], _sq["ib_low"], "Alpaca"
                _margin = (_ib_h - _ib_l) * 0.05
                if price >= _ib_h + _margin:       _ib_pos = "Extended Above IB"
                elif price <= _ib_l - _margin:     _ib_pos = "Extended Below IB"
                elif price <= _ib_l + _margin:     _ib_pos = "At IB Low"
                elif price >= _ib_h - _margin:     _ib_pos = "At IB High"
                else:                              _ib_pos = "Inside IB"
                _ib_ok  = _ib_pos == "At IB Low"
                _go     = _tcs_ok and _ib_ok
                _tcs_clr = "#4caf50" if _tcs_ok else "#FFD700" if _tcs_v >= 70 else "#ef5350"
                _ib_clr  = "#4caf50" if _ib_ok else "#ef5350" if "Extended" in _ib_pos else "#FF9500"
                _go_bg   = "#0d2e1a" if _go else "#2e0d0d"
                _go_brd  = "#4caf50" if _go else "#ef5350"
                _go_txt  = "✅ GO" if _go else "⛔ WAIT"
                _go_clr  = "#4caf50" if _go else "#ef5350"
                _ib_warn = "" if _ib_formed else " ⚠️"
                _src_tag = f'({_ib_src})'

            # ── Fallback quality row from gap data when market closed ────────
            _sq_err = (_sq or {}).get("error", "") if not _has_quality else ""
            _market_closed_fallback = not _has_quality and "No bar data" in _sq_err
            if _market_closed_fallback:
                _abs_gap = abs(gap)
                if _abs_gap >= 10:
                    _gq_lbl, _gq_clr = "Strong Gap", "#4caf50"
                elif _abs_gap >= 5:
                    _gq_lbl, _gq_clr = "Moderate Gap", "#FF9500"
                elif _abs_gap >= 2:
                    _gq_lbl, _gq_clr = "Weak Gap", "#FFD700"
                else:
                    _gq_lbl, _gq_clr = "Minimal", "#888"
                _rvol_fb = f"{rvol:.1f}×" if rvol and rvol > 0 else "—"
                _rvol_fb_clr = _rvol_color(rvol) if rvol and rvol > 0 else "#555"

            # ── Unified card — everything at a glance ────────────────────────
            _quality_row = ""
            if _has_quality:
                _quality_row = (
                    f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:14px;'
                    f'margin-top:10px;padding-top:10px;border-top:1px solid #1e2a3a;">'
                    f'<div style="text-align:center;min-width:52px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:2px;">TCS</div>'
                    f'<div style="font-size:20px;font-weight:800;color:{_tcs_clr};">{_tcs_v}</div>'
                    f'<div style="font-size:9px;color:#555;">{_tcs_bkt}</div>'
                    f'</div>'
                    f'<div style="text-align:center;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:2px;">Structure{_ib_warn}</div>'
                    f'<div style="font-size:13px;font-weight:700;color:{_ib_clr};">{_ib_pos}</div>'
                    f'<div style="font-size:9px;color:#444;">IB ${_ib_l}–${_ib_h} {_src_tag}</div>'
                    f'</div>'
                    f'<div style="margin-left:auto;background:{_go_bg};border:2px solid {_go_brd};'
                    f'border-radius:8px;padding:6px 16px;text-align:center;">'
                    f'<div style="font-size:16px;font-weight:800;color:{_go_clr};">{_go_txt}</div>'
                    f'<div style="font-size:9px;color:#555;margin-top:1px;">'
                    + (
                        lambda _rg=st.session_state.get("breadth_regime"): (
                            f'Regime {_rg["label"]} · TCS floor '
                            f'{55 + _rg.get("tcs_floor_adj", 0)} '
                            f'{"✓" if _tcs_ok else "✗"}'
                            if _rg and _rg.get("regime_tag", "unknown") != "unknown"
                            else f'TCS 55–70 {"✓" if _tcs_ok else "✗"}'
                        )
                    )()
                    + f' · At IB Low {"✓" if _ib_ok else "✗"}</div>'
                    f'</div>'
                    f'</div>'
                )
            elif _market_closed_fallback:
                _quality_row = (
                    f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:14px;'
                    f'margin-top:10px;padding-top:10px;border-top:1px solid #1e2a3a;">'
                    f'<div style="text-align:center;min-width:72px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:2px;">Gap Quality</div>'
                    f'<div style="font-size:15px;font-weight:800;color:{_gq_clr};">{_gq_lbl}</div>'
                    f'<div style="font-size:9px;color:#555;">{abs(gap):.1f}% move</div>'
                    f'</div>'
                    f'<div style="text-align:center;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:2px;">PM RVOL</div>'
                    f'<div style="font-size:15px;font-weight:800;color:{_rvol_fb_clr};">{_rvol_fb}</div>'
                    f'</div>'
                    f'<div style="text-align:center;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:2px;">IB</div>'
                    f'<div style="font-size:13px;font-weight:700;color:#555;">Pending</div>'
                    f'<div style="font-size:9px;color:#444;">Forms 9:30–10:30 AM ET</div>'
                    f'</div>'
                    f'<div style="margin-left:auto;background:#1a1a2e;border:2px solid #333;'
                    f'border-radius:8px;padding:6px 16px;text-align:center;">'
                    f'<div style="font-size:14px;font-weight:700;color:#555;">🕐 After Open</div>'
                    f'<div style="font-size:9px;color:#444;margin-top:1px;">TCS + GO/WAIT after 10:30 AM</div>'
                    f'</div>'
                    f'</div>'
                )

            _border_clr = _go_brd if _has_quality else ("#1e3a2e" if _market_closed_fallback else "#2a2a4a")
            st.markdown(
                f'<div style="background:#12122299;border:1px solid {_border_clr};'
                f'border-left:4px solid {_border_clr};'
                f'border-radius:10px;padding:16px 20px;margin:8px 0;">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">'
                f'<div>'
                f'<div style="font-size:26px;font-weight:900;color:#e0e0e0;letter-spacing:1px;">{sym}</div>'
                f'<div style="font-size:13px;color:#888;">${price:.2f}</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">Gap</div>'
                f'<div style="font-size:22px;font-weight:800;color:{gap_clr};">{gap_txt}</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">PM RVOL</div>'
                f'<div style="font-size:22px;font-weight:800;color:{rc};">{rvol_str}</div>'
                f'<div style="font-size:11px;color:#555;">{pm_str} vs avg {avg_str}</div>'
                f'</div>'
                f'</div>'
                + _quality_row +
                f'</div>',
                unsafe_allow_html=True,
            )

            # IB Override expander (only shown when quality data loaded)
            if _has_quality:
                with st.expander(f"✏️ Override IB levels for {sym} (use your Webull values)", expanded=False):
                    _ov_cols = st.columns(3)
                    _new_ib_h = _ov_cols[0].number_input(
                        "IB High", min_value=0.01, value=float(_ib_h),
                        step=0.01, format="%.2f", key=f"ib_h_input_{sym}"
                    )
                    _new_ib_l = _ov_cols[1].number_input(
                        "IB Low", min_value=0.01, value=float(_ib_l),
                        step=0.01, format="%.2f", key=f"ib_l_input_{sym}"
                    )
                    if _ov_cols[2].button("Apply", key=f"ib_ov_btn_{sym}", use_container_width=True):
                        if _new_ib_h > _new_ib_l:
                            st.session_state[_ov_key_h] = _new_ib_h
                            st.session_state[_ov_key_l] = _new_ib_l
                            st.rerun()
                        else:
                            st.error("IB High must be greater than IB Low.")
                    if _ov_h and _ov_l:
                        if st.button("↩ Reset to Alpaca values", key=f"ib_ov_reset_{sym}"):
                            st.session_state.pop(_ov_key_h, None)
                            st.session_state.pop(_ov_key_l, None)
                            st.rerun()

            if _sq and _sq.get("error") and "No bar data" not in _sq.get("error", ""):
                st.caption(f"⚠️ Quality check unavailable: {_sq['error']}")

            # ── Pattern Alert Badge ──────────────────────────────────────────
            _sp = st.session_state.get("scanner_patterns", {}).get(sym, [])
            _bullish_sp = [p for p in _sp if p["direction"] == "Bullish"]
            if _bullish_sp:
                _p_rows_html = ""
                for _p in _bullish_sp:
                    _pct = int(_p["score"] * 100)
                    _sc = "#4caf50" if _pct >= 80 else "#ffa726" if _pct >= 65 else "#ef9a9a"
                    _tfc = "#90caf9" if _p["timeframe"] == "1hr" else "#b0bec5"
                    _nl = _p.get("neckline")
                    _nl_html = (f' &middot; Neckline <b style="color:#FFD700;">'
                                f'${_nl:.2f}</b>') if _nl else ""
                    _cf = " &middot; ".join(_p["confluence"]) if _p["confluence"] else ""
                    _cf_html = (f'<div style="font-size:10px;color:#FFD700;margin-top:2px;">'
                                f'&#9889; {_cf}</div>') if _cf else ""
                    _p_rows_html += (
                        f'<div style="display:flex;align-items:flex-start;gap:10px;'
                        f'padding:6px 0;border-bottom:1px solid #1a3a1a;">'
                        f'<span style="font-size:18px;line-height:1;">&#128276;</span>'
                        f'<div style="flex:1;">'
                        f'<div style="font-size:12px;font-weight:700;color:#4caf50;">'
                        f'&#9650;&nbsp;{_p["name"]}</div>'
                        f'<div style="font-size:11px;color:#888;margin-top:1px;">'
                        f'{_p["description"]}{_nl_html}</div>'
                        f'{_cf_html}'
                        f'</div>'
                        f'<div style="text-align:right;white-space:nowrap;">'
                        f'<span style="font-size:11px;color:{_tfc};">{_p["timeframe"]}</span>'
                        f'&nbsp;<span style="font-size:14px;font-weight:700;color:{_sc};">'
                        f'{_pct}%</span></div>'
                        f'</div>'
                    )
                st.markdown(
                    f'<div style="background:#0a1f0a;border:1px solid #2a5a2a;'
                    f'border-radius:8px;padding:10px 16px;margin:4px 0;">'
                    f'<div style="font-size:11px;color:#4caf50;text-transform:uppercase;'
                    f'letter-spacing:1px;margin-bottom:6px;font-weight:700;">'
                    f'&#128276; Pattern Alerts</div>'
                    f'{_p_rows_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Clickable button — loads Volume Profile for this ticker
            if st.button(f"📊 Load {sym} Volume Profile", key=f"load_{sym}",
                         use_container_width=False):
                st.session_state["_load_ticker"] = sym
                st.session_state["_last_scan_load"] = sym
                st.session_state.auto_run = True
                st.rerun()

        _last = st.session_state.get("_last_scan_load", "")
        if _last:
            st.success(f"✅ **{_last}** loaded — click the **📈 Main Chart** tab to view the Volume Profile.")
        else:
            st.caption("Click a **Load** button to populate the ticker and switch to Main Chart.")

    # ── Watchlist Prediction Engine ────────────────────────────────────────────
    st.markdown("---")
    st.header("🔮 Watchlist Prediction Engine")
    st.caption(
        "Score every ticker in your saved watchlist, save the predictions, "
        "then verify next day to see how accurate the engine was."
    )

    # ── Supabase connectivity check ────────────────────────────────────────────
    _sb_ok = False
    if supabase:
        try:
            supabase.table("trade_journal").select("id").limit(1).execute()
            _sb_ok = True
        except Exception:
            _sb_ok = False
    if not _sb_ok:
        st.warning(
            "⚠️ **Supabase is offline** — predictions will score on-screen only "
            "and won't be saved. To enable saving, go to "
            "[supabase.com](https://supabase.com) → your project → "
            "**Restore / Resume** if paused. Once online, also run the table setup SQL "
            "in the expander below.",
            icon=None,
        )

    # ── Ticker source: saved watchlist OR direct input (fallback) ─────────────
    _saved_ticker_str = (
        st.session_state.get("_watchlist_tickers", "")
        or st.session_state.get("watchlist_textarea", "")
        or st.session_state.get("watchlist_raw", "")
    )
    _wpe_ticker_input = st.text_area(
        "Tickers to score (comma or newline separated)",
        value=_saved_ticker_str,
        height=60,
        key="wpe_ticker_input",
        placeholder="AAPL, GME, AMC — or paste from your watchlist",
        help="These tickers will be scored by the prediction engine. "
             "Auto-filled from your saved watchlist if available.",
    )
    _wpe_saved_tickers = [
        t.strip().upper()
        for t in _wpe_ticker_input.replace("\n", ",").split(",")
        if t.strip()
    ]
    _wpe_count = len(_wpe_saved_tickers)

    # ── Always-visible action buttons ─────────────────────────────────────────
    _wpe_feed = st.selectbox("Feed", ["sip", "iex"], key="wpe_feed_select",
                             help="SIP = full national tape (recommended). IEX = free tier fallback.")
    _wpe_col1, _wpe_col2, _wpe_col3 = st.columns(3)

    # Predict All — only active when tickers are entered
    if _wpe_count > 0:
        if _wpe_col1.button(f"🔮 Predict All ({_wpe_count})", use_container_width=True, key="wpe_predict_btn"):
            if not api_key or not secret_key:
                st.error("Add your Alpaca credentials in the sidebar first.")
            else:
                with st.spinner(f"Scoring {_wpe_count} tickers + generating setup briefs… (~45 s for large lists)"):
                    _wpe_rows = [{"ticker": t} for t in _wpe_saved_tickers]
                    import copy as _cp
                    import concurrent.futures as _cf
                    _wpe_scored = score_playbook_tickers(
                        _cp.deepcopy(_wpe_rows),
                        api_key, secret_key,
                        feed=_wpe_feed,
                        max_tickers=_wpe_count,
                        user_id=_AUTH_USER_ID,
                    )
                    # Target today's session if before market open (9:30 AM ET),
                    # otherwise target the next trading day so we don't label
                    # a prediction with a session that's already underway.
                    _now_et = datetime.now(EASTERN)
                    _mkt_open_et = _now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                    _wpe_as_of = date.today() if _now_et < _mkt_open_et else date.today() + timedelta(days=1)
                    _wpe_pred_date = get_next_trading_day(
                        as_of=_wpe_as_of,
                        api_key=api_key,
                        secret_key=secret_key,
                    )

                    # Generate full setup briefs in parallel (one per ticker)
                    def _gen_brief(_r):
                        return compute_setup_brief(
                            api_key, secret_key,
                            _r["ticker"],
                            pred_date=_wpe_pred_date,
                            user_id=_AUTH_USER_ID,
                            feed=_wpe_feed,
                        )

                    _wpe_briefs = {}
                    with _cf.ThreadPoolExecutor(max_workers=4) as _exec:
                        _futures = {
                            _exec.submit(_gen_brief, r): r["ticker"]
                            for r in _wpe_scored
                        }
                        for _f in _cf.as_completed(_futures):
                            _tk = _futures[_f]
                            try:
                                _b = _f.result()
                                if _b and "error" not in _b:
                                    _wpe_briefs[_tk] = _b
                            except Exception:
                                pass

                    _wpe_payload = []
                    for r in _wpe_scored:
                        _tk = r["ticker"]
                        _brief = _wpe_briefs.get(_tk, {})
                        _row = {
                            "ticker":              _tk,
                            "pred_date":           _wpe_pred_date,
                            "predicted_structure": r.get("structure") or "—",
                            "tcs":                 r.get("tcs") or 0,
                            "edge_score":          r.get("edge_score") or 0,
                        }
                        if _brief:
                            _row.update({
                                "entry_zone_low":   _brief.get("entry_zone_low"),
                                "entry_zone_high":  _brief.get("entry_zone_high"),
                                "entry_trigger":    _brief.get("entry_trigger") or "",
                                "stop_level":       _brief.get("stop_level"),
                                "targets":          _brief.get("targets") or [],
                                "pattern":          _brief.get("pattern") or "",
                                "pattern_neckline": _brief.get("pattern_neckline"),
                                "win_rate_pct":     _brief.get("win_rate_pct"),
                                "win_rate_context": _brief.get("win_rate_context") or "",
                                "confidence_label": _brief.get("confidence_label") or "LOW",
                                "rvol":             _brief.get("rvol"),
                            })
                        _wpe_payload.append(_row)

                    _wpe_ok = save_watchlist_predictions(_wpe_payload, user_id=_AUTH_USER_ID)
                    _wpe_brief_count = len(_wpe_briefs)
                    st.session_state["_wpe_last_predictions"] = _wpe_scored
                    st.session_state["_wpe_last_briefs"]      = _wpe_briefs
                    st.session_state["_wpe_pred_date"] = str(_wpe_pred_date)
                    if _wpe_ok:
                        st.success(
                            f"✅ {len(_wpe_payload)} predictions saved for {_wpe_pred_date}"
                            + (f" · Setup briefs generated for {_wpe_brief_count}/{len(_wpe_payload)} tickers"
                               if _wpe_brief_count else "")
                        )
                    else:
                        st.warning("Scored locally — Supabase table missing. See setup section below.")
    else:
        _wpe_col1.button("🔮 Predict All", use_container_width=True, key="wpe_predict_btn", disabled=True)
        st.caption("⬅ Add tickers in **⭐ My Watchlist** sidebar then Save to enable Predict All.")

    # ── Verify — date picker + button ──────────────────────────────────────────
    _verify_date = _wpe_col2.date_input(
        "Verify date",
        value=date.today() - timedelta(days=1),
        max_value=date.today() - timedelta(days=1),
        key="wpe_verify_date",
        label_visibility="collapsed",
    )
    if _wpe_col2.button("✅ Verify Date", use_container_width=True, key="wpe_verify_btn"):
        if not api_key or not secret_key:
            st.error("Add your Alpaca credentials in the sidebar first.")
        else:
            with st.spinner("Fetching end-of-day data and verifying predictions…"):
                _vr = verify_watchlist_predictions(
                    api_key, secret_key,
                    user_id=_AUTH_USER_ID,
                    pred_date=_verify_date,
                )
            st.session_state["_wpe_verify_result"] = _vr

    # ── Load Saved — always visible ────────────────────────────────────────────
    if _wpe_col3.button("📂 Load Saved", use_container_width=True, key="wpe_load_btn"):
        _wpe_df = _cached_load_watchlist_predictions(user_id=_AUTH_USER_ID)
        st.session_state["_wpe_loaded_df"] = _wpe_df

    # ── Show verify result ─────────────────────────────────────────────────────
    _vr = st.session_state.get("_wpe_verify_result")
    if _vr:
        if _vr.get("error") and _vr.get("verified", 0) == 0:
            st.warning(f"Verify: {_vr['error']}")
        else:
            _acc_color = "#4caf50" if _vr["accuracy"] >= 60 else "#ff9800" if _vr["accuracy"] >= 45 else "#ef5350"
            _vr_total  = _vr.get("total", _vr["verified"])
            _vr_wrong  = _vr["verified"] - _vr["correct"]
            try:
                _vr_date_fmt = date.fromisoformat(_vr.get("date","")).strftime("%b %-d, %Y")
            except Exception:
                _vr_date_fmt = _vr.get("date", "")
            # Show note if bar data came from a different (next) trading day
            _vr_bar_date  = _vr.get("bar_date", _vr.get("date", ""))
            _bar_note = ""
            if _vr_bar_date and _vr_bar_date != _vr.get("date", ""):
                try:
                    _bar_fmt  = date.fromisoformat(_vr_bar_date).strftime("%b %-d")
                    _bar_note = f" &nbsp;·&nbsp; bars from {_bar_fmt} (next trading day)"
                except Exception:
                    pass
            st.markdown(
                f'<div style="background:#12122299;border:1px solid #2a2a4a;border-radius:10px;'
                f'padding:14px 20px;margin:8px 0;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">'
                f'Verify Results — Pred Date: {_vr_date_fmt}{_bar_note}</div>'
                f'<div style="display:flex;gap:32px;align-items:center;flex-wrap:wrap;">'
                f'<div style="text-align:center;">'
                f'<div style="font-size:11px;color:#888;text-transform:uppercase;margin-bottom:2px;">Verified</div>'
                f'<div style="font-size:26px;font-weight:800;color:#e0e0e0;">{_vr["verified"]}'
                f'<span style="font-size:13px;color:#555;">/{_vr_total}</span></div>'
                f'<div style="font-size:9px;color:#555;">predictions checked</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:11px;color:#888;text-transform:uppercase;margin-bottom:2px;">Correct</div>'
                f'<div style="font-size:26px;font-weight:800;color:#4caf50;">{_vr["correct"]}</div>'
                f'<div style="font-size:9px;color:#555;">structure matched</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:11px;color:#888;text-transform:uppercase;margin-bottom:2px;">Wrong</div>'
                f'<div style="font-size:26px;font-weight:800;color:#ef5350;">{_vr_wrong}</div>'
                f'<div style="font-size:9px;color:#555;">structure missed</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:11px;color:#888;text-transform:uppercase;margin-bottom:2px;">Accuracy</div>'
                f'<div style="font-size:26px;font-weight:800;color:{_acc_color};">{_vr["accuracy"]:.1f}%</div>'
                f'<div style="font-size:9px;color:#555;">correct / verified</div>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Show predictions table ─────────────────────────────────────────────────
    _wpe_preds = st.session_state.get("_wpe_last_predictions")
    _wpe_df_loaded = st.session_state.get("_wpe_loaded_df")

    if _wpe_preds:
        _wpe_briefs_ss = st.session_state.get("_wpe_last_briefs", {})
        _wpe_for_date = st.session_state.get("_wpe_pred_date", "")
        try:
            _wpe_for_fmt = date.fromisoformat(str(_wpe_for_date)).strftime("%b %-d, %Y")
        except Exception:
            _wpe_for_fmt = str(_wpe_for_date)
        st.subheader(f"📋 Predictions for Trading Session: {_wpe_for_fmt}")
        st.caption("These are your predictions to validate when the market opens on that date. "
                   "Run ✅ Verify Date after close to check accuracy.")

        if _wpe_briefs_ss:
            # Rich setup brief cards (with key levels)
            _sc = {
                "Trending Up":       ("#4caf50", "▲"), "Trending Down":     ("#ef5350", "▼"),
                "At IB High":        ("#ffa726", "◆"), "At IB Low":         ("#29b6f6", "◆"),
                "Inside IB":         ("#9e9e9e", "◼"), "Extended Above IB":  ("#7c4dff", "▲"),
                "Extended Below IB": ("#ff5252", "▼"),
            }
            _cc = {"HIGH": ("#4caf50", "🟢"), "MODERATE": ("#ffa726", "🟡"), "LOW": ("#9e9e9e", "⚪")}
            for r in _wpe_preds:
                _tk = r["ticker"]
                b   = _wpe_briefs_ss.get(_tk, {})
                if not b:
                    continue
                _pstr = b.get("predicted_structure", r.get("structure", "—"))
                _pclr, _pico = _sc.get(_pstr, ("#888", "●"))
                _conf = b.get("confidence_label", "LOW")
                _cclr, _cico = _cc.get(_conf, ("#9e9e9e", "⚪"))
                _elo  = b.get("entry_zone_low")
                _ehi  = b.get("entry_zone_high")
                _stop = b.get("stop_level")
                _tgts = b.get("targets") or []
                _trig = str(b.get("entry_trigger") or "—")
                _pat  = str(b.get("pattern") or "")
                _nl   = b.get("pattern_neckline")
                _wrc  = str(b.get("win_rate_context") or "No calibration data yet.")
                _wrp  = b.get("win_rate_pct")
                # Key levels
                _pdh  = b.get("pdh"); _pdl = b.get("pdl"); _pdc = b.get("pdc")
                _onh  = b.get("onh"); _onl = b.get("onl")
                _rns  = b.get("round_numbers") or []
                _sh   = b.get("swing_highs") or []
                _sl   = b.get("swing_lows") or []
                _has_conf = b.get("has_confluence", False)
                _entry_str = (f"${_elo:.4f} – ${_ehi:.4f}"
                              if _elo is not None and _ehi is not None else "—")
                _stop_str  = f"${_stop:.4f}" if _stop is not None else "—"
                _tgt_str   = "  ·  ".join(f"R{i+1} ${t:.4f}" for i, t in enumerate(_tgts[:3]))
                _pat_str   = f"{_pat} · nkl ${_nl:.4f}" if _pat and _nl else (_pat or "")
                _wr_display = (f"  ·  {_cico} {_conf} ({_wrp:.0f}%)" if _wrp
                               else f"  ·  {_cico} {_conf}")
                # Key level badges string
                _kl_parts = []
                if _pdh: _kl_parts.append(f'<span style="background:#ef535022;border:1px solid #ef535044;'
                                           f'border-radius:3px;padding:1px 5px;font-size:9px;color:#ef5350;">PDH ${_pdh:.4f}</span>')
                if _pdl: _kl_parts.append(f'<span style="background:#4caf5022;border:1px solid #4caf5044;'
                                           f'border-radius:3px;padding:1px 5px;font-size:9px;color:#4caf50;">PDL ${_pdl:.4f}</span>')
                if _pdc: _kl_parts.append(f'<span style="background:#ffa72622;border:1px solid #ffa72644;'
                                           f'border-radius:3px;padding:1px 5px;font-size:9px;color:#ffa726;">PDC ${_pdc:.4f}</span>')
                if _onh: _kl_parts.append(f'<span style="background:#7c4dff22;border:1px solid #7c4dff44;'
                                           f'border-radius:3px;padding:1px 5px;font-size:9px;color:#ab82ff;">ONH ${_onh:.4f}</span>')
                if _onl: _kl_parts.append(f'<span style="background:#29b6f622;border:1px solid #29b6f644;'
                                           f'border-radius:3px;padding:1px 5px;font-size:9px;color:#29b6f6;">ONL ${_onl:.4f}</span>')
                for _rn in _rns[:3]:
                    _kl_parts.append(f'<span style="background:#37474f;border:1px solid #546e7a;'
                                     f'border-radius:3px;padding:1px 5px;font-size:9px;color:#90a4ae;">'
                                     f'${_rn:.2f} round</span>')
                for _sv in _sh[:2]:
                    _kl_parts.append(f'<span style="background:#ef535011;border:1px solid #ef535033;'
                                     f'border-radius:3px;padding:1px 5px;font-size:9px;color:#ef9a9a;">liq↑ ${_sv:.4f}</span>')
                for _sv in _sl[:2]:
                    _kl_parts.append(f'<span style="background:#4caf5011;border:1px solid #4caf5033;'
                                     f'border-radius:3px;padding:1px 5px;font-size:9px;color:#a5d6a7;">liq↓ ${_sv:.4f}</span>')
                _kl_html = ('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">'
                            + "".join(_kl_parts) + '</div>') if _kl_parts else ""
                _conf_badge = (' <span style="background:#ffa72633;border:1px solid #ffa72666;'
                               'border-radius:3px;padding:1px 6px;font-size:9px;color:#ffa726;">⭐ CONFLUENCE</span>'
                               if _has_conf else "")
                st.markdown(
                    f'<div style="background:#071b2e;border:1px solid #1e3a5f;border-left:4px solid {_pclr};'
                    f'border-radius:8px;padding:12px 16px;margin-bottom:10px;">'
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                    f'<span style="font-size:15px;font-weight:800;color:#fff;">{_tk}</span>'
                    f'<span style="background:{_pclr}22;border:1px solid {_pclr}66;border-radius:5px;'
                    f'padding:2px 8px;font-size:11px;color:{_pclr};">{_pico} {_pstr}</span>'
                    + (f'<span style="background:#1a1a1a;border:1px solid #333;border-radius:5px;'
                       f'padding:2px 8px;font-size:10px;color:#aaa;">{_pat_str}</span>' if _pat_str else "")
                    + f'<span style="font-size:10px;color:#666;margin-left:4px;">TCS {b.get("tcs",0):.0f}'
                    f'  RVOL {b.get("rvol_band","")}</span>'
                    + _conf_badge
                    + f'</div>'
                    + _kl_html
                    + f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Entry Zone</div>'
                    f'<div style="font-size:12px;color:#29b6f6;font-weight:600;">{_entry_str}</div></div>'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Stop</div>'
                    f'<div style="font-size:12px;color:#ef5350;font-weight:600;">{_stop_str}</div></div>'
                    f'<div style="background:#0d2a42;border-radius:5px;padding:6px 10px;">'
                    f'<div style="font-size:9px;color:#555;text-transform:uppercase;margin-bottom:3px;">Targets</div>'
                    f'<div style="font-size:11px;color:#4caf50;font-weight:600;">{_tgt_str or "—"}</div></div></div>'
                    f'<div style="font-size:10px;color:#aaa;margin-bottom:4px;line-height:1.4;">'
                    f'<span style="color:#ffa726;font-weight:600;">Trigger:</span> {_trig}</div>'
                    f'<div style="font-size:10px;color:#7986cb;line-height:1.4;">'
                    f'<span style="font-weight:600;">Win Rate:</span> {_wrc}{_wr_display}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            # Fallback simple table when briefs not available
            _wpe_display = []
            for r in _wpe_preds:
                _tcs_v = r.get("tcs")
                _edg_v = r.get("edge_score")
                _wpe_display.append({
                    "Ticker":    r["ticker"],
                    "Structure": r.get("structure") or "—",
                    "TCS":       f"{_tcs_v:.0f}" if _tcs_v is not None else "—",
                    "Edge":      f"{_edg_v:.0f}" if _edg_v is not None else "—",
                })
            st.dataframe(
                _wpe_display,
                use_container_width=True,
                height=min(400, 35 + 35 * len(_wpe_display)),
            )

    elif _wpe_df_loaded is not None and not _wpe_df_loaded.empty:
        st.subheader("📂 Saved Predictions")
        st.caption("Grouped by trading session date — most recent first. "
                   "TCS and Edge are blank for rows saved before the Supabase schema update.")

        _show_cols = ["ticker", "predicted_structure", "tcs", "edge_score",
                      "actual_structure", "correct"]
        _grp_df = _wpe_df_loaded.copy()

        # Normalize pred_date to string for grouping
        if "pred_date" in _grp_df.columns:
            _grp_df["pred_date"] = _grp_df["pred_date"].astype(str)
            _all_dates = sorted(_grp_df["pred_date"].unique(), reverse=True)
        else:
            _all_dates = ["(unknown)"]
            _grp_df["pred_date"] = "(unknown)"

        for _sess_date in _all_dates:
            try:
                _sess_fmt = date.fromisoformat(_sess_date).strftime("%b %-d, %Y")
            except Exception:
                _sess_fmt = _sess_date

            _sess_df = _grp_df[_grp_df["pred_date"] == _sess_date].copy()
            _n_rows  = len(_sess_df)
            _n_corr  = (_sess_df.get("correct", pd.Series()) == "✅").sum() if "correct" in _sess_df.columns else 0
            _n_ver   = (_sess_df.get("correct", pd.Series()).isin(["✅", "❌"])).sum() if "correct" in _sess_df.columns else 0
            _acc_str = f" · {_n_corr}/{_n_ver} correct" if _n_ver > 0 else " · unverified"

            # Most recent date auto-expanded
            _is_next = (_sess_date == _all_dates[0])
            with st.expander(
                f"📅 Session: {_sess_fmt}  —  {_n_rows} ticker{'s' if _n_rows != 1 else ''}{_acc_str}",
                expanded=_is_next,
            ):
                _disp_df = _sess_df[[c for c in _show_cols if c in _sess_df.columns]].copy()
                _col_rename = {
                    "ticker": "Ticker",
                    "predicted_structure": "Predicted Structure",
                    "tcs": "TCS",
                    "edge_score": "Edge",
                    "actual_structure": "Actual Structure",
                    "correct": "Correct?",
                }
                _disp_df.rename(columns=_col_rename, inplace=True)
                if "TCS" in _disp_df.columns:
                    _disp_df["TCS"] = _disp_df["TCS"].apply(
                        lambda x: f"{x:.0f}" if pd.notna(x) and x != "" else "—"
                    )
                if "Edge" in _disp_df.columns:
                    _disp_df["Edge"] = _disp_df["Edge"].apply(
                        lambda x: f"{x:.0f}" if pd.notna(x) and x != "" else "—"
                    )
                st.dataframe(
                    _disp_df, use_container_width=True,
                    height=min(400, 40 + 36 * len(_disp_df)),
                    hide_index=True,
                )
    elif _wpe_df_loaded is not None:
        st.info("No saved predictions found. Run **🔮 Predict All** first.")

    # ── Setup instructions if table missing ───────────────────────────────────
    with st.expander("⚙️ First-time Supabase setup for predictions", expanded=False):
        st.caption("**New table** — run this SQL once in your Supabase SQL editor to enable predictions + setup briefs:")
        st.code(
            "CREATE TABLE IF NOT EXISTS watchlist_predictions (\n"
            "  id                 BIGSERIAL PRIMARY KEY,\n"
            "  user_id            TEXT,\n"
            "  ticker             TEXT,\n"
            "  pred_date          DATE,\n"
            "  predicted_structure TEXT,\n"
            "  tcs                FLOAT,\n"
            "  edge_score         FLOAT,\n"
            "  actual_structure   TEXT DEFAULT '',\n"
            "  verified           BOOLEAN DEFAULT FALSE,\n"
            "  correct            TEXT DEFAULT '',\n"
            "  entry_zone_low     FLOAT,\n"
            "  entry_zone_high    FLOAT,\n"
            "  entry_trigger      TEXT DEFAULT '',\n"
            "  stop_level         FLOAT,\n"
            "  targets            JSONB,\n"
            "  pattern            TEXT DEFAULT '',\n"
            "  pattern_neckline   FLOAT,\n"
            "  win_rate_pct       FLOAT,\n"
            "  win_rate_context   TEXT DEFAULT '',\n"
            "  confidence_label   TEXT DEFAULT 'LOW',\n"
            "  UNIQUE(user_id, ticker, pred_date)\n"
            ");",
            language="sql",
        )
        st.caption("**Existing table migration** — if you already have the table, run this to add the setup brief columns:")
        st.code(
            "ALTER TABLE watchlist_predictions\n"
            "  ADD COLUMN IF NOT EXISTS entry_zone_low     FLOAT,\n"
            "  ADD COLUMN IF NOT EXISTS entry_zone_high    FLOAT,\n"
            "  ADD COLUMN IF NOT EXISTS entry_trigger      TEXT DEFAULT '',\n"
            "  ADD COLUMN IF NOT EXISTS stop_level         FLOAT,\n"
            "  ADD COLUMN IF NOT EXISTS targets            JSONB,\n"
            "  ADD COLUMN IF NOT EXISTS pattern            TEXT DEFAULT '',\n"
            "  ADD COLUMN IF NOT EXISTS pattern_neckline   FLOAT,\n"
            "  ADD COLUMN IF NOT EXISTS win_rate_pct       FLOAT,\n"
            "  ADD COLUMN IF NOT EXISTS win_rate_context   TEXT DEFAULT '',\n"
            "  ADD COLUMN IF NOT EXISTS confidence_label   TEXT DEFAULT 'LOW';",
            language="sql",
        )
        st.caption("Go to supabase.com → your project → SQL Editor → paste and run.")

# ── Volume Profile tab: auto_run + all historical/live content ─────────────────
# Consume the auto-run flag set by scanner ticker buttons (runs before tab renders)
auto_trigger = st.session_state.get("auto_run", False)
if auto_trigger:
    st.session_state.auto_run = False

with tab_chart:
    # ── Historical mode ────────────────────────────────────────────────────────
    if mode == "📅 Historical":
        if run_button or auto_trigger:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday (Mon–Fri).")
            else:
                # Fresh analysis — reset alert state so alerts fire for this data
                st.session_state.tcs_fired_high = False
                st.session_state.tcs_was_high = False
                with st.spinner(f"Fetching 1-min bars for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        df = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if df.empty:
                            now_et = datetime.now(EASTERN)
                            pre_mkt = (selected_date >= now_et.date() and
                                       now_et <= EASTERN.localize(datetime(selected_date.year, selected_date.month, selected_date.day, 9, 30)))
                            if pre_mkt:
                                st.warning("Market hasn't opened yet (9:30 AM ET). Come back once trading starts and bars are available.")
                            elif data_feed == "sip":
                                st.warning(f"No data for **{ticker}** on {selected_date} via SIP. Try switching to IEX, or confirm the date was a trading day.")
                            else:
                                st.warning(f"No data for **{ticker}** on {selected_date} via IEX. Small-caps may be absent on IEX — try SIP.")
                        else:
                            st.success(f"Loaded **{len(df)}** 1-min bars via {data_feed.upper()}.")
                            # ── Pre-fetch RVOL baseline (5-day avg daily volume) ──────
                            try:
                                avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                avg_vol = None
                            st.session_state.rvol_avg_vol = avg_vol

                            # ── Time-segmented RVOL intraday curve ───────────────────
                            try:
                                curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=50, feed=data_feed)
                            except Exception:
                                curve = None
                            st.session_state.rvol_intraday_curve = curve

                            # ── Sector ETF % change for that date ────────────────────
                            try:
                                etf_chg = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                            except Exception:
                                etf_chg = 0.0
                            sector_bonus = 10.0 if etf_chg > 1.0 else 0.0
                            st.session_state.sector_pct_chg = etf_chg

                            render_analysis(df, num_bins, ticker,
                                            f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')}",
                                            avg_daily_vol=avg_vol,
                                            sector_bonus=sector_bonus,
                                            sector_etf=sector_etf,
                                            intraday_curve=curve,
                                            is_live=False)
                            render_log_entry_ui()
                    except Exception as e:
                        err = str(e)
                        if "forbidden" in err.lower() or "403" in err or "unauthorized" in err.lower():
                            st.error("Authentication failed — check your API Key and Secret Key.")
                        elif "subscription" in err.lower() or "not entitled" in err.lower() or "422" in err:
                            if data_feed == "sip":
                                st.warning("SIP feed requires a paid Alpaca subscription. Retrying with IEX…")
                                try:
                                    df2 = fetch_bars(api_key, secret_key, ticker, selected_date, feed="iex")
                                    if df2.empty:
                                        st.error(f"No data for **{ticker}** on {selected_date} via IEX either. Confirm the date was a trading day and the ticker is valid.")
                                    else:
                                        st.success(f"Loaded **{len(df2)}** 1-min bars via IEX (auto-switched from SIP).")
                                        render_analysis(df2, num_bins, ticker,
                                                        f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')} (IEX)",
                                                        avg_daily_vol=None, sector_bonus=0.0,
                                                        sector_etf=sector_etf, intraday_curve=None,
                                                        is_live=False)
                                        render_log_entry_ui()
                                except Exception as e2:
                                    st.error(f"IEX fallback also failed: {e2}")
                            else:
                                st.error("Not subscribed to IEX feed — check your Alpaca account.")
                        else:
                            st.error(f"Error: {err}")
        else:
            # If a previous analysis exists (e.g. user clicked LOG ENTRY), show
            # the log panel instead of resetting to the welcome screen.
            if st.session_state.get("last_analysis_state"):
                render_log_entry_ui()
            else:
                st.info("👈 Enter credentials and settings in the sidebar, then click **Fetch & Analyze**.")
                st.markdown("### How it works")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**📊 Volume Profile**")
                    st.markdown("Bins the day's 1-min bars to show where volume concentrated.")
                with c2:
                    st.markdown("**🎯 Point of Control**")
                    st.markdown("Gold line — the price level with the highest volume. Price gravitates here.")
                with c3:
                    st.markdown("**📐 Initial Balance**")
                    st.markdown("9:30–10:30 EST High/Low — the key reference range for day structure.")

    # ── Replay mode ────────────────────────────────────────────────────────────
    elif mode == "🎬 Replay":
        # ── Load full-day bars on demand ──────────────────────────────────────
        if replay_load:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday.")
            else:
                with st.spinner(f"Loading full day for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        _rdf = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if _rdf.empty:
                            st.error("No bars returned. Check the ticker/date/feed.")
                        else:
                            st.session_state.replay_bars    = _rdf
                            st.session_state.replay_bar_idx = 0
                            st.session_state.replay_playing = False
                            st.session_state.replay_ticker  = ticker
                            st.session_state.replay_date    = selected_date
                            # Pre-fetch baselines for the replay session
                            try:
                                st.session_state.replay_avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                st.session_state.replay_avg_vol = None
                            try:
                                st.session_state.replay_intraday_curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=50, feed=data_feed)
                            except Exception:
                                st.session_state.replay_intraday_curve = None
                            try:
                                _etf = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                                st.session_state.replay_sector_bonus = 10.0 if _etf > 1.0 else 0.0
                            except Exception:
                                st.session_state.replay_sector_bonus = 0.0
                            # Reset Brain state for fresh replay
                            for _bk in ("brain_ib_high", "brain_ib_low", "brain_ib_set",
                                        "brain_high_touched", "brain_low_touched", "brain_predicted"):
                                st.session_state[_bk] = _DEFAULTS[_bk]
                            st.success(f"✅ Loaded {len(_rdf)} bars — use the slider and controls in the sidebar to replay.")
                            st.rerun()
                    except Exception as _e:
                        st.error(f"Load error: {_e}")

        # ── Run analysis for current replay bar ───────────────────────────────
        if st.session_state.replay_bars is not None:
            _rdf    = st.session_state.replay_bars
            _ridx   = st.session_state.replay_bar_idx
            _rtk    = st.session_state.replay_ticker
            _rdate  = st.session_state.replay_date
            _df_now = _rdf.iloc[:_ridx + 1]   # only bars up to current time

            _cur_et  = pd.Timestamp(_rdf.index[_ridx]).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rdf.index[-1]).tz_convert(EASTERN)

            # Clock banner
            st.markdown(
                f'<div style="background:#0f3460; border:1px solid #5c6bc0; border-radius:8px; '
                f'padding:10px 20px; margin-bottom:10px; display:flex; align-items:center; gap:16px;">'
                f'<span style="font-size:22px; font-family:monospace; color:#90caf9; font-weight:700;">'
                f'🎬 {_cur_et.strftime("%H:%M")} ET</span>'
                f'<span style="font-size:12px; color:#888;">Bar {_ridx + 1} / {len(_rdf)} &nbsp;|&nbsp; '
                f'{_rtk} &nbsp;|&nbsp; {_rdate}</span>'
                f'<span style="font-size:11px; color:#555; margin-left:auto;">'
                f'through {_total_et.strftime("%H:%M")} ET total</span></div>',
                unsafe_allow_html=True,
            )

            if len(_df_now) < 2:
                st.info("Need at least 2 bars for a full analysis — advance the slider forward.")
            else:
                render_analysis(
                    _df_now, num_bins, _rtk,
                    f"🎬 Replay — {_rtk} | {_cur_et.strftime('%H:%M ET')} | {_rdate}",
                    is_ib_live=(_cur_et.time() <= dtime(10, 30)),
                    avg_daily_vol=st.session_state.replay_avg_vol,
                    sector_bonus=st.session_state.replay_sector_bonus,
                    sector_etf=sector_etf,
                    intraday_curve=st.session_state.replay_intraday_curve,
                    is_live=False,
                )
                render_log_entry_ui()
        else:
            st.info("👈 Pick a date and click **📥 Load Day for Replay** in the sidebar.")
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**How Replay works**")
                st.markdown(
                    "- Fetches the full day's bars once\n"
                    "- Shows only bars up to the selected time\n"
                    "- All analysis (Volume Profile, Structure, TCS, Brain) updates in real-time\n"
                    "- Use the slider to jump to any moment, or ▶ Play to auto-advance"
                )
            with rc2:
                st.markdown("**Uses**")
                st.markdown(
                    "- Review your trade decisions at the exact moment you entered\n"
                    "- See how the IB formed bar by bar\n"
                    "- Practice reading structure before it becomes obvious\n"
                    "- Study how RVOL evolved during the session"
                )

    # ── Live mode ──────────────────────────────────────────────────────────────
    else:
        if start_live:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials first.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            else:
                today = date.today()

                # ── Step 1: Pre-load today's historical bars (9:30 AM → now) ──
                _hist_bars: list = []
                with st.spinner(
                    f"Pre-loading today's session bars for {ticker} — "
                    "IB, VWAP, volume profile, and TCS will be fully seeded at start…"
                ):
                    try:
                        _hist_df = fetch_bars(api_key, secret_key, ticker,
                                              today, feed=live_feed)
                        if not _hist_df.empty:
                            for _ts, _row in _hist_df.iterrows():
                                _hist_bars.append({
                                    "open":      float(_row["open"]),
                                    "high":      float(_row["high"]),
                                    "low":       float(_row["low"]),
                                    "close":     float(_row["close"]),
                                    "volume":    float(_row["volume"]),
                                    "timestamp": _ts,
                                })
                    except Exception:
                        _hist_bars = []

                # ── Step 2: RVOL baseline and sector ETF ───────────────────────
                with st.spinner("Computing RVOL baseline & sector data…"):
                    try:
                        avg_vol = fetch_avg_daily_volume(api_key, secret_key, ticker, today)
                    except Exception:
                        avg_vol = None
                    st.session_state.rvol_avg_vol = avg_vol

                    try:
                        curve = build_rvol_intraday_curve(
                            api_key, secret_key, ticker, today,
                            lookback_days=50, feed=live_feed)
                    except Exception:
                        curve = None
                    st.session_state.rvol_intraday_curve = curve

                    try:
                        etf_chg = fetch_etf_pct_change(
                            api_key, secret_key, sector_etf, today, feed=live_feed)
                    except Exception:
                        etf_chg = 0.0
                    st.session_state.sector_pct_chg = etf_chg

                # ── Step 3: Start WebSocket — seeded with full day context ─────
                start_stream(api_key, secret_key, ticker, live_feed,
                             historical_bars=_hist_bars if _hist_bars else None)
                if _hist_bars:
                    st.success(
                        f"✅ Seeded {len(_hist_bars)} bars from today's session — "
                        "IB, VWAP, and volume profile are fully loaded."
                    )
                st.rerun()

        if stop_live:
            stop_stream()
            st.rerun()

        if st.session_state.live_error:
            err = st.session_state.live_error
            if "forbidden" in err.lower() or "unauthorized" in err.lower():
                st.error(f"Auth error: {err}. Check your API credentials.")
            elif "subscription" in err.lower() or "entitled" in err.lower():
                st.error("Subscription error: SIP requires an Alpaca data plan. Switch to IEX.")
            else:
                st.error(f"Stream error: {err}")

        if st.session_state.live_active:
            drain_queue()
            df = build_live_df()
            now_est = datetime.now(EASTERN)
            is_ib_live = now_est.time() <= dtime(10, 30)

            if df.empty:
                if now_est.time() < dtime(9, 30):
                    mins = int((datetime.combine(date.today(), dtime(9, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60)
                    st.info(f"⏳ Market opens in ~{mins} min (9:30 AM EST). WebSocket connected, waiting...")
                elif now_est.time() > dtime(16, 0):
                    st.warning("Market is closed. No new data will arrive until tomorrow's session.")
                else:
                    st.info(f"🔌 Connected. Waiting for first trade on **{st.session_state.live_ticker}**... "
                            f"({now_est.strftime('%H:%M:%S')} EST)")
            else:
                chart_title = (f"🔴 LIVE — {st.session_state.live_ticker} | "
                               f"{now_est.strftime('%H:%M:%S')} EST"
                               + (" | 📐 IB FORMING" if is_ib_live else ""))
                live_sector_bonus = (10.0
                                     if st.session_state.get("sector_pct_chg", 0.0) > 1.0
                                     else 0.0)
                render_analysis(df, num_bins, st.session_state.live_ticker,
                                chart_title, is_ib_live=is_ib_live,
                                avg_daily_vol=st.session_state.get("rvol_avg_vol"),
                                sector_bonus=live_sector_bonus,
                                sector_etf=sector_etf,
                                intraday_curve=st.session_state.get("rvol_intraday_curve"),
                                is_live=True)
                render_log_entry_ui()
        else:
            if not st.session_state.live_error:
                st.info("👈 Enter credentials and ticker, then click **▶ Start Live Stream**.")
                st.markdown("### What Live Mode does")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown("**🔌 WebSocket Feed**")
                    st.markdown("Subscribes to real-time trades + 1-min bars from Alpaca.")
                with c2:
                    st.markdown("**📐 Dynamic IB**")
                    st.markdown("IB High/Low lines expand in real time from 9:30–10:30 AM EST.")
                with c3:
                    st.markdown("**⚡ Vol Velocity**")
                    st.markdown("Compares recent vol/min to prior bars — early breakout signal.")
                with c4:
                    st.markdown("**🎯 Probabilities**")
                    st.markdown("Every structure type scored continuously as the tape develops.")

# ── Playbook tab ──────────────────────────────────────────────────────────────
with tab_playbook:
    render_playbook_tab(api_key=api_key, secret_key=secret_key)

# ── Backtest tab ───────────────────────────────────────────────────────────────
with tab_backtest:
    render_backtest_tab(api_key=api_key, secret_key=secret_key)

# ── Journal tab ───────────────────────────────────────────────────────────────
with tab_journal:
    render_journal_tab(api_key=api_key, secret_key=secret_key)

# ── Analytics tab ─────────────────────────────────────────────────────────────
with tab_analytics:
    render_analytics_tab()

# ── Small Account Challenge tab ────────────────────────────────────────────────
with tab_sa:
    render_sa_tab()

# ── Paper Trade tab ────────────────────────────────────────────────────────────
with tab_paper:
    render_paper_trade_tab(api_key=api_key, secret_key=secret_key)

# ── Performance tab ─────────────────────────────────────────────────────────────
with tab_perf:
    render_performance_tab()

# ── Decision Log tab ─────────────────────────────────────────────────────────────
with tab_decision:
    render_decision_log_tab()

# ── Auto-refresh loop for live mode ───────────────────────────────────────────
if mode == "🔴 Live Stream" and st.session_state.live_active:
    time.sleep(2)
    st.rerun()

# ── Paper Trade live auto-scan loop ───────────────────────────────────────────
if st.session_state.get("_pt_live_mode"):
    _pt_now      = datetime.now(EASTERN)
    _pt_mkt_on   = (
        _pt_now.weekday() < 5
        and _pt_now.replace(hour=9, minute=30, second=0, microsecond=0)
        <= _pt_now
        <= _pt_now.replace(hour=16, minute=0, second=0, microsecond=0)
    )
    if _pt_mkt_on:
        _pt_last_ts  = st.session_state.get("_pt_last_auto_scan", 0)
        _pt_elapsed  = _pt_now.timestamp() - _pt_last_ts
        if _pt_elapsed >= 1800:
            _pt_at  = st.session_state.get("pt_scan_date", date.today())
            _pt_tkrs = [
                t.strip().upper()
                for t in st.session_state.get("pt_tickers", "").replace("\n", ",").split(",")
                if t.strip() and t.strip().isalpha()
            ]
            _pt_min  = st.session_state.get("pt_min_tcs", 50)
            _pt_fd   = "sip" if "SIP" in st.session_state.get("pt_feed", "SIP") else "iex"
            _pt_pr   = st.session_state.get("pt_price_range", (1.0, 20.0))
            if _pt_tkrs and api_key and secret_key:
                _auto_res, _auto_sum = run_historical_backtest(
                    api_key, secret_key,
                    trade_date=_pt_at,
                    tickers=_pt_tkrs,
                    feed=_pt_fd,
                    price_min=float(_pt_pr[0]),
                    price_max=float(_pt_pr[1]),
                    slippage_pct=0.75,
                )
                _auto_regime_tag = (st.session_state.get("breadth_regime") or {}).get("regime_tag")
                _auto_q = [
                    dict(r, sim_date=str(_pt_at), **({"regime_tag": _auto_regime_tag} if _auto_regime_tag else {}))
                    for r in _auto_res
                    if float(r.get("tcs", 0)) >= _pt_min
                ]
                if _auto_q:
                    log_paper_trades(_auto_q, user_id=_AUTH_USER_ID, min_tcs=_pt_min)
                st.session_state["_pt_last_auto_scan"] = _pt_now.timestamp()
                st.session_state.pop("pt_tracker_df", None)
        time.sleep(60)
        st.rerun()

# ── Replay auto-advance loop ──────────────────────────────────────────────────
if (mode == "🎬 Replay"
        and st.session_state.replay_playing
        and st.session_state.replay_bars is not None):
    _rdf_all = st.session_state.replay_bars
    _max     = len(_rdf_all) - 1
    _nxt     = min(_max, st.session_state.replay_bar_idx + st.session_state.replay_speed)
    if _nxt >= _max:
        st.session_state.replay_playing = False   # reached end, stop
    st.session_state.replay_bar_idx = _nxt
    time.sleep(0.5)   # ~2 bars/sec at Normal speed
    st.rerun()
