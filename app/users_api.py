"""帳號相關的 API（登入、申請、管理員核准）。函式都收 (handler, body_or_query)，由 server.py 的路由表呼叫。

權限：管理員登入碼（config.json 的 token）＝「主管理員」，永遠可用；一般帳號登入後拿到 30 天的憑證。
一般帳號只能用：狀態、模型清單、對話、自己的對話紀錄；其他（下載模型、終端機、帳號管理）要管理員。
"""
import time

USER_OK = {"/api/status", "/api/models", "/api/models/show", "/api/chat", "/api/me", "/api/logout", "/api/password",
           "/api/sessions", "/api/sessions/get", "/api/sessions/save", "/api/sessions/delete",
           "/api/agreement", "/api/agreement/accept"}
MASTER = {"name": "admin", "role": "admin", "status": "active", "master": True}


def resolve(app, tok):
    """Bearer 憑證 → 使用者；主登入碼回 MASTER。"""
    if app.check(tok):
        return MASTER
    return app.users.who(tok)


def allowed(user, path):
    return user.get("role") == "admin" or path in USER_OK or path.startswith("/api/rooms") or path.startswith("/api/ai") or path == "/api/usage/me"


def login(h):
    ip = h.client_address[0]
    if h.app.locked(ip):
        return h.err("錯太多次，10 分鐘後再試", 429)
    try:
        b = h.body()
    except ValueError:
        b = {}
    tok = str(b.get("token", "")).strip()
    if tok:
        if h.app.check(tok):
            return h.json({"ok": True, "token": tok, "user": MASTER["name"], "role": "admin", "master": True,
                           "needs_setup": not h.app.users.has_admin(), "version": h.app.version})
        return _fail(h, ip, "登入碼不對")
    name, pw = str(b.get("user", "")).strip().lower(), str(b.get("password", ""))
    u = h.app.users.verify(name, pw)
    if not u:
        return _fail(h, ip, "帳號或密碼不對")
    if u["status"] == "pending":
        return h.err("帳號還在等管理員核准", 403)
    if u["status"] != "active":
        return h.err("這個帳號已停用", 403)
    return h.json({"ok": True, "token": h.app.users.issue(name), "user": name, "role": u["role"], "version": h.app.version})


def _fail(h, ip, msg):
    h.app.failed(ip)
    time.sleep(0.8)
    return h.err(msg, 401)


def apply(h):
    """申請帳號（不用登入）：建成待核准。"""
    ip = h.client_address[0]
    if h.app.locked(ip):
        return h.err("請稍後再試", 429)
    try:
        b = h.body()
    except ValueError:
        b = {}
    name = str(b.get("user", "")).strip().lower()
    try:
        h.app.users.create(name, str(b.get("password", "")), note=b.get("note", ""))
    except ValueError as e:
        h.app.failed(ip)
        return h.err(str(e))
    h.json({"ok": True, "status": "pending"})


def me(h, q):
    u = dict(h.user)
    if u["role"] == "admin":
        u["pending"] = h.app.users.pending_count()
        u["needs_setup"] = not h.app.users.has_admin()
    h.json(u)


def logout(h, b):
    auth = h.headers.get("Authorization", "")
    h.app.users.revoke(auth[7:] if auth.startswith("Bearer ") else "")
    h.json({"ok": True})


def change_password(h, b):
    if h.user.get("master"):
        return h.err("主登入碼請改 config.json")
    if not h.app.users.verify(h.user["name"], str(b.get("old", ""))):
        return h.err("舊密碼不對", 403)
    try:
        h.app.users.set_password(h.user["name"], str(b.get("password", "")))
    except ValueError as e:
        return h.err(str(e))
    h.json({"ok": True})


# ── 管理員 ──
def list_users(h, q):
    h.json({"users": h.app.users.list(), "needs_setup": not h.app.users.has_admin()})


def setup_admin(h, b):
    """用主登入碼建第一個管理員帳號。"""
    try:
        u = h.app.users.create(str(b.get("user", "")).strip().lower(), str(b.get("password", "")), role="admin", status="active")
    except ValueError as e:
        return h.err(str(e))
    h.json(u)


def _target(h, b):
    name = str(b.get("user", "")).strip().lower()
    if name == h.user.get("name") and not h.user.get("master"):
        raise ValueError("不能對自己做這個動作")
    return name


def approve(h, b):
    _change(h, b, "active")


def reject(h, b):
    try:
        h.app.users.delete(_target(h, b))
    except ValueError as e:
        return h.err(str(e))
    h.json({"ok": True})


def disable(h, b):
    _change(h, b, "disabled")


def enable(h, b):
    _change(h, b, "active")


def make_admin(h, b):
    _change(h, b, "active", "admin")


def _change(h, b, status, role=None):
    try:
        h.json(h.app.users.set_status(_target(h, b), status, role))
    except ValueError as e:
        h.err(str(e))


def reset_password(h, b):
    try:
        h.app.users.set_password(_target(h, b), str(b.get("password", "")))
    except ValueError as e:
        return h.err(str(e))
    h.json({"ok": True})


GET = {"/api/me": me, "/api/users": list_users}
POST = {"/api/logout": logout, "/api/password": change_password, "/api/users/setup": setup_admin,
        "/api/users/approve": approve, "/api/users/reject": reject, "/api/users/disable": disable,
        "/api/users/enable": enable, "/api/users/admin": make_admin, "/api/users/reset": reset_password}
