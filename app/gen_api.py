"""生圖、生影片的 API。登入的帳號都能用（路徑在 users_api.USER_OK），送出有頻率上限（guard.LIMITS）。
每個帳號只看得到自己的成品；管理員也一樣（別人的作品是別人的）。"""
import os

import comfy
import hub
from hub_api import _catalog


def _admin(h):
    return h.user.get("role") == "admin"


def models(h, q):
    st = hub.comfy_status(h.app.cfg)
    ms = h.app.gen.models(_catalog(h.app), st["dir"] or None) if st["installed"] else []
    out = {"installed": st["installed"], "running": st["running"], "models": [m for m in ms if m["variants"]],
           "admin": _admin(h)}
    if _admin(h):        # 管理員才看得到「還沒下載」的模型，方便去模型庫補
        out["missing"] = [{"id": m["id"], "name": m["name"], "kind": m["kind"]} for m in ms if not m["variants"]]
    h.json(out)


def start(h, b):
    try:
        rec = h.app.gen.start(h.user, _catalog(h.app), h.app.cfg, b)
    except (ValueError, comfy.ComfyError) as e:
        return h.err(str(e), 409 if "ComfyUI" in str(e) else 400)
    h.json(h.app.gen.view(rec, _admin(h)))


def status(h, q):
    try:
        rec = h.app.gen.status(h.user, q.get("id", ""), h.app.cfg)
    except ValueError as e:
        return h.err(str(e))
    if not rec:
        return h.err("找不到這個作品（可能已經刪掉了）", 404)
    h.json(h.app.gen.view(rec, _admin(h)))


def _send(h, data, mime, length=None, path=None):
    h._headers(200, mime, length if length is not None else len(data), {"Content-Disposition": "inline"})
    if path is None:
        h.wfile.write(data)
        return
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.wfile.write(chunk)


def file(h, q):
    try:
        got = h.app.gen.file(h.user, q.get("id", ""))
    except ValueError as e:
        return h.err(str(e))
    if not got:
        return h.err("找不到這個作品（可能已經刪掉了）", 404)
    path, mime = got
    h.connection.settimeout(300)
    _send(h, None, mime, os.path.getsize(path), path)


def preview(h, q):
    pv = h.app.gen.preview(h.user, q.get("id", ""))
    if not pv:
        return h.err("還沒有預覽", 404)
    _send(h, pv[1], pv[0])


def cancel(h, b):
    try:
        ok = h.app.gen.cancel(h.user, str(b.get("id", "")))
    except ValueError as e:
        return h.err(str(e))
    h.json({"ok": ok})


def delete(h, b):
    try:
        ok = h.app.gen.delete(h.user, str(b.get("id", "")))
    except ValueError as e:
        return h.err(str(e))
    h.json({"ok": ok})


PATHS = {"/api/gen/models", "/api/gen/start", "/api/gen/status", "/api/gen/file", "/api/gen/preview", "/api/gen/cancel", "/api/gen/delete"}
GET = {"/api/gen/models": models, "/api/gen/status": status, "/api/gen/file": file, "/api/gen/preview": preview}
POST = {"/api/gen/start": start, "/api/gen/cancel": cancel, "/api/gen/delete": delete}
