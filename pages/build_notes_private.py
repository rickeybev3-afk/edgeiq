import streamlit as st
import os

def _find_notes_path():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "build_notes_private.md"),
        os.path.join(os.getcwd(), ".local", "build_notes_private.md"),
        "/home/runner/workspace/.local/build_notes_private.md",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".local", "build_notes_private.md"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0]

NOTES_PATH = _find_notes_path()

st.set_page_config(
    page_title="EdgeIQ — Full Build Notes (Private)",
    page_icon="🔒",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
  [data-testid="stSidebar"] { display: none; }
  .block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 900px; }
  h1, h2 { color: #7986cb; }
  h3 { color: #9fa8da; }
  code { background: #1a1a30; color: #80cbc4; padding: 2px 6px; border-radius: 3px; }
</style>
""", unsafe_allow_html=True)

_NOTES_PASSCODE = os.environ.get("NOTES_PASSCODE", "")

if not _NOTES_PASSCODE:
    st.error("NOTES_PASSCODE environment variable not set.")
    st.stop()

if "private_notes_unlocked" not in st.session_state:
    st.session_state.private_notes_unlocked = False

if not st.session_state.private_notes_unlocked:
    st.markdown("## 🔒 Private Build Notes")
    code_input = st.text_input("Passcode", type="password", placeholder="Enter passcode")
    if st.button("Unlock"):
        if code_input == _NOTES_PASSCODE:
            st.session_state.private_notes_unlocked = True
            st.rerun()
        else:
            st.error("Incorrect passcode.")
    st.stop()

st.markdown("# 🔒 EdgeIQ Build Notes — Full Private")
st.caption("Everything. Keep this URL to yourself.")
st.divider()

try:
    with open(NOTES_PATH, "r") as f:
        content = f.read()
    st.markdown(content, unsafe_allow_html=True)
except FileNotFoundError:
    st.error(f"Private build notes file not found. Tried: `{NOTES_PATH}` | cwd: `{os.getcwd()}`")
