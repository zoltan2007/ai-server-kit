#!/usr/bin/env python3
"""製作人端｜收模型伺服器（AI Server Kit receiver）

收 AI 主機面板「回傳」來的模型，驗 token、存檔、算 SHA256，附一個可登入看清單／下載的網頁。
只用 Python 標準函式庫（3.8 以上），Windows／Linux 都能跑。

    python server.py --config config.json

config.json：
    {"host": "0.0.0.0", "port": 8790, "token": "至少12字的通關碼",
     "dir": "received", "recv_path": "/upload"}

面板那邊的 config.json 要設：
    "share": { "url": "https://你的網域/upload", "label": "製作人", "token": "同一組通關碼" }

⚠ token 會放在 HTTP 標頭裡，公開到網路上時一定要用 HTTPS（前面擺 Nginx／Caddy 之類的反向代理）。
"""
import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1.0"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
HERE = os.path.dirname(os.path.abspath(__file__))


def slug(s, fallback="model"):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip()).strip("-.")
    return (s[:80] or fallback)


class Store:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    def sub_dir(self, model, sender):
        d = os.path.join(self.root, "%s__%s" % (slug(model), slug(sender, "unknown")))
        os.makedirs(d, exist_ok=True)
        return d

    def save(self, model, sender, name, reader, size):
        d = self.sub_dir(model, sender)
        dst = os.path.join(d, name)
        tmp = dst + ".part"
        h = hashlib.sha256()
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = reader(4 * 1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
        if size is not None and got != size:
            os.remove(tmp)
            raise IOError("收到的大小不符（說好 %d，實際 %d）" % (size, got))
        os.replace(tmp, dst)
        with self.lock:
            meta_path = os.path.join(d, "_meta.json")
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                meta = {"model": model, "from": sender, "created": int(time.time()), "files": {}}
            meta["model"] = model
            meta["from"] = sender
            meta["updated"] = int(time.time())
            meta.setdefault("files", {})[name] = {"size": got, "sha256": h.hexdigest(), "at": int(time.time())}
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
        return got, h.hexdigest()

    def list(self):
        out = []
        for name in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, name)
            mp = os.path.join(d, "_meta.json")
            if not os.path.isdir(d) or not os.path.exists(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            files = meta.get("files", {})
            out.append({"id": name, "model": meta.get("model"), "from": meta.get("from"),
                        "created": meta.get("created"), "updated": meta.get("updated"),
                        "total": sum(v.get("size", 0) for v in files.values()),
                        "files": [{"name": k, "size": v.get("size"), "sha256": v.get("sha256")} for k, v in files.items()]})
        out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
        return out

    def file_path(self, sub, name):
        if not SAFE_NAME.match(name) or ".." in sub or "/" in sub or "\\" in sub:
            return None
        p = os.path.join(self.root, sub, name)
        if os.path.isfile(p) and os.path.realpath(p).startswith(os.path.realpath(self.root)):
            return p
        return None


class App:
    def __init__(self, cfg):
        self.token = cfg["token"]
        self.recv_path = cfg.get("recv_path", "/upload")
        self.store = Store(cfg.get("dir") or os.path.join(HERE, "received"))

    def check(self, tok):
        return bool(tok) and hmac.compare_digest(tok.encode("utf-8"), self.token.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AISKReceiver/" + VERSION
    app = None  # type: App

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body=b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _bearer(self):
        a = self.headers.get("Authorization", "")
        tok = a[7:] if a.startswith("Bearer ") else ""
        if not tok:
            from urllib.parse import urlsplit, parse_qs
            tok = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
        return self.app.check(tok)

    def do_GET(self):
        from urllib.parse import urlsplit, parse_qs
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/ping":
                return self._json({"ok": True, "version": VERSION})
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if u.path == "/api/list":
                if not self._bearer():
                    return self._json({"error": "unauthorized"}, 401)
                return self._json({"items": self.app.store.list()})
            if u.path == "/api/file":
                if not self._bearer():
                    return self._json({"error": "unauthorized"}, 401)
                p = self.app.store.file_path(q.get("sub", ""), q.get("name", ""))
                if not p:
                    return self._json({"error": "not found"}, 404)
                data = open(p, "rb").read()
                return self._send(200, "application/octet-stream", data,
                                  {"Content-Disposition": 'attachment; filename="%s"' % os.path.basename(p)})
            self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        from urllib.parse import urlsplit, parse_qs
        u = urlsplit(self.path)
        self.close_connection = True
        if u.path != self.app.recv_path:
            return self._json({"error": "not found"}, 404)
        if not self._bearer():
            return self._json({"error": "unauthorized"}, 401)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        name = os.path.basename(q.get("name", ""))
        if not SAFE_NAME.match(name):
            return self._json({"error": "bad name"}, 400)
        model = q.get("model", "model")
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0:
            return self._json({"error": "empty"}, 400)
        sender = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        remaining = [size]

        def reader(n):   # 只讀剩下的位元組，不然 keep-alive 連線會卡住等不存在的資料
            if remaining[0] <= 0:
                return b""
            chunk = self.rfile.read(min(n, remaining[0]))
            remaining[0] -= len(chunk)
            return chunk

        try:
            got, digest = self.app.store.save(model, sender, name, reader, size)
        except (OSError, IOError) as e:
            return self._json({"error": str(e)}, 500)
        return self._json({"ok": True, "name": name, "size": got, "sha256": digest})


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>收到的模型</title>
<style>
:root{--bg:#0f1318;--card:#161b22;--bd:#2a333e;--tx:#e6ebf1;--mut:#8b97a6;--ac:#3ec7b4;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 -apple-system,"Segoe UI","Microsoft JhengHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:20px 16px}h1{font-size:20px}
input,button{font:inherit;color:inherit;border-radius:9px;border:1px solid var(--bd);padding:8px 12px;background:var(--card)}
button{background:var(--ac);color:#062621;font-weight:600;cursor:pointer;border-color:var(--ac)}
.row{display:flex;gap:8px}.row input{flex:1}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;margin:10px 0}
.card h3{margin:0 0 4px;font-size:16px;word-break:break-all}.mut{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}td,th{border:1px solid var(--bd);padding:5px 8px;text-align:left;word-break:break-all}
a.dl{color:var(--ac);cursor:pointer;text-decoration:underline}.err{color:#ef6363}
</style></head><body><div class="wrap">
<h1>收到的模型</h1>
<div id="login"><p class="mut">輸入通關碼（跟伺服器 config.json 的 token 一樣）</p>
<div class="row"><input id="tok" type="password" placeholder="通關碼"><button onclick="go()">進入</button></div>
<p class="err" id="e"></p></div>
<div id="list"></div></div>
<script>
let T="";
async function go(){T=document.getElementById("tok").value.trim();document.getElementById("e").textContent="";
 try{const r=await fetch("/api/list",{headers:{Authorization:"Bearer "+T}});if(r.status===401){document.getElementById("e").textContent="通關碼不對";return;}
 const j=await r.json();document.getElementById("login").style.display="none";render(j.items);}catch(e){document.getElementById("e").textContent=e;}}
function gb(b){return b>=1e9?(b/1e9).toFixed(2)+" GB":b>=1e6?(b/1e6).toFixed(1)+" MB":(b/1e3).toFixed(0)+" KB";}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function render(items){const L=document.getElementById("list");
 if(!items.length){L.innerHTML='<p class="mut">目前還沒有收到任何模型。</p>';return;}
 L.innerHTML=items.map(it=>`<div class="card"><h3>${esc(it.model)}</h3>
  <div class="mut">來自 ${esc(it.from)} · ${new Date((it.updated||0)*1000).toLocaleString()} · 共 ${gb(it.total)}</div>
  <table><tr><th>檔案</th><th>大小</th><th>SHA256</th><th></th></tr>
  ${it.files.map(f=>`<tr><td>${esc(f.name)}</td><td>${gb(f.size)}</td><td>${esc((f.sha256||"").slice(0,16))}…</td>
   <td><a class="dl" onclick='dl(${JSON.stringify(it.id)},${JSON.stringify(f.name)})'>下載</a></td></tr>`).join("")}
  </table></div>`).join("");}
async function dl(sub,name){const r=await fetch("/api/file?sub="+encodeURIComponent(sub)+"&name="+encodeURIComponent(name),{headers:{Authorization:"Bearer "+T}});
 const b=await r.blob();const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=name;a.click();URL.revokeObjectURL(a.href);}
document.getElementById("tok").addEventListener("keydown",e=>{if(e.key==="Enter")go();});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="AI Server Kit 收模型伺服器")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    with open(args.config, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not cfg.get("token") or len(cfg["token"]) < 12:
        raise SystemExit("config.json 的 token 至少要 12 個字（這是別人把模型傳進來的通關碼）")
    Handler.app = App(cfg)
    host, port = cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8790))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("收模型伺服器 %s：http://%s:%d/（清單頁）" % (VERSION, "127.0.0.1" if host in ("0.0.0.0", "") else host, port), flush=True)
    print("面板 share.url 設成： http(s)://<這台的網域或IP>:%d%s" % (port, Handler.app.recv_path), flush=True)
    if host in ("0.0.0.0", ""):
        print("⚠ 對外公開時務必用 HTTPS（反向代理），token 才不會被看到。", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
