import time
import streamlit as st


def _auto_dismiss_success(key, msg=None, icon="✅", seconds=3.0):
    """Show a timed success banner and rerun when it expires.

    The session-state value at ``key`` may be either:
    - a **float** timestamp  →  ``st.session_state[key] = time.time()``
    - a **dict** with ``"at"`` (timestamp) and ``"msg"`` (message) keys
      →  ``st.session_state[key] = {"at": time.time(), "msg": "..."}``

    Pass ``msg`` to override any message stored in the dict.
    Pass ``icon=None`` to omit the icon from the success banner.
    Adding a new auto-dismiss success takes 2 lines: set the key, then call this.
    """
    val = st.session_state.get(key)
    if not val:
        return
    if isinstance(val, dict):
        saved_at = val["at"]
        if msg is None:
            msg = val.get("msg", "Saved.")
    else:
        saved_at = val
        if msg is None:
            msg = "Saved."
    elapsed = time.time() - saved_at
    if elapsed < seconds:
        if icon:
            st.success(msg, icon=icon)
        else:
            st.success(msg)
        time.sleep(seconds - elapsed)
        del st.session_state[key]
        st.rerun()
    else:
        del st.session_state[key]


def _render_copy_link_button(btn_id: str = "copy-link-btn") -> None:
    """Render a 🔗 Copy link button that copies window.parent.location.href to clipboard.

    Each call site should pass a unique *btn_id* so that multiple buttons on the
    same page don't share the same DOM id or JS function name.

    Includes a legacy-copy fallback (execCommand) and a manual-copy text field for
    browsers/contexts where the Clipboard API is blocked.
    """
    import streamlit.components.v1 as _cmp_v1
    _safe_id = btn_id.replace("-", "_")
    _fallback_id = f"{btn_id}-fallback"
    _field_id = f"{btn_id}-field"
    _cmp_v1.html(
        f"""
        <div style="font-family:sans-serif;">
        <button id="{btn_id}" onclick="copyLink_{_safe_id}()" style="
            display:inline-flex;align-items:center;gap:6px;
            padding:4px 10px;font-size:12px;cursor:pointer;
            border:1px solid #555;border-radius:6px;
            background:#1e1e2e;color:#cdd6f4;
            font-family:sans-serif;transition:background 0.2s;">
          \U0001f517 Copy link
        </button>
        <div id="{_fallback_id}" style="display:none;margin-top:6px;">
          <input id="{_field_id}" type="text" readonly style="
            width:100%;box-sizing:border-box;
            padding:4px 8px;font-size:12px;
            background:#1e1e2e;color:#cdd6f4;
            border:1px solid #a6e3a1;border-radius:6px;
            font-family:sans-serif;" />
          <div style="font-size:11px;color:#a6e3a1;margin-top:3px;">
            \U0001f4cb Tap the field above and copy the URL manually.
          </div>
        </div>
        </div>
        <script>
        function showBtn_{_safe_id}(text, color, borderColor) {{
            var btn = document.getElementById('{btn_id}');
            btn.textContent = text;
            btn.style.color = color;
            btn.style.borderColor = borderColor;
        }}
        function resetBtn_{_safe_id}() {{
            var btn = document.getElementById('{btn_id}');
            btn.innerHTML = '\U0001f517 Copy link';
            btn.style.color = '#cdd6f4';
            btn.style.borderColor = '#555';
        }}
        function showFallback_{_safe_id}(url) {{
            var box = document.getElementById('{_fallback_id}');
            var field = document.getElementById('{_field_id}');
            field.value = url;
            box.style.display = 'block';
            field.focus();
            field.select();
            setTimeout(function() {{ box.style.display = 'none'; }}, 8000);
        }}
        function legacyCopy_{_safe_id}(url) {{
            var ta = document.createElement('textarea');
            ta.value = url;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            var ok = false;
            try {{ ok = document.execCommand('copy'); }} catch(e) {{}}
            document.body.removeChild(ta);
            return ok;
        }}
        function copyLink_{_safe_id}() {{
            var url = window.parent.location.href;
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(url).then(function() {{
                    showBtn_{_safe_id}('\u2713 Copied!', '#a6e3a1', '#a6e3a1');
                    setTimeout(resetBtn_{_safe_id}, 1500);
                }}).catch(function() {{
                    if (legacyCopy_{_safe_id}(url)) {{
                        showBtn_{_safe_id}('\u2713 Copied!', '#a6e3a1', '#a6e3a1');
                        setTimeout(resetBtn_{_safe_id}, 1500);
                    }} else {{
                        showFallback_{_safe_id}(url);
                        resetBtn_{_safe_id}();
                    }}
                }});
            }} else {{
                if (legacyCopy_{_safe_id}(url)) {{
                    showBtn_{_safe_id}('\u2713 Copied!', '#a6e3a1', '#a6e3a1');
                    setTimeout(resetBtn_{_safe_id}, 1500);
                }} else {{
                    showFallback_{_safe_id}(url);
                }}
            }}
        }}
        </script>
        """,
        height=100,
    )


def _render_config_health_badge() -> None:
    """Show a ⚠ Config issues badge in the sidebar when bad env vars are detected.

    Absent when all variables are correctly configured.  When issues exist the
    badge is rendered as a Streamlit expander so the operator can click to see
    exactly which variables are affected and what defaults are being used.
    """
    from log_utils import get_config_issues
    _issues = get_config_issues()
    if not _issues:
        return

    _n = len(_issues)
    _label = "issue" if _n == 1 else "issues"
    with st.sidebar.expander(
        f"⚠️ Config {_label} detected ({_n})",
        expanded=False,
    ):
        st.caption(
            "The following environment variables were set to invalid values. "
            "The defaults shown below are being used instead."
        )
        for _issue in _issues:
            st.markdown(
                f"**`{_issue['name']}`**  \n"
                f"Bad value: `{_issue['bad_value']!r}`  \n"
                f"Default used: `{_issue['default']}`  \n"
                f"_{_issue['description']}_"
            )
            st.divider()


def _render_setup_checklist() -> None:
    """Render the 🔐 Setup Checklist content (sidebar expander body)."""
    import streamlit as _st
    from backend import (
        _startup_errors,
        _SECRET_CATALOG,
        _secret_statuses,
        _runtime_credential_errors,
        recheck_secret_statuses,
        check_credentials_runtime,
        get_runtime_last_check_ts,
    )
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

        if "_cred_check_interval_min" not in _st.session_state:
            _st.session_state["_cred_check_interval_min"] = 5
        _st.number_input(
            "Auto-check interval (minutes)",
            min_value=1,
            max_value=60,
            step=1,
            help="How often the background thread re-validates Alpaca & Supabase credentials. Changes take effect on the next app rerun.",
            key="_cred_check_interval_min",
        )
        _col_rt_caption, _col_rt_btn = _st.columns([3, 1])
        with _col_rt_caption:
            _st.caption(
                "**Force a live credential validation** against Alpaca & Supabase right now, "
                "without waiting for the background cycle."
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

        import time as _time
        _last_ts = get_runtime_last_check_ts()
        if _last_ts > 0.0:
            _elapsed_s = _time.monotonic() - _last_ts
            if _elapsed_s < 60:
                _age_label = "just now"
            elif _elapsed_s < 3600:
                _age_label = f"{int(_elapsed_s // 60)} min ago"
            else:
                _age_label = f"{int(_elapsed_s // 3600)} hr ago"
            if _runtime_credential_errors:
                _n_errs = len(_runtime_credential_errors)
                _err_word = "error" if _n_errs == 1 else "errors"
                _st.caption(f"Last checked: {_age_label} — {_n_errs} {_err_word}")
            else:
                _st.caption(f"Last checked: {_age_label} — all OK ✅")
        else:
            _st.caption("Last checked: not yet run")

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
