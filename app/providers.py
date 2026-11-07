"""外部 AI（使用者自己訂閱的雲端服務）：Claude、OpenAI、Gemini、Grok。只用標準函式庫。

隱私規則：送出去的只有「對話文字」＋一段固定的系統提示。主機狀態、檔案、終端機、其他使用者的資料一律不給。
金鑰只存在主機（users.json，0600），不會回傳給瀏覽器。
"""
import json
import re
import urllib.error
import urllib.request

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")   # 模型名會進網址，只准這些字

PROVIDERS = {
    "anthropic": {"name": "Claude", "handle": "claude", "example": "claude-sonnet-5-5", "url": "https://api.anthropic.com/v1/messages"},
    "openai": {"name": "OpenAI", "handle": "gpt", "example": "gpt-5", "url": "https://api.openai.com/v1/chat/completions"},
    "gemini": {"name": "Gemini", "handle": "gemini", "example": "gemini-2.5-flash", "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"},
    "xai": {"name": "Grok", "handle": "grok", "example": "grok-4", "url": "https://api.x.ai/v1/chat/completions"},
}
TIMEOUT = 300
MAX_TOKENS = 4096


class ProviderError(Exception):
    pass


def _sse(resp):
    """逐行讀 SSE，回傳每個 data 的 JSON。"""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except ValueError:
            continue


def _open(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict({"Content-Type": "application/json", "User-Agent": "KikiCloude/0.1"}, **headers))
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail) if detail.startswith("{") else detail
        except (ValueError, AttributeError):
            pass
        raise ProviderError("%s 回應 %d：%s" % (url.split("/")[2], e.code, detail))
    except (urllib.error.URLError, OSError) as e:
        raise ProviderError("連不到 %s：%s" % (url.split("/")[2], e))


def _clean(messages):
    """只留 user／assistant 的文字；system 由這裡統一給，圖片不送。"""
    out = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        text = str(m.get("content") or "")
        if text:
            out.append({"role": m["role"], "content": text})
    return out


def stream(provider, key, model, messages, system):
    """產生文字片段；最後一個產出是 ("usage", {"prompt": n, "completion": n})。"""
    p = PROVIDERS.get(provider)
    if not p:
        raise ProviderError("不支援的服務：%s" % provider)
    if not key:
        raise ProviderError("還沒設定 %s 的金鑰" % p["name"])
    model = model or p["example"]
    if not MODEL_RE.match(model):
        raise ProviderError("模型名稱只能用英數、. _ : / -")
    msgs = _clean(messages)
    if not msgs:
        raise ProviderError("沒有內容")
    fn = {"anthropic": _anthropic, "openai": _openai_like, "gemini": _gemini, "xai": _openai_like}[provider]
    return fn(p, key, model, msgs, system)


def _anthropic(p, key, model, msgs, system):
    resp = _open(p["url"], {"x-api-key": key, "anthropic-version": "2023-06-01"},
                 {"model": model, "max_tokens": MAX_TOKENS, "system": system, "stream": True, "messages": msgs})
    usage = {"prompt": 0, "completion": 0}
    with resp:
        for ev in _sse(resp):
            t = ev.get("type")
            if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                yield ev["delta"].get("text", "")
            elif t == "message_start":
                usage["prompt"] = ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
            elif t == "message_delta":
                usage["completion"] = ev.get("usage", {}).get("output_tokens", 0)
            elif t == "error":
                raise ProviderError(ev.get("error", {}).get("message", "錯誤"))
    yield ("usage", usage)


def _openai_like(p, key, model, msgs, system):
    body = {"model": model, "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system}] + msgs}
    resp = _open(p["url"], {"Authorization": "Bearer " + key}, body)
    usage = {"prompt": 0, "completion": 0}
    with resp:
        for ev in _sse(resp):
            if ev.get("error"):
                raise ProviderError(str(ev["error"].get("message", ev["error"])))
            for c in ev.get("choices") or []:
                text = (c.get("delta") or {}).get("content")
                if text:
                    yield text
            if ev.get("usage"):
                usage = {"prompt": ev["usage"].get("prompt_tokens", 0), "completion": ev["usage"].get("completion_tokens", 0)}
    yield ("usage", usage)


def _gemini(p, key, model, msgs, system):
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in msgs]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
    resp = _open(p["url"].format(model=model), {"x-goog-api-key": key}, body)
    usage = {"prompt": 0, "completion": 0}
    with resp:
        for ev in _sse(resp):
            if ev.get("error"):
                raise ProviderError(str(ev["error"].get("message", ev["error"])))
            for cand in ev.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("text"):
                        yield part["text"]
            um = ev.get("usageMetadata")
            if um:
                usage = {"prompt": um.get("promptTokenCount", 0), "completion": um.get("candidatesTokenCount", 0)}
    yield ("usage", usage)
