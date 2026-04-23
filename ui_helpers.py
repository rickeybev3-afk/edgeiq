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
