#!/usr/bin/env python3
"""AI 主機面板：機器狀態、模型管理、跟 AI 對話、終端機。

只用 Python 標準函式庫（3.8 以上），Windows／Linux 都能跑。
    python server.py --config config.json
config.json：{"host": "127.0.0.1", "port": 8780, "token": "...", "terminal": true,
              "ollama": "http://127.0.0.1:11434", "data_dir": "..."}
"""
import argparse
import hmac
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agreement  # noqa: E402
import export_api  # noqa: E402
import guard  # noqa: E402
import ai_api  # noqa: E402
import jobs  # noqa: E402
import ollama_api  # noqa: E402
import rooms  # noqa: E402
import rooms_api  # noqa: E402
import sessions  # noqa: E402
import users  # noqa: E402
import users_api  # noqa: E402
import sysinfo  # noqa: E402
import usage  # noqa: E402
from ollama_api import NAME_RE, OllamaError  # noqa: E402
from sessions import ID_RE  # noqa: E402

VERSION = "0.1.0"
WEB = os.path.join(HERE, "web")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".json": "application/json", ".webmanifest": "application/manifest+json"}
MAX_JSON = 48 * 1024 * 1024        # 對話可以帶圖片（base64），給大一點
UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,150}\.gguf$", re.I)
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = cfg["token"]
        self.ollama = ollama_api.Ollama(cfg.get("ollama", "http://127.0.0.1:11434"))
        self.jobs = jobs.JobStore()
        self.terminal = bool(cfg.get("terminal", True))
        self.share = cfg.get("share") or {}
        self.data_dir = cfg.get("data_dir") or os.path.join(os.path.expanduser("~"), ".ai-server-kit")
        self.imports_dir = cfg.get("imports_dir") or os.path.join(self.data_dir, "imports")
        self.users = users.UserStore(self.data_dir)
        self.rooms = rooms.RoomStore(self.data_dir)
        self.usage = usage.Usage(self.data_dir)
        self.guard = guard.Guard()
        self.version = VERSION
        self.session_stores = {}
        self.store_lock = threading.Lock()
        self.fails = {}
        self.fail_lock = threading.Lock()
        with open(os.path.join(HERE, "catalog.json"), encoding="utf-8") as f:
            self.catalog = json.load(f)

    def sessions_for(self, user):
        """每個帳號自己的對話資料夾；主登入碼和管理員帳號 admin 共用同一個。"""
        name = user["name"]
        with self.store_lock:
            st = self.session_stores.get(name)
            if not st:
                st = self.session_stores[name] = sessions.SessionStore(os.path.join(self.data_dir, "sessions", name))
            return st

    def share_configured(self):
        return bool(self.share.get("url"))

    def agreement_text(self):
        return agreement.text(self.share.get("label", "製作人"), self.share_configured(), agreement.load_custom(self.data_dir))

    def share_opted_in(self):
        return agreement.status(self.data_dir, self.agreement_text()).get("share_optin", False)

    # 登入失敗太多次就鎖 10 分鐘
    def locked(self, ip):
        with self.fail_lock:
            n, t0 = self.fails.get(ip, (0, 0))
            if time.time() - t0 > 600:
                self.fails.pop(ip, None)
                return False
            return n >= 8

    def failed(self, ip):
        with self.fail_lock:
            n, t0 = self.fails.get(ip, (0, time.time()))
            self.fails[ip] = (n + 1, t0)

    def check(self, tok):
        return bool(tok) and hmac.compare_digest(tok.encode("utf-8"), self.token.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AIServerKit/" + VERSION
    timeout = 30            # 慢速送標頭／內容的連線 30 秒就斷；串流時 stream_start 會放寬
    app = None  # type: App

    # ── 小工具 ──
    def log_message(self, fmt, *args):
        pass  # 存取紀錄不寫，錯誤另外記

    def _headers(self, code, ctype, length=None, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if length is None:
            self.send_header("Connection", "close")
            self.close_connection = True
        else:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._headers(code, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def err(self, msg, code=400):
        self.json({"error": msg}, code)

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支援 chunked")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Content-Length 不對")
        if n < 0 or n > min(MAX_JSON, self.app.guard.body_max(self.route()[0])):
            raise ValueError("內容太大")
        raw = self.rfile.read(n) if n else b"{}"
        obj = json.loads(raw or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("要是 JSON 物件")
        return obj

    def stream_start(self):
        self.connection.settimeout(900)
        self._headers(200, "application/x-ndjson; charset=utf-8", None, {"X-Accel-Buffering": "no"})

    def send_line(self, obj):
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def authed(self, path=""):
        auth = self.headers.get("Authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else ""
        self.user = users_api.resolve(self.app, tok)
        if self.user and users_api.allowed(self.user, path):
            return True
        self.close_connection = True  # 請求內容沒讀，這條連線不能再用
        self.err("請先登入" if not self.user else "這個功能只有管理員能用", 401 if not self.user else 403)
        return False

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    # ── HTTP 方法 ──
    def do_GET(self):
        path, q = self.route()
        try:
            if not path.startswith("/api/"):
                return self.static(path)
            if path == "/api/ping":
                return self.json({"ok": True})
            if not self.authed(path):
                return
            fn = GET_ROUTES.get(path)
            return fn(self, q) if fn else self.err("沒有這個功能", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # 不讓單一請求把伺服器弄掛
            log_error("GET %s: %r" % (path, e))
            try:
                self.err("伺服器出錯，已記錄", 500)
            except OSError:
                pass

    def do_POST(self):
        path, q = self.route()
        try:
            if path in ("/api/login", "/api/apply"):
                if not self.app.guard.allow("ip:" + self.client_address[0], path):
                    return self.err("太頻繁了，等一分鐘再試", 429)
                return users_api.login(self) if path == "/api/login" else users_api.apply(self)
            if not self.authed(path):
                return
            if not self.app.guard.allow(self.user["name"], path):
                self.close_connection = True
                return self.err("太頻繁了，等一分鐘再試", 429)
            fn = POST_ROUTES.get(path)
            if not fn:
                self.close_connection = True
                return self.err("沒有這個功能", 404)
            try:
                b = self.body()
            except ValueError as e:
                return self.err("格式錯誤：%s" % e)
            return fn(self, b)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OllamaError as e:
            self.err(str(e), 502)
        except Exception as e:
            log_error("POST %s: %r" % (path, e))
            try:
                self.err("伺服器出錯，已記錄", 500)
            except OSError:
                pass

    def do_PUT(self):
        path, q = self.route()
        try:
            if path != "/api/upload":
                self.close_connection = True
                return self.err("沒有這個功能", 404)
            if self.authed(path):
                self.upload(q)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log_error("PUT %s: %r" % (path, e))
            try:
                self.err(str(e), 500)
            except OSError:
                pass

    # ── 網頁 ──
    def static(self, path):
        # 根目錄是舊面板；/studio/ 是 Kiki Cloude（像 Claude 程式介面的對話頁）（子資料夾一層，不吃 ..）
        folder, name = WEB, path.lstrip("/")
        if name.startswith("studio/") or name == "studio":
            folder, name = os.path.join(WEB, "studio"), name[7:]
        if name in ("", "index.html"):
            name = "index.html"
        if "/" in name or "\\" in name or name.startswith(".") or name not in os.listdir(folder):
            return self.err("找不到", 404)
        with open(os.path.join(folder, name), "rb") as f:
            data = f.read()
        ext = os.path.splitext(name)[1].lower()
        self._headers(200, MIME.get(ext, "application/octet-stream"), len(data),
                      {"Content-Security-Policy": CSP} if ext == ".html" else None)
        self.wfile.write(data)

    # ── 狀態 ──
    def status(self, q):
        snap = sysinfo.snapshot()
        snap["ollama"] = self.app.ollama.summary()
        snap["panel"] = {"version": VERSION, "terminal": self.app.terminal,
                         "jobs_running": len(self.app.jobs.running())}
        self.json(snap)

    # ── 使用協議（第一次用面板要同意，就一個勾） ──
    def agreement_get(self, q):
        t = self.app.agreement_text()
        st = agreement.status(self.app.data_dir, t)
        self.json(dict(st, text=t, share_configured=self.app.share_configured(), label=self.app.share.get("label", "製作人")))

    def agreement_accept(self, b):
        # 一個勾：同意協議＝同意協議揭露的內容（含製作人設定的選用模型分享）。
        # 這只是讓「匯入並回傳」按鈕出現；實際傳送仍要每個模型本人按下並確認，不會自動上傳。
        t = self.app.agreement_text()
        optin = self.app.share_configured()
        if not agreement.accept(self.app.data_dir, str(b.get("version", "")), t, optin):
            return self.err("協議內容已更新，請重新閱讀再同意", 409)
        self.json({"ok": True, "share_optin": optin})

    # ── 模型 ──
    def models(self, q):
        ol = self.app.ollama
        try:
            tags = ol.get("/api/tags").get("models", [])
            up = True
        except OllamaError as e:
            tags, up = [], False
            err = str(e)
        loaded = {m["name"]: m for m in (ol.summary().get("loaded") or [])} if up else {}
        installed = [{"name": m.get("name"), "size": m.get("size"), "modified_at": m.get("modified_at"),
                      "details": m.get("details", {}), "loaded": m.get("name") in loaded,
                      "size_vram": (loaded.get(m.get("name")) or {}).get("size_vram")} for m in tags]
        cards, _ = sysinfo.gpus()
        mem = sysinfo.memory() or {}
        sh = self.app.share
        optin = self.app.share_opted_in()
        if self.user.get("role") != "admin":
            loaded = {}
        out = {"up": up, "installed": installed, "catalog": self.app.catalog,
               "hw": {"vram": sum(c["mem_total"] for c in cards), "gpus": len(cards), "ram": mem.get("total")},
               "share": {"configured": self.app.share_configured(), "optin": optin,
                         "enabled": self.app.share_configured() and optin, "label": sh.get("label", "製作人")}}
        if not up:
            out["error"] = err
        self.json(out)

    def model_search(self, q):
        term = (q.get("q") or "").strip()
        if len(term) < 2:
            return self.err("搜尋字至少 2 個字")
        src = q.get("source", "all")
        out = {"q": term}
        if src in ("all", "ollama"):
            try:
                out["ollama"] = ollama_api.search_library(term)
            except OllamaError as e:
                out["ollama"], out["ollama_err"] = [], str(e)
        if src in ("all", "hf"):
            try:
                out["hf"] = ollama_api.search_hf(term)
            except OllamaError as e:
                out["hf"], out["hf_err"] = [], str(e)
        self.json(out)

    def model_hf_tags(self, q):
        repo = (q.get("repo") or "").strip()
        if "/" not in repo or not NAME_RE.match(repo):
            return self.err("HF 倉庫格式是 使用者/倉庫")
        try:
            self.json(ollama_api.hf_quant_tags(repo))
        except OllamaError as e:
            self.err(str(e), 502)

    def model_identify(self, b):
        try:
            self.json(ollama_api.identify(str(b.get("path", ""))))
        except ValueError as e:
            self.err(str(e))

    def model_share(self, b):
        if not self.app.share_configured():
            return self.err("這台沒有設定分享位置（config.json 的 share）", 403)
        if not self.app.share_opted_in():
            return self.err("尚未開啟模型分享（要在使用協議裡勾選同意）", 403)
        try:
            job = ollama_api.start_share(self.app.jobs, self.app.share, str(b.get("path", "")))
        except ValueError as e:
            return self.err(str(e))
        self.json({"id": job.id})

    def model_show(self, q):
        name = q.get("name", "")
        if not NAME_RE.match(name):
            return self.err("模型名稱不對")
        try:
            self.json(self.app.ollama.show(name))
        except OllamaError as e:
            self.err(str(e), 502)

    def model_size(self, q):
        name = q.get("name", "")
        if not NAME_RE.match(name):
            return self.err("模型名稱不對")
        self.json({"name": name, "size": ollama_api.registry_size(name)})

    def model_pull(self, b):
        name = str(b.get("name", "")).strip()
        if not NAME_RE.match(name):
            return self.err("模型名稱不對（例如 qwen3.5:9b 或 hf.co/使用者/模型:Q4_K_M）")
        for j in self.app.jobs.running():
            if j.kind == "pull" and j.meta.get("model") == name:
                return self.json({"id": j.id, "existing": True})
        self.json({"id": ollama_api.start_pull(self.app.jobs, self.app.ollama, name).id})

    def model_delete(self, b):
        name = str(b.get("name", ""))
        if not NAME_RE.match(name):
            return self.err("模型名稱不對")
        self.app.ollama.delete("/api/delete", {"model": name})
        self.json({"ok": True})

    def model_load(self, b):
        name = str(b.get("name", ""))
        if not NAME_RE.match(name):
            return self.err("模型名稱不對")
        keep = b.get("keep_alive", "30m")
        self.app.ollama.post("/api/generate", {"model": name, "keep_alive": keep}, timeout=600)
        self.json({"ok": True})

    def model_unload(self, b):
        name = str(b.get("name", ""))
        if not NAME_RE.match(name):
            return self.err("模型名稱不對")
        self.app.ollama.post("/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
        self.json({"ok": True})

    def model_import(self, b):
        name = str(b.get("name", "")).strip()
        if not NAME_RE.match(name):
            return self.err("模型名稱只能用英數字和 . _ - : /")
        try:
            job = ollama_api.start_import(self.app.jobs, self.app.ollama, name, str(b.get("path", "")),
                                          system=b.get("system") or None, num_ctx=b.get("num_ctx") or None,
                                          quantize=b.get("quantize") or None)
        except ValueError as e:
            return self.err(str(e))
        self.json({"id": job.id})

    def imports(self, q):
        d = self.app.imports_dir
        files = []
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if n.lower().endswith(".gguf") and os.path.isfile(p):
                    files.append({"name": n, "path": p, "size": os.path.getsize(p)})
        self.json({"dir": d, "files": files})

    def upload(self, q):
        self.close_connection = True  # 失敗時內容可能沒讀完
        name = q.get("name", "")
        if not UPLOAD_NAME.match(name) or name != os.path.basename(name):
            return self.err("檔名要是英數字，結尾 .gguf")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self.err("沒有內容")
        os.makedirs(self.app.imports_dir, exist_ok=True)
        free = shutil.disk_usage(self.app.imports_dir).free
        if n + 1024 ** 3 > free:
            return self.err("磁碟空間不夠（剩 %.1f GB）" % (free / 1024 ** 3))
        dst = os.path.join(self.app.imports_dir, name)
        tmp = dst + ".part"
        left = n
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    b = self.rfile.read(min(left, 4 * 1024 * 1024))
                    if not b:
                        raise OSError("上傳中斷")
                    f.write(b)
                    left -= len(b)
            os.replace(tmp, dst)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.json({"path": dst, "size": n})

    # ── 對話（直接轉給 Ollama，一邊生一邊傳） ──
    def chat(self, b):
        if not self.app.guard.chat_start(self.user["name"]):
            return self.err("你已經有對話在生成中，等它結束再送", 429)
        try:
            self._chat(b)
        finally:
            self.app.guard.chat_end(self.user["name"])

    def _chat(self, b):
        name = str(b.get("model", ""))
        if ai_api.ext_model(name):
            return self.chat_external(name, b.get("messages") or [])
        if not NAME_RE.match(name):
            return self.err("請先選模型")
        payload = {"model": name, "messages": b.get("messages") or [], "stream": True}
        if b.get("think") is not None:
            payload["think"] = b["think"] if isinstance(b["think"], (bool, str)) else bool(b["think"])
        opts = b.get("options") if isinstance(b.get("options"), dict) else {}
        clean = {}
        for k, hi in (("temperature", 2.0), ("top_p", 1.0), ("top_k", 200), ("seed", 2 ** 31), ("num_ctx", 32768), ("num_predict", 8192), ("repeat_penalty", 3.0)):
            v = opts.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                clean[k] = max(0, min(hi, v))
        if clean:
            payload["options"] = clean
        if b.get("format") == "json":
            payload["format"] = "json"
        resp, gen = self.app.ollama.stream("/api/chat", payload, timeout=900)
        self.stream_start()
        try:
            for obj in gen:
                self.send_line(obj)
                if obj.get("done") and obj.get("eval_count") is not None:
                    self.app.usage.add(self.user["name"], name, obj.get("prompt_eval_count"), obj.get("eval_count"))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 使用者按了停止
        finally:
            resp.close()  # 關掉就會叫 Ollama 停止生成

    def chat_external(self, name, messages):
        """使用者自己的雲端 AI。只送對話文字（providers 會把 system、圖片丟掉），回傳格式和 Ollama 一樣。"""
        provider, model = ai_api.ext_model(name)
        key, _ = ai_api.key_for(self.app, self.user["name"], provider)
        if not key:
            return self.err("你還沒在「自訂 → 我的 AI」設定 %s 的金鑰" % ai_api.P.PROVIDERS[provider]["name"])
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            return self.err("messages 格式不對")
        self.stream_start()
        usage_row = {"prompt": 0, "completion": 0}
        try:
            for chunk in ai_api.P.stream(provider, key, model, messages, ai_api.SYSTEM_CHAT):
                if isinstance(chunk, tuple):
                    usage_row = chunk[1]
                else:
                    self.send_line({"message": {"role": "assistant", "content": chunk}, "done": False})
            self.send_line({"done": True, "eval_count": usage_row["completion"], "prompt_eval_count": usage_row["prompt"], "model": name})
            self.app.usage.add(self.user["name"], name, usage_row["prompt"], usage_row["completion"], provider=provider)
        except ai_api.P.ProviderError as e:
            self.send_line({"error": str(e), "done": True})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ── 對話紀錄（工作台用） ──
    def session_list(self, q):
        self.json({"sessions": self.app.sessions_for(self.user).list()})

    def session_get(self, q):
        s = self.app.sessions_for(self.user).get(q.get("id", "")) if ID_RE.match(q.get("id", "")) else None
        return self.json(s) if s else self.err("找不到這個對話", 404)

    def session_save(self, b):
        try:
            self.json(self.app.sessions_for(self.user).save(b))
        except ValueError as e:
            self.err(str(e))

    def session_delete(self, b):
        sid = str(b.get("id", ""))
        if not ID_RE.match(sid):
            return self.err("對話編號不對")
        self.json({"ok": self.app.sessions_for(self.user).delete(sid)})

    # ── 背景工作 ──
    def job_list(self, q):
        self.json({"jobs": self.app.jobs.list()})

    def job_stream(self, q):
        job = self.app.jobs.get(q.get("id", ""))
        if not job:
            return self.err("找不到這個工作（伺服器可能重開過）", 404)
        offset = int(q.get("from") or 0)
        self.stream_start()
        try:
            while True:
                evs, offset, finished = job.read(offset, 15)
                for ev in evs:
                    self.send_line(ev)
                if finished:
                    self.send_line({"t": "end", "state": job.state, "result": job.result, "offset": offset})
                    return
                if not evs:
                    self.send_line({"t": "hb", "offset": offset})  # 心跳，順便知道使用者還在不在
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def job_clear(self, b):
        self.app.jobs.clear_finished()
        self.json({"ok": True})

    def job_kill(self, b):
        job = self.app.jobs.get(str(b.get("id", "")))
        if not job:
            return self.err("找不到這個工作", 404)
        self.json({"ok": jobs.kill(job)})

    # ── 終端機 ──
    def term_info(self, q):
        if not self.app.terminal:
            return self.err("終端機在設定裡關掉了（config.json 的 terminal）", 403)
        self.json({"shells": jobs.available_shells(), "home": os.path.expanduser("~"),
                   "platform": sysinfo.static_info()["platform"]})

    def term_run(self, b):
        if not self.app.terminal:
            return self.err("終端機在設定裡關掉了", 403)
        cmd = str(b.get("cmd", "")).strip()
        if not cmd:
            return self.err("沒有指令")
        shells = {s["id"] for s in jobs.available_shells()}
        shell = b.get("shell") if b.get("shell") in shells else sorted(shells)[0]
        job = jobs.start_terminal(self.app.jobs, cmd, str(b.get("cwd") or ""), shell)
        self.json({"id": job.id, "cwd": job.meta["cwd"]})

    def term_input(self, b):
        if not self.app.terminal:
            return self.err("終端機在設定裡關掉了", 403)
        job = self.app.jobs.get(str(b.get("id", "")))
        if not job or job.kind != "term":
            return self.err("找不到這個指令", 404)
        self.json({"ok": jobs.send_input(job, str(b.get("text", "")))})


GET_ROUTES = {
    "/api/status": Handler.status, "/api/agreement": Handler.agreement_get,
    "/api/models": Handler.models, "/api/models/show": Handler.model_show,
    "/api/models/size": Handler.model_size, "/api/models/search": Handler.model_search,
    "/api/models/hf_tags": Handler.model_hf_tags, "/api/imports": Handler.imports, "/api/jobs": Handler.job_list,
    "/api/jobs/stream": Handler.job_stream, "/api/term/info": Handler.term_info,
    "/api/sessions": Handler.session_list, "/api/sessions/get": Handler.session_get,
    **users_api.GET, **rooms_api.GET, **ai_api.GET, **export_api.GET,
}
POST_ROUTES = {
    "/api/agreement/accept": Handler.agreement_accept,
    "/api/models/pull": Handler.model_pull, "/api/models/delete": Handler.model_delete,
    "/api/models/load": Handler.model_load, "/api/models/unload": Handler.model_unload,
    "/api/models/import": Handler.model_import, "/api/models/identify": Handler.model_identify,
    "/api/models/share": Handler.model_share, "/api/chat": Handler.chat,
    "/api/jobs/clear": Handler.job_clear, "/api/jobs/kill": Handler.job_kill,
    "/api/term/run": Handler.term_run, "/api/term/input": Handler.term_input,
    "/api/sessions/save": Handler.session_save, "/api/sessions/delete": Handler.session_delete,
    **users_api.POST, **rooms_api.POST, **ai_api.POST,
}

_log_path = None


def log_error(msg):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if sys.stderr:
        sys.stderr.write(line)
    if _log_path:
        try:
            if os.path.exists(_log_path) and os.path.getsize(_log_path) > 1024 * 1024:
                os.replace(_log_path, _log_path + ".1")
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def make_server(cfg):
    Handler.app = App(cfg)
    srv = ThreadingHTTPServer((cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8780))), Handler)
    srv.daemon_threads = True
    return srv


def main():
    global _log_path
    ap = argparse.ArgumentParser(description="AI 主機面板")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    with open(args.config, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not cfg.get("token") or len(cfg["token"]) < 12:
        raise SystemExit("config.json 的 token 至少要 12 個字")
    cfg.setdefault("data_dir", os.path.dirname(os.path.abspath(args.config)))
    _log_path = os.path.join(cfg["data_dir"], "panel.log")
    if sys.stdout is None:  # pythonw 沒有主控台
        sys.stdout = open(os.devnull, "w")
    try:
        srv = make_server(cfg)
    except OSError as e:
        log_error("無法開在 %s:%s：%s（是不是已經開著一個？）" % (cfg.get("host"), cfg.get("port"), e))
        raise SystemExit(1)
    host = cfg.get("host", "127.0.0.1")
    print("AI 主機面板 %s：http://%s:%s/" % (VERSION, "127.0.0.1" if host in ("0.0.0.0", "") else host,
                                          cfg.get("port", 8780)), flush=True)
    log_error("啟動 %s，監聽 %s:%s" % (VERSION, host, cfg.get("port", 8780)))
    # 解除安裝時用這個 PID 停掉面板（不用指令字串比對）
    pid_path = os.path.join(cfg["data_dir"], "panel.pid")
    try:
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pid_path = None
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for j in Handler.app.jobs.running():
            jobs.kill(j)
        srv.server_close()
        if pid_path:
            try:
                os.remove(pid_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
