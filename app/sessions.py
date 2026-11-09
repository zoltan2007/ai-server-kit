"""對話紀錄：每個對話一個 JSON 檔，放 data_dir/sessions/，手機和電腦看到同一份。"""
import json
import os
import re
import secrets
import threading
import time

from users import safe_write

ID_RE = re.compile(r"^[a-f0-9]{12}$")
MAX_BYTES = 8 * 1024 * 1024      # 一個對話最多 8 MB（含圖片）
MAX_COUNT = 500
MAX_TOTAL = 300 * 1024 * 1024     # 一個帳號所有對話加起來最多 300 MB
MAX_INSTRUCTIONS = 4000           # 專案指示（每次都會附給 Kiki）的字數上限


class SessionStore:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "sessions")
        self.lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)
        self.meta = {}       # sid → (mtime, size, 清單用的摘要)；檔案沒變就不重新解析

    def _path(self, sid):
        if not ID_RE.match(sid or ""):
            raise ValueError("對話編號不對")
        return os.path.join(self.dir, sid + ".json")

    @staticmethod
    def _summary(s, sid):
        return {"id": s.get("id", sid), "title": s.get("title", ""), "model": s.get("model", ""),
                "updated": s.get("updated", 0), "pinned": bool(s.get("pinned")),
                "group": s.get("group", ""), "parent": s.get("parent", ""), "partner": s.get("partner"),
                "count": len(s.get("messages") or [])}

    def list(self):
        out = []
        with self.lock:
            seen = set()
            for name in os.listdir(self.dir):
                if not name.endswith(".json"):
                    continue
                sid = name[:-5]
                path = os.path.join(self.dir, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                seen.add(sid)
                cached = self.meta.get(sid)
                if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    out.append(cached[2])
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        s = json.load(f)
                except (OSError, ValueError):
                    continue
                summ = self._summary(s, sid)
                self.meta[sid] = (st.st_mtime, st.st_size, summ)
                out.append(summ)
            for sid in list(self.meta):
                if sid not in seen:
                    del self.meta[sid]
        out.sort(key=lambda s: (not s["pinned"], -s["updated"]))
        return out

    def _total_size(self, except_sid):
        return sum(m[1] for sid, m in self.meta.items() if sid != except_sid)

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
                "partner": s.get("partner") if isinstance(s.get("partner"), dict) else None,
                "instructions": str(s.get("instructions") or "")[:MAX_INSTRUCTIONS],
                "messages": s.get("messages") or [], "updated": time.time()}
        if not isinstance(body["messages"], list):
            raise ValueError("messages 要是清單")
        data = json.dumps(body, ensure_ascii=False)
        if len(data.encode("utf-8")) > MAX_BYTES:
            raise ValueError("這個對話太大了（超過 8 MB），請開新的對話")
        if not self.meta:
            self.list()      # 第一次先建索引
        with self.lock:
            if sid not in self.meta and len(self.meta) >= MAX_COUNT:
                raise ValueError("對話數量到上限了，先刪掉一些舊的")
            if self._total_size(sid) + len(data.encode("utf-8")) > MAX_TOTAL:
                raise ValueError("你的對話總量超過 300 MB，先刪掉一些舊的")
            safe_write(path, data)
            st = os.stat(path)
            self.meta[sid] = (st.st_mtime, st.st_size, self._summary(body, sid))
        return body

    def delete(self, sid):
        with self.lock:
            self.meta.pop(sid, None)
            try:
                os.remove(self._path(sid))
                return True
            except FileNotFoundError:
                return False
