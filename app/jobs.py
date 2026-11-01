"""背景工作：終端機指令、下載模型、匯入模型都是一個 Job。

頁面關掉、手機鎖螢幕都不會中斷；重新連上時用 offset 從斷掉的地方接著讀。
"""
import codecs
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
MAX_BYTES = 2 * 1024 * 1024   # 每個工作最多留 2 MB 輸出，超過就丟最舊的
MAX_JOBS = 40


class Job:
    def __init__(self, kind, title, meta=None):
        self.id = secrets.token_hex(6)
        self.kind = kind            # term | pull | import
        self.title = title
        self.meta = meta or {}
        self.events = []            # [{"t": "o", "d": 文字} | {"t": "p", ...進度} | {"t": "end", ...}]
        self.base = 0               # events[0] 的序號（丟掉舊的之後會變大）
        self.bytes = 0
        self.state = "running"      # running | done | error | killed
        self.result = {}
        self.started = time.time()
        self.ended = None
        self.progress = None        # 最新一筆進度（列表頁用）
        self.cond = threading.Condition()
        self.proc = None
        self.cancel = None          # 取消時要呼叫的函式
        self.killed = False         # 使用者按了停止（工作執行緒看到就不要記成「失敗」）

    def emit(self, ev):
        with self.cond:
            self.events.append(ev)
            if ev.get("t") == "p":
                self.progress = ev
            self.bytes += len(ev.get("d", "")) + 48
            while self.bytes > MAX_BYTES and len(self.events) > 1:
                old = self.events.pop(0)
                self.base += 1
                self.bytes -= len(old.get("d", "")) + 48
            self.cond.notify_all()

    def finish(self, state, **result):
        with self.cond:
            if self.state != "running":
                return
            self.state = state
            self.result = result
            self.ended = time.time()
            self.cond.notify_all()

    def read(self, offset, timeout):
        """回傳 (事件, 下一個 offset, 是否結束)。沒有新東西就等到 timeout。"""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                end = self.base + len(self.events)
                if offset < self.base:
                    offset = self.base
                if offset < end or self.state != "running":
                    evs = self.events[offset - self.base:]
                    return evs, end, self.state != "running" and offset + len(evs) >= end
                left = deadline - time.time()
                if left <= 0:
                    return [], offset, False
                self.cond.wait(left)

    def summary(self):
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "started": int(self.started), "ended": int(self.ended) if self.ended else None,
                "progress": self.progress, "result": self.result, "meta": self.meta}


class JobStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def add(self, job):
        with self._lock:
            self._jobs[job.id] = job
            done = [j for j in self._jobs.values() if j.state != "running"]
            done.sort(key=lambda j: j.started)
            while len(self._jobs) > MAX_JOBS and done:
                self._jobs.pop(done.pop(0).id, None)
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.started, reverse=True)
        return [j.summary() for j in jobs]

    def clear_finished(self):
        with self._lock:
            for k in [k for k, j in self._jobs.items() if j.state != "running"]:
                del self._jobs[k]

    def running(self):
        with self._lock:
            return [j for j in self._jobs.values() if j.state == "running"]


# ── 終端機 ──
def available_shells():
    if IS_WIN:
        out = [{"id": "cmd", "name": "CMD"}, {"id": "powershell", "name": "PowerShell"}]
        if shutil.which("pwsh"):
            out.append({"id": "pwsh", "name": "PowerShell 7"})
        return out
    if shutil.which("bash"):
        return [{"id": "bash", "name": "bash"}]
    return [{"id": "sh", "name": "sh"}]


def _ps_script(cmd, marker):
    # 用 Out-String 把物件排版成寬一點的文字，不然表格會被截在 80 欄
    return ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8\n"
            "$ProgressPreference='SilentlyContinue'; $global:LASTEXITCODE=0; $__e0=$Error.Count\n"
            "& {\n" + cmd + "\n} | Out-String -Stream -Width 220\n"
            "$__ec = if ($global:LASTEXITCODE) { $global:LASTEXITCODE } elseif ($Error.Count -gt $__e0) { 1 } else { 0 }\n"
            "[Console]::Out.Write(\"" + marker + "$__ec|$((Get-Location).ProviderPath)`n\")\n")


def _build_command(cmd, shell, marker):
    """回傳 Popen 的第一個參數。每個 shell 跑完都印一行「記號 結束碼|目前資料夾」，cd 才記得住。"""
    if shell == "cmd":
        # /v:on 讓 !CD! !errorlevel! 在執行時才展開；chcp 65001 讓 dir 等內建指令輸出 UTF-8
        return 'cmd.exe /d /s /v:on /c "chcp 65001>nul & ' + cmd + ' & echo ' + marker + '!errorlevel!^|!CD!"'
    if shell in ("powershell", "pwsh"):
        import base64
        exe = "powershell.exe" if shell == "powershell" else (shutil.which("pwsh") or "pwsh")
        enc = base64.b64encode(_ps_script(cmd, marker).encode("utf-16-le")).decode()
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
    sh = shutil.which("bash") if shell == "bash" else "/bin/sh"
    return [sh or "/bin/sh", "-c", cmd + '\n__ec=$?\nprintf "%s%s|%s\\n" "' + marker + '" "$__ec" "$PWD"\n']


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            for _ in range(20):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class _Decoder:
    """先當 UTF-8 解；不是 UTF-8 的片段（不理 chcp 的舊程式）改用系統的 OEM 編碼（例如 cp950）。"""

    def __init__(self):
        self.u = codecs.getincrementaldecoder("utf-8")("strict")
        self.alt = None
        if IS_WIN:
            import ctypes
            self.alt = "cp%d" % ctypes.windll.kernel32.GetOEMCP()

    def decode(self, b, final=False):
        try:
            return self.u.decode(b, final)
        except UnicodeDecodeError:
            self.u.reset()
            try:
                return b.decode(self.alt or "utf-8", "replace")
            except LookupError:
                return b.decode("utf-8", "replace")


def _popen_kw():
    if not IS_WIN:
        return {"start_new_session": True}
    # 給子程式一個隱藏的主控台：沒有主控台的話 chcp 65001 沒用，CMD 的中文會變亂碼
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": si}


def start_terminal(store, cmd, cwd, shell):
    if not cwd or not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    marker = "__AISK_" + secrets.token_hex(4) + "__"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if not IS_WIN:
        env.setdefault("TERM", "dumb")
    job = store.add(Job("term", cmd, {"shell": shell, "cwd": cwd}))
    kw = _popen_kw()
    try:
        proc = subprocess.Popen(_build_command(cmd, shell, marker), cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except OSError as e:
        job.emit({"t": "o", "d": "無法執行：%s\n" % e})
        job.finish("error", exit=-1, cwd=cwd)
        return job
    job.proc = proc
    job.cancel = lambda: _kill_tree(proc)
    threading.Thread(target=_pump, args=(job, proc, marker, cwd), daemon=True).start()
    return job


def _pump(job, proc, marker, cwd):
    dec = _Decoder()
    pending = ""
    found = {}

    def flush(final=False):
        nonlocal pending
        while True:
            i = pending.find(marker)
            if i < 0:
                break
            nl = pending.find("\n", i)
            if nl < 0 and not final:
                if i:
                    job.emit({"t": "o", "d": pending[:i]})
                    pending = pending[i:]
                return
            line = pending[i + len(marker):nl if nl >= 0 else None].strip()
            if i:
                job.emit({"t": "o", "d": pending[:i]})
            pending = pending[nl + 1:] if nl >= 0 else ""
            code, _, path = line.partition("|")
            found["exit"] = int(code) if code.strip().lstrip("-").isdigit() else None
            found["cwd"] = path.strip() or None
        # 留住結尾可能是記號開頭的幾個字，其他都送出去
        keep = 0
        if not final:
            for k in range(min(len(marker) - 1, len(pending)), 0, -1):
                if pending.endswith(marker[:k]):
                    keep = k
                    break
        out = pending[:len(pending) - keep]
        pending = pending[len(pending) - keep:]
        if out:
            job.emit({"t": "o", "d": out})

    try:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            pending += dec.decode(chunk).replace("\r\n", "\n")
            flush()
    except (OSError, ValueError):
        pass
    pending += dec.decode(b"", final=True)
    flush(final=True)
    rc = proc.wait()
    # shell 本身正常結束時，以記號回報的結束碼（使用者指令的）為準
    exit_code = found["exit"] if rc == 0 and found.get("exit") is not None else rc
    if job.killed:
        job.finish("killed", exit=None, cwd=cwd)
    else:
        job.finish("done" if exit_code == 0 else "error", exit=exit_code, cwd=found.get("cwd") or cwd)


def send_input(job, text):
    if not job.proc or job.proc.poll() is not None or job.proc.stdin is None:
        return False
    try:
        job.proc.stdin.write((text + "\n").encode("utf-8"))
        job.proc.stdin.flush()
        return True
    except OSError:
        return False


def kill(job):
    if job.state != "running":
        return False
    job.killed = True
    if job.cancel:
        try:
            job.cancel()
        except Exception:
            pass
    job.emit({"t": "o", "d": "\n^C 已停止\n"})
    job.finish("killed", exit=None, cwd=job.meta.get("cwd"))
    return True
