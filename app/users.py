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
RESERVED = {"admin", "kiki", "claude", "gpt", "gemini", "grok", "system", "root", "ai"}
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
        self.fails = {}   # 帳號 → 最近失敗的時間（只放記憶體）

    @staticmethod
    def _load(path):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
                return obj if isinstance(obj, dict) else {}
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # 檔案壞了（例如同步到一半）：留著證據、改用 .bak，絕對不要用空的蓋掉
            try:
                os.replace(path, path + ".corrupt-%d" % int(time.time()))
            except OSError:
                pass
            try:
                with open(path + ".bak", encoding="utf-8") as f:
                    obj = json.load(f)
                    return obj if isinstance(obj, dict) else {}
            except (OSError, ValueError):
                pass
        return {}

    def _save(self, path, obj):
        safe_write(path, json.dumps(obj, ensure_ascii=False, indent=1), secret=True, backup=True)

    # ── 帳號 ──
    @staticmethod
    def valid_name(name):
        return bool(NAME_RE.match(name or ""))

    @staticmethod
    def check_password(pw):
        if len(pw or "") < MIN_PW:
            raise ValueError("密碼至少 %d 個字" % MIN_PW)
        if len(pw) > 128:
            raise ValueError("密碼最多 128 個字")

    def has_admin(self):
        return any(u.get("role") == "admin" and u.get("status") == "active" for u in self.users.values())

    def public(self, name):
        u = self.users.get(name)
        if not u:
            return None
        return {"name": name, "role": u["role"], "status": u["status"], "note": u.get("note", ""), "email": u.get("email", ""),
                "verified": bool(u.get("verified", True)), "created": u.get("created"), "last_login": u.get("last_login"),
                "quota": u.get("quota") or {"day": 0, "week": 0}}

    def list(self):
        rows = [self.public(n) for n in self.users]
        rows.sort(key=lambda r: ({"pending": 0, "active": 1, "disabled": 2}.get(r["status"], 3), r["name"]))
        return rows

    def pending_count(self):
        return sum(1 for u in self.users.values() if u.get("status") == "pending")

    def create(self, name, pw, role="user", status="pending", note="", email="", verified=True):
        if not self.valid_name(name):
            raise ValueError("帳號只能用小寫英文、數字、_ . -，3～20 個字")
        self.check_password(pw)
        with self.lock:
            if name in self.users:
                raise ValueError("這個帳號已經有人用了")
            if len(self.users) >= 200:
                raise ValueError("帳號數量到上限了")
            if status == "pending" and sum(1 for u in self.users.values() if u.get("status") == "pending") >= 20:
                raise ValueError("等待核准的申請太多了，請晚點再試")
            if name in RESERVED:
                raise ValueError("這個名字不能用")
            salt = secrets.token_hex(16)
            self.users[name] = {"salt": salt, "hash": _hash(pw, salt), "role": role, "status": status,
                                "note": str(note or "")[:200], "created": time.time(), "email": str(email or "")[:120], "verified": bool(verified)}
            self._save(self.path, self.users)
        return self.public(name)

    def set_quota(self, name, day, week):
        """每天／每週 token 上限，0 = 不限。"""
        with self.lock:
            u = self.users.get(name)
            if not u:
                raise ValueError("沒有這個帳號")
            u["quota"] = {"day": max(0, int(day or 0)), "week": max(0, int(week or 0))}
            self._save(self.path, self.users)
        return self.public(name)

    def set_verified(self, name):
        with self.lock:
            if name in self.users:
                self.users[name]["verified"] = True
                self._save(self.path, self.users)

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

    def flush(self):
        """外部程式改了 self.users（例如 AI 金鑰）後存檔。"""
        with self.lock:
            self._save(self.path, self.users)

    def verify(self, name, pw):
        """帳號密碼對了就回公開資料，不對回 None；還沒核准或停用也回 None（錯誤訊息由呼叫端決定）。
        同一個帳號 10 分鐘內錯 10 次就鎖住，防止分散 IP 猜密碼。"""
        u = self.users.get(name or "")
        if not u or len(pw or "") > 128:
            _hash("x", "00" * 16)   # 不存在的帳號也算一次，讓時間差不出來
            return None
        now = time.time()
        with self.lock:
            fails = [t for t in self.fails.get(name, []) if now - t < 600]
            self.fails[name] = fails
        if len(fails) >= 10:
            return None
        if not secrets.compare_digest(_hash(pw, u["salt"]), u["hash"]):
            with self.lock:
                self.fails[name] = fails + [now]
            return None
        with self.lock:
            self.fails.pop(name, None)
        return self.public(name)

    # ── 登入憑證 ──
    def issue(self, name):
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.tokens = {t: v for t, v in self.tokens.items() if v["exp"] > now}
            self.tokens[tok] = {"user": name, "exp": now + TOKEN_DAYS * 86400}
            u = self.users.get(name)
            if not u:
                raise ValueError("帳號不存在")
            self._save(self.tok_path, self.tokens)
            if now - (u.get("last_login") or 0) > 3600:     # 最後登入時間一小時記一次就好，不用每次都重寫 users.json
                u["last_login"] = now
                self._save(self.path, self.users)
            else:
                u["last_login"] = now
        return tok

    def set_ai(self, name, provider, key=None, model=None):
        """外部 AI 的金鑰與模型，在鎖裡改，不然存檔時可能撞到。回傳這個人的 ai 設定。"""
        with self.lock:
            u = self.users.get(name)
            if not u:
                raise ValueError("沒有這個帳號")
            ai = u.setdefault("ai", {})
            cur = ai.setdefault(provider, {})
            if key:
                cur["key"] = key
            if model is not None:
                cur["model"] = model
            if not cur.get("key"):
                ai.pop(provider, None)
                self._save(self.path, self.users)
                raise ValueError("請填金鑰")
            self._save(self.path, self.users)
            return dict(ai)

    def remove_ai(self, name, provider):
        with self.lock:
            u = self.users.get(name)
            if u and (u.get("ai") or {}).pop(provider, None) is not None:
                self._save(self.path, self.users)
            return dict((u or {}).get("ai") or {})

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


def safe_write(path, text, secret=False, backup=False):
    """先寫 .tmp 再換名：機密檔用 0600 建檔、寫完 fsync，斷電不會留下空檔；backup=True 先把舊檔留成 .bak。"""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if secret else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if backup and os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)
