"""在 Kiki 裡生圖、生影片：用內建的工作流程（app/gen.json）叫本機的 ComfyUI 做。

安全：使用者只能給提示詞和幾個數字（尺寸、長度、步數、種子），伺服器把它們塞進內建工作流程的固定位置；
工作流程、節點、模型檔名都由伺服器決定，使用者送不進任何節點。成品存在 data_dir/gen/<帳號>/，只有本人拿得到。
"""
import json
import math
import os
import re
import secrets
import threading
import time

import comfy
import hub
from users import NAME_RE, WIN_DEVICES, safe_write

HERE = os.path.dirname(os.path.abspath(__file__))
ID_RE = re.compile(r"^[a-f0-9]{16}$")
PH_RE = re.compile(r"^\$(prompt|negative|seed|width|height|steps|length|fps|prefix|file[0-3])$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
EMBED_RE = re.compile(r"embedding\s*:", re.I)
RULE_RE = re.compile(r"^(\d+)n\+(\d+)$")
EXT = {"image": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"},
       "video": {".mp4": "video/mp4", ".webm": "video/webm", ".gif": "image/gif", ".webp": "image/webp"}}
MAX_PROMPT = 2000
MAX_STEPS = 100
MAX_FILE = 1024 ** 3                 # 單一成品最多 1 GB
USER_QUOTA = 3 * 1024 ** 3           # 一個帳號的成品加起來最多 3 GB
PER_USER = 2                         # 一個帳號同時最多幾個生成（含排隊）
TOTAL = 8                            # 整台同時最多幾個
MAX_WAIT = 4 * 3600                  # 一個工作最多等 4 小時
POLL = 2.0
QUOTA_MSG = "你的成品超過 %d GB，先刪掉一些舊的" % (USER_QUOTA // 1024 ** 3)


def load_recipes(path=None):
    try:
        with open(path or os.path.join(HERE, "gen.json"), encoding="utf-8") as f:
            return json.load(f).get("models") or {}
    except (OSError, ValueError):
        return {}


def clean_text(s, limit=MAX_PROMPT):
    s = CTRL_RE.sub("", str(s or "")).strip()[:limit]
    return EMBED_RE.sub("embedding ", s)       # ComfyUI 會把「embedding:檔名」當成去讀主機上的檔案


def _int(v, default):
    try:
        f = float(v)
        return int(f) if math.isfinite(f) else default
    except (TypeError, ValueError, OverflowError):
        return default


def frames(seconds, fps, rule, max_length):
    """秒數 → 影格數，符合模型要求的「Kn+B」（例如 4n+1、17n+5），至少 K+B 格。"""
    m = RULE_RE.match(str(rule or "")) or RULE_RE.match("1n+1")
    k, b = max(1, int(m.group(1))), int(m.group(2))
    n = max(1, int((seconds * fps - b) / k + 0.5))
    while n > 1 and n * k + b > max_length:
        n -= 1
    return n * k + b


def params(recipe, b):
    """把使用者送來的設定整理成工作流程要的值；超出範圍就夾回去，不合理就報錯。"""
    d, lim = recipe.get("defaults") or {}, recipe.get("limits") or {}
    prompt = clean_text(b.get("prompt"))
    if not prompt:
        raise ValueError("請先寫下想生成的內容")
    mult = max(8, _int(lim.get("multiple"), 16))
    lo, hi = _int(lim.get("min_side"), 256), _int(lim.get("max_side"), 2048)

    def side(v, dv):
        v = _int(v, dv)
        return max(lo, min(hi, int(v / mult + 0.5) * mult))
    vals = {"$prompt": prompt, "$width": side(b.get("width"), d.get("width", 1024)), "$height": side(b.get("height"), d.get("height", 1024)),
            "$steps": max(1, min(MAX_STEPS, _int(b.get("steps"), d.get("steps", 20)))),
            "$seed": _int(b.get("seed"), -1)}
    if not 0 <= vals["$seed"] < 2 ** 50:
        vals["$seed"] = secrets.randbelow(2 ** 48)
    neg = clean_text(b.get("negative"), 1000) if "negative" in b else recipe.get("negative_default", "")
    vals["$negative"] = neg or recipe.get("negative_default", "")
    if recipe.get("kind") == "video":
        fps = _int(d.get("fps"), 24)
        max_len = _int(lim.get("max_length"), d.get("length", 81))
        default_sec = _int(d.get("length"), 81) / fps
        try:
            sec = float(b.get("seconds", default_sec))
        except (TypeError, ValueError, OverflowError):
            sec = default_sec
        if not math.isfinite(sec):
            sec = default_sec
        vals["$fps"], vals["$length"] = fps, frames(max(0.1, min(sec, 60.0)), fps, lim.get("length_rule"), max_len)
    return vals


def fill(node, vals):
    """把工作流程裡「整格就是 $名稱」的值換掉。使用者的文字只會變成某一格的值，不會被當成結構解析。"""
    if isinstance(node, dict):
        return {k: fill(v, vals) for k, v in node.items()}
    if isinstance(node, list):
        return [fill(v, vals) for v in node]
    if isinstance(node, str) and node in vals:
        return vals[node]
    return node


def placeholders(node):
    """工作流程裡用到哪些 $名稱。"""
    if isinstance(node, dict):
        return set().union(*(placeholders(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(placeholders(v) for v in node)) if node else set()
    return {node} if isinstance(node, str) and PH_RE.match(node) else set()


def graph_for(recipe, variant, vals, prefix):
    files = variant["source"].get("files") or []
    v = dict(vals, **{"$prefix": prefix})
    for i, f in enumerate(files[:4]):
        v["$file%d" % i] = os.path.basename(str(f.get("path", "")))
    if placeholders(recipe["workflow"]) - set(v):      # 先檢查內建流程（不是填完再查，使用者的字可能剛好長得像 $名稱）
        raise ValueError("這個模型的工作流程設定不完整，請管理員更新面板")
    return fill(recipe["workflow"], v)


def ids_in(session):
    """一個對話裡生成過的作品編號。"""
    out = set()
    for m in (session or {}).get("messages") or []:
        g = m.get("gen") if isinstance(m, dict) else None
        if isinstance(g, dict) and ID_RE.match(str(g.get("id", ""))):
            out.add(g["id"])
    return out


class Gen:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "gen")
        self.lock = threading.Lock()
        self.io = threading.Lock()       # 紀錄檔的讀寫排隊：Windows 上檔案被讀的時候不能被取代
        self.gone = set()                # 刪掉的作品：背景工作晚一步想存紀錄時不准再寫回來
        self.live = {}           # id → {"user", "status", "progress", "step", "queue", "preview", "pn", "cancel"}
        self.recipes = load_recipes()

    # ── 檔案 ──
    def _udir(self, user):
        name = str(user.get("name", ""))
        if not (NAME_RE.match(name) or name == "admin") or name.split(".")[0] in WIN_DEVICES:
            raise ValueError("帳號不對")
        return os.path.join(self.dir, name)

    def _path(self, user, gid, ext=".json"):
        if not ID_RE.match(str(gid or "")):
            raise ValueError("編號不對")
        return os.path.join(self._udir(user), gid + ext)

    def _load(self, user, gid):
        try:
            p = self._path(user, gid)
            with self.io, open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, user, rec):
        os.makedirs(self._udir(user), exist_ok=True)
        data = json.dumps(rec, ensure_ascii=False)
        with self.io:
            if rec["id"] in self.gone:
                return
            safe_write(self._path(user, rec["id"]), data)

    def used(self, user):
        d, n = self._udir(user), 0
        try:
            for name in os.listdir(d):
                n += os.path.getsize(os.path.join(d, name))
        except OSError:
            pass
        return n

    # ── 給前端 ──
    def models(self, catalog, comfy_dir):
        out = []
        for m in catalog.get("models", []):
            r = self.recipes.get(m["id"])
            if not r:
                continue
            have = [v["label"] for v in m["variants"] if v["source"].get("kind") == "comfy" and hub._has(v, (), comfy_dir)]
            out.append({"id": m["id"], "name": m["name"], "vendor": m.get("vendor"), "kind": r["kind"], "variants": have,
                        "defaults": r.get("defaults") or {}, "limits": r.get("limits") or {},
                        "negative": "$negative" in placeholders(r["workflow"]), "steps": "$steps" in placeholders(r["workflow"])})
        return out

    def view(self, rec, admin=False):
        v = {k: rec.get(k) for k in ("id", "kind", "model", "variant", "name", "prompt", "width", "height", "length", "fps",
                                     "seed", "steps", "status", "error", "created", "finished", "size", "mime")}
        live = self.live.get(rec["id"])
        if live:
            v.update(status=live["status"], progress=live["progress"], step=live["step"], queue=live["queue"], pn=live["pn"])
        if admin and rec.get("detail"):
            v["detail"] = rec["detail"]
        return v

    # ── 開始 ──
    def start(self, user, catalog, cfg, b):
        st = hub.comfy_status(cfg)
        if not st["installed"]:
            raise ValueError("這台還沒裝 ComfyUI，請管理員重新跑安裝檔並選「裝 ComfyUI」")
        if not st["running"]:
            raise ValueError("ComfyUI 沒有在跑，請管理員啟動它（sudo systemctl start comfyui）")
        label = str(b.get("variant") or "")
        if not label:        # 沒指定版本：用第一個下載好的
            for mm in catalog.get("models", []):
                if mm.get("id") == b.get("model"):
                    label = next((x["label"] for x in mm["variants"] if x["source"].get("kind") == "comfy" and hub._has(x, (), st["dir"])), "")
        m, v = hub.find(catalog, b.get("model"), label)
        recipe = self.recipes.get(m["id"])
        if not recipe:
            raise ValueError("這個模型還不能在 Kiki 裡直接生成")
        if v["source"].get("kind") != "comfy" or not hub._has(v, (), st["dir"]):
            raise ValueError("這個模型還沒下載完，請管理員到模型庫下載")
        vals = params(recipe, b)
        if self.used(user) > USER_QUOTA:
            raise ValueError(QUOTA_MSG)
        gid = secrets.token_hex(8)
        graph = graph_for(recipe, v, vals, "kiki/" + gid)
        with self.lock:            # 檢查名額和占住名額要在同一把鎖裡，同時送很多個也不會超過
            busy = [x for x in self.live.values() if x["status"] in ("queued", "running")]
            if sum(x["user"] == user["name"] for x in busy) >= PER_USER:
                raise ValueError("你已經有 %d 個在生成了，等它們做完再送" % PER_USER)
            if len(busy) >= TOTAL:
                raise ValueError("主機現在太忙，等一下再試")
            self._track_locked(user, gid)
        rec = {"id": gid, "kind": recipe["kind"], "model": m["id"], "variant": v["label"], "name": m["name"],
               "prompt": vals["$prompt"], "width": vals["$width"], "height": vals["$height"], "length": vals.get("$length"),
               "fps": vals.get("$fps"), "seed": vals["$seed"], "steps": vals["$steps"], "status": "queued",
               "created": time.time(), "prompt_id": ""}
        try:
            self._save(user, rec)
        except OSError:
            self.live.pop(gid, None)
            raise ValueError("存不了紀錄（主機硬碟滿了？）")
        threading.Thread(target=self._run, args=(user, rec, graph, cfg), daemon=True).start()
        return rec

    def _track_locked(self, user, gid):
        old = time.time() - 600
        for k in [k for k, x in self.live.items() if x["status"] not in ("queued", "running") and x.get("ended", 0) < old]:
            del self.live[k]
        self.live[gid] = {"user": user["name"], "status": "queued", "progress": 0, "step": "", "queue": 0,
                          "preview": None, "pn": 0, "cancel": False, "deleted": False, "ended": 0}

    # ── 背景執行 ──
    def _run(self, user, rec, graph, cfg):
        live = self.live[rec["id"]]
        ws = None
        try:
            c = comfy.Comfy(hub.comfy_cfg(cfg)["url"])
            cid = secrets.token_hex(12)
            try:
                ws = c.ws(cid)
            except OSError:
                ws = None
            if graph is not None:
                rec["prompt_id"] = c.submit(graph, cid)
                self._save(user, rec)
            entry = self._wait(c, ws, rec, live)
            self._finish(user, rec, live, c, entry, cfg)
        except comfy.ComfyError as e:
            self._fail(user, rec, live, str(e), e.detail)
        except Exception as e:  # 背景工作不能讓伺服器掛掉
            self._fail(user, rec, live, "生成失敗", repr(e))
        finally:
            if ws:
                ws.close()
            live["ended"] = time.time()
            if live["deleted"]:          # 做到一半被刪掉：剛存的紀錄和檔案一起清掉
                self._purge(user, rec["id"])

    def _wait(self, c, ws, rec, live):
        pid, start, last, missing = rec["prompt_id"], time.time(), 0, 0
        while time.time() - start < MAX_WAIT:
            if live["cancel"]:
                c.cancel(pid)
                live["cancel"] = False
            if ws:
                try:
                    msg = ws.recv(1.0)
                except OSError:
                    ws = None
                    msg = None
                self._on(msg, pid, live)
            else:
                time.sleep(1.0)
            if time.time() - last >= POLL:
                last = time.time()
                entry = c.history(pid)          # 只有做完（成功或失敗）才會出現在 history
                if entry and (entry.get("status") or entry.get("outputs")):
                    return entry
                running, pending = c.queue()
                if pid in running:
                    live["status"], live["queue"], missing = "running", 0, 0
                elif pid in pending:
                    live["queue"], missing = pending.index(pid) + len(running), 0      # 前面還有幾個
                else:
                    # 兩次查詢之間剛好做完：history 再問一次；連續好幾次都找不到才算不見
                    entry = c.history(pid)
                    if entry and (entry.get("status") or entry.get("outputs")):
                        return entry
                    missing += 1
                    if missing >= 3:
                        raise comfy.ComfyError("這個工作不見了（ComfyUI 可能重新啟動過），請再送一次")
        c.cancel(pid)
        raise comfy.ComfyError("等太久了（超過 4 小時），已取消")

    @staticmethod
    def _on(msg, pid, live):
        if isinstance(msg, tuple) and msg[0] == "preview":
            if live["status"] == "running":
                live["preview"], live["pn"] = (msg[1], msg[2]), live["pn"] + 1
            return
        if not isinstance(msg, dict):
            return
        data = msg.get("data") or {}
        if not isinstance(data, dict) or data.get("prompt_id") not in (pid, None):
            return
        t = msg.get("type")
        if t in ("execution_start", "executing") and data.get("prompt_id") == pid:
            live["status"], live["queue"] = "running", 0
        if t == "progress" and data.get("prompt_id") == pid:
            try:
                live["progress"] = max(0.0, min(1.0, float(data["value"]) / float(data["max"])))
                live["step"] = "%d／%d" % (int(data["value"]), int(data["max"]))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass

    def _finish(self, user, rec, live, c, entry, cfg):
        status = entry.get("status") or {}
        if status.get("status_str") == "error" or status.get("completed") is False:
            msg, detail = comfy.explain(status.get("messages"))
            return self._fail(user, rec, live, msg, detail, cancelled=(msg == "已取消"))
        items = comfy.outputs(entry)
        allow = EXT[rec["kind"]]
        pick = next((it for it in items if os.path.splitext(it["filename"])[1].lower() in allow), None)
        if not pick:
            return self._fail(user, rec, live, "ComfyUI 做完了，但沒有產生%s" % ("影片" if rec["kind"] == "video" else "圖片"),
                              json.dumps(items, ensure_ascii=False)[:1000])
        ext = os.path.splitext(pick["filename"])[1].lower()
        if self.used(user) > USER_QUOTA:
            return self._fail(user, rec, live, QUOTA_MSG)
        dst = self._path(user, rec["id"], ext)
        rec["size"] = c.download(pick, dst, MAX_FILE)
        rec.update(status="done", ext=ext, mime=allow[ext], finished=time.time())
        self._save(user, rec)
        live.update(status="done", progress=1.0, preview=None)
        for it in items:
            self._remove_comfy_output(cfg, it)
        c.forget(rec["prompt_id"])

    @staticmethod
    def _remove_comfy_output(cfg, it):
        """ComfyUI 的 output/ 裡那一份刪掉（我們已經存了一份），只刪 output/kiki/ 底下的。"""
        d = hub.comfy_cfg(cfg)["dir"]
        if not d or it.get("type") != "output":
            return
        base = os.path.abspath(os.path.join(d, "output", "kiki"))
        p = os.path.abspath(os.path.join(d, "output", str(it.get("subfolder", "")), str(it["filename"])))
        if os.path.dirname(p) == base:
            try:
                os.remove(p)
            except OSError:
                pass

    def _fail(self, user, rec, live, msg, detail="", cancelled=False):
        rec.update(status="cancelled" if cancelled else "error", error="" if cancelled else msg, detail=detail[:2000],
                   finished=time.time())
        self._save(user, rec)
        live.update(status=rec["status"], preview=None)

    # ── 查詢、取消、刪除 ──
    def status(self, user, gid, cfg):
        rec = self._load(user, gid)
        if not rec:
            return None
        if rec["status"] in ("queued", "running") and gid not in self.live:
            # 面板重新啟動過：有 prompt_id 就接著看，沒有就算中斷
            if rec.get("prompt_id"):
                with self.lock:
                    if gid in self.live:         # 另一個查詢剛好先接手了
                        return rec
                    self._track_locked(user, gid)
                threading.Thread(target=self._run, args=(user, rec, None, cfg), daemon=True).start()
            else:
                rec.update(status="error", error="面板重新啟動過，這次生成中斷了，請再送一次")
                self._save(user, rec)
        return rec

    def preview(self, user, gid):
        live = self.live.get(gid)
        if not live or live["user"] != user["name"] or not live["preview"]:
            return None
        return live["preview"]

    def cancel(self, user, gid):
        rec = self._load(user, gid)
        live = self.live.get(gid)
        if not rec or not live or live["user"] != user["name"]:
            return False
        live["cancel"] = True
        return True

    def file(self, user, gid):
        rec = self._load(user, gid)
        if not rec or rec.get("status") != "done" or rec.get("ext") not in EXT[rec.get("kind", "image")]:
            return None
        p = self._path(user, gid, rec["ext"])
        return (p, rec["mime"]) if os.path.isfile(p) else None

    def delete(self, user, gid):
        rec = self._load(user, gid)
        if not rec:
            return False
        if gid in self.live and self.live[gid]["status"] in ("queued", "running"):
            self.cancel(user, gid)
        with self.io:
            self.gone.add(gid)
        if gid in self.live:
            self.live[gid]["deleted"] = True
        self._purge(user, gid)
        return True

    def _purge(self, user, gid):
        d = self._udir(user)
        for name in os.listdir(d) if os.path.isdir(d) else []:
            if os.path.splitext(name)[0] == gid:
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass

    def purge_unused(self, user, ids, sessions_dir):
        """刪掉一個對話留下的作品；分支出去的對話（或別的對話）還用到的就留著。"""
        keep = set()
        try:
            for name in os.listdir(sessions_dir):
                if name.endswith(".json"):
                    with open(os.path.join(sessions_dir, name), encoding="utf-8") as f:
                        text = f.read()
                    keep.update(i for i in ids if i in text)
        except OSError:
            return
        for gid in ids - keep:
            self.delete(user, gid)
