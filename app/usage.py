"""token 用量紀錄：每次對話一行 JSON（data_dir/usage.jsonl），給管理員儀表板按日／週／月／年統計。"""
import datetime as dt
import json
import os
import threading


class Usage:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "usage.jsonl")
        self.lock = threading.Lock()

    def add(self, user, model, prompt, completion, kind="chat", provider="ollama"):
        row = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "user": user, "model": model, "provider": provider,
               "kind": kind, "prompt": int(prompt or 0), "completion": int(completion or 0)}
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def rows(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except FileNotFoundError:
            return

    @staticmethod
    def bucket(ts, period):
        d = dt.datetime.fromisoformat(ts)
        if period == "day":
            return d.strftime("%Y-%m-%d")
        if period == "week":
            y, w, _ = d.isocalendar()
            return "%d-W%02d" % (y, w)
        if period == "month":
            return d.strftime("%Y-%m")
        return d.strftime("%Y")

    def summary(self, period="day", user=None, limit=60):
        """回傳 {"buckets": [...], "users": {name: {bucket: tokens}}, "total": {...}}。"""
        per = {}
        for r in self.rows():
            if user and r["user"] != user:
                continue
            b = self.bucket(r["ts"], period)
            u = per.setdefault(r["user"], {})
            cell = u.setdefault(b, {"prompt": 0, "completion": 0, "calls": 0})
            cell["prompt"] += r["prompt"]
            cell["completion"] += r["completion"]
            cell["calls"] += 1
        buckets = sorted({b for u in per.values() for b in u})[-limit:]
        total = {b: {"prompt": 0, "completion": 0, "calls": 0} for b in buckets}
        for u in per.values():
            for b, c in u.items():
                if b in total:
                    for k in c:
                        total[b][k] += c[k]
        return {"period": period, "buckets": buckets, "users": per, "total": total}
