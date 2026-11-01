"""跟 Ollama 講話：查模型、下載、匯入、對話。只用標準函式庫。"""
import html as _html
import hashlib
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from jobs import Job

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{0,160}$")
REGISTRY = "https://registry.ollama.ai/v2/"
UA = "AIServerKit/0.1 (+panel)"
_size_cache = {}
_size_lock = threading.Lock()


def web_get(url, timeout=15, as_json=False):
    """抓一個公開網址（給搜尋用）。回傳文字或 JSON，連不到就丟 OllamaError。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json" if as_json else "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError("連不到 %s：%s" % (urllib.parse.urlsplit(url).netloc, getattr(e, "reason", e)))
    return json.loads(data) if as_json else data.decode("utf-8", "replace")


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def _req(self, method, path, body=None, timeout=10, headers=None):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(msg).get("error", msg)
            except ValueError:
                pass
            raise OllamaError(msg or ("HTTP %d" % e.code))
        except (urllib.error.URLError, OSError) as e:
            raise OllamaError("連不到 Ollama（%s）：%s" % (self.base, getattr(e, "reason", e)))

    def get(self, path, timeout=5):
        with self._req("GET", path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def post(self, path, body, timeout=30):
        with self._req("POST", path, body, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}

    def delete(self, path, body):
        with self._req("DELETE", path, body) as r:
            r.read()

    def stream(self, path, body, timeout=600):
        """POST 之後一行一行回傳 dict（NDJSON）。回傳 (回應物件, 產生器)，回應物件 close() 就會中斷。"""
        resp = self._req("POST", path, body, timeout=timeout)

        def gen():
            with resp:
                for line in resp:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        return resp, gen()

    def summary(self):
        try:
            ver = self.get("/api/version", timeout=2).get("version")
        except OllamaError as e:
            return {"up": False, "error": str(e), "base": self.base}
        try:
            loaded = self.get("/api/ps", timeout=3).get("models", [])
        except OllamaError:
            loaded = []
        return {"up": True, "version": ver, "base": self.base, "loaded": [
            {"name": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram"),
             "expires_at": m.get("expires_at"), "details": m.get("details", {})} for m in loaded]}

    def show(self, name):
        info = self.post("/api/show", {"model": name}, timeout=15)
        mi = info.get("model_info") or {}
        ctx = next((v for k, v in mi.items() if k.endswith(".context_length")), None)
        return {"name": name, "capabilities": info.get("capabilities") or [], "details": info.get("details") or {},
                "context_length": ctx, "parameters": info.get("parameters"), "system": info.get("system")}

    def blob_exists(self, digest):
        try:
            with self._req("HEAD", "/api/blobs/" + digest, timeout=10):
                return True
        except OllamaError:
            return False


# ── 下載模型 ──
def start_pull(store, ol, name):
    job = store.add(Job("pull", "下載 " + name, {"model": name}))

    def run():
        try:
            resp, gen = ol.stream("/api/pull", {"model": name, "stream": True}, timeout=300)
        except OllamaError as e:
            job.emit({"t": "o", "d": str(e) + "\n"})
            job.finish("error", error=str(e))
            return
        job.cancel = resp.close
        last_t, last_status, ok = 0.0, None, False
        try:
            for obj in gen:
                if "error" in obj:
                    job.emit({"t": "o", "d": obj["error"] + "\n"})
                    job.finish("error", error=obj["error"])
                    return
                st = obj.get("status", "")
                ok = ok or st == "success"
                now = time.time()
                done = obj.get("total") and obj.get("completed") == obj.get("total")
                if st != last_status or now - last_t > 0.5 or done:
                    job.emit({"t": "p", "status": st, "completed": obj.get("completed"), "total": obj.get("total")})
                    last_t, last_status = now, st
        except (OSError, ValueError, AttributeError) as e:
            if not job.killed:
                job.finish("error", error="連線中斷：%s" % e)
            return
        job.finish("done" if ok else "error", **({} if ok else {"error": "沒有收到 success"}))

    threading.Thread(target=run, daemon=True).start()
    return job


# ── 匯入自己的模型（GGUF 檔、切片 GGUF、或 safetensors 資料夾） ──
SAFETENSORS_KEEP = (".safetensors", ".json", ".model", ".tiktoken", ".txt", ".jinja")


def import_files(path):
    """回傳 [(檔名, 完整路徑)]。"""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        if not path.lower().endswith(".gguf"):
            raise ValueError("單一檔案要是 .gguf；safetensors 請給整個資料夾")
        return [(os.path.basename(path), path)]
    if not os.path.isdir(path):
        raise ValueError("找不到：%s" % path)
    names = sorted(os.listdir(path))
    ggufs = [n for n in names if n.lower().endswith(".gguf")]
    if ggufs:
        return [(n, os.path.join(path, n)) for n in ggufs]
    if not any(n.lower().endswith(".safetensors") for n in names):
        raise ValueError("資料夾裡沒有 .gguf 也沒有 .safetensors")
    return [(n, os.path.join(path, n)) for n in names
            if n.lower().endswith(SAFETENSORS_KEEP) and os.path.isfile(os.path.join(path, n))]


class _ProgressReader:
    def __init__(self, f, cb):
        self.f, self.cb, self.n = f, cb, 0

    def read(self, size=-1):
        # http.client 每次只要 8 KB，大檔太慢；一次給 1 MB 它也收
        b = self.f.read(1024 * 1024)
        self.n += len(b)
        self.cb(self.n)
        return b


def start_import(store, ol, name, path, system=None, num_ctx=None, quantize=None):
    files = import_files(path)  # 路徑錯誤直接丟回去，不建工作
    total = sum(os.path.getsize(p) for _, p in files)
    job = store.add(Job("import", "匯入 " + name, {"model": name, "path": path}))

    def run():
        digests = {}
        last = [0.0]

        def prog(status, done, tot, force=False):
            now = time.time()
            if force or now - last[0] > 0.5:
                job.emit({"t": "p", "status": status, "completed": done, "total": tot})
                last[0] = now

        base = 0
        try:
            for fname, fpath in files:
                if job.state != "running":
                    return
                h = hashlib.sha256()
                size = os.path.getsize(fpath)
                with open(fpath, "rb") as f:
                    n = 0
                    while True:
                        b = f.read(4 * 1024 * 1024)
                        if not b or job.state != "running":
                            break
                        h.update(b)
                        n += len(b)
                        prog("計算檢查碼 " + fname, base + n, total)
                digest = "sha256:" + h.hexdigest()
                if ol.blob_exists(digest):
                    job.emit({"t": "o", "d": "%s 已經在 Ollama 裡，略過上傳\n" % fname})
                else:
                    with open(fpath, "rb") as f:
                        body = _ProgressReader(f, lambda k: prog("傳給 Ollama " + fname, base + k, total))
                        req = urllib.request.Request(ol.base + "/api/blobs/" + digest, data=body, method="POST",
                                                     headers={"Content-Length": str(size),
                                                              "Content-Type": "application/octet-stream"})
                        try:
                            urllib.request.urlopen(req, timeout=600).read()
                        except urllib.error.HTTPError as e:
                            raise OllamaError("上傳 %s 失敗：HTTP %d %s" % (fname, e.code, e.read()[:200]))
                digests[fname] = digest
                base += size
                prog("檔案就緒 " + fname, base, total, force=True)

            if job.killed:
                return
            body = {"model": name, "files": digests, "stream": True}
            if system:
                body["system"] = system
            if num_ctx:
                body["parameters"] = {"num_ctx": int(num_ctx)}
            if quantize:
                body["quantize"] = quantize
            resp, gen = ol.stream("/api/create", body, timeout=1800)
            job.cancel = resp.close
            ok = False
            for obj in gen:
                if "error" in obj:
                    raise OllamaError(obj["error"])
                st = obj.get("status", "")
                ok = ok or st == "success"
                job.emit({"t": "p", "status": st, "completed": obj.get("completed"), "total": obj.get("total")})
            job.finish("done" if ok else "error", **({} if ok else {"error": "沒有收到 success"}))
        except (OllamaError, OSError, ValueError, AttributeError) as e:
            if not job.killed:
                job.emit({"t": "o", "d": "%s\n" % e})
                job.finish("error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job


# ── 查模型下載大小（Ollama 官方模型庫） ──
def registry_size(name):
    """回傳位元組數；不是官方模型庫的名稱（例如 hf.co/…）或查不到回傳 None。"""
    if name.startswith(("hf.co/", "huggingface.co/")) or "." in name.split("/")[0] and "/" in name:
        return None
    repo, _, tag = name.partition(":")
    tag = tag or "latest"
    if "/" not in repo:
        repo = "library/" + repo
    key = repo + ":" + tag
    with _size_lock:
        if key in _size_cache:
            return _size_cache[key]
    url = REGISTRY + urllib.parse.quote(repo) + "/manifests/" + urllib.parse.quote(tag)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            m = json.loads(r.read())
        size = sum(int(l.get("size", 0)) for l in m.get("layers", []))
    except (urllib.error.URLError, OSError, ValueError):
        size = None
    with _size_lock:
        _size_cache[key] = size
    return size


# ── 搜尋全網開源模型 ──
CAP_WORDS = ("tools", "vision", "thinking", "embedding", "audio", "cloud", "insert", "decision")
_search_cache = {}


def _cached_search(key, fn, ttl=300):
    now = time.time()
    with _size_lock:
        hit = _search_cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _size_lock:
        _search_cache[key] = (now, val)
    return val


def search_library(q, limit=30):
    """搜尋 Ollama 官方模型庫（網站搜尋）。回傳 [{name, desc, caps, pulls, source}]。"""
    def go():
        html = web_get("https://ollama.com/search?q=" + urllib.parse.quote(q))
        out = []
        for m in re.finditer(r'href="/library/([^"]+)"\s+class="group grid', html):
            name = _html.unescape(m.group(1))
            block = html[m.end():m.end() + 2200]
            desc = re.search(r'<p class="mt-1 line-clamp-2[^"]*"[^>]*>(.*?)</p>', block, re.S)
            dl = re.search(r'title="([\d,]+) downloads"', block)
            caps = re.findall(r'>(%s)</span>' % "|".join(CAP_WORDS), block[:block.find("<p") if "<p" in block else 600])
            out.append({"name": name, "desc": _html.unescape(re.sub(r"<[^>]+>", "", desc.group(1)).strip()) if desc else "",
                        "caps": list(dict.fromkeys(caps)), "pulls": int(dl.group(1).replace(",", "")) if dl else None,
                        "source": "ollama"})
            if len(out) >= limit:
                break
        return out
    return _cached_search("lib:" + q.lower(), go)


HF_QUANT = re.compile(r"(?:^|[-_.])((?:IQ|Q)\d+(?:_[0-9A-Za-z]+)*|BF16|F16|F32|FP16|FP8)\.gguf$", re.I)


def search_hf(q, limit=25):
    """搜尋 Hugging Face 上的 GGUF 模型。回傳 [{name, repo, pulls, likes, gated, source}]。"""
    def go():
        url = ("https://huggingface.co/api/models?search=" + urllib.parse.quote(q) +
               "&filter=gguf&sort=downloads&direction=-1&limit=%d" % limit)
        data = web_get(url, as_json=True)
        out = []
        for m in data if isinstance(data, list) else []:
            rid = m.get("id") or m.get("modelId")
            if not rid:
                continue
            out.append({"name": rid, "repo": rid, "pulls": m.get("downloads"), "likes": m.get("likes"),
                        "gated": bool(m.get("gated")), "source": "hf"})
        return out
    return _cached_search("hf:" + q.lower(), go)


def hf_quant_tags(repo):
    """列出 HF 某個 GGUF 倉庫裡有哪些量化版本（給 Ollama 用 hf.co/<repo>:<tag> 下載）。"""
    data = web_get("https://huggingface.co/api/models/" + urllib.parse.quote(repo), as_json=True)
    tags, seen = [], set()
    for s in data.get("siblings", []) if isinstance(data, dict) else []:
        fn = s.get("rfilename", "")
        if not fn.lower().endswith(".gguf"):
            continue
        base = re.sub(r"-\d{5}-of-\d{5}", "", fn.rsplit("/", 1)[-1])
        m = HF_QUANT.search(base)
        tag = (m.group(1).upper() if m else base[:-5].rsplit("-", 1)[-1].upper())
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return {"repo": repo, "gated": bool(data.get("gated")) if isinstance(data, dict) else False, "tags": tags}


# ── 讀本機 GGUF 檔，看這是什麼模型 ──
_GGUF_STR = 8


def gguf_info(path):
    """從 .gguf 檔頭讀出架構、名稱等中介資料（不解析整個檔，只掃前段）。失敗回傳 {}。"""
    try:
        with open(path, "rb") as f:
            head = f.read(2 * 1024 * 1024)
    except OSError:
        return {}
    if head[:4] != b"GGUF":
        return {}
    info = {"gguf_version": struct.unpack_from("<I", head, 4)[0] if len(head) >= 8 else None}
    wanted = {b"general.architecture": "architecture", b"general.name": "name", b"general.basename": "basename",
              b"general.size_label": "size_label", b"general.finetune": "finetune",
              b"general.license": "license", b"general.quantization_version": "quant_version"}
    for key, label in wanted.items():
        i = head.find(key)
        if i < 0:
            continue
        p = i + len(key)
        if p + 12 > len(head):
            continue
        vtype = struct.unpack_from("<I", head, p)[0]
        p += 4
        if vtype != _GGUF_STR:
            continue
        vlen = struct.unpack_from("<Q", head, p)[0]
        p += 8
        if vlen > 8192 or p + vlen > len(head):
            continue
        try:
            info[label] = head[p:p + vlen].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return info


def find_online(query, limit=8):
    """拿一個名字去 Ollama 庫和 HF 找。回傳 {found, ollama, hf}；兩邊都沒有就是 found False。"""
    q = (query or "").strip()
    ol, hf = [], []
    if q:
        try:
            ol = search_library(q, limit)
        except OllamaError:
            ol = []
        try:
            hf = search_hf(q, limit)
        except OllamaError:
            hf = []
    return {"query": q, "found": bool(ol or hf), "ollama": ol, "hf": hf}


def _search_query(name):
    """把一個模型名／檔名變成適合拿去搜尋的字：去掉路徑、.gguf、結尾的量化標記。"""
    name = (name or "").strip().replace("\\", "/")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    name = re.sub(r"\.gguf$", "", name, flags=re.I)
    name = re.sub(r"[-_.](?:IQ|Q)\d+(?:_[0-9A-Za-z]+)*$", "", name, flags=re.I)
    return name.strip(" -_.")


def identify(path):
    """看一個要匯入的模型是什麼（讀 GGUF 檔頭或 safetensors 的 config.json），再拿去網路上找。"""
    files = import_files(path)  # 路徑錯誤會丟 ValueError
    first = files[0][1]
    info, q = {}, ""
    if first.lower().endswith(".gguf"):
        info = gguf_info(first)
        q = info.get("name") or info.get("basename") or os.path.splitext(os.path.basename(first))[0]
    else:
        cfgp = os.path.join(path, "config.json") if os.path.isdir(path) else ""
        if cfgp and os.path.exists(cfgp):
            try:
                with open(cfgp, encoding="utf-8") as f:
                    cfg = json.load(f)
                info = {"architecture": (cfg.get("architectures") or [None])[0], "model_type": cfg.get("model_type"),
                        "name": cfg.get("_name_or_path") or os.path.basename(path.rstrip("/\\"))}
            except (OSError, ValueError):
                pass
        q = info.get("name") or os.path.basename(path.rstrip("/\\"))
    online = find_online(_search_query(q))
    return {"files": [n for n, _ in files], "info": info, "online": online, "found": online["found"]}


# ── 分享模型給製作人（只有製作人在 config 設了 share.url、而且使用者自己按才會上傳） ──
def start_share(store, share, path):
    files = import_files(path)
    total = sum(os.path.getsize(p) for _, p in files)
    job = store.add(Job("share", "分享 " + os.path.basename(path.rstrip("/\\")) + " 給" + share.get("label", "製作人"),
                        {"path": path}))

    def run():
        last = [0.0]

        def prog(status, done, force=False):
            now = time.time()
            if force or now - last[0] > 0.5:
                job.emit({"t": "p", "status": status, "completed": done, "total": total})
                last[0] = now

        base = 0
        try:
            url = share["url"]
            for fname, fpath in files:
                if job.killed:
                    return
                size = os.path.getsize(fpath)
                sep = "&" if "?" in url else "?"
                u = url + sep + urllib.parse.urlencode({"name": fname, "model": os.path.basename(path.rstrip("/\\"))})
                with open(fpath, "rb") as f:
                    body = _ProgressReader(f, lambda k: prog("上傳 " + fname, base + k))
                    req = urllib.request.Request(u, data=body, method="POST", headers={
                        "Content-Length": str(size), "Content-Type": "application/octet-stream", "User-Agent": UA})
                    if share.get("token"):
                        req.add_header("Authorization", "Bearer " + share["token"])
                    with urllib.request.urlopen(req, timeout=3600) as r:
                        r.read()
                base += size
                prog("已送出 " + fname, base, force=True)
            job.finish("done", url=urllib.parse.urlsplit(url).netloc)
        except (OSError, urllib.error.URLError, ValueError) as e:
            if not job.killed:
                job.emit({"t": "o", "d": "%s\n" % e})
                job.finish("error", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job
