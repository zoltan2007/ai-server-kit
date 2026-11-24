"""模型庫：分類、適合本機排行、能力分數排行，以及 ComfyUI 模型檔的下載。

資料在 hub.json（研究過的精選清單，每個數字都附來源）。語言／程式碼模型交給 Ollama 下載（重用 ollama_api.start_pull）；
生圖、影片、配音等模型是 ComfyUI 用的 safetensors，下載到 ComfyUI 的 models/<子資料夾>。
下載的網址和存檔位置只從 hub.json 來，使用者只能選「哪個模型的哪個版本」，不能自己給網址或路徑。
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import sysinfo
from jobs import Job

HERE = os.path.dirname(os.path.abspath(__file__))
GB = 1000 ** 3
COMFY_FOLDERS = {"checkpoints", "diffusion_models", "text_encoders", "vae", "clip_vision", "loras", "upscale_models",
                 "audio_encoders", "model_patches", "unet", "clip", "embeddings", "TTS", "tts"}
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
PATH_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._ -]{0,120}/){0,6}[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.(?:safetensors|gguf|pt|pth|bin|ckpt|json|onnx|txt|model)$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,79}$")
HF_RESOLVE = "https://huggingface.co/%s/resolve/main/%s"
UA = "ai-server-kit/0.1 (+https://github.com/zoltan2007/ai-server-kit)"
_urlopen = urllib.request.urlopen          # 測試時換成假的，不碰網路

# 還沒組好的主機也能先看排行：照這台的規格算「適不適合」
PROFILES = {
    "aibox": {"name": "AI 主機（2×RTX 3080 20GB＋RTX 5060 Ti 16GB、64GB 記憶體）",
              "gpus": [{"name": "RTX 3080（改裝）", "gb": 20, "fp8": False}, {"name": "RTX 3080（改裝）", "gb": 20, "fp8": False},
                       {"name": "RTX 5060 Ti", "gb": 16, "fp8": True}], "ram": 64},
}
TIERS = {3: "一張顯卡放得下", 2: "拆到好幾張顯卡，一樣全在顯卡上跑", 1: "有一部分要放記憶體，會慢很多", 0: "這台放不下"}
FP8_GPU = re.compile(r"RTX\s*(40|50)\d\d|RTX\s*PRO|\bL4\b|L40|H100|H200|B100|B200|GB10|Ada|Blackwell", re.I)


def load(path=None):
    with open(path or os.path.join(HERE, "hub.json"), encoding="utf-8") as f:
        return json.load(f)


# ── 這台機器的規格 ──
def host_profile():
    cards, _ = sysinfo.gpus()
    mem = sysinfo.memory() or {}
    gpus = [{"name": c.get("name", "顯示卡"), "gb": round((c.get("mem_total") or 0) / 1024 ** 3, 1),
             "fp8": bool(FP8_GPU.search(c.get("name") or ""))} for c in cards]
    return {"name": "這台主機", "gpus": gpus, "ram": round((mem.get("total") or 0) / 1024 ** 3)}


def fit(variant, runtime, prof):
    """這個版本在這台機器上能怎麼跑：(等級 0–3, 說明)。

    Ollama 可以把一個模型拆到好幾張顯卡、再放不下的放記憶體；ComfyUI 一次只用一張卡，放不下的部分靠記憶體 offload。
    只留 5% 給系統與畫面；記憶體只算六成能拿來放模型。都是粗估。
    """
    need = variant.get("run_gb") or variant.get("file_gb")
    if not need:
        return 0, "不知道要多少顯存"
    tier, text = _fit(need, runtime, prof)
    if not variant.get("run_gb"):
        text += "（官方沒寫最低顯存，用檔案大小估）"
    return tier, text


def _fit(need, runtime, prof):
    cards = sorted((g["gb"] for g in prof["gpus"]), reverse=True)
    best = cards[0] * 0.95 if cards else 0
    ram = (prof.get("ram") or 0) * 0.6
    if need <= best:
        return 3, TIERS[3]
    if runtime == "ollama" and len(cards) > 1 and need <= sum(cards) * 0.92:
        return 2, TIERS[2]
    pool = (sum(cards) * 0.92 if runtime == "ollama" else best) + ram
    if need <= pool:
        return 1, TIERS[1]
    return 0, TIERS[0]


def fp8_note(variant, prof):
    # 只管 ComfyUI 的 FP8／FP4 權重；Ollama 的 MXFP4 之類量化它自己會處理
    prec = (variant.get("precision") or variant.get("label") or "").lower()
    if variant["source"]["kind"] != "comfy" or not re.search(r"fp8|nvfp4|fp4", prec):
        return ""
    if any(g.get("fp8") for g in prof["gpus"]):
        if not all(g.get("fp8") for g in prof["gpus"]):
            return "只有支援 FP8 的那張卡跑得快，其他卡會先轉成 16 位元"
        return ""
    return "這台的顯卡沒有 FP8 加速，會先轉成 16 位元再算，比較慢"


def _group(tier):
    """排行用：放得進顯卡（一張或拆到好幾張）算同一級。Ollama 拆卡一樣全在顯卡上跑；真正會慢很多的是借記憶體。"""
    return min(tier, 2)


def _higher_better(src):
    return not (src or {}).get("lower_is_better")


def build(catalog, prof, installed=(), comfy_dir=None):
    """回傳給頁面的資料：每個模型算好每個版本適不適合、哪個版本最推薦、兩種排行的順序。"""
    cats = {c["id"]: c for c in catalog["categories"]}
    sources = catalog.get("sources", {})
    have = set(installed)
    models = []
    for m in catalog["models"]:
        runtime = "ollama" if any(v["source"]["kind"] == "ollama" for v in m["variants"]) else "comfy"
        runtime_of = lambda v: "ollama" if v["source"]["kind"] == "ollama" else "comfy"     # noqa: E731  擴充節點也是在 ComfyUI 裡跑
        vs = []
        for v in m["variants"]:
            tier, why = fit(v, runtime_of(v), prof)
            vs.append(dict(v, fit=tier, fit_text=why, note=fp8_note(v, prof), have=_has(v, have, comfy_dir)))
        # 推薦版本：顯卡放得下的話挑最大的（量化越高品質越好）；放不下就挑最小、最有機會跑起來的
        size = lambda v: v.get("run_gb") or v.get("file_gb") or 0     # noqa: E731
        top = max(vs, key=lambda v: (_group(v["fit"]), size(v) if _group(v["fit"]) == 2 else -size(v)))
        models.append(dict(m, variants=vs, runtime=runtime, best=vs.index(top), fit=top["fit"]))
    ranks = {}
    for cid in cats:
        mine = [i for i, m in enumerate(models) if cid in m["cat"]]
        sc = {i: _score(models[i], cid) for i in mine}
        src = sources.get(cats[cid].get("score_src"), {})
        sign = 1 if _higher_better(src) else -1
        by_score = sorted((i for i in mine if sc[i] is not None), key=lambda i: -sign * sc[i]) + [i for i in mine if sc[i] is None]
        # 適合本機：先看跑不跑得動（全在顯卡上 > 要借記憶體 > 放不下），同一級裡分數高的在前，沒有分數的放後面
        by_fit = sorted(mine, key=lambda i: (-_group(models[i]["fit"]), sc[i] is None, -sign * (sc[i] or 0),
                                             -(models[i]["variants"][models[i]["best"]].get("run_gb") or 0)))
        ranks[cid] = {"fit": [models[i]["id"] for i in by_fit], "score": [models[i]["id"] for i in by_score]}
    return {"categories": catalog["categories"], "vendors": catalog.get("vendors", {}), "sources": sources,
            "updated": catalog.get("updated"), "models": models, "ranks": ranks, "profile": prof}


def _score(m, cid):
    s = (m.get("score") or {}).get(cid)
    return s.get("value") if isinstance(s, dict) and isinstance(s.get("value"), (int, float)) else None


def _has(v, installed, comfy_dir):
    src = v["source"]
    if src["kind"] == "node" or src.get("gated"):
        return False
    if src["kind"] == "ollama":
        name = src["name"]
        return name in installed or (":" not in name and name + ":latest" in installed)
    if not comfy_dir:
        return False
    try:
        return all(os.path.exists(target(comfy_dir, f)) for f in src["files"])
    except ValueError:
        return False


# ── ComfyUI ──
def comfy_cfg(cfg):
    c = cfg.get("comfyui") or {}
    return {"dir": c.get("dir") or "", "url": c.get("url") or "http://127.0.0.1:8188"}


_comfy_cache = {"t": 0, "v": None}


def comfy_status(cfg):
    """裝了沒（資料夾裡有 main.py）、有沒有在跑（問一下 /system_stats）。只問本機的位址。"""
    c = comfy_cfg(cfg)
    installed = bool(c["dir"]) and os.path.isfile(os.path.join(c["dir"], "main.py"))
    now = time.time()
    if _comfy_cache["v"] is not None and now - _comfy_cache["t"] < 10 and _comfy_cache["k"] == (c["dir"], c["url"]):
        return _comfy_cache["v"]
    running, version = False, None
    host = urllib.parse.urlsplit(c["url"]).hostname
    if installed and host in ("127.0.0.1", "localhost", "::1"):
        try:
            with _urlopen(urllib.request.Request(c["url"].rstrip("/") + "/system_stats", headers={"User-Agent": UA}), timeout=1.5) as r:
                info = json.loads(r.read(65536) or b"{}")
            running, version = True, (info.get("system") or {}).get("comfyui_version")
        except (OSError, ValueError, urllib.error.URLError):
            pass
    v = {"installed": installed, "running": running, "version": version, "dir": c["dir"] if installed else ""}
    _comfy_cache.update(t=now, v=v, k=(c["dir"], c["url"]))
    return v


def target(comfy_dir, f):
    """一個檔案要存到哪裡。repo、路徑、子資料夾都要先通過檢查；檔名只取最後一段，不可能跳出 models/。"""
    repo, path, folder = str(f.get("repo", "")), str(f.get("path", "")), str(f.get("folder", ""))
    if not REPO_RE.match(repo) or not PATH_RE.match(path) or ".." in path.split("/") or folder not in COMFY_FOLDERS:
        raise ValueError("模型清單裡的檔案資料不對：%s/%s" % (repo, path))
    base = os.path.join(os.path.abspath(comfy_dir), "models", folder)
    dst = os.path.abspath(os.path.join(base, os.path.basename(path)))
    if os.path.dirname(dst) != base:
        raise ValueError("存檔位置不對")
    return dst


def find(catalog, mid, label):
    if not ID_RE.match(str(mid)):
        raise ValueError("模型代號不對")
    for m in catalog["models"]:
        if m["id"] == mid:
            for v in m["variants"]:
                if v["label"] == label:
                    return m, v
    raise ValueError("找不到這個模型版本")


def start_comfy_download(store, comfy_dir, m, v):
    files = v["source"]["files"]
    plan = [(f, target(comfy_dir, f)) for f in files]
    total = int(sum((f.get("gb") or 0) for f in files) * GB)
    os.makedirs(os.path.join(comfy_dir, "models"), exist_ok=True)
    free = shutil.disk_usage(os.path.join(comfy_dir, "models")).free
    if total + 2 * GB > free:
        raise ValueError("磁碟空間不夠：要 %.1f GB，只剩 %.1f GB" % (total / GB, free / GB))
    job = store.add(Job("hub", "下載 %s（%s）" % (m["name"], v["label"]), {"model": m["id"], "variant": v["label"]}))

    def run():
        done_before, last = 0, [0.0]

        def prog(status, n, force=False):
            now = time.time()
            if force or now - last[0] > 0.5:
                job.emit({"t": "p", "status": status, "completed": done_before + n, "total": total or None})
                last[0] = now

        try:
            for k, (f, dst) in enumerate(plan, 1):
                step = "第 %d／%d 個檔案" % (k, len(plan))
                if job.killed:
                    return
                name = os.path.basename(dst)
                if os.path.exists(dst):
                    job.emit({"t": "o", "d": "%s 已經有了，跳過\n" % name})
                    done_before += int((f.get("gb") or 0) * GB)
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                _fetch(job, HF_RESOLVE % (f["repo"], urllib.parse.quote(f["path"])), dst, lambda n: prog(step, n))
                if job.killed:
                    return
                done_before += os.path.getsize(dst)
                prog(step + "完成", 0, force=True)
                job.emit({"t": "o", "d": "%s → models/%s\n" % (name, f["folder"])})
            job.finish("done", model=m["id"], variant=v["label"])
        except (OSError, urllib.error.URLError, ValueError) as e:
            if not job.killed:
                job.emit({"t": "o", "d": "%s\n" % e})
                job.finish("error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job


def _fetch(job, url, dst, prog):
    """下載到 .part，斷了重來會從斷的地方接（Range）；完成才改名。"""
    part = dst + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = "bytes=%d-" % have
    try:
        resp = _urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:          # 早就下載完，只是沒改名
            os.replace(part, dst)
            return
        if e.code in (401, 403):
            raise ValueError("Hugging Face 拒絕下載（%d）：這個模型可能要先在網站上同意授權條款" % e.code)
        raise ValueError("下載失敗（HTTP %d）" % e.code)
    job.cancel = resp.close
    with resp:
        if have and getattr(resp, "status", 200) != 206:
            have = 0                        # 伺服器不支援續傳：從頭來
        with open(part, "ab" if have else "wb") as out:
            n = have
            while not job.killed:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                n += len(chunk)
                prog(n)
    if job.killed:
        return
    os.replace(part, dst)


def remove_comfy(comfy_dir, v):
    """刪掉這個版本的檔案（只刪清單裡列的那幾個；別的模型共用的文字編碼器、VAE 也會一起刪，頁面會先提醒）。"""
    gone = []
    for f in v["source"]["files"]:
        p = target(comfy_dir, f)
        for q in (p, p + ".part"):
            if os.path.isfile(q):
                os.remove(q)
                gone.append(os.path.basename(q))
    return gone
