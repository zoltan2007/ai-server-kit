"""群組 API：建群、邀人、發言、輪詢新訊息；訊息裡 @Kiki 就在背景叫模型回。函式都收 (handler, body_or_query)。"""
import threading
import time

import rooms as R

PATHS = {"/api/rooms", "/api/rooms/get", "/api/rooms/people", "/api/rooms/create", "/api/rooms/invite",
         "/api/rooms/remove", "/api/rooms/rename", "/api/rooms/post"}


def room_list(h, q):
    h.json({"rooms": h.app.rooms.list_for(h.user["name"])})


def room_get(h, q):
    try:
        since = int(q.get("since", 0) or 0)
    except ValueError:
        since = 0
    rid = q.get("id", "")
    if not R.ID_RE.match(rid):
        return h.err("群組編號不對")
    room = h.app.rooms.get(rid, h.user["name"], since)
    return h.json(room) if room else h.err("沒有這個群組", 404)


def people(h, q):
    """可以邀請的人：所有可用的帳號名稱（不含自己）。"""
    names = [u["name"] for u in h.app.users.list() if u["status"] == "active" and u["name"] != h.user["name"]]
    h.json({"people": names})


def create(h, b):
    model = str(b.get("model", ""))
    if not model:
        try:
            installed = [m["name"] for m in h.app.ollama.get("/api/tags").get("models", [])]
            model = installed[0] if installed else ""
        except Exception:
            model = ""
    h.json(h.app.rooms.create(h.user["name"], b.get("title", ""), model))


def _call(h, fn, *args):
    try:
        h.json(fn(*args) or {"ok": True})
    except ValueError as e:
        h.err(str(e))


def invite(h, b):
    name = str(b.get("user", "")).strip().lower()
    u = h.app.users.public(name)
    if not u or u["status"] != "active":
        return h.err("沒有這個帳號，或帳號還不能用")
    _call(h, h.app.rooms.invite, str(b.get("id", "")), h.user["name"], name)


def remove(h, b):
    _call(h, h.app.rooms.remove, str(b.get("id", "")), h.user["name"], str(b.get("user", "")).strip().lower() or h.user["name"])


def rename(h, b):
    _call(h, h.app.rooms.rename, str(b.get("id", "")), h.user["name"], str(b.get("title", "")).strip())


def post(h, b):
    rid = str(b.get("id", ""))
    try:
        msg = h.app.rooms.post(rid, h.user["name"], b.get("content", ""))
    except ValueError as e:
        return h.err(str(e))
    if R.mentioned(msg["content"]):
        threading.Thread(target=reply, args=(h.app, rid), daemon=True).start()
    h.json(msg)


def reply(app, rid):
    """背景叫模型回一則，邊生成邊寫回群組檔，大家輪詢就看得到字慢慢出來。"""
    room, lines = app.rooms.context(rid)
    if not room:
        return
    seq = app.rooms.start_reply(rid)
    if not seq:
        return
    model = room.get("model") or ""
    if not model:
        app.rooms.update_reply(rid, seq, "", done=True, error="這個群組還沒設定模型")
        return
    payload = {"model": model, "stream": True,
               "messages": [{"role": "system", "content": R.SYSTEM}, {"role": "user", "content": "\n".join(lines) + "\nKiki："}]}
    text, last_write = "", 0.0
    try:
        resp, gen = app.ollama.stream("/api/chat", payload, timeout=900)
        try:
            for obj in gen:
                if obj.get("error"):
                    raise RuntimeError(obj["error"])
                text += (obj.get("message") or {}).get("content", "")
                if time.time() - last_write > 0.5:
                    app.rooms.update_reply(rid, seq, text)
                    last_write = time.time()
        finally:
            resp.close()
        app.rooms.update_reply(rid, seq, text.strip(), done=True)
    except Exception as e:  # 模型掛了也要把訊息收尾，不然前端會一直等
        app.rooms.update_reply(rid, seq, text.strip(), done=True, error=str(e))


GET = {"/api/rooms": room_list, "/api/rooms/get": room_get, "/api/rooms/people": people}
POST = {"/api/rooms/create": create, "/api/rooms/invite": invite, "/api/rooms/remove": remove,
        "/api/rooms/rename": rename, "/api/rooms/post": post}
