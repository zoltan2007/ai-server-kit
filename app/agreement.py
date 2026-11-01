"""使用協議：第一次用面板要先看過、打勾同意才能用。

協議內容可由製作人覆寫（在 data_dir 放 agreement.txt）。開了「自動回傳模型」時，
協議會自動加入一段把這件事講清楚的條款——不藏字，對方同意了才會傳。
"""
import hashlib
import json
import os
import time

HEADER = "使用條款與資料協議\n\n歡迎使用本 AI 主機軟體。開始使用前，請閱讀並同意以下條款：\n"
FOOTER = "\n勾選下方選項並繼續，即表示你已閱讀並同意上述全部條款。"

CLAUSES = [
    "本軟體在你的電腦上執行開源 AI 模型，提供模型管理、對話與終端機功能。",
    "終端機功能可在這台電腦上執行指令，等同於操作本機；請妥善保管登入碼，勿交給不信任的人。",
    "本軟體不保證模型輸出的正確性，請勿單憑其輸出做醫療、法律、財務等需專業判斷的決定。",
    "你需為在本軟體中的一切操作負責。",
]
SHARE_CLAUSE = ("選用的模型分享功能：本軟體提供一個選用功能，讓你可以把自己匯入的「非公開／自製」模型"
                "（在公開網路上找不到的模型）分享給本軟體的提供者（{label}）。此功能預設為關閉；"
                "只有在你主動勾選同意、且每次匯入時自行按下並確認後，該模型檔案才會被傳送。"
                "公開的開源模型不涉及此功能。你可以選擇不使用它。")


def text(label="製作人", share_available=False, custom=None):
    if custom:
        return custom.strip()
    clauses = list(CLAUSES)
    if share_available:
        clauses.insert(2, SHARE_CLAUSE.format(label=label))  # 放在終端機那條之後、顯眼處
    body = "\n".join("%d. %s" % (i + 1, c) for i, c in enumerate(clauses))
    return HEADER + "\n" + body + "\n" + FOOTER


def version(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def load_custom(data_dir):
    p = os.path.join(data_dir, "agreement.txt")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                return f.read()
        except OSError:
            pass
    return None


def _path(data_dir):
    return os.path.join(data_dir, "agreement.json")


def status(data_dir, t):
    v = version(t)
    acc = None
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            acc = json.load(f)
    except (OSError, ValueError):
        pass
    acc = acc if isinstance(acc, dict) else {}
    return {"version": v, "accepted": acc.get("version") == v,
            "accepted_at": acc.get("at"), "share_optin": bool(acc.get("share_optin"))}


def accept(data_dir, v, t, share_optin=False):
    if not v or v != version(t):
        return False
    try:
        with open(_path(data_dir), "w", encoding="utf-8") as f:
            json.dump({"version": v, "at": int(time.time()), "share_optin": bool(share_optin)}, f)
        return True
    except OSError:
        return False
