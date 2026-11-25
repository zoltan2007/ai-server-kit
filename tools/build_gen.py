"""把研究整理好的 ComfyUI 工作流程檢查過，寫成 app/gen.json（Kiki 生圖、生影片用的內建流程）。

    python -I tools/build_gen.py <研究結果.json> [--write]

檢查：只收 supported 的模型；類別要跟模型庫一致；每個節點都要有查證紀錄；連線都要接到存在的節點；
只用允許的 $名稱；$fileN 不能超過模型庫裡每個版本的檔案數；一定要有存檔節點、存檔名稱用 $prefix。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import gen  # noqa: E402

SAVE = {"image": {"SaveImage"}, "video": {"SaveVideo"}}
KEEP = ("kind", "defaults", "limits", "negative_default", "workflow", "sources", "notes")


def check(mid, m, cat_model):
    errs = []
    kind = m.get("kind")
    if kind not in SAVE:
        errs.append("kind 不對")
        return errs
    if kind not in cat_model["cat"]:
        errs.append("類別跟模型庫不一樣：%s vs %s" % (kind, cat_model["cat"]))
    wf = m.get("workflow") or {}
    if not wf:
        errs.append("沒有工作流程")
    verified = m.get("verified_nodes") or {}
    for nid, node in wf.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            errs.append("節點 %s 格式不對" % nid)
            continue
        if node["class_type"] not in verified:
            errs.append("節點 %s（%s）沒有查證紀錄" % (nid, node["class_type"]))
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int) and v[0] not in wf:
                errs.append("節點 %s 的 %s 接到不存在的節點 %s" % (nid, k, v[0]))
            if isinstance(v, str) and v.startswith("$") and not gen.PH_RE.match(v):
                errs.append("節點 %s 的 %s 用了不認識的 %s" % (nid, k, v))
    used = gen.placeholders(wf)
    for need in ("$prompt", "$prefix"):
        if need not in used:
            errs.append("沒有用到 %s" % need)
    if kind == "video" and not {"$length", "$fps"} <= used:
        errs.append("影片要用到 $length 和 $fps")
    saves = [n for n in wf.values() if n.get("class_type") in SAVE[kind]]
    if not saves or any(n["inputs"].get("filename_prefix") != "$prefix" for n in saves):
        errs.append("存檔節點要有，而且檔名用 $prefix")
    idx = [int(p[5:]) for p in used if p.startswith("$file")]
    variants = [v for v in cat_model["variants"] if v["source"].get("kind") == "comfy"]
    if not variants:
        errs.append("模型庫裡沒有可下載的 ComfyUI 版本")
    for v in variants:
        if idx and max(idx) >= len(v["source"].get("files") or []):
            errs.append("版本 %s 的檔案不夠 $file%d" % (v["label"], max(idx)))
    lim = m.get("limits") or {}
    if kind == "video" and not gen.RULE_RE.match(str(lim.get("length_rule", ""))):
        errs.append("length_rule 要像 4n+1")
    d = m.get("defaults") or {}
    for k in ("width", "height") + (("length", "fps") if kind == "video" else ()):
        if not isinstance(d.get(k), int) or d[k] <= 0:
            errs.append("defaults.%s 要是正整數" % k)
    return errs


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        research = json.load(f)
    with open(os.path.join(ROOT, "app", "hub.json"), encoding="utf-8") as f:
        catalog = {m["id"]: m for m in json.load(f)["models"]}
    out, bad, skipped = {}, 0, []
    for mid, m in sorted(research["models"].items()):
        if not m.get("supported"):
            skipped.append("%s：%s" % (mid, m.get("reason", "")[:80]))
            continue
        if mid not in catalog:
            print("NG %s 不在模型庫" % mid)
            bad += 1
            continue
        errs = check(mid, m, catalog[mid])
        if errs:
            bad += 1
            print("NG %s\n  - %s" % (mid, "\n  - ".join(errs)))
            continue
        out[mid] = {k: m[k] for k in KEEP if k in m}
        print("OK %s（%s，%d 個節點）" % (mid, m["kind"], len(m["workflow"])))
    for s in skipped:
        print("-- 先不支援 %s" % s)
    if bad:
        print("有 %d 個沒過，沒有寫檔" % bad)
        return 1
    if "--write" in argv:
        doc = {"checked": research.get("comfyui_commit_checked", ""),
               "note": "由 tools/build_gen.py 產生；工作流程照 Comfy-Org/workflow_templates 官方範本攤平，節點名稱對過 ComfyUI 原始碼",
               "models": out}
        with open(os.path.join(ROOT, "app", "gen.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("已寫入 app/gen.json（%d 個模型）" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
