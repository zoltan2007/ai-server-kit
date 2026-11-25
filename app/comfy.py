"""跟本機的 ComfyUI 講話：送工作流程、看進度、拿成品。只准連本機位址（127.0.0.1／localhost／::1）。

進度走 ComfyUI 的 WebSocket（/ws）；連不上就退回每隔一下問 /history。WebSocket 只用標準函式庫寫，只收文字訊息。
"""
import base64
import json
import os
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

UA = "ai-server-kit"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_JSON = 8 * 1024 * 1024          # /history、/queue 的回應上限
MAX_WS_FRAME = 32 * 1024 * 1024     # 預覽圖是二進位訊息，讀完就丟

_urlopen = urllib.request.urlopen   # 測試時換成假的
_connect = socket.create_connection


class ComfyError(Exception):
    """detail 只給管理員看（可能有檔名、路徑）；str(e) 是給一般使用者的說法。"""

    def __init__(self, msg, detail=""):
        super().__init__(msg)
        self.detail = detail


def check_url(url):
    u = urllib.parse.urlsplit(url or "")
    if u.scheme != "http" or u.hostname not in LOCAL_HOSTS:
        raise ComfyError("ComfyUI 的位址只能是本機（http://127.0.0.1:埠號）")
    return url.rstrip("/")


class Comfy:
    def __init__(self, url):
        self.url = check_url(url)

    def _req(self, path, body=None, timeout=15, limit=MAX_JSON):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"User-Agent": UA, "Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=timeout) as r:
                raw = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read(limit)
            except OSError:
                raw = b""
            raise ComfyError("ComfyUI 拒絕了這個工作（HTTP %d）" % e.code, raw.decode("utf-8", "replace")[:4000]) from None
        except (OSError, urllib.error.URLError) as e:
            raise ComfyError("連不上 ComfyUI，請管理員確認它有在跑", repr(e)) from None
        if len(raw) > limit:
            raise ComfyError("ComfyUI 回的資料太大")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise ComfyError("ComfyUI 回的資料看不懂", raw[:300].decode("utf-8", "replace")) from None

    def submit(self, graph, client_id):
        try:
            r = self._req("/prompt", {"prompt": graph, "client_id": client_id})
        except ComfyError as e:
            raise ComfyError(_why(e.detail) or str(e), e.detail) from None
        pid = r.get("prompt_id")
        if not isinstance(pid, str) or not pid or r.get("node_errors"):
            raise ComfyError("ComfyUI 拒絕了這個工作", json.dumps(r, ensure_ascii=False)[:4000])
        return pid

    def history(self, pid):
        """完成（成功或失敗）才會有資料；還在排隊或跑就回 None。"""
        h = self._req("/history/" + urllib.parse.quote(pid, safe=""))
        return h.get(pid) if isinstance(h, dict) else None

    def queue(self):
        """（正在跑的 prompt_id 清單, 排隊中的 prompt_id 清單）。"""
        q = self._req("/queue")
        ids = lambda rows: [r[1] for r in rows if isinstance(r, list) and len(r) > 1]  # noqa: E731
        return ids(q.get("queue_running") or []), ids(q.get("queue_pending") or [])

    def cancel(self, pid):
        """排隊中就從隊伍拿掉；正在跑就只中斷這一個（帶 prompt_id，不會打斷別人的）。"""
        for path, body in (("/queue", {"delete": [pid]}), ("/interrupt", {"prompt_id": pid})):
            try:
                self._req(path, body, timeout=5)
            except ComfyError:
                pass

    def forget(self, pid):
        try:
            self._req("/history", {"delete": [pid]}, timeout=5)
        except ComfyError:
            pass

    def download(self, item, dst, limit):
        """把一個成品（/view）存到 dst。超過 limit 就放棄。"""
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
        req = urllib.request.Request(self.url + "/view?" + q, headers={"User-Agent": UA})
        n = 0
        try:
            with _urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > limit:
                        raise ComfyError("成品檔案太大")
                    f.write(chunk)
        except (OSError, urllib.error.URLError, ComfyError) as e:
            try:
                os.remove(dst)
            except OSError:
                pass
            if isinstance(e, ComfyError):
                raise
            raise ComfyError("從 ComfyUI 拿成品失敗", repr(e)) from None
        return n

    def ws(self, client_id, timeout=3):
        u = urllib.parse.urlsplit(self.url)
        return WebSocket(u.hostname, u.port or 80, "/ws?clientId=" + urllib.parse.quote(client_id), timeout)


def _why(detail):
    """把 ComfyUI 的驗證錯誤翻成一句話（不含檔名、路徑）。"""
    try:
        j = json.loads(detail)
    except (TypeError, ValueError):
        return ""
    errs = [e for n in (j.get("node_errors") or {}).values() if isinstance(n, dict) for e in n.get("errors", [])]
    text = " ".join(str(e.get("message", "")) for e in errs) + " " + str((j.get("error") or {}).get("message", ""))
    if "not in list" in text or "Value not in list" in text:
        return "ComfyUI 找不到需要的模型檔，請管理員到模型庫確認檔案都下載完了"
    if "does not exist" in text or "invalid_prompt" in json.dumps(j):
        return "這台的 ComfyUI 版本不支援這個模型的工作流程，請管理員更新 ComfyUI"
    return ""


def explain(messages):
    """history 的 status.messages → （給使用者的話, 給管理員的細節）。"""
    for kind, data in messages or []:
        if kind == "execution_interrupted":
            return "已取消", ""
        if kind == "execution_error" and isinstance(data, dict):
            msg = str(data.get("exception_message", ""))[:600]
            low = msg.lower()
            if "out of memory" in low or "outofmemory" in low or "allocation on device" in low:
                return "顯示卡記憶體不夠：試試小一點的尺寸、短一點的影片，或換小一點的模型", msg
            return "ComfyUI 生成時出錯（%s）" % str(data.get("node_type", ""))[:60], msg
    return "ComfyUI 生成失敗", ""


def outputs(entry):
    """history 裡所有成品（圖片、影片）的清單，照節點順序。"""
    out = []
    for node in (entry.get("outputs") or {}).values():
        if not isinstance(node, dict):
            continue
        for key in ("images", "gifs", "videos", "video"):
            for it in node.get(key) or []:
                if isinstance(it, dict) and isinstance(it.get("filename"), str):
                    out.append(it)
    return out


MAX_PREVIEW = 4 * 1024 * 1024


def _preview(data):
    """ComfyUI 的二進位訊息：前 4 位元組是種類（1＝預覽圖，4＝帶資料的預覽圖），再來是圖片格式（1＝JPEG，2＝PNG）。"""
    if len(data) < 8 or len(data) > MAX_PREVIEW:
        return None
    kind = struct.unpack(">I", data[:4])[0]
    if kind == 1:
        fmt, img = struct.unpack(">I", data[4:8])[0], data[8:]
        mime = {1: "image/jpeg", 2: "image/png"}.get(fmt)
    elif kind == 4:
        n = struct.unpack(">I", data[4:8])[0]
        try:
            meta = json.loads(data[8:8 + n].decode("utf-8"))
        except ValueError:
            return None
        img, mime = data[8 + n:], str(meta.get("image_type", "")) if isinstance(meta, dict) else ""
    else:
        return None
    sig = {"image/jpeg": bytes([0xFF, 0xD8]), "image/png": bytes([0x89]) + b"PNG"}.get(mime)
    return ("preview", mime, img) if sig and img.startswith(sig) else None


class WebSocket:
    """最小的 WebSocket 用戶端：只讀伺服器送來的訊息，回 ping。"""

    def __init__(self, host, port, path, timeout):
        self.sock = _connect((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        hosthdr = "[%s]" % host if ":" in host else host
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nUser-Agent: %s\r\n\r\n") % (path, hosthdr, port, key, UA)
        self.sock.sendall(req.encode("ascii"))
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk or len(self.buf) > 16384:
                self.close()
                raise OSError("WebSocket 握手失敗")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
            self.close()
            raise OSError("ComfyUI 不接受 WebSocket")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise OSError("WebSocket 斷了")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout):
        """回下一則訊息：文字是 dict；預覽圖是 ("preview", mime, bytes)；時間到回 None。"""
        self.sock.settimeout(timeout)
        parts = []
        while True:
            try:
                b1, b2 = self._read(2)
            except socket.timeout:
                return None
            op, masked, n = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            self.sock.settimeout(30)          # 一則訊息開始了，就要讀完
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > MAX_WS_FRAME:
                raise OSError("WebSocket 訊息太大")
            mask = self._read(4) if masked else b""
            data = self._read(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 8:
                raise OSError("WebSocket 關了")
            if op == 9:
                self._send(0xA, data)
            if op == 2 and b1 & 0x80:
                pv = _preview(data)
                if pv:
                    return pv
            if op in (1, 0) and (op == 1 or parts):
                parts.append(data)
                if sum(map(len, parts)) > MAX_WS_FRAME:
                    raise OSError("WebSocket 訊息太大")
                if b1 & 0x80:
                    try:
                        msg = json.loads(b"".join(parts).decode("utf-8"))
                    except ValueError:
                        msg = None
                    parts = []
                    if isinstance(msg, dict):
                        return msg
            self.sock.settimeout(timeout)

    def _send(self, op, data):
        mask = os.urandom(4)
        head = bytes([0x80 | op, 0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | op, 0x80 | 126]) + struct.pack(">H", len(data))
        self.sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
