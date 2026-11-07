"""寄驗證信：用管理員的 Gmail（應用程式密碼）透過 SMTP 寄 6 位數驗證碼。設定存 data_dir/mail.json（0600），不進瀏覽器。"""
import hashlib
import json
import os
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage

CODE_TTL = 15 * 60
MAX_TRIES = 5


class Mailer:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "mail.json")
        self.lock = threading.Lock()
        try:
            with open(self.path, encoding="utf-8") as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            self.cfg = {}
        self.codes = {}   # 帳號 → {"hash", "exp", "tries", "email"}

    # ── 設定 ──
    def configured(self):
        return bool(self.cfg.get("user") and self.cfg.get("password"))

    def public(self):
        return {"configured": self.configured(), "host": self.cfg.get("host", "smtp.gmail.com"), "port": self.cfg.get("port", 587),
                "user": self.cfg.get("user", ""), "sender": self.cfg.get("sender", "Kiki Cloude"), "require": bool(self.cfg.get("require", True))}

    def save(self, host, port, user, password, sender, require):
        with self.lock:
            self.cfg = {"host": host or "smtp.gmail.com", "port": int(port or 587), "user": user, "sender": sender or "Kiki Cloude", "require": bool(require),
                        "password": password or self.cfg.get("password", "")}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # ── 寄信 ──
    def send(self, to, subject, text):
        msg = EmailMessage()
        msg["From"] = "%s <%s>" % (self.cfg.get("sender", "Kiki Cloude"), self.cfg["user"])
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        with smtplib.SMTP(self.cfg.get("host", "smtp.gmail.com"), int(self.cfg.get("port", 587)), timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(self.cfg["user"], self.cfg["password"])
            s.send_message(msg)

    # ── 驗證碼 ──
    @staticmethod
    def _h(code):
        return hashlib.sha256(code.encode()).hexdigest()

    def issue(self, name, email):
        code = "%06d" % secrets.randbelow(1_000_000)
        with self.lock:
            self.codes[name] = {"hash": self._h(code), "exp": time.time() + CODE_TTL, "tries": 0, "email": email}
        self.send(email, "Kiki Cloude 驗證碼：%s" % code,
                  "你申請了 Kiki Cloude 的帳號「%s」。\n\n驗證碼：%s（15 分鐘內有效）\n\n不是你申請的話，忽略這封信就好。" % (name, code))

    def check(self, name, code):
        with self.lock:
            c = self.codes.get(name)
            if not c or c["exp"] < time.time():
                return "驗證碼過期了，請重寄"
            if c["tries"] >= MAX_TRIES:
                return "試太多次了，請重寄"
            c["tries"] += 1
            if not secrets.compare_digest(c["hash"], self._h(str(code or "").strip())):
                return "驗證碼不對"
            del self.codes[name]
            return None
