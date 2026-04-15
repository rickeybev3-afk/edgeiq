import streamlit as st
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES_PATH         = os.path.join(_ROOT, ".local", "build_notes.md")
NOTES_FALLBACK     = os.path.join(_ROOT, "replit.md")

st.set_page_config(
    page_title="EdgeIQ Build Notes",
    page_icon="📋",
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

st.markdown("# 📋 EdgeIQ Build Notes")
st.caption("Live document — always current")
st.divider()

if os.path.exists(NOTES_PATH):
    with open(NOTES_PATH, "r") as f:
        content = f.read()
    st.markdown(content, unsafe_allow_html=True)
elif os.path.exists(NOTES_FALLBACK):
    with open(NOTES_FALLBACK, "r") as f:
        content = f.read()
    st.markdown(content, unsafe_allow_html=True)
else:
    st.error("Build notes file not found.")
