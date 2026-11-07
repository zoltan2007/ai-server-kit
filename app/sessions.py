"""對話紀錄：每個對話一個 JSON 檔，放 data_dir/sessions/，手機和電腦看到同一份。"""
import json
import os
import re
import secrets
import threading
import time

ID_RE = re.compile(r"^[a-f0-9]{12}$")
MAX_BYTES = 8 * 1024 * 1024      # 一個對話最多 8 MB（含圖片）
MAX_COUNT = 500


class SessionStore:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "sessions")
        self.lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, sid):
        if not ID_RE.match(sid or ""):
            raise ValueError("對話編號不對")
        return os.path.join(self.dir, sid + ".json")

    def list(self):
        out = []
        with self.lock:
            for name in os.listdir(self.dir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                        s = json.load(f)
                except (OSError, ValueError):
                    continue
                out.append({"id": s.get("id", name[:-5]), "title": s.get("title", ""), "model": s.get("model", ""),
                            "updated": s.get("updated", 0), "pinned": bool(s.get("pinned")),
                            "group": s.get("group", ""), "parent": s.get("parent", ""),
                            "count": len(s.get("messages") or [])})
        out.sort(key=lambda s: (not s["pinned"], -s["updated"]))
        return out

    def get(self, sid):
        with self.lock:
            try:
                with open(self._path(sid), encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                return None

    def save(self, s):
        sid = s.get("id") or secrets.token_hex(6)
        path = self._path(sid)
        body = {"id": sid, "title": str(s.get("title", ""))[:120], "model": str(s.get("model", ""))[:200],
                "pinned": bool(s.get("pinned")), "settings": s.get("settings") or {},
                "group": str(s.get("group") or "")[:60], "parent": str(s.get("parent") or "")[:12],
                "messages": s.get("messages") or [], "updated": time.time()}
        if not isinstance(body["messages"], list):
            raise ValueError("messages 要是清單")
        data = json.dumps(body, ensure_ascii=False)
        if len(data.encode("utf-8")) > MAX_BYTES:
            raise ValueError("這個對話太大了（超過 8 MB），請開新的對話")
        with self.lock:
            if not os.path.exists(path) and len(os.listdir(self.dir)) >= MAX_COUNT:
                raise ValueError("對話數量到上限了，先刪掉一些舊的")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        return body

    def delete(self, sid):
        with self.lock:
            try:
                os.remove(self._path(sid))
                return True
            except FileNotFoundError:
                return False
