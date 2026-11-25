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

import fake_comfy  # noqa: E402
import fake_ollama  # noqa: E402
import hub  # noqa: E402
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


class _FakeResp:
    def __init__(self, data, status=200):
        import io
        self.buf, self.status = io.BytesIO(data), status

    def read(self, n=-1):
        import time as _time
        _time.sleep(0.15)          # 慢一點，才看得到進度條
        return self.buf.read(min(n, 256 * 1024) if n and n > 0 else -1)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


_real_open = hub._urlopen


def fake_hf(req, timeout=None):
    """預覽用的假 Hugging Face：不連網，下載回一段假資料（約 4 MB）。本機的假 ComfyUI 照常連。"""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if url.startswith("http://127.0.0.1:"):
        return _real_open(req, timeout=timeout)
    if url.startswith("https://huggingface.co/"):
        return _FakeResp(b"\0" * (4 * 1024 * 1024))
    raise OSError("預覽不連網：%s" % url)


hub._urlopen = fake_hf
PRE_INSTALLED = {"z-image-turbo": "bf16", "flux2-klein-4b": "bf16", "wan2.2-ti2v-5b": "fp16"}   # 預覽時當作已經下載好


def fake_video(data):
    """有 ffmpeg 就做一支 3 秒的測試影片，假 ComfyUI 的影片成品才播得出來。"""
    import shutil
    import subprocess
    out = os.path.join(data, "preview-video.mp4")
    if not os.path.exists(out) and shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x352:rate=24:duration=3",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", out], check=False, timeout=60)
    return open(out, "rb").read() if os.path.exists(out) else None

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
           "share": {"url": ol_url + "/sink", "label": "製作人（預覽）"},
           "comfyui": {"dir": os.path.join(data, "comfyui"), "url": "http://127.0.0.1:8188"}}
    # 假的 ComfyUI 資料夾：有 main.py 就算「已安裝」
    os.makedirs(cfg["comfyui"]["dir"], exist_ok=True)
    with open(os.path.join(cfg["comfyui"]["dir"], "main.py"), "w", encoding="utf-8") as f:
        f.write("# 預覽用的假 ComfyUI\n")
    fc = fake_comfy.serve(0)
    fake_comfy.S.steps, fake_comfy.S.delay = 12, 0.35
    fake_comfy.S.video = fake_video(data)
    cfg["comfyui"]["url"] = "http://127.0.0.1:%d" % fc.server_address[1]
    for m in hub.load()["models"]:
        for v in m["variants"]:
            if PRE_INSTALLED.get(m["id"]) == v["label"]:
                for f in v["source"]["files"]:
                    p = hub.target(cfg["comfyui"]["dir"], f)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    open(p, "a").close()
    srv = server.make_server(cfg)
    print("預覽：http://127.0.0.1:%d/" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
