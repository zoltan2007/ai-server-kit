"""生圖、生影片：參數檢查、工作流程填值、假 ComfyUI 上的完整流程、權限、刪除。不碰網路、不需要顯示卡。"""
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
sys.path.insert(0, HERE)

import comfy  # noqa: E402
import fake_comfy  # noqa: E402
import fake_ollama  # noqa: E402
import gen  # noqa: E402
import hub  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-gen-0123456789"
F = lambda i, folder, name: {"repo": "Comfy-Org/x", "path": "split_files/%s/%s" % (folder, name), "folder": folder, "gb": 0.001}  # noqa: E731
IMG_FLOW = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "$file0", "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "$file1", "type": "lumina2"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "$prompt", "clip": ["2", 0]}},
    "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": "$width", "height": "$height", "batch_size": 1}},
    "5": {"class_type": "KSampler", "inputs": {"seed": "$seed", "steps": "$steps", "cfg": 1, "model": ["1", 0], "positive": ["3", 0],
                                               "latent_image": ["4", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "$prefix", "images": ["5", 0]}},
}
VID_FLOW = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "$file0"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "$prompt"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "$negative"}},
    "4": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": "$width", "height": "$height", "length": "$length"}},
    "5": {"class_type": "KSampler", "inputs": {"seed": "$seed", "steps": "$steps"}},
    "7": {"class_type": "CreateVideo", "inputs": {"fps": "$fps"}},
    "9": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "$prefix", "format": "mp4", "codec": "h264"}},
}
RECIPES = {
    "img-a": {"kind": "image", "defaults": {"width": 1024, "height": 1024, "steps": 8},
              "limits": {"min_side": 512, "max_side": 2048, "multiple": 16}, "workflow": IMG_FLOW},
    "vid-a": {"kind": "video", "defaults": {"width": 1280, "height": 704, "steps": 20, "length": 121, "fps": 24},
              "limits": {"min_side": 256, "max_side": 1280, "multiple": 32, "max_length": 121, "length_rule": "4n+1"},
              "negative_default": "blurry", "workflow": VID_FLOW},
}
CATALOG = {
    "categories": [{"id": "image", "name": "圖", "score_src": None}, {"id": "video", "name": "影片", "score_src": None}],
    "vendors": {"v": {"name": "V"}}, "sources": {},
    "models": [
        {"id": "img-a", "cat": ["image"], "name": "Image A", "vendor": "v", "variants": [
            {"label": "bf16", "file_gb": 1, "run_gb": 2, "source": {"kind": "comfy", "files": [
                F(0, "diffusion_models", "img_a.safetensors"), F(1, "text_encoders", "te.safetensors")]}}]},
        {"id": "vid-a", "cat": ["video"], "name": "Video A", "vendor": "v", "variants": [
            {"label": "fp8", "file_gb": 1, "run_gb": 2, "source": {"kind": "comfy", "files": [F(0, "diffusion_models", "vid_a.safetensors")]}}]},
        {"id": "img-b", "cat": ["image"], "name": "Image B（沒下載）", "vendor": "v", "variants": [
            {"label": "bf16", "file_gb": 1, "run_gb": 2, "source": {"kind": "comfy", "files": [F(0, "diffusion_models", "img_b.safetensors")]}}]},
    ]}


class UnitTest(unittest.TestCase):
    def test_params_clamp_and_clean(self):
        r = RECIPES["img-a"]
        v = gen.params(r, {"prompt": "  一隻貓\x00\x07在窗邊 ", "width": 5000, "height": 100, "steps": 999, "seed": -5})
        self.assertEqual(v["$prompt"], "一隻貓在窗邊")
        self.assertEqual((v["$width"], v["$height"], v["$steps"]), (2048, 512, gen.MAX_STEPS))
        self.assertTrue(0 <= v["$seed"] < 2 ** 48)
        v = gen.params(r, {"prompt": "x", "width": "1000", "height": "abc", "seed": 42})
        self.assertEqual((v["$width"], v["$height"], v["$seed"]), (1008, 1024, 42))
        with self.assertRaises(ValueError):
            gen.params(r, {"prompt": " \x00 "})
        self.assertEqual(len(gen.params(r, {"prompt": "長" * 9000})["$prompt"]), gen.MAX_PROMPT)

    def test_video_length_rule(self):
        r = RECIPES["vid-a"]
        v = gen.params(r, {"prompt": "x", "seconds": 3})
        self.assertEqual((v["$fps"], v["$length"]), (24, 73))          # 3 秒 × 24 = 72 → 4n+1
        self.assertEqual(gen.params(r, {"prompt": "x", "seconds": 60})["$length"], 121)   # 上限
        self.assertEqual(gen.params(r, {"prompt": "x", "seconds": 0})["$length"], 5)
        self.assertEqual(gen.params(r, {"prompt": "x"})["$negative"], "blurry")
        self.assertEqual(gen.frames(5, 24, "8n+1", 241), 121)

    def test_fill_never_parses_user_text(self):
        v = gen.params(RECIPES["img-a"], {"prompt": "$width"})
        g = gen.graph_for(RECIPES["img-a"], CATALOG["models"][0]["variants"][0], v, "kiki/abc")
        self.assertEqual(g["3"]["inputs"]["text"], "$width")           # 使用者打的字原封不動，不會被當成要換的格子
        self.assertEqual(g["1"]["inputs"]["unet_name"], "img_a.safetensors")
        self.assertEqual(g["2"]["inputs"]["clip_name"], "te.safetensors")
        self.assertEqual(g["9"]["inputs"]["filename_prefix"], "kiki/abc")
        self.assertEqual(g["5"]["inputs"]["model"], ["1", 0])
        bad = dict(RECIPES["img-a"], workflow={"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "$file3"}}})
        with self.assertRaises(ValueError):       # 檔案不夠填 → 不送
            gen.graph_for(bad, CATALOG["models"][0]["variants"][0], v, "kiki/abc")

    def test_overflow_and_embedding_text(self):
        v = gen.params(RECIPES["img-a"], {"prompt": "x embedding:../../secret EMBEDDING :a", "width": 1e308, "height": "1e999",
                                          "steps": float("inf"), "seed": float("nan")})
        self.assertEqual((v["$width"], v["$height"], v["$steps"]), (2048, 1024, 8))
        self.assertNotIn("embedding:", v["$prompt"].lower().replace(" :", ":"))
        v = gen.params(RECIPES["vid-a"], {"prompt": "x", "seconds": "inf"})
        self.assertEqual(v["$length"], 121)

    def test_account_names(self):
        import users
        for bad in ("alice.", "con", "nul.x", "com1", "ab"):
            self.assertFalse(users.UserStore.valid_name(bad), bad)
        for ok in ("alice", "bob-", "a.b", "console"):
            self.assertTrue(users.UserStore.valid_name(ok), ok)

    def test_ids_in(self):
        s = {"messages": [{"gen": {"id": "0123456789abcdef"}}, {"gen": {"id": "../../etc"}}, {"content": "x"}, "bad"]}
        self.assertEqual(gen.ids_in(s), {"0123456789abcdef"})

    def test_preview_frames(self):
        img = fake_comfy.png(4, 4, (1, 2, 3))
        self.assertEqual(comfy._preview(struct.pack(">II", 1, 2) + img), ("preview", "image/png", img))
        meta = json.dumps({"image_type": "image/png"}).encode()
        self.assertEqual(comfy._preview(struct.pack(">II", 4, len(meta)) + meta + img)[1], "image/png")
        self.assertIsNone(comfy._preview(struct.pack(">II", 1, 1) + img))      # 說是 JPEG 卻是 PNG → 丟掉
        self.assertIsNone(comfy._preview(struct.pack(">II", 3, 2) + img))

    def test_only_local_comfy(self):
        for url in ("http://192.168.1.5:8188", "https://127.0.0.1:8188", "file:///etc/passwd", "http://evil.com"):
            with self.assertRaises(comfy.ComfyError):
                comfy.Comfy(url)
        comfy.Comfy("http://localhost:8188")

    def test_explain(self):
        msg, detail = comfy.explain([["execution_error", {"exception_message": "CUDA out of memory", "node_type": "KSampler"}]])
        self.assertIn("顯示卡記憶體不夠", msg)
        self.assertEqual(comfy.explain([["execution_interrupted", {}]])[0], "已取消")
        self.assertIn("找不到需要的模型檔", comfy._why(json.dumps({"node_errors": {"1": {"errors": [{"message": "Value not in list"}]}}})))


class GenApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ol = fake_ollama.serve()
        cls.fc = fake_comfy.serve()
        cls.tmp = tempfile.mkdtemp(prefix="aisk-gen-")
        cls.cdir = os.path.join(cls.tmp, "comfyui")
        for rel in ("main.py", "models/diffusion_models/img_a.safetensors", "models/text_encoders/te.safetensors",
                    "models/diffusion_models/vid_a.safetensors"):
            p = os.path.join(cls.cdir, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        cfg = {"host": "127.0.0.1", "port": 0, "token": TOKEN, "terminal": False, "data_dir": os.path.join(cls.tmp, "data"),
               "ollama": "http://127.0.0.1:%d" % cls.ol.server_address[1],
               "comfyui": {"dir": cls.cdir, "url": "http://127.0.0.1:%d" % cls.fc.server_address[1]}}
        cls.srv = server.make_server(cfg)
        cls.app = server.Handler.app
        cls.app.hub_catalog = CATALOG
        cls.app.gen.recipes = RECIPES
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.utok = None

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.ol.shutdown()
        cls.fc.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app.guard.hits.clear()
        hub._comfy_cache.update(t=0, v=None)
        fake_comfy.S.fail, fake_comfy.S.ws_enabled, fake_comfy.S.steps, fake_comfy.S.delay = None, True, 4, 0.05
        fake_comfy.S.hide_queue = False

    def req(self, path, body=None, token=TOKEN, raw=False):
        h = {"Authorization": "Bearer " + token}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        r = urllib.request.Request(self.base + path, data=data, headers=h, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                b = resp.read()
                return resp.status, (b, resp.headers.get("Content-Type")) if raw else json.loads(b or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def graph(self, gid):
        """送出是在背景做的：等假 ComfyUI 收到這個作品的工作流程。"""
        for _ in range(200):
            for p in list(fake_comfy.S.prompts.values()):
                if any(n.get("inputs", {}).get("filename_prefix") == "kiki/" + gid for n in p["graph"].values()):
                    return p["graph"]
            time.sleep(0.02)
        self.fail("ComfyUI 沒收到工作")

    def user(self):
        if not GenApiTest.utok:
            self.req("/api/apply", {"user": "genuser", "password": "genuser-pass1", "email": "genuser@gmail.com"}, token="")
            self.req("/api/users/approve", {"user": "genuser"})
            GenApiTest.utok = self.req("/api/login", {"user": "genuser", "password": "genuser-pass1"}, token="")[1]["token"]
        return GenApiTest.utok

    def wait(self, gid, token=TOKEN, until=("done", "error", "cancelled")):
        seen = set()
        for _ in range(300):
            code, s = self.req("/api/gen/status?id=" + gid, token=token)
            self.assertEqual(code, 200, s)
            seen.add(s["status"])
            if s["status"] in until:
                return s, seen
            time.sleep(0.05)
        self.fail("沒有做完")

    def test_models_list(self):
        code, r = self.req("/api/gen/models", token=self.user())
        self.assertEqual(code, 200)
        self.assertTrue(r["running"])
        self.assertEqual({m["id"]: m["variants"] for m in r["models"]}, {"img-a": ["bf16"], "vid-a": ["fp8"]})
        self.assertNotIn("missing", r)                     # 一般帳號看不到沒下載的清單
        self.assertTrue(next(m for m in r["models"] if m["id"] == "vid-a")["negative"])
        code, r = self.req("/api/gen/models")
        self.assertEqual(r["missing"], [])                 # img-b 沒有工作流程，不算「缺」

    def test_image_flow_with_progress_and_preview(self):
        tok = self.user()
        fake_comfy.S.steps, fake_comfy.S.delay = 6, 0.15
        code, r = self.req("/api/gen/start", {"model": "img-a", "variant": "bf16", "prompt": "海邊的燈塔", "width": 768, "height": 512}, token=tok)
        self.assertEqual(code, 200, r)
        g = self.graph(r["id"])
        self.assertEqual(g["3"]["inputs"]["text"], "海邊的燈塔")
        self.assertEqual((g["4"]["inputs"]["width"], g["4"]["inputs"]["height"]), (768, 512))
        self.assertEqual(g["9"]["inputs"]["filename_prefix"], "kiki/" + r["id"])
        got_preview = False
        for _ in range(100):
            code, s = self.req("/api/gen/status?id=" + r["id"], token=tok)
            if s.get("pn"):
                code, (img, ctype) = self.req("/api/gen/preview?id=" + r["id"], token=tok, raw=True)
                got_preview = code == 200 and ctype == "image/png" and img.startswith(b"\x89PNG")
            if got_preview or s["status"] == "done":
                break
            time.sleep(0.05)
        self.assertTrue(got_preview, "生成中要看得到預覽圖")
        s, seen = self.wait(r["id"], tok)
        self.assertEqual(s["status"], "done", s)
        self.assertGreater(s["progress"], 0.99)
        code, (img, ctype) = self.req("/api/gen/file?id=" + r["id"], token=tok, raw=True)
        self.assertEqual((code, ctype), (200, "image/png"))
        self.assertTrue(img.startswith(b"\x89PNG"))
        # 別人（包括管理員）拿不到這個帳號的作品
        self.assertEqual(self.req("/api/gen/file?id=" + r["id"])[0], 404)
        self.assertEqual(self.req("/api/gen/status?id=" + r["id"])[0], 404)
        # ComfyUI output/ 裡那份已經清掉、history 也清了
        self.assertFalse(os.path.exists(os.path.join(self.cdir, "output", "kiki", r["id"] + "_00001_.png")))
        code, r2 = self.req("/api/gen/delete", {"id": r["id"]}, token=tok)
        self.assertTrue(r2["ok"])
        self.assertEqual(self.req("/api/gen/file?id=" + r["id"], token=tok)[0], 404)

    def test_video_without_websocket(self):
        fake_comfy.S.ws_enabled = False
        code, r = self.req("/api/gen/start", {"model": "vid-a", "variant": "fp8", "prompt": "下雨的街道", "seconds": 2})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["length"], 49)
        g = self.graph(r["id"])
        self.assertEqual((g["7"]["inputs"]["fps"], g["6"]["inputs"]["text"]), (24, "blurry"))
        s, _ = self.wait(r["id"])
        self.assertEqual(s["status"], "done", s)
        code, (data, ctype) = self.req("/api/gen/file?id=" + r["id"], raw=True)
        self.assertEqual((code, ctype), (200, "video/mp4"))

    def test_finished_between_checks_is_not_lost(self):
        # 回歸：history 還沒有、queue 裡也已經沒有（剛好做完）時，不能說「工作不見了」
        fake_comfy.S.ws_enabled, fake_comfy.S.hide_queue = False, True
        code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"})
        s, _ = self.wait(r["id"])
        self.assertEqual(s["status"], "done", s)

    def test_errors_are_plain_language(self):
        fake_comfy.S.fail = "oom"
        code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"}, token=self.user())
        s, _ = self.wait(r["id"], self.user())
        self.assertEqual(s["status"], "error")
        self.assertIn("顯示卡記憶體不夠", s["error"])
        self.assertNotIn("detail", s)                      # 細節只給管理員
        fake_comfy.S.fail = "invalid"
        code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"})
        s, _ = self.wait(r["id"])
        self.assertIn("找不到需要的模型檔", s["error"])
        self.assertIn("not in", s["detail"])

    def test_cancel(self):
        fake_comfy.S.steps, fake_comfy.S.delay = 40, 0.1
        code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"})
        self.wait(r["id"], until=("running",))
        self.assertTrue(self.req("/api/gen/cancel", {"id": r["id"]})[1]["ok"])
        s, _ = self.wait(r["id"])
        self.assertEqual(s["status"], "cancelled")

    def test_refusals(self):
        tok = self.user()
        self.assertEqual(self.req("/api/gen/start", {"model": "img-b", "prompt": "x"}, token=tok)[0], 400)    # 沒工作流程
        self.assertEqual(self.req("/api/gen/start", {"model": "img-a", "prompt": ""}, token=tok)[0], 400)
        self.assertEqual(self.req("/api/gen/start", {"model": "../x", "prompt": "x"}, token=tok)[0], 400)
        self.assertEqual(self.req("/api/gen/status?id=../../users", token=tok)[0], 404)
        self.assertEqual(self.req("/api/gen/start", {"model": "img-a", "prompt": "x"}, token="nope")[0], 401)
        # 同一個帳號最多同時兩個
        fake_comfy.S.steps, fake_comfy.S.delay = 40, 0.1
        ids = [self.req("/api/gen/start", {"model": "img-a", "prompt": "x"}, token=tok)[1].get("id") for _ in range(2)]
        code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"}, token=tok)
        self.assertEqual(code, 400)
        self.assertIn("等它們做完", r["error"])
        for i in ids:
            self.req("/api/gen/cancel", {"id": i}, token=tok)
            self.wait(i, tok)

    def test_comfy_down(self):
        self.app.cfg["comfyui"]["url"], old = "http://127.0.0.1:9", self.app.cfg["comfyui"]["url"]
        try:
            code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"})
            self.assertEqual(code, 409)
            self.assertIn("沒有在跑", r["error"])
        finally:
            self.app.cfg["comfyui"]["url"] = old

    def test_session_delete_removes_unshared_works(self):
        ids = []
        for _ in range(2):
            code, r = self.req("/api/gen/start", {"model": "img-a", "prompt": "x"})
            ids.append(r["id"])
            self.wait(r["id"])
        a = self.req("/api/sessions/save", {"title": "a", "messages": [{"role": "assistant", "gen": {"id": i}} for i in ids]})[1]
        self.req("/api/sessions/save", {"title": "分支", "messages": [{"role": "assistant", "gen": {"id": ids[1]}}]})
        self.req("/api/sessions/delete", {"id": a["id"]})
        self.assertEqual(self.req("/api/gen/file?id=" + ids[0])[0], 404)     # 只有被刪的對話用 → 刪
        self.assertEqual(self.req("/api/gen/file?id=" + ids[1], raw=True)[0], 200)     # 分支還在用 → 留


if __name__ == "__main__":
    unittest.main()
