import os, json

NOTES_KEY = "a5e1fcab-8369-42c4-8550-a8a19734510c"
NOTES_PATH = os.path.join(os.path.dirname(__file__), ".local", "build_notes.md")
OUT_PATH   = os.path.join(os.path.dirname(__file__), "static", "notes.html")

with open(NOTES_PATH, "r") as f:
    content = f.read()

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EdgeIQ Build Notes</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0e0e1a;color:#d0d0e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;line-height:1.75;padding-bottom:80px}}
header{{background:#12122a;border-bottom:1px solid #2a2a4a;padding:18px 20px;position:sticky;top:0;z-index:10}}
header h1{{font-size:19px;font-weight:800;color:#7986cb;margin:0}}
header p{{font-size:11px;color:#555;margin:3px 0 0 0}}
#content{{max-width:840px;margin:0 auto;padding:28px 20px}}
h1,h2{{color:#7986cb;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #2a2a4a}}
h3{{color:#9fa8da;margin:22px 0 8px}}
h4{{color:#b0bec5;margin:14px 0 5px}}
p{{margin:0 0 11px}}
ul,ol{{margin:0 0 11px 20px}}
li{{margin:3px 0}}
strong{{color:#e0e0ff}}
em{{color:#b0bec5}}
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
#gate{{display:none;position:fixed;inset:0;background:#0e0e1a;z-index:99;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px}}
#gate p{{color:#555;font-size:14px}}
</style>
</head>
<body>
<div id="gate"><p>Access denied.</p></div>
<header style="display:none" id="hdr">
  <h1>&#x1F4CB; EdgeIQ Build Notes</h1>
  <p>Live document &mdash; regenerated on each update</p>
</header>
<div id="content" style="display:none"></div>
<script>
const KEY = {json.dumps(NOTES_KEY)};
const raw = {json.dumps(content)};
const params = new URLSearchParams(window.location.search);
if (params.get('key') === KEY) {{
  document.getElementById('gate').style.display = 'none';
  document.getElementById('hdr').style.display = 'block';
  document.getElementById('content').style.display = 'block';
  document.getElementById('content').innerHTML = marked.parse(raw);
}}
</script>
</body>
</html>"""

with open(OUT_PATH, "w") as f:
    f.write(html)

print(f"Generated: {OUT_PATH} ({len(content):,} chars of markdown)")
