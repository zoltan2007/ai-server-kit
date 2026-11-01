"""收模型伺服器測試：收檔、驗 token、清單、下載、擋路徑穿越。

    python -m unittest discover -s ai-server-kit/tests
"""
import importlib.util
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 用檔案路徑載入，避免跟 app/server.py 的模組名撞在一起
_spec = importlib.util.spec_from_file_location("aisk_receiver", os.path.join(os.path.dirname(HERE), "receiver", "server.py"))
rsv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rsv)

TOKEN = "recv-token-abcdefgh"


class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aisk-rx-")
        rsv.Handler.app = rsv.App({"token": TOKEN, "dir": cls.tmp, "recv_path": "/upload"})
        cls.srv = rsv.ThreadingHTTPServer(("127.0.0.1", 0), rsv.Handler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, data=None, method=None, token=TOKEN, headers=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + token
        r = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
        try:
            resp = urllib.request.urlopen(r, timeout=15)
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        return resp.status, resp.read()

    def test_page_and_ping(self):
        code, body = self.req("/", token=None)
        self.assertEqual(code, 200)
        self.assertIn("收到的模型", body.decode("utf-8"))
        self.assertEqual(self.req("/api/ping", token=None)[0], 200)

    def test_auth_required(self):
        self.assertEqual(self.req("/api/list", token=None)[0], 401)
        self.assertEqual(self.req("/api/list", token="wrong")[0], 401)
        self.assertEqual(self.req("/upload?name=a.gguf&model=m", data=b"x", token=None)[0], 401)

    def test_receive_and_list_and_download(self):
        blob = b"GGUF" + os.urandom(50000)
        code, body = self.req("/upload?name=my-model.gguf&model=MySecret", data=blob)
        self.assertEqual(code, 200, body)
        j = json.loads(body)
        self.assertEqual(j["size"], len(blob))
        # 第二個檔案，同一個 model → 併在同一筆
        self.req("/upload?name=extra.json&model=MySecret", data=b"{}")
        code, body = self.req("/api/list")
        items = json.loads(body)["items"]
        it = [x for x in items if x["model"] == "MySecret"][0]
        self.assertEqual(len(it["files"]), 2)
        self.assertEqual(it["total"], len(blob) + 2)
        # 下載回來一致
        code, got = self.req("/api/file?sub=%s&name=my-model.gguf" % it["id"])
        self.assertEqual(code, 200)
        self.assertEqual(got, blob)

    def test_reject_bad_name_and_traversal(self):
        # 路徑穿越被 basename 中和成安全檔名後才存，所以 ../evil.gguf → evil.gguf（安全），不是 400
        code, body = self.req("/upload?name=../evil.gguf&model=trav", data=b"x")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["name"], "evil.gguf")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.gguf")))
        # 真正不合法的名字（basename 後還是壞的）→ 400
        self.assertEqual(self.req("/upload?name=..&model=m", data=b"x")[0], 400)
        self.assertEqual(self.req("/upload?name=&model=m", data=b"x")[0], 400)
        # 下載不給穿越
        self.assertEqual(self.req("/api/file?sub=..&name=_meta.json")[0], 404)
        # 空內容 → 400
        self.assertEqual(self.req("/upload?name=ok.gguf&model=m", data=b"", method="POST")[0], 400)


if __name__ == "__main__":
    unittest.main()
