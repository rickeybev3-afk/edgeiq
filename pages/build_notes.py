import streamlit as st
import os

PUBLIC_KEY = "a5e1fcab-8369-42c4-8550-a8a19734510c"
NOTES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".local", "build_notes.md")

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

key = st.query_params.get("key", "")

if key != PUBLIC_KEY:
    st.error("Access denied.")
    st.stop()

st.markdown("# 📋 EdgeIQ Build Notes")
st.caption("Live document — always current")
st.divider()

try:
    with open(NOTES_PATH, "r") as f:
        content = f.read()
    st.markdown(content, unsafe_allow_html=True)
except FileNotFoundError:
    st.error("Build notes file not found.")
