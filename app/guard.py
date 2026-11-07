"""防濫用：頻率限制、同時進行的對話數、每個路徑的內容大小上限。純記憶體，重開就歸零。"""
import threading
import time

# 每個路徑每分鐘的次數上限（以帳號計；沒登入的以 IP 計）。沒列的用 DEFAULT。
LIMITS = {
    "/api/login": 10, "/api/apply": 3, "/api/chat": 30, "/api/rooms/post": 60, "/api/rooms/create": 10,
    "/api/ai/test": 6, "/api/sessions/save": 120, "/api/upload": 10, "/api/models/pull": 10, "/api/term/run": 60,
}
DEFAULT = 240
# 內容大小上限：對話可以帶圖，其他都很小
BODY_MAX = {"/api/chat": 24 * 1024 * 1024, "/api/sessions/save": 8 * 1024 * 1024 + 4096, "/api/models/import": 64 * 1024}
BODY_DEFAULT = 256 * 1024
MAX_CONCURRENT_CHATS = 2       # 一個帳號同時最多幾個對話在生成（GPU 只有一組）
MAX_CONCURRENT_TOTAL = 8


class Guard:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits = {}          # (key, path) -> [timestamps]
        self.active = {}        # key -> 進行中的對話數

    def allow(self, key, path):
        if path not in LIMITS and len(self.hits) > 5000:
            return True          # 亂打的路徑不要撐大這張表；它們本來就會 404
        limit = LIMITS.get(path, DEFAULT)
        now = time.time()
        with self.lock:
            ts = [t for t in self.hits.get((key, path), []) if now - t < 60]
            if len(ts) >= limit:
                self.hits[(key, path)] = ts
                return False
            ts.append(now)
            self.hits[(key, path)] = ts
            if len(self.hits) > 20000:   # 別讓表無限長大
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 60}
            return True

    @staticmethod
    def body_max(path):
        return BODY_MAX.get(path, BODY_DEFAULT)

    def chat_start(self, key):
        with self.lock:
            if self.active.get(key, 0) >= MAX_CONCURRENT_CHATS or sum(self.active.values()) >= MAX_CONCURRENT_TOTAL:
                return False
            self.active[key] = self.active.get(key, 0) + 1
            return True

    def chat_end(self, key):
        with self.lock:
            self.active[key] = max(0, self.active.get(key, 0) - 1)
