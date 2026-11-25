"""假的 ComfyUI：測試與預覽用。認 /system_stats、/prompt、/queue、/history、/view、/interrupt 和 /ws（WebSocket）。

送進來的工作流程會被「執行」：每一步送一則 progress 和一張小預覽圖（PNG），做完在 history 放一個成品。
影片成品：有 S.video（mp4 的位元組）就回它，沒有就回假資料。
"""
import base64
import hashlib
import json
import socket
import struct
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

GUID = "258EAFA5-E914-47D5-95CA-C5AB0DC85B11"


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.lock = threading.Lock()
        self.prompts = {}       # pid → {"graph", "client", "status"}
        self.order = []
        self.history = {}
        self.files = {}         # filename → bytes
        self.ws = {}            # client_id → socket
        self.steps, self.delay = 4, 0.05
        self.fail = None        # "oom" → 執行時出錯；"invalid" → 送出時驗證失敗
        self.video = None
        self.ws_enabled = True
        self.hide_queue = False   # /queue 永遠回空的：模擬「兩次查詢之間剛好做完」
        self.interrupted = set()
        self.last_graph = None


S = State()


def png(w, h, rgb):
    """純色漸層 PNG（只用標準函式庫）。"""
    r0, g0, b0 = rgb
    rows = []
    for y in range(h):
        k = y * 255 // max(1, h - 1)
        rows.append(b"\x00" + bytes([(r0 + k // 3) % 256, (g0 + k // 4) % 256, (b0 + k // 2) % 256]) * w)
    raw = zlib.compress(b"".join(rows), 1)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _frame(op, data):
    n = len(data)
    head = bytes([0x80 | op, n]) if n < 126 else bytes([0x80 | op, 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | op, 127]) + struct.pack(">Q", n)
    return head + data


def _send(client, obj=None, binary=None):
    sock = S.ws.get(client)
    if not sock:
        return
    try:
        sock.sendall(_frame(1, json.dumps(obj).encode()) if obj is not None else _frame(2, binary))
    except OSError:
        S.ws.pop(client, None)


def _find(graph, cls):
    return next((n for n in graph.values() if n.get("class_type") == cls), None)


def _run(pid):
    p = S.prompts[pid]
    g, client = p["graph"], p["client"]
    p["status"] = "running"
    _send(client, {"type": "execution_start", "data": {"prompt_id": pid}})
    for i in range(1, S.steps + 1):
        time.sleep(S.delay)
        if pid in S.interrupted:
            S.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                                      "messages": [["execution_interrupted", {"prompt_id": pid}]]}}
            p["status"] = "done"
            return
        _send(client, {"type": "progress", "data": {"value": i, "max": S.steps, "prompt_id": pid, "node": "3"}})
        _send(client, binary=struct.pack(">II", 1, 2) + png(64, 64, (40 * i % 256, 90, 200)))
    if S.fail == "oom":
        S.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
            ["execution_error", {"prompt_id": pid, "node_type": "KSampler", "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB"}]]}}
    else:
        video = any(n.get("class_type") in ("SaveVideo", "SaveAnimatedWEBP") for n in g.values())
        save = _find(g, "SaveVideo") or _find(g, "SaveImage") or {"inputs": {}}
        prefix = str(save["inputs"].get("filename_prefix", "out"))
        sub, _, base = prefix.rpartition("/")
        name = "%s_00001_.%s" % (base, "mp4" if video else "png")
        size = next((n for n in g.values() if isinstance(n["inputs"].get("width"), int) and isinstance(n["inputs"].get("height"), int)), {"inputs": {}})
        w, h = int(size["inputs"].get("width", 64)), int(size["inputs"].get("height", 64))
        S.files[(sub, name)] = (S.video or b"\x00\x00\x00\x18ftypmp42fake") if video else png(min(w, 1024), min(h, 1024), (180, 60, 120))
        S.history[pid] = {"outputs": {"9": {"images": [{"filename": name, "subfolder": sub, "type": "output"}], "animated": [video]}},
                          "status": {"status_str": "success", "completed": True, "messages": []}}
    _send(client, {"type": "executing", "data": {"node": None, "prompt_id": pid}})
    p["status"] = "done"


def _worker():
    while True:
        time.sleep(0.02)
        with S.lock:
            pid = next((x for x in S.order if S.prompts[x]["status"] == "pending"), None)
        if pid:
            _run(pid)


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

    def do_GET(self):
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/system_stats":
            return self._json({"system": {"comfyui_version": "0.0.0-fake"}})
        if u.path == "/ws":
            return self._ws(q.get("clientId", ""))
        if u.path == "/queue":
            with S.lock:
                run = [[0, p] for p in S.order if S.prompts[p]["status"] == "running" and not S.hide_queue]
                pend = [[0, p] for p in S.order if S.prompts[p]["status"] == "pending" and not S.hide_queue]
            return self._json({"queue_running": run, "queue_pending": pend})
        if u.path.startswith("/history/"):
            pid = u.path[len("/history/"):]
            return self._json({pid: S.history[pid]} if pid in S.history else {})
        if u.path == "/view":
            data = S.files.get((q.get("subfolder", ""), q.get("filename", "")))
            if data is None:
                return self._json({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"error": "no"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/prompt":
            if S.fail == "invalid":
                return self._json({"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                   "node_errors": {"1": {"errors": [{"message": "Value not in list", "details": "unet_name: 'x' not in []"}]}}}, 400)
            pid = str(uuid.uuid4())
            with S.lock:
                S.last_graph = body["prompt"]
                S.prompts[pid] = {"graph": body["prompt"], "client": body.get("client_id", ""), "status": "pending"}
                S.order.append(pid)
            return self._json({"prompt_id": pid, "number": len(S.order), "node_errors": {}})
        if self.path == "/interrupt":
            S.interrupted.add(body.get("prompt_id"))
            return self._json({})
        if self.path == "/queue":
            with S.lock:
                for pid in body.get("delete", []):
                    if pid in S.prompts and S.prompts[pid]["status"] == "pending":
                        S.prompts[pid]["status"] = "done"
                        S.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                                                  "messages": [["execution_interrupted", {}]]}}
            return self._json({})
        if self.path == "/history":
            for pid in body.get("delete", []):
                S.history.pop(pid, None)
            return self._json({})
        self._json({"error": "no"}, 404)

    def _ws(self, client):
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not S.ws_enabled or not key:
            return self._json({"error": "no ws"}, 404)
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
        self.wfile.flush()
        S.ws[client] = self.connection
        _send(client, {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 0}}, "sid": client}})
        try:
            while client in S.ws:
                self.connection.settimeout(1)
                try:
                    if not self.connection.recv(1024):
                        break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            S.ws.pop(client, None)
            self.close_connection = True


def serve(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=_worker, daemon=True).start()
    return srv
