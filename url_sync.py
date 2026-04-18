"""url_sync — shared helpers for the URL-query-param ↔ session-state guard pattern.

Every URL-backed Streamlit control follows the same two-step protocol:

  PRE-WIDGET  (guard + initialise from QP)
    _url_init_*(qp_key, ss_key, default, ...)

  POST-WIDGET (write-back to QP + update guard)
    _url_push(qp_key, value_str, ...)
    _url_push_opt(qp_key, value_str, ...)   # deletes QP when value_str is falsy

The _last_url sentinel key is derived automatically as ``f"_{qp_key}_last_url"``
unless an explicit ``last_url_key`` override is supplied (needed for a small
number of legacy names that don't follow this convention).

Usage example (string / select):
    _url_init_str("hist_scan_feed", "hist_scan_feed", _gdf, allowed=_OPTS)
    data_feed = st.selectbox("Data Feed", _OPTS, key="hist_scan_feed")
    _url_push("hist_scan_feed", data_feed)
"""

import streamlit as st


def _url_init_str(qp_key, ss_key, default="", *, allowed=None, last_url_key=None):
    """Guard-initialise a string/select session-state key from URL query params.

    Parameters
    ----------
    qp_key       : URL query-param name.
    ss_key       : ``st.session_state`` key for the widget.
    default      : Fallback value when the QP is absent or invalid.
    allowed      : Optional iterable of valid values; QP outside this set
                   is replaced with ``default``.
    last_url_key : Override for the sentinel key (defaults to
                   ``f"_{qp_key}_last_url"``).

    Returns the validated QP string so the caller can use it for
    conditional logic if needed.
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_val = st.query_params.get(qp_key, default)
    if allowed is not None and qp_val not in allowed:
        qp_val = default
    if ss_key not in st.session_state or st.session_state.get(_luk) != qp_val:
        st.session_state[ss_key] = qp_val
        st.session_state[_luk] = qp_val
    return qp_val


def _url_init_str_pref(qp_key, ss_key, default="", *, pref_val="",
                       pref_first=False, allowed=None, last_url_key=None):
    """Guard-initialise a string/select key with a saved-preference fallback.

    On first load (``ss_key`` not in ``st.session_state``):
        ``pref_first=False`` (URL-first)  : URL > pref > default
        ``pref_first=True``  (pref-first) : pref > URL > default

    On URL change in an active session (sentinel exists but differs from QP):
        URL wins if it is in ``allowed``; sentinel is always updated.

    On same URL (sentinel == QP):
        No-op — widget retains user's in-session edits.

    Returns the validated QP string (empty when QP is absent or invalid).
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_val = st.query_params.get(qp_key, "")
    if allowed is not None:
        qp_valid = qp_val if qp_val in allowed else ""
        pref_valid = pref_val if pref_val in allowed else ""
    else:
        qp_valid = qp_val
        pref_valid = pref_val

    if ss_key not in st.session_state:
        if pref_first:
            st.session_state[ss_key] = pref_valid or qp_valid or default
        else:
            st.session_state[ss_key] = qp_valid or pref_valid or default
        st.session_state[_luk] = qp_val
    elif st.session_state.get(_luk) != qp_val:
        if qp_valid:
            st.session_state[ss_key] = qp_valid
        st.session_state[_luk] = qp_val
    return qp_valid


def _url_init_int(qp_key, ss_key, default=0, *, last_url_key=None):
    """Guard-initialise an int session-state key from URL query params.

    Returns the raw QP string (already stored; caller rarely needs it).
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_raw = st.query_params.get(qp_key, str(default))
    if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
        try:
            st.session_state[ss_key] = int(qp_raw)
        except (ValueError, TypeError):
            st.session_state[ss_key] = default
        st.session_state[_luk] = qp_raw
    return qp_raw


def _url_init_float(qp_key, ss_key, default=0.0, *, last_url_key=None):
    """Guard-initialise a float session-state key from URL query params.

    Returns the raw QP string.
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_raw = st.query_params.get(qp_key, str(default))
    if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
        try:
            st.session_state[ss_key] = float(qp_raw)
        except (ValueError, TypeError):
            st.session_state[ss_key] = default
        st.session_state[_luk] = qp_raw
    return qp_raw


def _url_init_float_none(qp_key, ss_key, *, pref_val="NOT_SET", clamp=None,
                         last_url_key=None):
    """Guard-initialise a float-or-None session-state key from URL query params.

    Parameters
    ----------
    pref_val  : Saved-preference value (the actual ``float`` or ``None``,
                not a string).  Pass the sentinel string ``"NOT_SET"``
                (default) to skip the preference fallback entirely.
    clamp     : Optional ``(min, max)`` tuple.  Values outside the range
                are replaced with ``None``.

    The absent-QP sentinel is ``""`` so that ``_url_push_opt`` (which
    writes ``""`` and deletes the QP when the value is ``None``) and this
    init helper stay in sync without looping.

    Returns the raw QP string (empty when absent).
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_raw = st.query_params.get(qp_key) or ""
    if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
        if ss_key not in st.session_state and pref_val != "NOT_SET":
            st.session_state[ss_key] = pref_val
        else:
            try:
                raw = float(qp_raw) if qp_raw else None
                if raw is not None and clamp is not None:
                    lo, hi = clamp
                    if not (lo <= raw <= hi):
                        raw = None
                st.session_state[ss_key] = raw
            except (ValueError, TypeError):
                st.session_state[ss_key] = None
        st.session_state[_luk] = qp_raw
    return qp_raw


def _url_init_bool(qp_key, ss_key, default=False, *, true_val="true",
                   default_factory=None, last_url_key=None):
    """Guard-initialise a bool session-state key from URL query params.

    Parameters
    ----------
    true_val        : The QP string that means ``True``.  Use ``"true"``
                      (default) for the "true"/"false" convention, or
                      ``"1"`` for the "1"/"0" convention.  The complementary
                      false value is inferred automatically.
    default_factory : Zero-argument callable called when the QP is absent
                      (empty string) to compute a dynamic default.  When
                      provided, the absent-QP sentinel is ``""`` so that
                      ``_url_push_opt`` stays in sync.  When omitted the
                      static ``default`` is used and the sentinel matches the
                      ``false_val`` / ``true_val`` convention.

    Returns the raw QP string.
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    if default_factory is not None:
        qp_raw = st.query_params.get(qp_key) or ""
        if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
            st.session_state[ss_key] = (qp_raw == true_val) if qp_raw else default_factory()
            st.session_state[_luk] = qp_raw
    else:
        false_val = "false" if true_val == "true" else "0"
        default_raw = true_val if default else false_val
        qp_raw = st.query_params.get(qp_key, default_raw)
        if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
            st.session_state[ss_key] = (qp_raw == true_val)
            st.session_state[_luk] = qp_raw
    return qp_raw


def _url_init_date(qp_key, ss_key, default_factory, *, last_url_key=None):
    """Guard-initialise a ``datetime.date`` session-state key from URL query params.

    Parameters
    ----------
    default_factory : Zero-argument callable that returns the fallback
                      ``date`` when the QP is absent or unparseable.

    Returns the raw QP string (empty string when absent).
    """
    from datetime import date as _date_cls
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_raw = st.query_params.get(qp_key) or ""
    if ss_key not in st.session_state or st.session_state.get(_luk) != qp_raw:
        if qp_raw:
            try:
                st.session_state[ss_key] = _date_cls.fromisoformat(qp_raw)
            except (ValueError, TypeError):
                st.session_state[ss_key] = default_factory()
        else:
            st.session_state[ss_key] = default_factory()
        st.session_state[_luk] = qp_raw
    return qp_raw


def _url_init_multiselect(qp_key, ss_key, default_list, *, allowed,
                          full_pref_key=None, last_url_key=None):
    """Guard-initialise a multiselect session-state key from URL query params.

    The QP is a comma-separated list of option names.  An absent QP restores
    ``default_list`` (filtered to values present in ``allowed``).  Values not
    present in ``allowed`` are silently dropped.

    The absent-QP sentinel is ``"__absent__"`` to distinguish "QP missing"
    from "QP explicitly set to empty string" (meaning the user deselected
    everything).

    Parameters
    ----------
    full_pref_key  : Optional session-state key for the "full preference"
                     list — i.e. including options not currently in the
                     available dataset.  When provided, the full deserialized
                     QP list is written to this key so it can be merged back
                     in on the next ticker switch.

    Returns the raw QP string (``None`` when absent).
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    qp_raw = st.query_params.get(qp_key, None)
    sentinel = qp_raw if qp_raw is not None else "__absent__"
    if ss_key not in st.session_state or st.session_state.get(_luk) != sentinel:
        if qp_raw is not None:
            full = (
                [c.strip() for c in qp_raw.split(",") if c.strip() in allowed]
                if qp_raw else []
            )
            filtered = [c for c in full if c in allowed]
            if full_pref_key is not None:
                st.session_state[full_pref_key] = full
            st.session_state[ss_key] = filtered
        else:
            valid_default = [c for c in default_list if c in allowed]
            if full_pref_key is not None:
                st.session_state[full_pref_key] = valid_default
            st.session_state[ss_key] = valid_default
        st.session_state[_luk] = sentinel
    return qp_raw


def _url_push(qp_key, value_str, *, last_url_key=None):
    """Write a serialised widget value back to query params and the _last_url guard.

    ``value_str`` must already be a ``str`` — serialise before calling.
    Example for a bool using the "1"/"0" convention:
        _url_push("rp_log_boosted", "1" if boosted else "0")
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    if st.query_params.get(qp_key) != value_str:
        st.query_params[qp_key] = value_str
    st.session_state[_luk] = value_str


def _url_push_opt(qp_key, value_str, *, last_url_key=None):
    """Like ``_url_push`` but deletes the QP when ``value_str`` is falsy.

    Use for controls where an absent QP means "default / off" — avoids
    cluttering the URL with ``?key=`` empty entries.  Designed to pair with
    ``_url_init_bool(... default_factory=...)`` or ``_url_init_float_none``
    which both use ``""`` as their absent-QP sentinel.

    Example:
        _url_push_opt("tkr_sort_rev", "1" if _sort_reverse else "")
    """
    _luk = last_url_key or f"_{qp_key}_last_url"
    _cur = st.query_params.get(qp_key, "")
    if value_str:
        if _cur != value_str:
            st.query_params[qp_key] = value_str
    else:
        if qp_key in st.query_params:
            del st.query_params[qp_key]
    st.session_state[_luk] = value_str
