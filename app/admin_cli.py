"""在主機上建立或重設管理員帳號（之後在登入畫面用一般的帳號密碼登入，不用登入碼）。

    python3 admin_cli.py --data /opt/ai-server-kit/data            問帳號、密碼（密碼打兩次，不會顯示）
    python3 admin_cli.py --data DIR --user zoltan                  只問密碼
    python3 admin_cli.py --data DIR --check                        已經有可用的管理員帳號就回 0，沒有回 1

面板執行中時不要直接跑這支（面板記憶體裡的帳號表會蓋掉）；Linux 用 sudo /opt/ai-server-kit/set-admin.sh，
它會先停面板、跑完再開。
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from users import RESERVED, UserStore  # noqa: E402

MAX_TRIES = 3


def name_problem(name: str) -> str:
    """帳號不能用的原因；可以用回空字串。先問帳號就檢查，免得密碼打完兩次才被拒。"""
    if not UserStore.valid_name(name):
        return "帳號只能用小寫英文、數字、_ . -，3～20 個字"
    if name in RESERVED:
        return "「%s」是保留字，換一個（例如你的名字）" % name
    return ""


def set_admin(data_dir: str, name: str, pw: str) -> str:
    """有這個帳號 → 改密碼並設成可用的管理員；沒有 → 建一個。回傳 "created" 或 "updated"。"""
    store = UserStore(data_dir)
    name = (name or "").strip().lower()
    if name in store.users:
        store.set_password(name, pw)
        store.set_status(name, "active", role="admin")
        store.set_verified(name)
        return "updated"
    store.create(name, pw, role="admin", status="active")
    return "created"


def has_admin(data_dir: str) -> bool:
    return UserStore(data_dir).has_admin()


def _ask_name() -> str:
    for _ in range(MAX_TRIES):
        name = input("  管理員帳號（小寫英文、數字、_ . -，3～20 個字）：").strip().lower()
        problem = name_problem(name)
        if not problem:
            return name
        print("  " + problem + "，再打一次")
    raise ValueError("帳號格式不對")


def _ask_password() -> str:
    for _ in range(MAX_TRIES):
        pw = getpass.getpass("  密碼（至少 8 個字，打字不會顯示）：")
        try:
            UserStore.check_password(pw)
        except ValueError as e:
            print("  " + str(e))
            continue
        if getpass.getpass("  再打一次密碼：") == pw:
            return pw
        print("  兩次密碼不一樣，重來")
    raise ValueError("密碼沒有設定成功")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="建立或重設管理員帳號")
    ap.add_argument("--data", required=True, help="面板的 data_dir（config.json 裡的 data_dir）")
    ap.add_argument("--user", help="帳號；不給就問")
    ap.add_argument("--check", action="store_true", help="只檢查有沒有可用的管理員帳號")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.data):
        print("找不到資料夾：%s" % a.data, file=sys.stderr)
        return 2
    if a.check:
        return 0 if has_admin(a.data) else 1
    try:
        name = a.user.strip().lower() if a.user else _ask_name()
        if name_problem(name):
            raise ValueError(name_problem(name))
        result = set_admin(a.data, name, _ask_password())
    except (ValueError, EOFError, KeyboardInterrupt) as e:
        print("\n  沒有設定：%s" % (str(e) or "取消"), file=sys.stderr)
        return 1
    print("  %s管理員帳號 %s。之後在登入畫面輸入這組帳號密碼登入。" % ("已建立" if result == "created" else "已更新", name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
