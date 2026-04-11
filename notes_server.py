import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

NOTES_KEY = "a5e1fcab-8369-42c4-8550-a8a19734510c"
NOTES_PATH = os.path.join(os.path.dirname(__file__), ".local", "build_notes.md")
PORT = int(os.environ.get("PORT", 8082))

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EdgeIQ Build Notes</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0e0e1a;
    color: #d0d0e8;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 15px;
    line-height: 1.7;
    padding: 0 0 80px 0;
  }}
  header {{
    background: #12122a;
    border-bottom: 1px solid #2a2a4a;
    padding: 20px 24px;
    position: sticky;
    top: 0;
    z-index: 10;
  }}
  header h1 {{
    font-size: 20px;
    font-weight: 800;
    color: #7986cb;
    margin: 0;
  }}
  header p {{
    font-size: 11px;
    color: #555;
    margin: 2px 0 0 0;
  }}
  #content {{
    max-width: 860px;
    margin: 0 auto;
    padding: 32px 24px;
  }}
  h1, h2 {{ color: #7986cb; margin: 32px 0 12px 0; padding-bottom: 6px; border-bottom: 1px solid #2a2a4a; }}
  h3 {{ color: #9fa8da; margin: 24px 0 8px 0; }}
  h4 {{ color: #b0bec5; margin: 16px 0 6px 0; }}
  p {{ margin: 0 0 12px 0; }}
  ul, ol {{ margin: 0 0 12px 20px; }}
  li {{ margin: 4px 0; }}
  strong {{ color: #e0e0ff; }}
  em {{ color: #b0bec5; }}
  code {{
    background: #1a1a30;
    border: 1px solid #2a2a4a;
    border-radius: 4px;
    padding: 1px 6px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 13px;
    color: #80cbc4;
  }}
  pre {{
    background: #1a1a30;
    border: 1px solid #2a2a4a;
    border-radius: 6px;
    padding: 16px;
    overflow-x: auto;
    margin: 0 0 16px 0;
  }}
  pre code {{ background: none; border: none; padding: 0; color: #80cbc4; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 0 0 16px 0;
    font-size: 14px;
  }}
  th {{
    background: #1a1a30;
    color: #7986cb;
    padding: 8px 12px;
    text-align: left;
    border: 1px solid #2a2a4a;
  }}
  td {{ padding: 7px 12px; border: 1px solid #2a2a4a; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #12122a; }}
  hr {{ border: none; border-top: 1px solid #2a2a4a; margin: 28px 0; }}
  blockquote {{
    border-left: 3px solid #7986cb;
    padding: 8px 16px;
    margin: 0 0 16px 0;
    background: #12122a;
    color: #9fa8da;
  }}
  a {{ color: #7986cb; }}
</style>
</head>
<body>
<header>
  <h1>📋 EdgeIQ Build Notes</h1>
  <p>Live document — always current</p>
</header>
<div id="content"><p style="color:#555">Loading...</p></div>
<script>
const raw = {content};
document.getElementById('content').innerHTML = marked.parse(raw);
</script>
</body>
</html>"""


class NotesHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        key = params.get("key", [""])[0]

        if key != NOTES_KEY:
            self.send_response(403)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body style='background:#0e0e1a;color:#555;font-family:sans-serif;padding:40px;text-align:center'><h2>Access denied</h2></body></html>")
            return

        try:
            with open(NOTES_PATH, "r") as f:
                markdown_content = f.read()
        except FileNotFoundError:
            markdown_content = "# Build notes file not found."

        html = HTML_TEMPLATE.format(content=json.dumps(markdown_content))
        encoded = html.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), NotesHandler)
    print(f"Notes server running on port {PORT}")
    server.serve_forever()
