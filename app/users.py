"""帳號：使用者申請 → 管理員核准 → 帳號密碼登入。資料放 data_dir/users.json，登入憑證放 data_dir/tokens.json。

密碼用 PBKDF2-HMAC-SHA256（20 萬次）加鹽存；只用標準函式庫。
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,19}$")   # 3～20 字，小寫英數
TOKEN_DAYS = 30
MIN_PW = 8
ROUNDS = 200_000


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), ROUNDS).hex()


class UserStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "users.json")
        self.tok_path = os.path.join(data_dir, "tokens.json")
        self.lock = threading.Lock()
        self.users = self._load(self.path)
        self.tokens = self._load(self.tok_path)

    @staticmethod
    def _load(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # ── 帳號 ──
    @staticmethod
    def valid_name(name):
        return bool(NAME_RE.match(name or ""))

    @staticmethod
    def check_password(pw):
        if len(pw or "") < MIN_PW:
            raise ValueError("密碼至少 %d 個字" % MIN_PW)

    def has_admin(self):
        return any(u.get("role") == "admin" and u.get("status") == "active" for u in self.users.values())

    def public(self, name):
        u = self.users.get(name)
        if not u:
            return None
        return {"name": name, "role": u["role"], "status": u["status"], "note": u.get("note", ""),
                "created": u.get("created"), "last_login": u.get("last_login")}

    def list(self):
        rows = [self.public(n) for n in self.users]
        rows.sort(key=lambda r: ({"pending": 0, "active": 1, "disabled": 2}.get(r["status"], 3), r["name"]))
        return rows

    def pending_count(self):
        return sum(1 for u in self.users.values() if u.get("status") == "pending")

    def create(self, name, pw, role="user", status="pending", note=""):
        if not self.valid_name(name):
            raise ValueError("帳號只能用小寫英文、數字、_ . -，3～20 個字")
        self.check_password(pw)
        with self.lock:
            if name in self.users:
                raise ValueError("這個帳號已經有人用了")
            if len(self.users) >= 200:
                raise ValueError("帳號數量到上限了")
            salt = secrets.token_hex(16)
            self.users[name] = {"salt": salt, "hash": _hash(pw, salt), "role": role, "status": status,
                                "note": str(note or "")[:200], "created": time.time()}
            self._save(self.path, self.users)
        return self.public(name)

    def set_status(self, name, status, role=None):
        with self.lock:
            u = self.users.get(name)
            if not u:
                raise ValueError("沒有這個帳號")
            u["status"] = status
            if role:
                u["role"] = role
            self._save(self.path, self.users)
            if status != "active":
                self._drop_tokens(name)
        return self.public(name)

    def delete(self, name):
        with self.lock:
            if name not in self.users:
                raise ValueError("沒有這個帳號")
            del self.users[name]
            self._save(self.path, self.users)
            self._drop_tokens(name)

    def set_password(self, name, pw):
        self.check_password(pw)
        with self.lock:
            u = self.users.get(name)
            if not u:
                raise ValueError("沒有這個帳號")
            u["salt"] = secrets.token_hex(16)
            u["hash"] = _hash(pw, u["salt"])
            self._save(self.path, self.users)
            self._drop_tokens(name)

    def verify(self, name, pw):
        """帳號密碼對了就回公開資料，不對回 None；還沒核准或停用也回 None（錯誤訊息由呼叫端決定）。"""
        u = self.users.get(name or "")
        if not u:
            _hash(pw or "", "00" * 16)   # 不存在的帳號也算一次，讓時間差不出來
            return None
        if not secrets.compare_digest(_hash(pw or "", u["salt"]), u["hash"]):
            return None
        return self.public(name)

    # ── 登入憑證 ──
    def issue(self, name):
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.tokens = {t: v for t, v in self.tokens.items() if v["exp"] > now}
            self.tokens[tok] = {"user": name, "exp": now + TOKEN_DAYS * 86400}
            self.users[name]["last_login"] = now
            self._save(self.tok_path, self.tokens)
            self._save(self.path, self.users)
        return tok

    def who(self, tok):
        v = self.tokens.get(tok or "")
        if not v or v["exp"] < time.time():
            return None
        u = self.public(v["user"])
        return u if u and u["status"] == "active" else None

    def revoke(self, tok):
        with self.lock:
            if self.tokens.pop(tok, None) is not None:
                self._save(self.tok_path, self.tokens)

    def _drop_tokens(self, name):
        self.tokens = {t: v for t, v in self.tokens.items() if v["user"] != name}
        self._save(self.tok_path, self.tokens)
