#!/usr/bin/env python3
"""
Local review page for clip samples: watch each ranked sample, mark it keep or reject,
and add a note. Choices are saved straight into pipeline.db, where
`pipeline.clips render --status keep` picks them up.

Runs only on this Mac (127.0.0.1); nothing leaves the machine.

Usage:
  python3 -m pipeline.review            # then open http://127.0.0.1:8765
"""

import argparse
import json
import os
import re
import sqlite3
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .analyze import open_db
from .clips import CLIPS_SCHEMA

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clip review</title><style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--quiet:#6b6a66;--line:#dddbd5;--keep:#2f7d4f;--reject:#b3412f}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#22221f;--ink:#ecebe6;--quiet:#9b9a94;--line:#3a3935}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:14px 20px;border-bottom:1px solid var(--line);z-index:1}
h1{font-size:18px;margin:0 0 8px} nav button{margin:0 6px 6px 0}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:5px 12px;cursor:pointer}
button.on{border-color:var(--ink);font-weight:600}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;padding:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.card.keep{border-color:var(--keep);box-shadow:0 0 0 1px var(--keep)} .card.reject{opacity:.55}
video{width:100%;border-radius:6px;background:#000;display:block}
.meta{display:flex;justify-content:space-between;margin:8px 0 6px;color:var(--quiet);font-size:13px}
.meta b{color:var(--ink)} .acts{display:flex;gap:6px} .acts button{flex:1}
.acts .k.on{background:var(--keep);color:#fff;border-color:var(--keep)}
.acts .r.on{background:var(--reject);color:#fff;border-color:var(--reject)}
input{width:100%;box-sizing:border-box;margin-top:6px;font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.empty{padding:40px 20px;color:var(--quiet)}
</style></head><body><header><h1>Clip review</h1><nav id="tabs"></nav></header>
<main id="grid"></main><script>
const clips = __CLIPS__;
const recipes = [...new Set(clips.map(c => c.recipe))];
let current = recipes[0];
function tabs(){
  const nav = document.getElementById('tabs'); nav.innerHTML = '';
  for (const r of recipes){
    const n = clips.filter(c => c.recipe === r), k = n.filter(c => c.status === 'keep').length;
    const b = document.createElement('button'); b.textContent = `${r} (${k} kept of ${n.length})`;
    b.className = r === current ? 'on' : ''; b.onclick = () => { current = r; draw(); }; nav.appendChild(b);
  }
}
async function save(c){
  await fetch('/api/clip', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id:c.id, status:c.status, note:c.note || ''})});
}
function draw(){
  tabs(); const g = document.getElementById('grid'); g.innerHTML = '';
  const list = clips.filter(c => c.recipe === current);
  if (!list.length){ g.innerHTML = '<p class="empty">No samples yet. Run: pipeline.clips samples</p>'; return; }
  for (const c of list){
    const d = document.createElement('div'); d.className = 'card ' + c.status;
    d.innerHTML = `<video src="/media/${c.id}" controls loop muted playsinline preload="metadata"></video>
      <div class="meta"><span><b>${c.day}</b> &middot; #${c.rank}</span><span>score ${Math.round(c.score)} &middot; ${Math.round(c.seconds)} s</span></div>
      <div class="acts"><button class="k ${c.status==='keep'?'on':''}">Keep</button><button class="r ${c.status==='reject'?'on':''}">Reject</button></div>
      <input placeholder="Note (optional)" value="${(c.note||'').replace(/"/g,'&quot;')}">`;
    const [k, r] = d.querySelectorAll('.acts button'), note = d.querySelector('input');
    k.onclick = () => { c.status = c.status === 'keep' ? 'new' : 'keep'; save(c); draw(); };
    r.onclick = () => { c.status = c.status === 'reject' ? 'new' : 'reject'; save(c); draw(); };
    note.onchange = () => { c.note = note.value; save(c); };
    const v = d.querySelector('video'); v.onmouseenter = () => v.play(); v.onmouseleave = () => v.pause();
    g.appendChild(d);
  }
}
draw();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    @property
    def db(self):
        # one connection per request: the server handles each request on its own thread
        if not hasattr(self, "_db"):
            self._db = sqlite3.connect(config.DB_PATH, timeout=30)
        return self._db

    def finish(self):
        super().finish()
        if hasattr(self, "_db"):
            self._db.close()

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            rows = self.db.execute("SELECT id, recipe, day, score, rank, seconds, status, note FROM clips "
                                   "WHERE sample_path IS NOT NULL ORDER BY recipe, rank").fetchall()
            clips = [dict(zip(["id", "recipe", "day", "score", "rank", "seconds", "status", "note"], r))
                     for r in rows]
            page = PAGE.replace("__CLIPS__", json.dumps(clips).replace("</", "<\\/"))
            return self._send(200, page.encode())
        m = re.fullmatch(r"/media/(\d+)", self.path)
        if m:
            row = self.db.execute("SELECT sample_path FROM clips WHERE id = ?", (int(m.group(1)),)).fetchone()
            if not row or not row[0] or not Path(row[0]).exists():
                return self._send(404, b"not found", "text/plain")
            return self._video(Path(row[0]))
        self._send(404, b"not found", "text/plain")

    def _video(self, path):
        # Safari only plays video served with byte-range support
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng:
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = size - int(rng.group(2))
            end = min(end, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_POST(self):
        if self.path != "/api/clip":
            return self._send(404, b"not found", "text/plain")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        status = body.get("status")
        if status not in ("new", "keep", "reject"):
            return self._send(400, b"bad status", "text/plain")
        self.db.execute("UPDATE clips SET status = ?, note = ? WHERE id = ?",
                        (status, str(body.get("note", ""))[:500], int(body["id"])))
        self.db.commit()
        self._send(200, b"{}", "application/json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    db = open_db()
    db.executescript(CLIPS_SCHEMA)
    db.close()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Review page at {url} (Ctrl-C to stop)")
    if not args.no_browser and os.environ.get("TL_NO_BROWSER") is None:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
