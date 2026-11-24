"""把研究整理出來的模型資料轉成 app/hub.json，順便檢查每一筆。

    python -I tools/build_hub.py 研究結果.json [--write]

檢查：分類與廠商對得上、每個 ComfyUI 檔案的 repo／路徑／資料夾都通過 hub.target、Ollama 名稱合法、
同一個分類只用一個分數來源。不合格的版本或模型會被丟掉並列出來，不會寫進 hub.json。
"""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import hub  # noqa: E402
from ollama_api import NAME_RE  # noqa: E402

# 要先登入 Hugging Face、同意授權才能下載的 repo（2026-11-25 用 HF API 的 gated 欄位查過）。面板沒有 HF 帳號功能，這些只列資訊
GATED = {"Lightricks/LTX-2.5", "black-forest-labs/flux-3-image-klein-comfy"}

# Hugging Face 上分成好幾個檔案的 GGUF（2026-11-25 查 HF API）。Ollama 用 hf.co 拉這種檔不一定成功，卡片上提醒
SHARDED = {"hf.co/unsloth/Qwen3.8-Flash-Next-GGUF:UD-IQ3_XXS", "hf.co/unsloth/DeepSeek-V4.1-Flash-1017-GGUF:UD-IQ1_S",
           "hf.co/unsloth/DeepSeek-V4.1-Flash-1017-GGUF:UD-Q4_K_XL", "hf.co/unsloth/GLM-5.3-GGUF:UD-IQ2_M",
           "hf.co/unsloth/GLM-5.3-GGUF:UD-Q4_K_XL"}

CATEGORIES = [
    ("llm", "語言模型", "ollama"), ("code", "程式碼", "ollama"), ("image", "生成圖片", "comfy"),
    ("video", "影片生成", "comfy"), ("tts", "配音", "comfy"), ("asr", "語音辨識", "comfy"), ("embed", "嵌入", "ollama"),
]


def num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def clean_variant(m, v, problems):
    src = v.get("source") or {}
    out = {"label": str(v.get("label") or "")[:40], "file_gb": num(v.get("file_gb")), "run_gb": num(v.get("run_gb"))}
    for k in ("precision", "run_note"):
        if v.get(k):
            out[k] = str(v[k])[:120]
    if not out["label"]:
        problems.append("%s：版本沒有名稱" % m["id"])
        return None
    if src.get("kind") == "ollama":
        if not NAME_RE.match(str(src.get("name", ""))):
            problems.append("%s %s：Ollama 名稱不合法 %r" % (m["id"], out["label"], src.get("name")))
            return None
        out["source"] = {"kind": "ollama", "name": src["name"]}
        if src["name"] in SHARDED:
            out["warn"] = "這個 GGUF 分成好幾個檔案，Ollama 不一定拉得下來（未實測），拉不下來就改用 llama.cpp"
    elif src.get("kind") == "comfy":
        files = []
        for f in src.get("files") or []:
            f = {"repo": f.get("repo"), "path": f.get("path"), "folder": f.get("folder"), "gb": num(f.get("gb"))}
            try:
                hub.target("/tmp/comfy", f)
            except ValueError as e:
                problems.append("%s %s：%s" % (m["id"], out["label"], e))
                return None
            files.append(f)
        if not files:
            problems.append("%s %s：沒有檔案" % (m["id"], out["label"]))
            return None
        out["source"] = {"kind": "comfy", "files": files}
        if src.get("needs"):
            # 要擴充節點的模型（配音、語音辨識…）各有自己的資料夾規則，檔名也常常都叫 model.safetensors：
            # 面板不替它下載，只列資訊和節點連結，照節點說明放
            out["source"] = {"kind": "node", "needs": str(src["needs"])[:200], "files": files}
        elif any(f["repo"] in GATED for f in files):
            out["source"]["gated"] = True
        else:
            names = [(f["folder"], f["path"].rsplit("/", 1)[-1]) for f in files]
            if len(names) != len(set(names)):
                problems.append("%s %s：同一個版本裡有兩個檔案會存成同一個檔名" % (m["id"], out["label"]))
                return None
        if not out["file_gb"] and all(f["gb"] for f in files):
            out["file_gb"] = round(sum(f["gb"] for f in files), 1)
    else:
        problems.append("%s %s：不認得的下載方式 %r" % (m["id"], out["label"], src.get("kind")))
        return None
    return out


def build(research):
    problems = []
    vendors = research.get("vendors") or {}
    sources = research.get("sources") or {}
    cat_ids = {c[0] for c in CATEGORIES}
    # 研究資料裡，只有一個分數的模型會直接寫 {"value", "src"}：用「這個來源在別的模型是給哪一類」決定它屬於哪一類
    src_cat = {}
    for m in research.get("models") or []:
        raw, cats = m.get("score") or {}, [c for c in (m.get("cat") or []) if c in cat_ids]
        if isinstance(raw, dict) and "value" in raw and len(cats) == 1:
            src_cat.setdefault(raw.get("src"), cats[0])
        elif isinstance(raw, dict):
            for c, v in raw.items():
                if isinstance(v, dict) and c in cat_ids:
                    src_cat.setdefault(v.get("src"), c)
    models, seen = [], set()
    for m in research.get("models") or []:
        mid = str(m.get("id") or "")
        if not hub.ID_RE.match(mid) or mid in seen:
            problems.append("模型代號不合法或重複：%r" % mid)
            continue
        cats = [c for c in (m.get("cat") or []) if c in cat_ids]
        if not cats or m.get("vendor") not in vendors:
            problems.append("%s：分類或廠商不對" % mid)
            continue
        vs = [x for x in (clean_variant(m, v, problems) for v in m.get("variants") or []) if x]
        uniq = {}
        for v in vs:                                            # 同名的版本只留第一個
            uniq.setdefault(v["label"], v)
        vs = list(uniq.values())
        if not vs:
            problems.append("%s：沒有可用的版本，整個略過" % mid)
            continue
        score = {}
        raw = m.get("score") or {}
        if isinstance(raw, dict) and "value" in raw:
            c = src_cat.get(raw.get("src"))
            raw = {c if c in cats else cats[0]: raw}
        for c in cats:
            s = raw.get(c) if isinstance(raw, dict) else None
            if isinstance(s, dict) and isinstance(s.get("value"), (int, float)) and s.get("src") in sources:
                score[c] = {"value": s["value"], "src": s["src"]}
                if s.get("note"):
                    score[c]["note"] = str(s["note"])[:40]         # 例如測的是哪個推理強度
        seen.add(mid)
        out = {"id": mid, "cat": cats, "name": str(m.get("name") or mid)[:60], "vendor": m["vendor"],
               "license": str(m.get("license") or "")[:40], "released": str(m.get("released") or "")[:7],
               "desc": str(m.get("desc") or "")[:80], "url": m.get("url") if str(m.get("url") or "").startswith("https://") else "",
               "variants": vs, "score": score}
        models.append(out)
    # 不同模型共用同名檔案（例如同一個 VAE）時，大小要一樣，不然「已經有了就跳過」會拿錯檔
    shared = {}
    for m in models:
        for v in m["variants"]:
            if v["source"]["kind"] != "comfy" or v["source"].get("gated"):
                continue
            for f in v["source"]["files"]:
                key = (f["folder"], f["path"].rsplit("/", 1)[-1])
                if key in shared and shared[key][0] != f["gb"]:
                    problems.append("%s %s：%s/%s 跟 %s 的同名檔案大小不同" % (m["id"], v["label"], key[0], key[1], shared[key][1]))
                shared.setdefault(key, (f["gb"], m["id"]))
    # 每個分類只用一個分數來源：用最多模型的那個，其他來源的分數拿掉
    cats_out = []
    for cid, name, runtime in CATEGORIES:
        cnt = collections.Counter(m["score"][cid]["src"] for m in models if cid in m["score"])
        src = cnt.most_common(1)[0][0] if cnt else None
        for m in models:
            if cid in m["score"] and m["score"][cid]["src"] != src:
                problems.append("%s：%s 的分數來源跟同類不同，拿掉" % (m["id"], cid))
                del m["score"][cid]
        cats_out.append({"id": cid, "name": name, "runtime": runtime, "score_src": src})
    used = {s for c in cats_out for s in [c["score_src"]] if s}
    srcs = {k: {"name": v.get("name"), "url": v.get("url") if str(v.get("url") or "").startswith("https://") else "",
                "date": v.get("date"), "lower_is_better": bool(v.get("lower_is_better"))} for k, v in sources.items() if k in used}
    vend = {k: {"name": str(v.get("name") or k)[:40], "color": v.get("color") if str(v.get("color") or "").startswith("#") else "#7b8190"}
            for k, v in vendors.items() if any(m["vendor"] == k for m in models)}
    return {"updated": research.get("checked") or "", "note": "每個數字都有來源；顯存需求是粗估。用 tools/build_hub.py 從研究結果重建。",
            "vendors": vend, "sources": srcs, "categories": cats_out, "models": models}, problems


def main():
    research = json.load(open(sys.argv[1], encoding="utf-8"))
    out, problems = build(research)
    for p in problems:
        print("  ✗ " + p)
    for c in out["categories"]:
        ms = [m for m in out["models"] if c["id"] in m["cat"]]
        print("%-6s %2d 個，分數來源：%s，有分數 %d 個" % (c["id"], len(ms), c["score_src"], sum(c["id"] in m["score"] for m in ms)))
    if "--write" in sys.argv:
        with open(os.path.join(ROOT, "app", "hub.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("已寫入 app/hub.json")


if __name__ == "__main__":
    main()
