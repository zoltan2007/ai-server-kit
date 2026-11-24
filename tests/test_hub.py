"""模型庫測試：適合本機的計算、兩種排行、路徑檢查、權限、ComfyUI 下載與刪除。不碰網路（hub._urlopen 換成假的）。

    python -m unittest discover -s tests
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
sys.path.insert(0, HERE)

import fake_ollama  # noqa: E402
import hub  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-hub-0123456789"
AIBOX = hub.PROFILES["aibox"]
SMALL = {"name": "小機器", "gpus": [{"name": "RTX 4060 Ti 8GB", "gb": 8, "fp8": True}], "ram": 32}


def ollama(gb, label="Q4"):
    return {"label": label, "file_gb": gb, "run_gb": gb, "source": {"kind": "ollama", "name": "m:" + label.lower()}}


def comfy(gb, files, label="fp8"):
    return {"label": label, "precision": label, "file_gb": gb, "run_gb": gb, "source": {"kind": "comfy", "files": files}}


class FitTest(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(hub.fit(ollama(18), "ollama", AIBOX)[0], 3)        # 一張 20GB 放得下
        self.assertEqual(hub.fit(ollama(30), "ollama", AIBOX)[0], 2)        # 拆到好幾張
        self.assertEqual(hub.fit(ollama(80), "ollama", AIBOX)[0], 1)        # 要借記憶體
        self.assertEqual(hub.fit(ollama(200), "ollama", AIBOX)[0], 0)
        self.assertEqual(hub.fit(ollama(30), "comfy", AIBOX)[0], 1)         # ComfyUI 不會拆卡，只能 offload
        self.assertEqual(hub.fit({"label": "x", "source": {"kind": "ollama"}}, "ollama", AIBOX)[0], 0)
        self.assertEqual(hub.fit(ollama(5), "ollama", {"gpus": [], "ram": 16})[0], 1)   # 沒有顯示卡：只能用記憶體

    def test_fp8_note(self):
        v = comfy(14, [], "fp8")
        self.assertIn("沒有 FP8", hub.fp8_note(v, {"gpus": [{"gb": 20, "fp8": False}]}))
        self.assertIn("只有支援 FP8", hub.fp8_note(v, AIBOX))
        self.assertEqual(hub.fp8_note(v, SMALL), "")
        self.assertEqual(hub.fp8_note(ollama(60, "MXFP4"), {"gpus": [{"gb": 20, "fp8": False}]}), "")   # Ollama 自己處理

    def test_ranks(self):
        cat = {"categories": [{"id": "llm", "name": "語言", "score_src": "s"}, {"id": "asr", "name": "辨識", "score_src": "w"}],
               "sources": {"s": {"name": "S"}, "w": {"name": "W", "lower_is_better": True}},
               "models": [
                   {"id": "big", "cat": ["llm"], "variants": [ollama(90)], "score": {"llm": {"value": 40, "src": "s"}}},
                   {"id": "mid", "cat": ["llm"], "variants": [ollama(16), ollama(30, "Q8")], "score": {"llm": {"value": 25, "src": "s"}}},
                   {"id": "small", "cat": ["llm"], "variants": [ollama(5)], "score": {"llm": {"value": 10, "src": "s"}}},
                   {"id": "noscore", "cat": ["llm"], "variants": [ollama(6)], "score": {}},
                   {"id": "w1", "cat": ["asr"], "variants": [ollama(1)], "score": {"asr": {"value": 9.5, "src": "w"}}},
                   {"id": "w2", "cat": ["asr"], "variants": [ollama(2)], "score": {"asr": {"value": 6.1, "src": "w"}}}]}
        out = hub.build(cat, SMALL)
        self.assertEqual(out["ranks"]["llm"]["score"], ["big", "mid", "small", "noscore"])
        self.assertEqual(out["ranks"]["llm"]["fit"][:2], ["small", "noscore"])     # 8GB 卡放得下的排前面
        self.assertEqual(out["ranks"]["asr"]["score"], ["w2", "w1"])               # WER 越低越前面
        mid = next(m for m in out["models"] if m["id"] == "mid")
        self.assertEqual(mid["variants"][mid["best"]]["label"], "Q4")               # 推薦跑得最順的版本
        out = hub.build(cat, AIBOX)
        mid = next(m for m in out["models"] if m["id"] == "mid")
        # 大機器上：mid 的 Q8 要拆兩張卡，跟一張卡放得下算同一級，所以照分數排、推薦較大的 Q8；big 要借記憶體，排後面
        self.assertEqual(out["ranks"]["llm"]["fit"], ["mid", "small", "noscore", "big"])
        self.assertEqual(mid["variants"][mid["best"]]["label"], "Q8")

    def test_target_rejects_bad_paths(self):
        d = tempfile.mkdtemp(prefix="aisk-hub-")
        try:
            ok = hub.target(d, {"repo": "Comfy-Org/x", "path": "split_files/vae/v.safetensors", "folder": "vae"})
            self.assertEqual(ok, os.path.join(os.path.abspath(d), "models", "vae", "v.safetensors"))
            bad = [{"repo": "../x", "path": "a.safetensors", "folder": "vae"},
                   {"repo": "a/b", "path": "../../etc/passwd.safetensors", "folder": "vae"},
                   {"repo": "a/b", "path": "/etc/x.safetensors", "folder": "vae"},
                   {"repo": "a/b", "path": "a/../../x.safetensors", "folder": "vae"},
                   {"repo": "a/b", "path": "x.exe", "folder": "vae"},
                   {"repo": "a/b", "path": "x.safetensors", "folder": "../../.ssh"},
                   {"repo": "a/b/c", "path": "x.safetensors", "folder": "vae"},
                   {"repo": "a/b", "path": "C:\\x.safetensors", "folder": "vae"}]
            for f in bad:
                with self.assertRaises(ValueError, msg=f):
                    hub.target(d, f)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_catalog_is_consistent(self):
        """hub.json 本身：分類、廠商、分數來源都對得上；每個 ComfyUI 檔案都通過路徑檢查；大小都是數字。"""
        c = hub.load()
        cats = {x["id"]: x for x in c["categories"]}
        ids = set()
        for m in c["models"]:
            self.assertRegex(m["id"], hub.ID_RE)
            self.assertNotIn(m["id"], ids, m["id"])
            ids.add(m["id"])
            self.assertIn(m["vendor"], c["vendors"], m["id"])
            self.assertTrue(m["cat"] and all(x in cats for x in m["cat"]), m["id"])
            self.assertTrue(m["variants"], m["id"])
            labels = [v["label"] for v in m["variants"]]
            self.assertEqual(len(labels), len(set(labels)), m["id"])
            for v in m["variants"]:
                self.assertIn(v["source"]["kind"], ("ollama", "comfy", "node"), m["id"])
                for k in ("file_gb", "run_gb"):
                    self.assertTrue(v.get(k) is None or isinstance(v[k], (int, float)), (m["id"], k))
                if v["source"]["kind"] == "ollama":
                    self.assertRegex(v["source"]["name"], server.NAME_RE)
                else:
                    for f in v["source"]["files"]:
                        hub.target("/tmp/comfy", f)
            for cid, s in (m.get("score") or {}).items():
                self.assertIn(cid, m["cat"], m["id"])
                if s and s.get("value") is not None:
                    self.assertEqual(s["src"], cats[cid]["score_src"], (m["id"], cid))      # 同一類只用同一個來源
                    self.assertIn(s["src"], c["sources"])
        for src in c["sources"].values():
            self.assertTrue(src.get("name") and src.get("date"), src)


class FakeResp(io.BytesIO):
    status = 200


class HubApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ol = fake_ollama.serve()
        cls.tmp = tempfile.mkdtemp(prefix="aisk-hubapi-")
        cfg = {"host": "127.0.0.1", "port": 0, "token": TOKEN, "terminal": False, "data_dir": cls.tmp,
               "ollama": "http://127.0.0.1:%d" % cls.ol.server_address[1]}
        cls.srv = server.make_server(cfg)
        cls.app = server.Handler.app
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.real_open = hub._urlopen
        cls.seen = []

        def fake(req, timeout=None):
            url = req.full_url
            cls.seen.append(url)
            if url.endswith("/system_stats"):
                return FakeResp(b'{"system": {"comfyui_version": "test"}}')
            return FakeResp(b"x" * 3000)
        hub._urlopen = fake
        cls.app.hub_catalog = {
            "categories": [{"id": "llm", "name": "語言", "score_src": None}, {"id": "video", "name": "影片", "score_src": None}],
            "vendors": {"v": {"name": "V"}}, "sources": {},
            "models": [
                {"id": "llm-a", "cat": ["llm"], "name": "A", "vendor": "v", "variants": [
                    {"label": "Q4", "file_gb": 3.3, "run_gb": 4, "source": {"kind": "ollama", "name": "qwen3.5:4b"}}]},
                {"id": "vid-a", "cat": ["video"], "name": "VA", "vendor": "v", "variants": [comfy(1, [
                    {"repo": "Comfy-Org/x", "path": "split_files/diffusion_models/a.safetensors", "folder": "diffusion_models", "gb": 0.000003},
                    {"repo": "Comfy-Org/x", "path": "split_files/vae/shared.safetensors", "folder": "vae", "gb": 0.000003}])]},
                {"id": "vid-b", "cat": ["video"], "name": "VB", "vendor": "v", "variants": [comfy(1, [
                    {"repo": "Comfy-Org/x", "path": "split_files/diffusion_models/b.safetensors", "folder": "diffusion_models", "gb": 0.000003},
                    {"repo": "Comfy-Org/x", "path": "split_files/vae/shared.safetensors", "folder": "vae", "gb": 0.000003}])]}]}

    @classmethod
    def tearDownClass(cls):
        hub._urlopen = cls.real_open
        cls.srv.shutdown()
        cls.ol.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app.guard.hits.clear()
        self.app.fails.clear()
        hub._comfy_cache.update(t=0, v=None)

    def req(self, path, body=None, token=TOKEN):
        h = {"Authorization": "Bearer " + token}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        r = urllib.request.Request(self.base + path, data=data, headers=h, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, jid):
        r = urllib.request.Request(self.base + "/api/jobs/stream?id=%s&from=0" % jid, headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(r, timeout=30) as resp:
            evs = [json.loads(x) for x in resp.read().decode().splitlines() if x.strip()]
        self.assertEqual(evs[-1]["t"], "end")
        return evs[-1]

    def user_token(self):
        self.req("/api/apply", {"user": "hubuser", "password": "hubuser-pass1", "email": "hubuser@gmail.com"}, token="")
        self.req("/api/users/approve", {"user": "hubuser"})
        return self.req("/api/login", {"user": "hubuser", "password": "hubuser-pass1"}, token="")[1]["token"]

    def test_browse_and_permissions(self):
        tok = self.user_token()
        try:
            code, r = self.req("/api/hub", token=tok)
            self.assertEqual(code, 200, r)
            self.assertFalse(r["admin"])
            self.assertEqual(set(r["comfy"]), {"installed", "running"})                  # 一般帳號看不到主機上的資料夾
            self.assertEqual(r["ranks"]["llm"]["fit"], ["llm-a"])
            self.assertEqual(self.req("/api/hub/download", {"id": "llm-a", "variant": "Q4"}, token=tok)[0], 403)
            self.assertEqual(self.req("/api/hub/remove", {"id": "vid-a", "variant": "fp8"}, token=tok)[0], 403)
            r = self.req("/api/hub?profile=aibox")[1]
            self.assertEqual((r["profile_id"], r["profile"]["ram"]), ("aibox", 64))
            self.assertEqual(self.req("/api/hub?profile=../../x")[1]["profile_id"], "host")
            self.assertEqual(self.req("/api/hub/download", {"id": "../x", "variant": "Q4"})[0], 400)
            self.assertEqual(self.req("/api/hub/download", {"id": "llm-a", "variant": "nope"})[0], 400)
            self.assertEqual(self.req("/api/hub", token="")[0], 401)
        finally:
            self.req("/api/users/reject", {"user": "hubuser"})

    def test_ollama_download_reuses_pull(self):
        code, r = self.req("/api/hub/download", {"id": "llm-a", "variant": "Q4"})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.wait(r["id"])["state"], "done")
        self.assertTrue(next(m for m in self.req("/api/hub")[1]["models"] if m["id"] == "llm-a")["variants"][0]["have"])

    def test_comfy_download_and_remove(self):
        self.app.cfg.pop("comfyui", None)
        code, r = self.req("/api/hub/download", {"id": "vid-a", "variant": "fp8"})
        self.assertEqual(code, 409)                                                      # 還沒裝 ComfyUI
        comfy_dir = os.path.join(self.tmp, "ComfyUI")
        os.makedirs(comfy_dir, exist_ok=True)
        open(os.path.join(comfy_dir, "main.py"), "w").close()
        self.app.cfg["comfyui"] = {"dir": comfy_dir}
        try:
            r = self.req("/api/hub")[1]
            self.assertEqual((r["comfy"]["installed"], r["comfy"]["running"]), (True, True))
            for mid in ("vid-a", "vid-b"):
                code, r = self.req("/api/hub/download", {"id": mid, "variant": "fp8"})
                self.assertEqual(code, 200, r)
                self.assertEqual(self.wait(r["id"])["state"], "done")
            got = os.path.join(comfy_dir, "models", "diffusion_models", "a.safetensors")
            self.assertEqual(os.path.getsize(got), 3000)
            self.assertFalse(os.path.exists(got + ".part"))
            self.assertIn("https://huggingface.co/Comfy-Org/x/resolve/main/split_files/diffusion_models/a.safetensors", self.seen)
            models = {m["id"]: m for m in self.req("/api/hub")[1]["models"]}
            self.assertTrue(models["vid-a"]["variants"][0]["have"] and models["vid-b"]["variants"][0]["have"])
            # 刪 A：自己的主模型刪掉，B 也在用的 VAE 留著
            code, r = self.req("/api/hub/remove", {"id": "vid-a", "variant": "fp8"})
            self.assertEqual((code, r["removed"], r["kept"]), (200, ["a.safetensors"], 1))
            self.assertTrue(os.path.exists(os.path.join(comfy_dir, "models", "vae", "shared.safetensors")))
            code, r = self.req("/api/hub/remove", {"id": "vid-b", "variant": "fp8"})
            self.assertEqual(sorted(r["removed"]), ["b.safetensors", "shared.safetensors"])
            self.assertEqual(self.req("/api/hub/remove", {"id": "llm-a", "variant": "Q4"})[0], 400)   # Ollama 的不從這裡刪
        finally:
            self.app.cfg.pop("comfyui", None)


if __name__ == "__main__":
    unittest.main()
