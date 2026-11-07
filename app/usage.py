"""token 用量紀錄：每次對話一行 JSON（data_dir/usage.jsonl），給管理員儀表板按日／週／月／年統計。"""
import datetime as dt
import json
import os
import threading


class Usage:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "usage.jsonl")
        self.lock = threading.Lock()
        self.tot = {}          # user → {"day": (date, n), "week": (week_start, n), "total": n}；開機掃一次，之後只累加
        for r in self.rows():
            self._accumulate(r)

    def _accumulate(self, r):
        d = dt.datetime.fromisoformat(r["ts"]).date()
        n = r["prompt"] + r["completion"]
        t = self.tot.setdefault(r["user"], {"day": (d, 0), "week": (d - dt.timedelta(days=d.weekday()), 0), "total": 0})
        t["total"] += n
        if t["day"][0] == d:
            t["day"] = (d, t["day"][1] + n)
        elif d > t["day"][0]:
            t["day"] = (d, n)
        ws = d - dt.timedelta(days=d.weekday())
        if t["week"][0] == ws:
            t["week"] = (ws, t["week"][1] + n)
        elif ws > t["week"][0]:
            t["week"] = (ws, n)

    def add(self, user, model, prompt, completion, kind="chat", provider="ollama"):
        row = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "user": user, "model": model, "provider": provider,
               "kind": kind, "prompt": int(prompt or 0), "completion": int(completion or 0)}
        with self.lock:
            lead = ""
            try:   # 上次斷電寫到一半沒換行的話，先補一個，不然兩筆會黏在一起
                if os.path.getsize(self.path) and open(self.path, "rb").read()[-1:] != b"\n":
                    lead = "\n"
            except OSError:
                pass
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(lead + json.dumps(row, ensure_ascii=False) + "\n")
            self._accumulate(row)

    def rows(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if isinstance(r, dict) and isinstance(r.get("user"), str) and isinstance(r.get("ts"), str):
                            r["prompt"] = int(r.get("prompt") or 0); r["completion"] = int(r.get("completion") or 0)
                            dt.datetime.fromisoformat(r["ts"])
                            yield r
                    except (ValueError, TypeError):
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

    def hourly(self, user, days=7):
        """最近 days 天、每小時的 token（給熱力圖）。回傳由舊到新的天，每天 24 格。"""
        today = dt.date.today()
        start = today - dt.timedelta(days=days - 1)
        grid = {start + dt.timedelta(days=i): [0] * 24 for i in range(days)}
        calls = {d: 0 for d in grid}
        for r in self.rows():
            if r["user"] != user:
                continue
            t = dt.datetime.fromisoformat(r["ts"])
            if t.date() in grid:
                grid[t.date()][t.hour] += r["prompt"] + r["completion"]
                calls[t.date()] += 1
        wk = "一二三四五六日"
        out = []
        for d in sorted(grid):
            label = "今天" if d == today else "昨天" if d == today - dt.timedelta(days=1) else "週" + wk[d.weekday()]
            out.append({"date": d.isoformat(), "label": label, "cells": grid[d], "total": sum(grid[d]), "calls": calls[d]})
        return out

    def totals(self, user):
        """今天、這週（週一起）和從頭到現在的 token 總量（記憶體累計，不掃檔）。"""
        today = dt.date.today()
        week_start = today - dt.timedelta(days=today.weekday())
        with self.lock:
            t = self.tot.get(user)
            if not t:
                return {"day": 0, "week": 0, "total": 0}
            return {"day": t["day"][1] if t["day"][0] == today else 0,
                    "week": t["week"][1] if t["week"][0] == week_start else 0, "total": t["total"]}

    def over_quota(self, user, quota):
        """超過管理員設的上限就回一句說明，沒超過回 None。"""
        if not quota or not (quota.get("day") or quota.get("week")):
            return None
        t = self.totals(user)
        if quota.get("day") and t["day"] >= quota["day"]:
            return "今天的 token 用量已達上限（%s / %s），明天再來或請管理員調高" % (f"{t['day']:,}", f"{quota['day']:,}")
        if quota.get("week") and t["week"] >= quota["week"]:
            return "這週的 token 用量已達上限（%s / %s），下週再來或請管理員調高" % (f"{t['week']:,}", f"{quota['week']:,}")
        return None

