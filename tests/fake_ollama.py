"""假的 Ollama：只做面板會用到的 API，給測試和本機預覽用（不需要顯示卡、不需要下載模型）。

    python fake_ollama.py 11999
"""
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self):
        self.models = {"qwen3.5:4b": 3_320_000_000, "llama3.2:3b": 2_020_000_000}
        self.loaded = {}
        self.blobs = {}
        self.lock = threading.Lock()
        self.delay = 0.0  # 每個串流片段之間等多久（預覽時放慢一點比較像真的）


S = State()


def details(name):
    size = name.split(":")[-1]
    return {"format": "gguf", "family": name.split(":")[0].rstrip("0123456789.").rstrip("-") or "llama",
            "parameter_size": size.upper(), "quantization_level": "Q4_K_M"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _stream(self, objs):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for o in objs:
                self.wfile.write((json.dumps(o, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()
                if S.delay:
                    time.sleep(S.delay)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_HEAD(self):
        if self.path.startswith("/api/blobs/"):
            code = 200 if self.path.rsplit("/", 1)[1] in S.blobs else 404
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/version":
            return self._json({"version": "0.99.0-fake"})
        if self.path == "/api/tags":
            with S.lock:
                return self._json({"models": [{"name": n, "model": n, "size": s, "modified_at": "2026-11-02T10:00:00+08:00",
                                               "details": details(n)} for n, s in S.models.items()]})
        if self.path == "/api/ps":
            with S.lock:
                return self._json({"models": [{"name": n, "model": n, "size": S.models.get(n, 0),
                                               "size_vram": S.models.get(n, 0), "details": details(n),
                                               "expires_at": "2099-01-01T00:00:00Z"} for n in S.loaded]})
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        b = json.loads(self._body() or b"{}")
        with S.lock:
            if b.get("model") not in S.models:
                return self._json({"error": "model '%s' not found" % b.get("model")}, 404)
            S.models.pop(b["model"])
            S.loaded.pop(b["model"], None)
        self._json({})

    def do_POST(self):
        if self.path.startswith("/sink"):   # 假的「製作人收模型」端點，給預覽用
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/api/blobs/"):
            digest = self.path.rsplit("/", 1)[1]
            data = self._body()
            if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
                return self._json({"error": "digest mismatch"}, 400)
            S.blobs[digest] = len(data)
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        b = json.loads(self._body() or b"{}")
        name = b.get("model", "")
        if self.path == "/api/show":
            if name not in S.models:
                return self._json({"error": "model '%s' not found" % name}, 404)
            caps = ["completion"] + (["thinking"] if name.startswith("qwen") else []) + (["vision"] if "vl" in name or "qwen3.5" in name else [])
            return self._json({"capabilities": caps, "details": details(name), "model_info": {"llama.context_length": 32768}})
        if self.path == "/api/pull":
            if name.startswith("bad"):
                return self._stream([{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}])

            def gen():
                yield {"status": "pulling manifest"}
                for c in range(0, 1001, 100):
                    yield {"status": "pulling abc123", "digest": "sha256:abc123", "total": 1000, "completed": c}
                yield {"status": "verifying sha256 digest"}
                yield {"status": "writing manifest"}
                with S.lock:
                    S.models[name] = 1_500_000_000
                yield {"status": "success"}
            return self._stream(gen())
        if self.path == "/api/create":
            missing = [d for d in (b.get("files") or {}).values() if d not in S.blobs]
            if missing:
                return self._json({"error": "missing blob " + missing[0]}, 400)

            def gen():
                yield {"status": "parsing GGUF"}
                yield {"status": "using existing layer"}
                yield {"status": "writing manifest"}
                with S.lock:
                    S.models[name if ":" in name else name + ":latest"] = sum(S.blobs.values())
                yield {"status": "success"}
            return self._stream(gen())
        if self.path == "/api/generate":
            with S.lock:
                if b.get("keep_alive") == 0:
                    S.loaded.pop(name, None)
                elif name in S.models:
                    S.loaded[name] = True
                else:
                    return self._json({"error": "model '%s' not found" % name}, 404)
            return self._json({"model": name, "done": True, "done_reason": "unload" if b.get("keep_alive") == 0 else "load"})
        if self.path == "/api/chat":
            if name not in S.models:
                return self._json({"error": "model '%s' not found, try pulling it first" % name}, 404)
            with S.lock:
                S.loaded[name] = True
            last = (b.get("messages") or [{}])[-1].get("content", "")

            def gen():
                if b.get("think"):
                    for t in ["先想一下", "使用者說了：", last[:20]]:
                        yield {"model": name, "message": {"role": "assistant", "content": "", "thinking": t}, "done": False}
                reply = ["你好！", "我是測試用的假模型。", "\n\n```bash\n", "nvidia-smi\n", "```\n", "\n| 項目 | 值 |\n|---|---|\n| CPU | 12% |\n"]
                for t in reply:
                    yield {"model": name, "message": {"role": "assistant", "content": t}, "done": False}
                yield {"model": name, "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                       "total_duration": 1_500_000_000, "load_duration": 200_000_000, "prompt_eval_count": 12,
                       "prompt_eval_duration": 100_000_000, "eval_count": 42, "eval_duration": 1_000_000_000}
            return self._stream(gen())
        self._json({"error": "not found"}, 404)


def serve(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    S.delay = 0.08
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 11999), H)
    srv.daemon_threads = True
    print("fake ollama on", srv.server_address, flush=True)
    srv.serve_forever()
