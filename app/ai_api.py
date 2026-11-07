"""「我的 AI」：使用者把自己訂閱的 Claude／OpenAI／Gemini／Grok 接進來。金鑰只存主機，不回傳。函式都收 (handler, body_or_query)。"""
import providers as P

SYSTEM_CHAT = "你是使用者透過 Kiki Cloude 連接的 AI 助理。用繁體中文（台灣用語）回答，簡潔清楚。"


def ext_model(name):
    """'ext:anthropic:claude-sonnet-5-5' → ('anthropic', 'claude-sonnet-5-5')，不是外部模型回 None。"""
    if not str(name).startswith("ext:"):
        return None
    parts = name.split(":", 2)
    if len(parts) < 3 or parts[1] not in P.PROVIDERS:
        return None
    return parts[1], parts[2]


def _mine(h):
    if h.user.get("master"):
        raise ValueError("主登入碼沒有個人設定，請用管理員帳號登入")
    return h.app.users.users[h.user["name"]].setdefault("ai", {})


def public(ai):
    return [{"provider": k, "name": P.PROVIDERS[k]["name"], "handle": P.PROVIDERS[k]["handle"], "model": v.get("model", ""),
             "example": P.PROVIDERS[k]["example"], "has_key": bool(v.get("key"))} for k, v in ai.items() if k in P.PROVIDERS]


def list_ai(h, q):
    try:
        mine = _mine(h)
    except ValueError:
        mine = {}
    h.json({"ai": public(mine), "providers": [{"provider": k, "name": v["name"], "handle": v["handle"], "example": v["example"]}
                                              for k, v in P.PROVIDERS.items()]})


def set_ai(h, b):
    prov = str(b.get("provider", ""))
    if prov not in P.PROVIDERS:
        return h.err("不支援的服務")
    try:
        mine = _mine(h)
    except ValueError as e:
        return h.err(str(e))
    cur = mine.setdefault(prov, {})
    key = str(b.get("key", "")).strip()
    if key:
        cur["key"] = key
    if not cur.get("key"):
        return h.err("請填金鑰")
    cur["model"] = str(b.get("model", "")).strip()[:100] or P.PROVIDERS[prov]["example"]
    if not P.MODEL_RE.match(cur["model"]):
        return h.err("模型名稱只能用英數、. _ : / -")
    if len(cur["key"]) > 512 or any(c.isspace() for c in cur["key"]):
        cur.pop("key", None)
        return h.err("金鑰格式不對")
    h.app.users.flush()
    h.json({"ai": public(mine)})


def remove_ai(h, b):
    try:
        mine = _mine(h)
    except ValueError as e:
        return h.err(str(e))
    mine.pop(str(b.get("provider", "")), None)
    h.app.users.flush()
    h.json({"ai": public(mine)})


def test_ai(h, b):
    """打一句看金鑰能不能用。"""
    prov = str(b.get("provider", ""))
    try:
        mine = _mine(h)
        cfg = mine.get(prov) or {}
        text = ""
        for chunk in P.stream(prov, cfg.get("key"), cfg.get("model"), [{"role": "user", "content": "請只回「OK」"}], SYSTEM_CHAT):
            if isinstance(chunk, str):
                text += chunk
        h.json({"ok": True, "reply": text.strip()[:80]})
    except (ValueError, P.ProviderError) as e:
        h.err(str(e))


def key_for(app, user, provider):
    """某個帳號的某家金鑰與模型；沒有就 (None, None)。"""
    u = app.users.users.get(user) or {}
    cfg = (u.get("ai") or {}).get(provider) or {}
    return cfg.get("key"), cfg.get("model")


def my_usage(h, q):
    """自己最近七天每小時的 token 用量。"""
    try:
        days = max(1, min(31, int(q.get("days", 7) or 7)))
    except ValueError:
        days = 7
    h.json({"days": h.app.usage.hourly(h.user["name"], days)})


GET = {"/api/ai": list_ai, "/api/usage/me": my_usage}
POST = {"/api/ai/set": set_ai, "/api/ai/remove": remove_ai, "/api/ai/test": test_ai}
