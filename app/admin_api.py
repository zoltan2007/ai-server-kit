"""管理員儀表板：所有使用者的用量（日／週／月／年）、專案與對話內容。只有管理員走得到（不在 USER_OK 裡）。"""
import os

from sessions import ID_RE, SessionStore
from users import NAME_RE


def _store(app, name):
    return app.sessions_for({"name": name})


def overview(h, q):
    """每個帳號的專案數、訊息數、token 總量、最後登入；外加群組數。"""
    tokens = {}
    for r in h.app.usage.rows():
        t = tokens.setdefault(r["user"], {"tokens": 0, "calls": 0})
        t["tokens"] += r["prompt"] + r["completion"]
        t["calls"] += 1
    names = sorted({u["name"] for u in h.app.users.list()} | set(tokens) | {d for d in _session_dirs(h.app)})
    rows = []
    for n in names:
        u = h.app.users.public(n) or {"name": n, "role": "admin" if n == "admin" else "?", "status": "master" if n == "admin" else "deleted", "last_login": None}
        # 專案數、訊息數讀對話索引（記憶體），不再每次把每個人的資料夾掃一遍
        projects, messages = _store(h.app, n).stats() if NAME_RE.match(n) or n == "admin" else (0, 0)
        rows.append({"name": n, "role": u["role"], "status": u["status"], "last_login": u.get("last_login"),
                     "projects": projects, "messages": messages,
                     "tokens": tokens.get(n, {}).get("tokens", 0), "calls": tokens.get(n, {}).get("calls", 0)})
    rooms = sum(1 for f in os.listdir(h.app.rooms.dir) if f.endswith(".json"))
    h.json({"users": rows, "rooms": rooms})


def _session_dirs(app):
    base = os.path.join(app.data_dir, "sessions")
    try:
        return [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    except OSError:
        return []


def usage(h, q):
    period = q.get("period", "day")
    if period not in ("day", "week", "month", "year"):
        period = "day"
    try:
        limit = max(1, min(120, int(q.get("limit", 30) or 30)))
    except ValueError:
        limit = 30
    h.json(h.app.usage.summary(period, limit=limit))


def sessions(h, q):
    name = q.get("user", "")
    if not (NAME_RE.match(name) or name == "admin"):
        return h.err("帳號不對")
    h.json({"user": name, "sessions": _store(h.app, name).list()})


def session(h, q):
    name, sid = q.get("user", ""), q.get("id", "")
    if not (NAME_RE.match(name) or name == "admin") or not ID_RE.match(sid):
        return h.err("參數不對")
    s = _store(h.app, name).get(sid)
    return h.json(s) if s else h.err("找不到", 404)


GET = {"/api/admin/overview": overview, "/api/admin/usage": usage, "/api/admin/sessions": sessions, "/api/admin/session": session}
