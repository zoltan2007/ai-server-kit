"""群組：好幾個帳號一起聊，Kiki 默默看著，被 @ 到才回。每個群組一個 JSON，放 data_dir/rooms/。"""
import json
import os
import re
import secrets
import threading
import time

ID_RE = re.compile(r"^[a-f0-9]{12}$")
MENTION_RE = re.compile(r"(^|[\s,，。！？!?(（])@(kiki( cloude)?|ai|小機)\b", re.I)
MAX_MSGS = 2000
CONTEXT_MSGS = 40
SYSTEM = ("你是 Kiki，這個群組的 AI 成員。平常只默默觀察大家的討論，只有被 @ 到才回話。"
          "回答要針對 @ 你的人、簡短、用繁體中文（台灣用語）。下面的對話紀錄格式是「名字：內容」，你自己的發言標為「Kiki」。")


def mentioned(text):
    return bool(MENTION_RE.search(text or ""))


class RoomStore:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "rooms")
        self.lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)

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
        tmp = self._path(room["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(room, f, ensure_ascii=False)
        os.replace(tmp, self._path(room["id"]))

    @staticmethod
    def summary(room, me):
        return {"id": room["id"], "title": room["title"], "owner": room["owner"], "members": room["members"],
                "model": room.get("model", ""), "updated": room.get("updated", 0), "seq": room.get("seq", 0),
                "mine": room["owner"] == me}

    def list_for(self, user):
        out = []
        with self.lock:
            for name in os.listdir(self.dir):
                if not name.endswith(".json"):
                    continue
                room = self._read(name[:-5])
                if room and user in room["members"]:
                    out.append(self.summary(room, user))
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
                return None
            if name not in room["members"]:
                raise ValueError("這個人不在群組裡")
            room["members"].remove(name)
            self._append(room, "system", "", "%s 離開了群組" % name if name == user else "%s 把 %s 移出群組" % (user, name))
            self._write(room)
            return self.summary(room, user)

    def rename(self, rid, user, title):
        with self.lock:
            room = self._owned(rid, user)
            room["title"] = str(title or room["title"])[:80]
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
        if len(content) > 20000:
            raise ValueError("訊息太長")
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
                who = "Kiki" if m["role"] == "assistant" else m["user"]
                lines.append("%s：%s" % (who, m["content"]))
            return room, lines

    def start_reply(self, rid):
        with self.lock:
            room = self._read(rid)
            if not room:
                return None
            msg = self._append(room, "assistant", "kiki", "")
            room["busy"] = True
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
