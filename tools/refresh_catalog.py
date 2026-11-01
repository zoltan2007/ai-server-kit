"""更新 app/catalog.json 每個版本的下載大小（向 Ollama 官方模型庫查）。查不到的版本會列出來並移除。

    python -I tools/refresh_catalog.py          只顯示
    python -I tools/refresh_catalog.py --write  寫回 catalog.json
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import ollama_api  # noqa: E402

PATH = os.path.join(ROOT, "app", "catalog.json")


def main():
    write = "--write" in sys.argv
    with open(PATH, encoding="utf-8") as f:
        cat = json.load(f)
    missing = []
    for g in cat["groups"]:
        for m in g["models"]:
            keep = []
            for t in m["tags"]:
                name = "%s:%s" % (m["name"], t["tag"])
                size = ollama_api.registry_size(name)
                print("%-28s %s" % (name, "查不到" if size is None else "%.2f GB" % (size / 1e9)))
                if size is None:
                    missing.append(name)
                    continue
                t["size"] = size
                keep.append(t)
            m["tags"] = keep
        g["models"] = [m for m in g["models"] if m["tags"]]
    cat["updated"] = time.strftime("%Y-%m-%d")
    if missing:
        print("\n查不到（會移除）：" + ", ".join(missing))
    if write:
        with open(PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cat, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("已寫回 " + PATH)


if __name__ == "__main__":
    main()
