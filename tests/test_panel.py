"""面板測試：接假的 Ollama，真的跑終端機指令。

    python -m unittest discover ai-server-kit/tests -v
"""
import io
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
    def setUp(self):
        server.Handler.app.guard.hits.clear()   # 每個測試的頻率限制各自算，不然測試之間會互相擋到
        server.Handler.app.fails.clear()        # 登入失敗次數也是

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

    def test_studio_static(self):
        import re
        with urllib.request.urlopen(self.base + "/studio/", timeout=10) as r:
            page = r.read().decode()
        scripts = re.findall(r'<script src="([\w.-]+)"', page)      # 頁面引用的每一支程式都要真的存在
        self.assertIn("studio.js", scripts)
        for p in ["/studio/", "/studio/index.html", "/studio/studio.css"] + ["/studio/" + s for s in scripts]:
            with urllib.request.urlopen(self.base + p, timeout=10) as r:
                self.assertEqual(r.status, 200, p)
        with urllib.request.urlopen(self.base + "/studio/", timeout=10) as r:
            self.assertIn("Kiki Cloude", r.read().decode())
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        for bad in ("/studio/../server.py", "/studio/..%2findex.html", "/studio/nope.js"):
            code, _ = self.req(bad, token="")
            self.assertEqual(code, 404, bad)

    def test_port_in_use(self):
        # 同一個埠再開一個面板要直接報錯（Windows 預設會讓第二個「成功」綁上去，連線跑到哪個不一定）
        with self.assertRaises(OSError):
            server.PanelServer(("127.0.0.1", self.srv.server_address[1]), server.Handler).server_close()

    def test_sessions(self):
        self.assertEqual(self.req("/api/sessions", token="")[0], 401)
        code, r = self.req("/api/sessions/save", {"title": "第一個", "model": "qwen3.5:4b",
                                                  "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual(code, 200, r)
        sid = r["id"]
        self.assertRegex(sid, r"^[a-f0-9]{12}$")
        code, r = self.req("/api/sessions/save", {"id": sid, "title": "改名", "model": "qwen3.5:4b", "pinned": True,
                                                  "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "嗨"}]})
        self.assertEqual(code, 200, r)
        code, r = self.req("/api/sessions")
        row = next(s for s in r["sessions"] if s["id"] == sid)
        self.assertEqual((row["title"], row["count"], row["pinned"]), ("改名", 2, True))
        code, r = self.req("/api/sessions/get?id=" + sid)
        self.assertEqual(r["messages"][1]["content"], "嗨")
        self.assertEqual(self.req("/api/sessions/get?id=zzz")[0], 404)
        self.assertEqual(self.req("/api/sessions/save", {"id": "../x", "messages": []})[0], 400)
        self.assertEqual(self.req("/api/sessions/save", {"messages": "不是清單"})[0], 400)
        # 專案指示：存得進去、讀得回來，超過上限會截掉
        code, r = self.req("/api/sessions/save", {"id": sid, "title": "改名", "instructions": "先講結論" + "x" * 5000, "messages": []})
        self.assertEqual((code, len(r["instructions"])), (200, 4000), r)
        self.assertTrue(self.req("/api/sessions/get?id=" + sid)[1]["instructions"].startswith("先講結論"))
        code, r = self.req("/api/sessions/delete", {"id": sid})
        self.assertTrue(r["ok"])
        self.assertEqual(self.req("/api/sessions/get?id=" + sid)[0], 404)

    def test_accounts(self):
        # 申請 → 還不能登入 → 管理員核准 → 登入 → 只能用對話相關的功能 → 停用後憑證失效
        self.assertEqual(self.req("/api/apply", {"user": "Amy", "password": "short", "note": "x"}, token="")[0], 400)
        self.assertEqual(self.req("/api/apply", {"user": "bad name!", "password": "longenough1"}, token="")[0], 400)
        code, r = self.req("/api/apply", {"user": "amy", "password": "longenough1", "note": "朋友", "email": "amy@gmail.com"}, token="")
        self.assertEqual((code, r["status"]), (200, "pending"), r)
        server.Handler.app.guard.hits.clear()
        self.assertEqual(self.req("/api/apply", {"user": "amy", "password": "longenough1", "email": "amy2@gmail.com"}, token="")[0], 400)
        code, r = self.req("/api/login", {"user": "amy", "password": "longenough1"}, token="")
        self.assertEqual(code, 403, r)
        code, r = self.req("/api/me")
        self.assertEqual((r["role"], r["pending"], r["needs_setup"]), ("admin", 1, True))
        code, r = self.req("/api/users")
        self.assertEqual([u["name"] for u in r["users"] if u["status"] == "pending"], ["amy"])
        self.assertEqual(self.req("/api/users/approve", {"user": "amy"})[0], 200)
        code, r = self.req("/api/login", {"user": "AMY", "password": "wrong-pass-1"}, token="")
        self.assertEqual(code, 401)
        code, r = self.req("/api/login", {"user": "AMY", "password": "longenough1"}, token="")
        self.assertEqual((code, r["user"], r["role"]), (200, "amy", "user"), r)
        amy = r["token"]
        self.assertEqual(self.req("/api/me", token=amy)[1]["name"], "amy")
        self.assertEqual(self.req("/api/status", token=amy)[0], 200)
        self.assertEqual(self.req("/api/users", token=amy)[0], 403)
        self.assertEqual(self.req("/api/term/run", {"cmd": "echo hi"}, token=amy)[0], 403)
        self.assertEqual(self.req("/api/models/pull", {"name": "x:1b"}, token=amy)[0], 403)
        # 對話紀錄各自分開
        code, r = self.req("/api/sessions/save", {"title": "amy 的", "model": "qwen3.5:4b", "messages": []}, token=amy)
        self.assertEqual(code, 200, r)
        self.assertEqual([x["title"] for x in self.req("/api/sessions", token=amy)[1]["sessions"]], ["amy 的"])
        self.assertNotIn("amy 的", [x["title"] for x in self.req("/api/sessions")[1]["sessions"]])
        # 改密碼、登出、停用
        self.assertEqual(self.req("/api/password", {"old": "nope-nope-1", "password": "newpassword9"}, token=amy)[0], 403)
        self.assertEqual(self.req("/api/password", {"old": "longenough1", "password": "newpassword9"}, token=amy)[0], 200)
        self.assertEqual(self.req("/api/me", token=amy)[0], 401)   # 改密碼後舊憑證失效
        amy = self.req("/api/login", {"user": "amy", "password": "newpassword9"}, token="")[1]["token"]
        self.assertEqual(self.req("/api/logout", {}, token=amy)[0], 200)
        self.assertEqual(self.req("/api/me", token=amy)[0], 401)
        amy = self.req("/api/login", {"user": "amy", "password": "newpassword9"}, token="")[1]["token"]
        self.assertEqual(self.req("/api/users/disable", {"user": "amy"})[0], 200)
        self.assertEqual(self.req("/api/me", token=amy)[0], 401)
        self.assertEqual(self.req("/api/login", {"user": "amy", "password": "newpassword9"}, token="")[0], 403)
        # 用主登入碼建管理員帳號，之後管理員帳號也能管人
        code, r = self.req("/api/users/setup", {"user": "boss", "password": "bosspassword1"})
        self.assertEqual((code, r["role"]), (200, "admin"), r)
        boss = self.req("/api/login", {"user": "boss", "password": "bosspassword1"}, token="")[1]["token"]
        self.assertFalse(self.req("/api/me", token=boss)[1]["needs_setup"])
        self.assertEqual(self.req("/api/users/enable", {"user": "amy"}, token=boss)[0], 200)
        self.assertEqual(self.req("/api/users/disable", {"user": "boss"}, token=boss)[0], 400)
        self.assertEqual(self.req("/api/users/reject", {"user": "amy"}, token=boss)[0], 200)
        self.assertEqual(self.req("/api/users/reject", {"user": "boss"})[0], 200)

    def test_external_ai(self):
        # 假的雲端服務：把 providers._open 換掉，回 OpenAI 格式的 SSE
        import io
        import providers
        seen = {}

        def fake_open(url, headers, body):
            seen["url"], seen["headers"], seen["body"] = url, headers, body
            if "bad" in headers.get("Authorization", ""):
                raise providers.ProviderError("api.x.ai 回應 401：bad key")
            lines = ['data: {"choices":[{"delta":{"content":"OK\u5f9e\u96f2\u7aef"}}]}',
                     'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}', "data: [DONE]"]
            return io.BytesIO("\n".join(lines).encode("utf-8"))
        real = providers._open
        providers._open = fake_open
        try:
            self.req("/api/users/setup", {"user": "zed", "password": "zedpassword1"})
            zed = self.req("/api/login", {"user": "zed", "password": "zedpassword1"}, token="")[1]["token"]
            self.assertEqual(self.req("/api/ai/set", {"provider": "xai", "key": "xai-test-key"})[0], 400)   # 主登入碼沒有個人設定
            code, r = self.req("/api/ai/set", {"provider": "xai", "key": "xai-test-key", "model": "grok-4"}, token=zed)
            self.assertEqual(code, 200, r)
            self.assertEqual(r["ai"][0]["has_key"], True)
            self.assertNotIn("xai-test-key", json.dumps(r))
            code, r = self.req("/api/ai/test", {"provider": "xai"}, token=zed)
            self.assertEqual((code, r["reply"]), (200, "OK從雲端"), r)
            self.assertEqual(seen["headers"]["Authorization"], "Bearer xai-test-key")
            # 對話：system 和主機狀態不會送出去，只有對話文字
            code, resp = self.req("/api/chat", {"model": "ext:xai:grok-4", "messages": [
                {"role": "system", "content": "以下是這台主機現在的狀態 SECRET"}, {"role": "user", "content": "哈囉"}]}, token=zed, raw=True)
            self.assertEqual(code, 200)
            with resp:
                evs = [json.loads(l) for l in resp.read().decode().splitlines() if l.strip()]
            self.assertNotIn("SECRET", json.dumps(seen["body"], ensure_ascii=False))
            self.assertEqual(seen["body"]["messages"][-1]["content"], "哈囉")
            self.assertTrue(evs[-1]["done"]) ; self.assertEqual(evs[-1]["eval_count"], 3)
            self.assertIn("OK從雲端", "".join(e.get("message", {}).get("content", "") for e in evs))
            # 群組接 Grok：@grok 才回
            rid = self.req("/api/rooms/create", {"title": "雲端群"}, token=zed)[1]["id"]
            self.assertEqual(self.req("/api/rooms/bot", {"id": rid, "provider": "anthropic"}, token=zed)[0], 400)   # 沒設 Claude
            code, r = self.req("/api/rooms/bot", {"id": rid, "provider": "xai"}, token=zed)
            self.assertIn("grok", r["bots"])
            self.req("/api/rooms/post", {"id": rid, "content": "@grok 幫我看看"}, token=zed)
            for _ in range(60):
                msgs = self.req("/api/rooms/get?id=%s&since=0" % rid, token=zed)[1]["messages"]
                ai = [m for m in msgs if m["role"] == "assistant"]
                if ai and ai[-1].get("done"):
                    break
                time.sleep(0.2)
            self.assertEqual((ai[-1]["user"], ai[-1]["content"]), ("grok", "OK從雲端"))
            self.assertIn("zed：@grok 幫我看看", seen["body"]["messages"][-1]["content"])
            self.req("/api/apply", {"user": "guest1", "password": "guestpass1", "email": "guest1@gmail.com"}, token="")
            self.req("/api/users/approve", {"user": "guest1"})
            g1 = self.req("/api/login", {"user": "guest1", "password": "guestpass1"}, token="")[1]["token"]
            self.req("/api/rooms/invite", {"id": rid, "user": "guest1"}, token=zed)
            seen.clear()
            self.req("/api/rooms/post", {"id": rid, "content": "@grok 我也問"}, token=g1)
            time.sleep(0.6)
            self.assertEqual(seen, {})   # 不是接 AI 的本人 @，不會動用 zed 的金鑰
            self.req("/api/users/reject", {"user": "guest1"})
            self.assertEqual(self.req("/api/ai/remove", {"provider": "xai"}, token=zed)[1]["ai"], [])
            self.req("/api/rooms/remove", {"id": rid}, token=zed)
            self.req("/api/users/reject", {"user": "zed"})
        finally:
            providers._open = real

    def test_usage_heatmap(self):
        before = sum(self.req("/api/usage/me?days=7")[1]["days"][-1]["cells"])   # 其他測試也會記到 admin
        self.stream("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}]})
        code, r = self.req("/api/usage/me?days=7")
        self.assertEqual((code, len(r["days"]), r["days"][-1]["label"]), (200, 7, "今天"))
        self.assertEqual(sum(r["days"][-1]["cells"]) - before, 54)   # 假模型 12 + 42
        self.assertGreaterEqual(r["days"][-1]["calls"], 1)

    def test_guard(self):
        import guard
        # 頻率：同一路徑一分鐘超過上限就 429（申請帳號 3 次）
        g = server.Handler.app.guard
        g.hits.clear()
        codes = [self.req("/api/apply", {"user": "r%d" % i, "password": "x", "email": "r%d@gmail.com" % i}, token="")[0] for i in range(5)]
        self.assertEqual(codes[-1], 429, codes)
        g.hits.clear(); server.Handler.app.fails.clear()
        # 內容大小：一般路徑 256KB 上限
        try:
            code, r = self.req("/api/sessions/delete", {"id": "x" * (300 * 1024)})
            self.assertEqual(code, 400); self.assertIn("內容太大", r["error"])
        except (ConnectionAbortedError, ConnectionResetError):
            pass   # 伺服器不讀太大的內容就直接回絕並斷線，Windows 上有時客戶端來不及收到 400
        # 同時對話數：佔滿後第三個被擋
        self.assertTrue(g.chat_start("admin")); self.assertTrue(g.chat_start("admin"))
        self.assertEqual(self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]})[0], 429)
        g.chat_end("admin"); g.chat_end("admin")
        self.assertEqual(guard.Guard.body_max("/api/chat"), 24 * 1024 * 1024)
        # 外部模型名稱不能夾帶網址字元
        self.assertEqual(self.req("/api/users/setup", {"user": "gdx", "password": "gdpassword1"})[0], 200)
        gd = self.req("/api/login", {"user": "gdx", "password": "gdpassword1"}, token="")[1]["token"]
        self.assertEqual(self.req("/api/ai/set", {"provider": "gemini", "key": "k" * 20, "model": "x?y=1"}, token=gd)[0], 400)
        self.assertEqual(self.req("/api/ai/set", {"provider": "gemini", "key": "bad key", "model": "gemini-2.5-flash"}, token=gd)[0], 400)
        # 同一帳號猜密碼 10 次就鎖，正確密碼也進不去，10 分鐘後解
        for _ in range(10):
            self.req("/api/login", {"user": "gdx", "password": "wrong-pass-1"}, token="")
            server.Handler.app.fails.clear(); g.hits.clear()
        self.assertEqual(self.req("/api/login", {"user": "gdx", "password": "gdpassword1"}, token="")[0], 401)
        server.Handler.app.users.fails.pop("gdx", None)
        self.assertEqual(self.req("/api/login", {"user": "gdx", "password": "gdpassword1"}, token="")[0], 200)
        self.req("/api/users/reject", {"user": "gdx"})

    def test_export_zip(self):
        import zipfile
        self.req("/api/users/setup", {"user": "exp", "password": "exppassword1"})
        tok = self.req("/api/login", {"user": "exp", "password": "exppassword1"}, token="")[1]["token"]
        self.req("/api/sessions/save", {"title": "台股/筆記?", "model": "qwen3.5:4b", "group": "研究", "instructions": "一律先講結論",
                                        "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "by": "kiki", "content": "<think>想</think>嗨"}]}, token=tok)
        # 附件只是對話裡的資料：檔名再怪也不會變成路徑
        evil = {"name": "../../evil\\x.txt", "type": "text", "size": 5, "text": "附件裡的字"}
        self.req("/api/sessions/save", {"title": "第二個", "model": "qwen3.5:4b",
                                        "messages": [{"role": "user", "content": "再來", "files": [evil]}]}, token=tok)
        rid = self.req("/api/rooms/create", {"title": "小隊"}, token=tok)[1]["id"]
        self.req("/api/rooms/post", {"id": rid, "content": "群組第一句"}, token=tok)
        code, resp = self.req("/api/export/all", token=tok, raw=True)
        self.assertEqual(code, 200)
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        with resp:
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
        names = z.namelist()
        self.assertIn("專案/研究/台股_筆記_.txt", names)
        self.assertIn("專案/研究/台股_筆記_.記憶檔.md", names)
        self.assertIn("專案/第二個.txt", names)
        self.assertIn("群組/小隊.txt", names)
        self.assertIn("index.md", names)
        md = z.read("專案/研究/台股_筆記_.記憶檔.md").decode("utf-8")
        self.assertIn("### 2. Kiki", md); self.assertIn("嗨", md); self.assertNotIn("<think>", md)
        self.assertIn("## 專案指示", md); self.assertIn("一律先講結論", md)
        self.assertNotIn("## 專案指示", z.read("專案/第二個.記憶檔.md").decode("utf-8"))
        self.assertFalse([n for n in names if ".." in n or "evil" in n or n.startswith("/")], names)
        self.assertIn("〔附件：.._.._evil_x.txt〕", z.read("專案/第二個.txt").decode("utf-8"))
        self.assertIn("附件裡的字", "".join(z.read(n).decode("utf-8") for n in names if n.startswith("原始JSON/")))
        self.assertIn("群組第一句", z.read("群組/小隊.txt").decode("utf-8"))
        self.assertEqual(self.req("/api/export/all", token="")[0], 401)
        self.req("/api/users/reject", {"user": "exp"})

    def test_admin_dashboard(self):
        self.req("/api/apply", {"user": "dashu", "password": "dashupass1", "email": "dashu@gmail.com"}, token="")
        self.req("/api/users/approve", {"user": "dashu"})
        tok = self.req("/api/login", {"user": "dashu", "password": "dashupass1"}, token="")[1]["token"]
        sid = self.req("/api/sessions/save", {"title": "小明的專案", "model": "qwen3.5:4b", "messages": [{"role": "user", "content": "祕密"}]}, token=tok)[1]["id"]
        code, resp = self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]}, token=tok, raw=True)
        resp.read(); resp.close()
        self.assertEqual(self.req("/api/admin/overview", token=tok)[0], 403)   # 一般帳號不能看
        ov = self.req("/api/admin/overview")[1]
        me = next(u for u in ov["users"] if u["name"] == "dashu")
        self.assertEqual((me["projects"], me["messages"]), (1, 1)); self.assertGreater(me["tokens"], 0)
        # 總覽讀的是對話索引：存檔、刪除之後數字要跟著變，而且不會再去掃資料夾
        sid2 = self.req("/api/sessions/save", {"title": "第二個", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}, token=tok)[1]["id"]
        store = server.Handler.app.sessions_for({"name": "dashu"})
        scans = []
        real_list, store.list = store.list, lambda: scans.append(1) or real_list()
        try:
            me = next(u for u in self.req("/api/admin/overview")[1]["users"] if u["name"] == "dashu")
            self.assertEqual((me["projects"], me["messages"]), (2, 3))
            self.req("/api/sessions/delete", {"id": sid2}, token=tok)
            me = next(u for u in self.req("/api/admin/overview")[1]["users"] if u["name"] == "dashu")
            self.assertEqual((me["projects"], me["messages"]), (1, 1))
        finally:
            store.list = real_list
        self.assertEqual(scans, [])
        for p in ("day", "week", "month", "year"):
            us = self.req("/api/admin/usage?period=%s" % p)[1]
            self.assertIn("dashu", us["users"]); self.assertTrue(us["buckets"])
        self.assertEqual(self.req("/api/admin/usage?period=bogus")[1]["period"], "day")
        self.assertEqual(self.req("/api/admin/sessions?user=dashu")[1]["sessions"][0]["id"], sid)
        self.assertEqual(self.req("/api/admin/session?user=dashu&id=" + sid)[1]["messages"][0]["content"], "祕密")
        self.assertEqual(self.req("/api/admin/session?user=../x&id=" + sid)[0], 400)
        self.req("/api/users/reject", {"user": "dashu"})

    def test_gmail_verification(self):
        import mail as mailmod
        sent = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=0): sent.append({"host": host, "port": port})
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, u, p): sent[-1]["login"] = (u, p)
            def send_message(self, msg): sent[-1]["to"] = msg["To"]; sent[-1]["body"] = msg.get_content()
        real = mailmod.smtplib.SMTP
        mailmod.smtplib.SMTP = FakeSMTP
        try:
            self.assertEqual(self.req("/api/apply", {"user": "nomail", "password": "longenough1"}, token="")[0], 400)   # 沒填 Gmail
            self.assertEqual(self.req("/api/apply", {"user": "nomail", "password": "longenough1", "email": "x@yahoo.com"}, token="")[0], 400)
            # 還沒設定寄信 → 直接進待核准
            code, r = self.req("/api/apply", {"user": "nomail", "password": "longenough1", "email": "nomail@gmail.com"}, token="")
            self.assertEqual((code, r["status"]), (200, "pending"))
            self.assertEqual(self.req("/api/mail/set", {"user": "boss@gmail.com", "password": "app-pass"}, token="")[0], 401)
            self.assertEqual(self.req("/api/mail/set", {"user": "boss@gmail.com", "password": "app-pass"})[1]["configured"], True)
            self.assertNotIn("app-pass", json.dumps(self.req("/api/mail")[1]))
            self.assertEqual(self.req("/api/mail/test", {})[1]["to"], "boss@gmail.com")
            # 設定後 → 寄驗證碼
            server.Handler.app.guard.hits.clear()
            code, r = self.req("/api/apply", {"user": "gmx", "password": "longenough1", "email": "Gm.User+1@gmail.com"}, token="")
            self.assertEqual((code, r["status"]), (200, "verify"), r)
            self.assertEqual(sent[-1]["to"], "gm.user+1@gmail.com"); self.assertEqual(sent[-1]["login"], ("boss@gmail.com", "app-pass"))
            import re as _re
            code6 = _re.search(r"驗證碼：(\d{6})", sent[-1]["body"]).group(1)
            self.req("/api/users/approve", {"user": "gmx"})
            self.assertEqual(self.req("/api/login", {"user": "gmx", "password": "longenough1"}, token="")[0], 403)   # 沒驗證不能登入
            self.assertEqual(self.req("/api/apply/verify", {"user": "gmx", "code": "000000"}, token="")[0], 400)
            self.assertEqual(self.req("/api/apply/verify", {"user": "gmx", "code": code6}, token="")[0], 200)
            self.assertEqual(self.req("/api/login", {"user": "gmx", "password": "longenough1"}, token="")[0], 200)
            self.assertEqual(self.req("/api/apply", {"user": "gmx2", "password": "longenough1", "email": "gm.user+1@gmail.com"}, token="")[0], 400)   # 信箱重複
            self.assertTrue(next(u for u in self.req("/api/users")[1]["users"] if u["name"] == "gmx")["verified"])
        finally:
            mailmod.smtplib.SMTP = real
            server.Handler.app.mailer.cfg = {}
            self.req("/api/users/reject", {"user": "nomail"}); self.req("/api/users/reject", {"user": "gmx"})

    def test_quota(self):
        self.req("/api/apply", {"user": "quo", "password": "quopassword1", "email": "quo@gmail.com"}, token="")
        self.req("/api/users/approve", {"user": "quo"})
        tok = self.req("/api/login", {"user": "quo", "password": "quopassword1"}, token="")[1]["token"]
        self.assertEqual(self.req("/api/users/quota", {"user": "quo", "day": 100}, token=tok)[0], 403)   # 只有管理員能設
        code, r = self.req("/api/users/quota", {"user": "quo", "day": 100, "week": 0})
        self.assertEqual((code, r["quota"]), (200, {"day": 100, "week": 0}))
        code, resp = self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]}, token=tok, raw=True)
        self.assertEqual(code, 200); resp.read(); resp.close()      # 第一次 54 tokens，還沒超過
        self.assertEqual(self.req("/api/me", token=tok)[1]["used"]["day"], 54)
        code, resp = self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]}, token=tok, raw=True)
        self.assertEqual(code, 200); resp.read(); resp.close()      # 108，超過了
        code, r = self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]}, token=tok)
        self.assertEqual(code, 429); self.assertIn("今天", r["error"])
        rid = self.req("/api/rooms/create", {"title": "q"}, token=tok)[1]["id"]
        m = self.req("/api/rooms/post", {"id": rid, "content": "@kiki 在嗎"}, token=tok)[1]
        self.assertIn("quota_note", m)
        time.sleep(0.5)
        self.assertFalse([x for x in self.req("/api/rooms/get?id=%s&since=0" % rid, token=tok)[1]["messages"] if x["role"] == "assistant"])
        self.req("/api/users/quota", {"user": "quo", "day": 0, "week": 0})
        self.assertEqual(self.req("/api/chat", {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "x"}]}, token=tok, raw=True)[0], 200)
        self.req("/api/rooms/remove", {"id": rid}, token=tok)
        self.req("/api/users/reject", {"user": "quo"})

    def test_rooms(self):
        self.req("/api/apply", {"user": "bob", "password": "bobpassword1", "email": "bob@gmail.com"}, token="")
        self.req("/api/users/approve", {"user": "bob"})
        bob = self.req("/api/login", {"user": "bob", "password": "bobpassword1"}, token="")[1]["token"]
        code, room = self.req("/api/rooms/create", {"title": "研究室"})
        self.assertEqual((code, room["owner"], room["model"]), (200, "admin", "qwen3.5:4b"), room)
        rid = room["id"]
        self.assertEqual(self.req("/api/rooms/get?id=" + rid, token=bob)[0], 404)   # 還沒被邀
        self.assertEqual(self.req("/api/rooms/invite", {"id": rid, "user": "nobody"})[0], 400)
        self.assertEqual(self.req("/api/rooms/invite", {"id": rid, "user": "bob"})[0], 200)
        self.assertEqual(self.req("/api/rooms/invite", {"id": rid, "user": "bob"}, token=bob)[0], 400)   # 不是群主
        self.assertIn("研究室", [r["title"] for r in self.req("/api/rooms", token=bob)[1]["rooms"]])
        self.assertIn("bob", self.req("/api/rooms/people")[1]["people"])   # 主登入碼不是帳號，所以 bob 自己看到的是空的
        code, m = self.req("/api/rooms/post", {"id": rid, "content": "大家好"}, token=bob)
        self.assertEqual((code, m["user"]), (200, "bob"))
        code, r = self.req("/api/rooms/get?id=%s&since=0" % rid)
        self.assertEqual([x["role"] for x in r["messages"]], ["system", "user"])
        # 沒 @ 不回；@kiki 才回
        self.req("/api/rooms/post", {"id": rid, "content": "@kiki 你覺得呢？"})
        for _ in range(60):
            r = self.req("/api/rooms/get?id=%s&since=0" % rid, token=bob)[1]
            ai = [x for x in r["messages"] if x["role"] == "assistant"]
            if ai and ai[-1].get("done"):
                break
            time.sleep(0.2)
        self.assertEqual(len(ai), 1)
        self.assertIn("假模型", ai[0]["content"])
        self.assertEqual(ai[0]["user"], "kiki")
        since = r["messages"][-1]["seq"]
        self.assertEqual(self.req("/api/rooms/get?id=%s&since=%d" % (rid, since))[1]["messages"], [])
        self.assertEqual(self.req("/api/rooms/remove", {"id": rid}, token=bob)[0], 200)   # bob 自己離開
        self.assertEqual(self.req("/api/rooms/get?id=" + rid, token=bob)[0], 404)
        self.assertEqual(self.req("/api/rooms/remove", {"id": rid})[0], 200)              # 群主離開＝刪群
        self.assertEqual(self.req("/api/rooms/get?id=" + rid)[0], 404)
        self.req("/api/users/reject", {"user": "bob"})

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

    def test_chat_context_length(self):
        # 附了長文件時前端會要求較大的 num_ctx：上限 32768；管理員在 config 固定 num_ctx 時一律用那個值
        ask = {"model": "qwen3.5:4b", "messages": [{"role": "system", "content": "專案指示"}, {"role": "user", "content": "〈附件：a.txt〉\n內容"}]}
        self.stream("/api/chat", dict(ask, options={"num_ctx": 16384}))
        self.assertEqual(fake_ollama.S.last_chat["options"]["num_ctx"], 16384)
        self.assertEqual(fake_ollama.S.last_chat["messages"][0]["role"], "system")
        self.stream("/api/chat", dict(ask, options={"num_ctx": 9999999, "mmap": False, "num_gpu": 0}))
        self.assertEqual(fake_ollama.S.last_chat["options"], {"num_ctx": 32768})      # 沒列在白名單的選項不會傳下去
        self.stream("/api/chat", ask)
        self.assertNotIn("options", fake_ollama.S.last_chat)
        cfg = server.Handler.app.cfg
        cfg["num_ctx"] = 24576
        try:
            self.stream("/api/chat", dict(ask, options={"num_ctx": 8192}))
            self.assertEqual(fake_ollama.S.last_chat["options"]["num_ctx"], 24576)
            self.stream("/api/chat", ask)
            self.assertEqual(fake_ollama.S.last_chat["options"]["num_ctx"], 24576)
            cfg["num_ctx"] = 10 ** 9          # 設定打錯也不會要 Ollama 配一個天文數字
            self.stream("/api/chat", ask)
            self.assertEqual(fake_ollama.S.last_chat["options"]["num_ctx"], 262144)
        finally:
            del cfg["num_ctx"]

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
