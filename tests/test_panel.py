"""面板測試：接假的 Ollama，真的跑終端機指令。

    python -m unittest discover ai-server-kit/tests -v
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
sys.path.insert(0, HERE)

import fake_ollama  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-0123456789"
IS_WIN = os.name == "nt"
PY = sys.executable


def gguf_kv_str(key, val):
    kb, vb = key.encode(), val.encode()
    return struct.pack("<Q", len(kb)) + kb + struct.pack("<I", 8) + struct.pack("<Q", len(vb)) + vb


def gguf_blob(arch, name, tail=4096):
    return (b"GGUF" + struct.pack("<IQQ", 3, 0, 2) + gguf_kv_str("general.architecture", arch)
            + gguf_kv_str("general.name", name) + b"\0" * tail)


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ol = fake_ollama.serve()
        cls.tmp = tempfile.mkdtemp(prefix="aisk-test-")
        cfg = {"host": "127.0.0.1", "port": 0, "token": TOKEN, "terminal": True, "data_dir": cls.tmp,
               "ollama": "http://127.0.0.1:%d" % cls.ol.server_address[1]}
        cls.srv = server.make_server(cfg)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for j in server.Handler.app.jobs.running():
            server.jobs.kill(j)
        cls.srv.shutdown()
        cls.ol.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ── 小工具 ──
    def req(self, path, body=None, method=None, token=TOKEN, raw=False, data=None):
        h = {"Authorization": "Bearer " + token} if token else {}
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
        try:
            resp = urllib.request.urlopen(r, timeout=30)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
        if raw:
            return resp.status, resp
        with resp:
            return resp.status, json.loads(resp.read() or b"{}")

    def stream(self, path, body=None):
        code, resp = self.req(path, body, raw=True)
        self.assertEqual(code, 200)
        with resp:
            return [json.loads(l) for l in resp.read().decode().splitlines() if l.strip()]

    def wait_job(self, job_id, offset=0):
        evs = self.stream("/api/jobs/stream?id=%s&from=%d" % (job_id, offset))
        self.assertEqual(evs[-1]["t"], "end", evs[-1:])
        return evs

    def run_term(self, cmd, shell=None, cwd=None):
        code, r = self.req("/api/term/run", {"cmd": cmd, "shell": shell, "cwd": cwd or self.tmp})
        self.assertEqual(code, 200, r)
        evs = self.wait_job(r["id"])
        out = "".join(e.get("d", "") for e in evs if e["t"] == "o")
        return out, evs[-1]

    # ── 網頁與登入 ──
    def test_static(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertIn("AI 主機面板", r.read().decode())
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        for p in ("/app.js", "/app.css", "/icon.svg", "/manifest.webmanifest"):
            with urllib.request.urlopen(self.base + p, timeout=10) as r:
                self.assertEqual(r.status, 200, p)
        for bad in ("/../server.py", "/..%2fserver.py", "/server.py", "/.hidden"):
            code, _ = self.req(bad, token="")
            self.assertEqual(code, 404, bad)

    def test_auth(self):
        self.assertEqual(self.req("/api/ping", token="")[0], 200)
        self.assertEqual(self.req("/api/status", token="")[0], 401)
        self.assertEqual(self.req("/api/status", token="wrong-token-xxxxxxx")[0], 401)
        self.assertEqual(self.req("/api/term/run", {"cmd": "echo hi"}, token="")[0], 401)
        self.assertEqual(self.req("/api/login", {"token": TOKEN}, token="")[0], 200)
        self.assertEqual(self.req("/api/login", {"token": "nope"}, token="")[0], 401)

    # ── 狀態 ──
    def test_status(self):
        code, s = self.req("/api/status")
        self.assertEqual(code, 200)
        for k in ("hostname", "os", "cpu", "mem", "disks", "gpus", "uptime", "ollama", "panel"):
            self.assertIn(k, s)
        self.assertTrue(s["ollama"]["up"])
        self.assertEqual(s["ollama"]["version"], "0.99.0-fake")
        self.assertGreater(s["mem"]["total"], 0)
        self.assertTrue(s["disks"])
        self.assertIsNotNone(s["cpu"])

    def test_ollama_down(self):
        app = server.Handler.app
        real = app.ollama
        app.ollama = server.ollama_api.Ollama("http://127.0.0.1:9")
        try:
            code, s = self.req("/api/status")
            self.assertEqual(code, 200)
            self.assertFalse(s["ollama"]["up"])
            code, m = self.req("/api/models")
            self.assertEqual(code, 200)
            self.assertFalse(m["up"])
            code, r = self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(code, 502)
            self.assertIn("連不到", r["error"])
        finally:
            app.ollama = real

    # ── 模型 ──
    def test_models_and_show(self):
        code, m = self.req("/api/models")
        self.assertEqual(code, 200)
        names = [x["name"] for x in m["installed"]]
        self.assertIn("qwen3.5:4b", names)
        self.assertTrue(m["catalog"]["groups"])
        self.assertIn("ram", m["hw"])
        code, info = self.req("/api/models/show?name=qwen3.5:4b")
        self.assertIn("thinking", info["capabilities"])
        self.assertEqual(info["context_length"], 32768)
        self.assertEqual(self.req("/api/models/show?name=..%2F..%2Fetc")[0], 400)

    def test_pull(self):
        code, r = self.req("/api/models/pull", {"name": "gemma4:e2b"})
        self.assertEqual(code, 200)
        evs = self.wait_job(r["id"])
        self.assertEqual(evs[-1]["state"], "done")
        self.assertTrue(any(e["t"] == "p" and e.get("total") == 1000 for e in evs))
        self.assertIn("gemma4:e2b", [x["name"] for x in self.req("/api/models")[1]["installed"]])
        code, r = self.req("/api/models/pull", {"name": "bad:model"})
        evs = self.wait_job(r["id"])
        self.assertEqual(evs[-1]["state"], "error")
        self.assertIn("does not exist", evs[-1]["result"]["error"])
        self.assertEqual(self.req("/api/models/pull", {"name": "rm -rf /"})[0], 400)

    def test_load_unload_delete(self):
        self.req("/api/models/pull", {"name": "tmp:1b"})
        time.sleep(0.3)
        self.assertEqual(self.req("/api/models/load", {"name": "tmp:1b"})[0], 200)
        self.assertTrue([m for m in self.req("/api/status")[1]["ollama"]["loaded"] if m["name"] == "tmp:1b"])
        self.assertEqual(self.req("/api/models/unload", {"name": "tmp:1b"})[0], 200)
        self.assertFalse([m for m in self.req("/api/status")[1]["ollama"]["loaded"] if m["name"] == "tmp:1b"])
        self.assertEqual(self.req("/api/models/delete", {"name": "tmp:1b"})[0], 200)
        self.assertEqual(self.req("/api/models/delete", {"name": "tmp:1b"})[0], 502)

    def test_import_gguf(self):
        p = os.path.join(self.tmp, "tiny-model.gguf")
        with open(p, "wb") as f:
            f.write(b"GGUF" + os.urandom(3 * 1024 * 1024))
        code, r = self.req("/api/models/import", {"name": "my-tiny", "path": p, "system": "你是客服", "num_ctx": 8192})
        self.assertEqual(code, 200, r)
        evs = self.wait_job(r["id"])
        self.assertEqual(evs[-1]["state"], "done", evs[-3:])
        self.assertIn("my-tiny:latest", [x["name"] for x in self.req("/api/models")[1]["installed"]])
        # 第二次：檔案已經在 Ollama 裡，不用再傳
        code, r = self.req("/api/models/import", {"name": "my-tiny2", "path": p})
        evs = self.wait_job(r["id"])
        self.assertTrue(any("略過上傳" in e.get("d", "") for e in evs))
        self.assertEqual(self.req("/api/models/import", {"name": "x", "path": os.path.join(self.tmp, "nope.gguf")})[0], 400)
        self.assertEqual(self.req("/api/models/import", {"name": "x", "path": __file__})[0], 400)

    def test_import_safetensors_dir(self):
        d = os.path.join(self.tmp, "st-model")
        os.makedirs(d, exist_ok=True)
        for n, b in (("model.safetensors", os.urandom(4096)), ("config.json", b"{}"), ("tokenizer.json", b"{}"), ("README.md", b"x")):
            with open(os.path.join(d, n), "wb") as f:
                f.write(b)
        files = server.ollama_api.import_files(d)
        self.assertEqual(sorted(n for n, _ in files), ["config.json", "model.safetensors", "tokenizer.json"])
        code, r = self.req("/api/models/import", {"name": "st-model", "path": d})
        self.assertEqual(self.wait_job(r["id"])[-1]["state"], "done")

    def test_upload(self):
        data = b"GGUF" + b"\0" * 100000
        code, r = self.req("/api/upload?name=up-test.gguf", data=data, method="PUT")
        self.assertEqual(code, 200, r)
        self.assertEqual(os.path.getsize(r["path"]), len(data))
        code, r = self.req("/api/imports")
        self.assertIn("up-test.gguf", [f["name"] for f in r["files"]])
        self.assertEqual(self.req("/api/upload?name=..%2F..%2Fevil.gguf", data=b"x", method="PUT")[0], 400)
        self.assertEqual(self.req("/api/upload?name=evil.exe", data=b"x", method="PUT")[0], 400)

    # ── 對話 ──
    def test_chat_stream(self):
        evs = self.stream("/api/chat", {"model": "qwen3.5:4b", "think": True, "options": {"temperature": 0.2},
                                        "messages": [{"role": "user", "content": "你好"}]})
        thinking = "".join(e["message"].get("thinking", "") for e in evs if "message" in e)
        content = "".join(e["message"].get("content", "") for e in evs if "message" in e)
        self.assertIn("你好", thinking)
        self.assertIn("假模型", content)
        self.assertTrue(evs[-1]["done"])
        self.assertEqual(evs[-1]["eval_count"], 42)
        code, r = self.req("/api/chat", {"model": "nope:1b", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(code, 502)
        self.assertIn("not found", r["error"])

    # ── 終端機 ──
    def test_term_echo_and_utf8(self):
        out, end = self.run_term("echo hello-aisk")
        self.assertIn("hello-aisk", out)
        self.assertEqual(end["result"]["exit"], 0)
        self.assertNotIn("__AISK_", out)
        out, _ = self.run_term("echo 中文測試")
        self.assertIn("中文測試", out)

    def test_term_cd_remembered(self):
        sub = os.path.join(self.tmp, "子資料夾")
        os.makedirs(sub, exist_ok=True)
        _, end = self.run_term("cd ..", cwd=sub)
        self.assertEqual(os.path.normcase(end["result"]["cwd"].rstrip("\\/")), os.path.normcase(self.tmp.rstrip("\\/")))

    def test_term_exit_code(self):
        q = '"%s"' % PY
        _, end = self.run_term('%s -c "import sys; print(1); sys.exit(3)"' % q)
        self.assertEqual(end["result"]["exit"], 3)
        self.assertEqual(end["state"], "error")

    def test_term_input_and_kill(self):
        q = '"%s"' % PY
        code, r = self.req("/api/term/run", {"cmd": '%s -c "x=input(); print(\'got\', x)"' % q, "cwd": self.tmp})
        time.sleep(0.5)
        self.assertTrue(self.req("/api/term/input", {"id": r["id"], "text": "abc"})[1]["ok"])
        evs = self.wait_job(r["id"])
        self.assertIn("got abc", "".join(e.get("d", "") for e in evs))

        code, r = self.req("/api/term/run", {"cmd": '%s -c "import time; print(\'start\', flush=True); time.sleep(60)"' % q, "cwd": self.tmp})
        time.sleep(0.8)
        t0 = time.time()
        self.assertTrue(self.req("/api/jobs/kill", {"id": r["id"]})[1]["ok"])
        evs = self.wait_job(r["id"])
        self.assertEqual(evs[-1]["state"], "killed")
        self.assertLess(time.time() - t0, 10)

    def test_job_reconnect_offset(self):
        q = '"%s"' % PY
        code, r = self.req("/api/term/run", {"cmd": '%s -c "import time; [(print(\'line\', i, flush=True), time.sleep(0.2)) for i in range(4)]"' % q, "cwd": self.tmp})
        evs = self.wait_job(r["id"])
        n = len([e for e in evs if e["t"] != "end"])
        self.assertGreaterEqual(n, 1)
        again = self.wait_job(r["id"], offset=n)
        self.assertEqual([e["t"] for e in again], ["end"])  # 從最後接著讀：只剩結束訊號
        self.assertIn("line 3", "".join(e.get("d", "") for e in evs))

    @unittest.skipUnless(IS_WIN, "只有 Windows 有 PowerShell 5.1")
    def test_powershell(self):
        out, end = self.run_term("Write-Output '中文 PowerShell'; Get-Item . | Select-Object Name", shell="powershell")
        self.assertIn("中文 PowerShell", out)
        self.assertEqual(end["result"]["exit"], 0)
        _, end = self.run_term("Set-Location ..", shell="powershell", cwd=os.path.join(self.tmp))
        self.assertEqual(os.path.normcase(end["result"]["cwd"].rstrip("\\")), os.path.normcase(os.path.dirname(self.tmp.rstrip("\\"))))
        _, end = self.run_term("Get-Item C:\\no-such-thing-aisk", shell="powershell")
        self.assertEqual(end["result"]["exit"], 1)
        out, end = self.run_term("& '%s' -c \"import sys; sys.exit(5)\"" % PY, shell="powershell")
        self.assertEqual(end["result"]["exit"], 5)

    # ── 搜尋全網模型（把對外的 web_get 換成假的，不碰網路） ──
    def test_search_parse(self):
        import ollama_api as o
        html = ('<a href="/library/foo" class="group grid"><span>tools</span><span>vision</span>'
                '<p class="mt-1 line-clamp-2 text-x">Hello &amp; world</p>'
                '<span title="1,234 downloads"><span>1.2K</span></span></a>'
                '<a href="/library/bar" class="group grid"><p class="mt-1 line-clamp-2 a">Second</p></a>')

        def fake(url, timeout=15, as_json=False):
            if "ollama.com/search" in url:
                return html
            if "huggingface.co/api/models?" in url:
                return [{"id": "u/Repo-GGUF", "downloads": 9, "likes": 3, "gated": False}]
            if "huggingface.co/api/models/" in url:
                return {"siblings": [{"rfilename": "Repo-Q4_K_M.gguf"}, {"rfilename": "Repo-Q8_0.gguf"},
                                     {"rfilename": "readme.md"}], "gated": False}
            return ""

        orig = o.web_get
        o.web_get = fake
        o._search_cache.clear()
        try:
            lib = o.search_library("zzparse")
            self.assertEqual([m["name"] for m in lib], ["foo", "bar"])
            self.assertEqual(lib[0]["caps"], ["tools", "vision"])
            self.assertEqual(lib[0]["desc"], "Hello & world")
            self.assertEqual(lib[0]["pulls"], 1234)
            self.assertEqual(o.search_hf("zzparse")[0]["repo"], "u/Repo-GGUF")
            self.assertEqual(o.hf_quant_tags("u/Repo-GGUF")["tags"], ["Q4_K_M", "Q8_0"])
        finally:
            o.web_get = orig
            o._search_cache.clear()

    def test_search_route(self):
        import ollama_api as o
        orig_l, orig_h = o.search_library, o.search_hf
        o.search_library = lambda q, limit=30: [{"name": "qwen3.5", "desc": "d", "caps": ["tools"], "pulls": 100, "source": "ollama"}]
        o.search_hf = lambda q, limit=25: [{"name": "u/r-GGUF", "repo": "u/r-GGUF", "pulls": 5, "likes": 2, "gated": False, "source": "hf"}]
        try:
            code, r = self.req("/api/models/search?q=qwen&source=all")
            self.assertEqual(code, 200)
            self.assertEqual(r["ollama"][0]["name"], "qwen3.5")
            self.assertEqual(r["hf"][0]["repo"], "u/r-GGUF")
            self.assertEqual(self.req("/api/models/search?q=a")[0], 400)
        finally:
            o.search_library, o.search_hf = orig_l, orig_h
        self.assertEqual(self.req("/api/models/hf_tags?repo=badname")[0], 400)

    def test_gguf_identify(self):
        import ollama_api as o
        p = os.path.join(self.tmp, "mymodel-Q4_K_M.gguf")
        with open(p, "wb") as f:
            f.write(gguf_blob("llama", "My Secret Model"))
        info = o.gguf_info(p)
        self.assertEqual(info["architecture"], "llama")
        self.assertEqual(info["name"], "My Secret Model")
        orig = o.find_online
        o.find_online = lambda q, limit=8: {"query": q, "found": False, "ollama": [], "hf": []}
        try:
            code, r = self.req("/api/models/identify", {"path": p})
            self.assertEqual(code, 200)
            self.assertFalse(r["found"])
            self.assertEqual(r["info"]["name"], "My Secret Model")
            self.assertEqual(self.req("/api/models/identify", {"path": os.path.join(self.tmp, "nope.gguf")})[0], 400)
        finally:
            o.find_online = orig

    def _accept_agreement(self):
        v = self.req("/api/agreement")[1]["version"]
        return self.req("/api/agreement/accept", {"version": v})

    def test_agreement(self):
        app = server.Handler.app
        agpath = os.path.join(app.data_dir, "agreement.json")
        if os.path.exists(agpath):
            os.remove(agpath)
        try:
            code, a = self.req("/api/agreement")
            self.assertEqual(code, 200)
            self.assertFalse(a["accepted"])
            self.assertIn("使用條款", a["text"])
            # 版本不對 → 409
            self.assertEqual(self.req("/api/agreement/accept", {"version": "bogus"})[0], 409)
            self.assertEqual(self._accept_agreement()[0], 200)
            self.assertTrue(self.req("/api/agreement")[1]["accepted"])
            # 這台沒設定分享對象，所以同意協議後 share_optin 仍為 False
            self.assertFalse(self.req("/api/agreement")[1]["share_optin"])
        finally:
            if os.path.exists(agpath):
                os.remove(agpath)

    def test_share_opt_in(self):
        received = []

        class RX(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                received.append((self.path, self.headers.get("Authorization"), self.rfile.read(n)))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        rx = ThreadingHTTPServer(("127.0.0.1", 0), RX)
        threading.Thread(target=rx.serve_forever, daemon=True).start()
        app = server.Handler.app
        agpath = os.path.join(app.data_dir, "agreement.json")
        if os.path.exists(agpath):
            os.remove(agpath)
        # 沒設定分享對象 → /api/models 回報 configured=False、拒絕分享
        self.assertFalse(self.req("/api/models")[1]["share"]["configured"])
        self.assertEqual(self.req("/api/models/share", {"path": "x"})[0], 403)
        app.share = {"url": "http://127.0.0.1:%d/upload" % rx.server_address[1], "label": "製作人", "token": "sek"}
        try:
            # 有設定分享對象但還沒同意協議 → configured 但 enabled=False，分享仍被擋
            s = self.req("/api/models")[1]["share"]
            self.assertTrue(s["configured"])
            self.assertFalse(s["enabled"])
            self.assertEqual(self.req("/api/models/share", {"path": "x"})[0], 403)
            # 同意使用協議（一個勾）→ 分享按鈕開啟；實際傳送仍要本人按（這裡就是那個明確呼叫）
            self._accept_agreement()
            self.assertTrue(self.req("/api/models")[1]["share"]["enabled"])
            p = os.path.join(self.tmp, "share-me.gguf")
            with open(p, "wb") as f:
                f.write(gguf_blob("llama", "Private One", tail=5000))
            code, r = self.req("/api/models/share", {"path": p})
            self.assertEqual(code, 200, r)
            evs = self.wait_job(r["id"])
            self.assertEqual(evs[-1]["state"], "done", evs[-3:])
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][1], "Bearer sek")
            self.assertEqual(len(received[0][2]), os.path.getsize(p))
        finally:
            app.share = {}
            if os.path.exists(agpath):
                os.remove(agpath)
            rx.shutdown()

    def test_terminal_disabled(self):
        app = server.Handler.app
        app.terminal = False
        try:
            self.assertEqual(self.req("/api/term/run", {"cmd": "echo hi"})[0], 403)
            self.assertEqual(self.req("/api/term/info")[0], 403)
        finally:
            app.terminal = True


class MarkerTest(unittest.TestCase):
    """記號被切在兩個片段中間也要認得出來。"""

    def test_split_marker(self):
        import io
        from jobs import Job, _pump

        marker = "__AISK_deadbeef__"
        payload = ("hello\npartial" + marker + "0|C:\\x\nafter\n").encode()

        class P:
            def __init__(self):
                self.stdout = self
                self.chunks = [payload[i:i + 5] for i in range(0, len(payload), 5)]

            def read1(self, n):
                return self.chunks.pop(0) if self.chunks else b""

            def wait(self):
                return 0

        job = Job("term", "x")
        _pump(job, P(), marker, "C:\\")
        out = "".join(e["d"] for e in job.events)
        self.assertEqual(out, "hello\npartialafter\n")
        self.assertEqual(job.result, {"exit": 0, "cwd": "C:\\x"})
        self.assertEqual(job.state, "done")
        del io


if __name__ == "__main__":
    unittest.main()
