"""群組：好幾個帳號一起聊，Kiki 默默看著，被 @ 到才回。每個群組一個 JSON，放 data_dir/rooms/。"""
import json
import os
import re
import secrets
import threading
import time

from users import safe_write

ID_RE = re.compile(r"^[a-f0-9]{12}$")
MENTION_RE = re.compile(r"(^|[\s,，。！？!?(（])@(kiki( cloude)?|ai|小機)\b", re.I)
HANDLE_RE = re.compile(r"(^|[\s,，。！？!?(（])@([a-z][a-z0-9_-]{1,20})", re.I)
MAX_MSGS = 600
CONTEXT_MSGS = 40
SYSTEM = ("你是 Kiki，這個群組的 AI 成員。平常只默默觀察大家的討論，只有被 @ 到才回話。"
          "回答要針對 @ 你的人、簡短、用繁體中文（台灣用語）。下面的對話紀錄格式是「名字：內容」，你自己的發言標為「Kiki」。")


def mentioned(text):
    return bool(MENTION_RE.search(text or ""))


def handles(text):
    """訊息裡 @ 到的名字（小寫）。"""
    return {m.group(2).lower() for m in HANDLE_RE.finditer(text or "")}


class RoomStore:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "rooms")
        self.lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)
        self.index = {}      # rid → {"mtime", "members", "owner", "summary"}；列清單不用每次解析整個檔
        self._recover()

    def _recover(self):
        """重開後：busy 旗標清掉、沒收尾的 AI 回覆標成失敗，不然那個群組永遠不再回話。"""
        for fn in os.listdir(self.dir):
            if not fn.endswith(".json"):
                continue
            room = self._read(fn[:-5])
            if not room:
                continue
            dirty = False
            if room.get("busy"):
                room["busy"] = False
                dirty = True
            for m in room.get("messages") or []:
                if m.get("role") == "assistant" and not m.get("done"):
                    m["done"] = True
                    m["error"] = m.get("error") or "伺服器重啟，這則沒說完"
                    dirty = True
            if dirty:
                self._write(room)
            else:
                self._touch_index(room)

    def _path(self, rid):
        if not ID_RE.match(rid or ""):
            raise ValueError("群組編號不對")
        return os.path.join(self.dir, rid + ".json")

    def _read(self, rid):
        try:
            with open(self._path(rid), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def _write(self, room):
        room["updated"] = time.time()
        safe_write(self._path(room["id"]), json.dumps(room, ensure_ascii=False))
        self._touch_index(room)

    def _touch_index(self, room):
        self.index[room["id"]] = {"members": list(room["members"]), "owner": room["owner"],
                                  "summary": {k: room.get(k, v) for k, v in (("title", ""), ("model", ""), ("updated", 0), ("seq", 0), ("bots", {}))}}

    @staticmethod
    def summary(room, me):
        return {"id": room["id"], "title": room["title"], "owner": room["owner"], "members": room["members"],
                "model": room.get("model", ""), "updated": room.get("updated", 0), "seq": room.get("seq", 0),
                "bots": room.get("bots", {}), "mine": room["owner"] == me}

    def list_for(self, user):
        out = []
        with self.lock:
            for rid, ix in self.index.items():
                if user in ix["members"]:
                    s = dict(ix["summary"], id=rid, owner=ix["owner"], members=list(ix["members"]), mine=ix["owner"] == user)
                    out.append(s)
        out.sort(key=lambda r: -r["updated"])
        return out

    def get(self, rid, user, since=0):
        with self.lock:
            room = self._read(rid)
            if not room or user not in room["members"]:
                return None
            out = dict(self.summary(room, user))
            out["messages"] = [m for m in room["messages"] if m["seq"] > since]
            return out

    def create(self, owner, title, model):
        with self.lock:
            if sum(1 for r in self.list_for(owner) if r["owner"] == owner) >= 100:
                raise ValueError("群組數量到上限了")
            room = {"id": secrets.token_hex(6), "title": str(title or "新群組")[:80], "owner": owner, "members": [owner],
                    "model": str(model or "")[:200], "messages": [], "seq": 0, "created": time.time()}
            self._write(room)
            return self.summary(room, owner)

    def _owned(self, rid, user):
        room = self._read(rid)
        if not room:
            raise ValueError("沒有這個群組")
        if room["owner"] != user:
            raise ValueError("只有建立群組的人能做這個")
        return room

    def invite(self, rid, user, name):
        with self.lock:
            room = self._owned(rid, user)
            if name in room["members"]:
                raise ValueError("已經在群組裡了")
            if len(room["members"]) >= 50:
                raise ValueError("群組人數到上限了")
            room["members"].append(name)
            self._append(room, "system", "", "%s 把 %s 加進群組" % (user, name))
            self._write(room)
            return self.summary(room, user)

    def remove(self, rid, user, name):
        """自己離開，或群主踢人；群主離開就刪掉整個群組。"""
        with self.lock:
            room = self._read(rid)
            if not room or user not in room["members"]:
                raise ValueError("沒有這個群組")
            if name != user and room["owner"] != user:
                raise ValueError("只有建立群組的人能移除別人")
            if name == room["owner"]:
                os.remove(self._path(rid))
                self.index.pop(rid, None)
                return None
            if name not in room["members"]:
                raise ValueError("這個人不在群組裡")
            room["members"].remove(name)
            self._append(room, "system", "", "%s 離開了群組" % name if name == user else "%s 把 %s 移出群組" % (user, name))
            self._write(room)
            return self.summary(room, user)

    def purge_user(self, name):
        """帳號刪掉時：他建的群組刪掉、他參加的群組把他移出。"""
        with self.lock:
            for fn in os.listdir(self.dir):
                if not fn.endswith(".json"):
                    continue
                room = self._read(fn[:-5])
                if not room:
                    continue
                if room["owner"] == name:
                    os.remove(self._path(room["id"]))
                    self.index.pop(room["id"], None)
                elif name in room["members"]:
                    room["members"].remove(name)
                    self._write(room)

    def rename(self, rid, user, title):
        with self.lock:
            room = self._owned(rid, user)
            room["title"] = str(title or room["title"])[:80]
            self._write(room)
            return self.summary(room, user)

    def set_bot(self, rid, user, handle, bot):
        """群主把自己的外部 AI 接進群組；bot=None 移除。"""
        with self.lock:
            room = self._owned(rid, user)
            bots = room.setdefault("bots", {})
            if bot is None:
                bots.pop(handle, None)
            else:
                bots[handle] = bot
            self._write(room)
            return self.summary(room, user)

    def _append(self, room, role, user, content):
        room["seq"] = room.get("seq", 0) + 1
        msg = {"seq": room["seq"], "role": role, "user": user, "content": content, "ts": time.time()}
        room["messages"].append(msg)
        if len(room["messages"]) > MAX_MSGS:
            room["messages"] = room["messages"][-MAX_MSGS:]
        return msg

    def post(self, rid, user, content):
        content = str(content or "").strip()
        if not content:
            raise ValueError("沒有內容")
        if len(content) > 6000:
            raise ValueError("訊息太長（最多 6000 字）")
        with self.lock:
            room = self._read(rid)
            if not room or user not in room["members"]:
                raise ValueError("沒有這個群組")
            msg = self._append(room, "user", user, content)
            self._write(room)
            return msg

    def context(self, rid):
        """給 Kiki 看的最近對話。"""
        with self.lock:
            room = self._read(rid)
            if not room:
                return None, []
            lines = []
            for m in room["messages"][-CONTEXT_MSGS:]:
                if m["role"] == "system":
                    continue
                who = ("Kiki" if m["user"] == "kiki" else m["user"]) if m["role"] == "assistant" else m["user"]
                lines.append("%s：%s" % (who, m["content"]))
            return room, lines

    def start_reply(self, rid, who="kiki"):
        """在鎖裡檢查並設定 busy，兩個人同時 @ 只會有一個回；卡住超過 15 分鐘就當作沒在忙。"""
        with self.lock:
            room = self._read(rid)
            if not room:
                return None
            if room.get("busy") and time.time() - (room.get("busy_since") or 0) < 900:
                return None
            msg = self._append(room, "assistant", who, "")
            room["busy"] = True
            room["busy_since"] = time.time()
            self._write(room)
            return msg["seq"]

    def update_reply(self, rid, seq, content, done=False, error=""):
        with self.lock:
            room = self._read(rid)
            if not room:
                return
            for m in room["messages"]:
                if m["seq"] == seq:
                    m["content"] = content
                    if error:
                        m["error"] = error
                    if done:
                        m["done"] = True
            if done:
                room["busy"] = False
            self._write(room)
