"""匯出：把一個帳號的所有專案（對話）和群組打包成 zip，方便搬去別的地方或給別的 AI 讀。

每個專案兩個檔：<標題>.txt（純文字對話）、<標題>.記憶檔.md（給 AI 讀的 Markdown），外加 index.md 和 原始 JSON。
"""
import datetime as dt
import io
import json
import re
import zipfile

BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe(name, fallback):
    s = BAD.sub("_", str(name or "")).strip(" .")[:60]
    return s or fallback


def speaker(m, partner):
    by = m.get("by") or ("kiki" if m.get("role") == "assistant" else "user")
    if by == "user":
        return "使用者"
    if by == "kiki":
        return "Kiki"
    return (partner or {}).get("name") or by


def body(m):
    text = str(m.get("content") or "")
    if m.get("role") == "assistant" and text.startswith("<think>"):
        end = text.find("</think>")
        text = text[end + 8:].lstrip() if end >= 0 else ""
    return text


def session_txt(s):
    title = s.get("title") or "對話"
    lines = [title, "=" * min(40, len(title) * 2), ""]
    for m in s.get("messages") or []:
        lines += ["%s：" % speaker(m, s.get("partner")), body(m), ""]
    return "\n".join(lines)


def session_md(s, user):
    title = s.get("title") or "對話"
    p = s.get("partner") or {}
    members = ["使用者 %s" % user, "Kiki（本機模型 %s）" % (s.get("model") or "—")]
    if p:
        members.append("%s（%s，使用者自己的 AI）" % (p.get("name", ""), p.get("model", "")))
    msgs = s.get("messages") or []
    asked = ["- " + body(m).split("\n")[0][:120] for m in msgs if (m.get("by") or m.get("role")) in ("user",)]
    turns = ["### %d. %s\n\n%s\n" % (i + 1, speaker(m, p), body(m)) for i, m in enumerate(msgs)]
    when = dt.datetime.fromtimestamp(s.get("updated") or 0).strftime("%Y-%m-%d %H:%M") if s.get("updated") else "—"
    inst = str(s.get("instructions") or "").strip()
    return ("# %s\n\n> 這是 Kiki Cloude 匯出的專案記憶檔，給另一個 AI 接手用。讀完後請以這裡的事實與決定為準，再回答使用者。\n\n"
            "- 最後更新：%s\n- 成員：%s\n- 訊息數：%d\n%s\n%s## 使用者在這個專案裡提出的事\n\n%s\n\n## 完整對話\n\n%s"
            % (title, when, "、".join(members), len(msgs), ("- 群組：%s\n" % s["group"]) if s.get("group") else "",
               ("## 專案指示（使用者要求每次都遵守）\n\n%s\n\n" % inst) if inst else "",
               "\n".join(asked) or "（沒有）", "\n".join(turns)))


def room_txt(r):
    lines = [r.get("title") or "群組", "成員：" + "、".join(r.get("members") or []), ""]
    for m in r.get("messages") or []:
        who = "Kiki" if m.get("user") == "kiki" else (m.get("user") or "系統")
        when = dt.datetime.fromtimestamp(m.get("ts") or 0).strftime("%m-%d %H:%M")
        lines.append("[%s] %s：%s" % (when, who, m.get("content", "")))
    return "\n".join(lines)


def build_zip(app, user):
    store = app.sessions_for(user)
    buf = io.BytesIO()
    used = set()
    index = ["# %s 的匯出（%s）\n" % (user["name"], dt.date.today().isoformat()), "## 專案\n"]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for row in store.list():
            s = store.get(row["id"])
            if not s:
                continue
            base = safe(s.get("title"), s["id"])
            if base in used:
                base = "%s_%s" % (base, s["id"][:6])
            used.add(base)
            folder = "專案/" + (safe(s.get("group"), "") + "/" if s.get("group") else "")
            z.writestr(folder + base + ".txt", session_txt(s))
            z.writestr(folder + base + ".記憶檔.md", session_md(s, user["name"]))
            z.writestr("原始JSON/" + s["id"] + ".json", json.dumps(s, ensure_ascii=False, indent=1))
            index.append("- %s%s（%d 則）" % (folder, base, len(s.get("messages") or [])))
        rooms = app.rooms.list_for(user["name"])
        if rooms:
            index.append("\n## 群組\n")
        for r in rooms:
            full = app.rooms.get(r["id"], user["name"], 0)
            if not full:
                continue
            base = safe(full.get("title"), r["id"])
            z.writestr("群組/" + base + ".txt", room_txt(full))
            index.append("- 群組/%s（%d 人）" % (base, len(full.get("members") or [])))
        z.writestr("index.md", "\n".join(index) + "\n")
    return buf.getvalue()


def export_all(h, q):
    data = build_zip(h.app, h.user)
    name = "KikiCloude_%s_%s.zip" % (h.user["name"], dt.date.today().isoformat())
    h._headers(200, "application/zip", len(data), {"Content-Disposition": "attachment; filename=\"%s\"" % name})
    h.wfile.write(data)


GET = {"/api/export/all": export_all}
