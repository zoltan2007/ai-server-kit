"""模型庫的 API。看清單人人可以（GET /api/hub 在 USER_OK 裡），下載與刪除只有管理員（不在 USER_OK）。"""
import hub
import ollama_api
from ollama_api import NAME_RE, OllamaError


def _catalog(app):
    if getattr(app, "hub_catalog", None) is None:
        app.hub_catalog = hub.load()
    return app.hub_catalog


def _installed(app):
    try:
        return [m.get("name") for m in app.ollama.get("/api/tags").get("models", [])]
    except OllamaError:
        return []


def get(h, q):
    prof = hub.PROFILES.get(q.get("profile")) or hub.host_profile()
    status = hub.comfy_status(h.app.cfg)
    out = hub.build(_catalog(h.app), prof, _installed(h.app), status["dir"] or None)
    out["comfy"] = status
    out["profiles"] = [{"id": "host", "name": "這台主機"}] + [{"id": k, "name": p["name"]} for k, p in hub.PROFILES.items()]
    out["profile_id"] = q.get("profile") if q.get("profile") in hub.PROFILES else "host"
    out["admin"] = h.user.get("role") == "admin"
    out["gen_ids"] = sorted(h.app.gen.recipes)        # 哪些模型可以在 Kiki 裡直接生成
    if not out["admin"]:
        out["comfy"] = {k: status[k] for k in ("installed", "running")}      # 一般帳號不需要知道主機上的資料夾
    h.json(out)


def download(h, b):
    try:
        m, v = hub.find(_catalog(h.app), b.get("id"), str(b.get("variant", "")))
    except ValueError as e:
        return h.err(str(e))
    src = v["source"]
    if src["kind"] == "node":
        return h.err("這個模型要先裝 ComfyUI 擴充節點（%s），照節點的說明放檔案；面板不替它下載" % src.get("needs", ""), 409)
    if src.get("gated"):
        return h.err("這個模型要先登入 Hugging Face、同意授權才能下載，面板還不支援", 409)
    if src["kind"] == "ollama":
        name = src["name"]
        if not NAME_RE.match(name):
            return h.err("模型名稱不對")
        for j in h.app.jobs.running():
            if j.kind == "pull" and j.meta.get("model") == name:
                return h.json({"id": j.id, "existing": True})
        return h.json({"id": ollama_api.start_pull(h.app.jobs, h.app.ollama, name).id})
    status = hub.comfy_status(h.app.cfg)
    if not status["installed"]:
        return h.err("這台還沒裝 ComfyUI。重新跑安裝檔並選「裝 ComfyUI」，或在 config.json 的 comfyui.dir 指到已經裝好的資料夾", 409)
    for j in h.app.jobs.running():
        if j.kind == "hub" and j.meta.get("model") == m["id"] and j.meta.get("variant") == v["label"]:
            return h.json({"id": j.id, "existing": True})
    try:
        job = hub.start_comfy_download(h.app.jobs, status["dir"], m, v)
    except ValueError as e:
        return h.err(str(e))
    h.json({"id": job.id})


def remove(h, b):
    """刪 ComfyUI 的模型檔。別的已下載模型也在用的檔案（共用的文字編碼器、VAE）會留著。Ollama 的模型照舊在面板刪。"""
    cat = _catalog(h.app)
    try:
        m, v = hub.find(cat, b.get("id"), str(b.get("variant", "")))
    except ValueError as e:
        return h.err(str(e))
    if v["source"]["kind"] != "comfy":
        return h.err("Ollama 的模型請到面板的「模型」刪除")
    status = hub.comfy_status(h.app.cfg)
    if not status["installed"]:
        return h.err("這台還沒裝 ComfyUI", 409)
    d = status["dir"]
    keep = set()
    for om in cat["models"]:
        for ov in om["variants"]:
            if ov is v or ov["source"]["kind"] != "comfy" or not hub._has(ov, (), d):
                continue
            keep.update(hub.target(d, f) for f in ov["source"]["files"])
    mine = dict(v, source=dict(v["source"], files=[f for f in v["source"]["files"] if hub.target(d, f) not in keep]))
    h.json({"ok": True, "removed": hub.remove_comfy(d, mine), "kept": len(v["source"]["files"]) - len(mine["source"]["files"])})


GET = {"/api/hub": get}
POST = {"/api/hub/download": download, "/api/hub/remove": remove}
