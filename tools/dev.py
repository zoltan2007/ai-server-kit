"""本機預覽：假 Ollama（11999）＋面板（8781），不用裝 Ollama、不用下載模型。

    py -3 ai-server-kit/tools/dev.py
預覽專用的登入碼是下面的 DEV_TOKEN（只綁 127.0.0.1）。預設埠 8781，環境變數 PORT 可以改。
"""
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fake_ollama  # noqa: E402
import providers  # noqa: E402
import server  # noqa: E402


def fake_cloud(url, headers, body):
    """預覽用的假雲端 AI：不連網，回一段固定的話（會 @kiki 一次，看兩個 AI 接話）。"""
    import io
    import json as _json
    import time as _time
    _time.sleep(0.4)
    text = "（預覽用的假雲端 AI）我大致同意，但第二點要補充：先量實際溫度再決定功耗上限。@kiki 請把步驟整理成三點。"
    lines = ["data: " + _json.dumps({"choices": [{"delta": {"content": ch}}]}) for ch in text]
    lines.append('data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":48}}')
    lines.append("data: [DONE]")
    return io.BytesIO(chr(10).join(lines).encode("utf-8"))


providers._open = fake_cloud

DEV_TOKEN = "dev-preview-token-2026"

if __name__ == "__main__":
    fake_ollama.S.delay = 0.06
    # 預覽工具會用環境變數 PORT 指定埠（8781 被別的預覽占住時）；假 Ollama 一律用系統給的空埠，兩個預覽才不會撞在一起
    port = int(os.environ.get("PORT") or 8781)
    ol = fake_ollama.serve(0)
    ol_url = "http://127.0.0.1:%d" % ol.server_address[1]
    data = os.path.join(tempfile.gettempdir(), "aisk-dev")
    os.makedirs(data, exist_ok=True)
    # 預覽時設一個假的「製作人收模型」端點，好看到使用協議的分享同意選項
    cfg = {"host": "127.0.0.1", "port": port, "token": DEV_TOKEN, "terminal": True,
           "ollama": ol_url, "data_dir": data,
           "share": {"url": ol_url + "/sink", "label": "製作人（預覽）"}}
    srv = server.make_server(cfg)
    print("預覽：http://127.0.0.1:%d/" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
