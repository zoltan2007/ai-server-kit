"""機器狀態：CPU、記憶體、磁碟、顯示卡、開機時間。只用標準函式庫（Windows 用 ctypes，Linux 讀 /proc）。"""
import ctypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW：背景跑 nvidia-smi 不要閃黑窗

_lock = threading.Lock()
_cpu_prev = None
_gpu_cache = {"t": 0.0, "data": None}
_static = None


# ── CPU ──
def _cpu_times():
    """回傳 (閒置, 總計)，單位不拘，只拿來算差值。"""
    if IS_WIN:
        class FT(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
        idle, kern, user = FT(), FT(), FT()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        v = lambda f: (f.hi << 32) | f.lo
        # kernel 時間已經包含 idle
        return v(idle), v(kern) + v(user)
    try:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return idle, sum(parts[:8])
    except OSError:
        return None


def cpu_percent():
    global _cpu_prev
    now = _cpu_times()
    if now is None:
        return None
    with _lock:
        prev, _cpu_prev = _cpu_prev, now
    if prev is None:
        time.sleep(0.2)
        return cpu_percent()
    d_idle, d_total = now[0] - prev[0], now[1] - prev[1]
    if d_total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 1)


def _cpu_name():
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            return platform.processor()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


# ── 記憶體 ──
def memory():
    """回傳 {"total": bytes, "used": bytes}。"""
    if IS_WIN:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return {"total": m.ullTotalPhys, "used": m.ullTotalPhys - m.ullAvailPhys}
        return None
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        total = info["MemTotal"]
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return {"total": total, "used": total - avail}
    except (OSError, KeyError, ValueError):
        return None


# ── 磁碟 ──
_REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "ntfs", "ntfs3", "fuseblk", "exfat", "vfat"}


def disks():
    out = []
    if IS_WIN:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = "%s:\\" % chr(65 + i)
            if ctypes.windll.kernel32.GetDriveTypeW(root) != 3:  # 只算固定磁碟
                continue
            try:
                u = shutil.disk_usage(root)
                out.append({"mount": root[:2], "total": u.total, "used": u.used})
            except OSError:
                pass
        return out
    seen = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                dev, mnt, fs = line.split()[:3]
                mnt = mnt.replace("\\040", " ")
                if fs not in _REAL_FS or dev in seen or mnt.startswith(("/snap", "/boot")):
                    continue
                seen.add(dev)
                try:
                    u = shutil.disk_usage(mnt)
                    out.append({"mount": mnt, "total": u.total, "used": u.used})
                except OSError:
                    pass
    except OSError:
        u = shutil.disk_usage("/")
        out.append({"mount": "/", "total": u.total, "used": u.used})
    return out


# ── 開機時間 ──
def uptime():
    if IS_WIN:
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
        return int(ctypes.windll.kernel32.GetTickCount64() / 1000)
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except OSError:
        return None


# ── 顯示卡（NVIDIA，nvidia-smi） ──
def _num(s):
    s = s.strip()
    if not s or s.startswith("[") or s.upper() in ("N/A", "NOT SUPPORTED"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _smi(args):
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe] + args, capture_output=True, timeout=6, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:  # Windows 上中文程式名稱是系統編碼（cp950）
        return r.stdout.decode("mbcs" if IS_WIN else "latin-1", "replace")


def gpus():
    """回傳 (顯示卡清單, 用顯示卡記憶體的程式)。結果快取 1.5 秒，多個頁面同時看也只問一次。"""
    with _lock:
        if _gpu_cache["data"] is not None and time.time() - _gpu_cache["t"] < 1.5:
            return _gpu_cache["data"]
    cards, procs = [], []
    txt = _smi(["--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
                "power.draw,power.limit,fan.speed,driver_version", "--format=csv,noheader,nounits"])
    uuid_to_index = {}
    if txt:
        for line in txt.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) < 11:
                continue
            uuid_to_index[p[1]] = int(p[0])
            cards.append({
                "index": int(p[0]), "name": p[2], "util": _num(p[3]),
                "mem_used": (_num(p[4]) or 0) * 1048576, "mem_total": (_num(p[5]) or 0) * 1048576,
                "temp": _num(p[6]), "power": _num(p[7]), "power_limit": _num(p[8]),
                "fan": _num(p[9]), "driver": p[10],
            })
        ptxt = _smi(["--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits"])
        for line in (ptxt or "").strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) < 4:
                continue
            mem = _num(p[3])  # Windows（WDDM）不提供各程式用量，會是 None
            procs.append({"gpu": uuid_to_index.get(p[0]), "pid": int(p[1]) if p[1].isdigit() else None,
                          "name": os.path.basename(p[2]), "mem": mem * 1048576 if mem is not None else None})
    data = (cards, procs)
    with _lock:
        _gpu_cache.update(t=time.time(), data=data)
    return data


def _os_name():
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            name = winreg.QueryValueEx(k, "ProductName")[0]
            build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
            if build >= 22000:  # 登錄檔的 ProductName 在 Windows 11 還是寫 10
                name = name.replace("Windows 10", "Windows 11")
            return "%s (%d)" % (name, build)
        except (OSError, ValueError):
            pass
    else:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return platform.platform()


def static_info():
    global _static
    if _static is None:
        _static = {"hostname": socket.gethostname(), "os": _os_name(), "cpu_name": _cpu_name(),
                   "cpu_count": os.cpu_count(), "python": sys.version.split()[0],
                   "platform": "windows" if IS_WIN else sys.platform}
    return _static


def snapshot():
    cards, procs = gpus()
    return dict(static_info(), cpu=cpu_percent(), mem=memory(), disks=disks(),
                uptime=uptime(), gpus=cards, gpu_procs=procs, time=int(time.time()))
