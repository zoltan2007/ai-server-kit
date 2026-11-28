"""admin_cli：在主機上建立／重設管理員帳號，之後用一般登入畫面登入。

    python -m unittest discover -s ai-server-kit/tests -p "test_admin_cli.py" -v
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))

import admin_cli  # noqa: E402
from users import UserStore  # noqa: E402

PW = "correct-horse-9"


class AdminCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aisk-admin-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, argv, inputs=(), passwords=()):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=list(inputs)), \
                mock.patch("getpass.getpass", side_effect=list(passwords)), \
                redirect_stdout(out), redirect_stderr(err):
            rc = admin_cli.main(argv)
        return rc, out.getvalue() + err.getvalue()

    def test_creates_active_admin_that_can_log_in_with_password(self):
        self.assertEqual(admin_cli.set_admin(self.tmp, "Zoltan", PW), "created")
        store = UserStore(self.tmp)
        u = store.verify("zoltan", PW)
        self.assertIsNotNone(u)
        self.assertEqual((u["role"], u["status"]), ("admin", "active"))
        self.assertTrue(store.has_admin())

    def test_existing_pending_user_becomes_admin_with_new_password(self):
        UserStore(self.tmp).create("amy", "old-password-1", role="user", status="pending", verified=False)
        self.assertEqual(admin_cli.set_admin(self.tmp, "amy", PW), "updated")
        store = UserStore(self.tmp)
        self.assertIsNone(store.verify("amy", "old-password-1"))
        u = store.verify("amy", PW)
        self.assertEqual((u["role"], u["status"], u["verified"]), ("admin", "active", True))

    def test_reset_logs_out_old_sessions(self):
        admin_cli.set_admin(self.tmp, "zoltan", PW)
        tok = UserStore(self.tmp).issue("zoltan")
        admin_cli.set_admin(self.tmp, "zoltan", "another-pass-2")
        self.assertIsNone(UserStore(self.tmp).who(tok))

    def test_rejects_bad_name_and_short_password(self):
        with self.assertRaises(ValueError):
            admin_cli.set_admin(self.tmp, "admin", PW)          # 保留字
        with self.assertRaises(ValueError):
            admin_cli.set_admin(self.tmp, "zoltan", "short")
        self.assertFalse(admin_cli.has_admin(self.tmp))

    def test_check_exit_codes(self):
        self.assertEqual(self.run_main(["--data", self.tmp, "--check"])[0], 1)
        admin_cli.set_admin(self.tmp, "zoltan", PW)
        self.assertEqual(self.run_main(["--data", self.tmp, "--check"])[0], 0)

    def test_prompts_for_name_and_password_twice(self):
        rc, text = self.run_main(["--data", self.tmp], inputs=["bad name", "zoltan"], passwords=[PW, PW])
        self.assertEqual(rc, 0, text)
        self.assertIn("已建立管理員帳號 zoltan", text)
        self.assertIsNotNone(UserStore(self.tmp).verify("zoltan", PW))

    def test_reserved_name_is_refused_before_asking_for_the_password(self):
        rc, text = self.run_main(["--data", self.tmp], inputs=["admin", "zoltan"], passwords=[PW, PW])
        self.assertEqual(rc, 0, text)
        self.assertIn("保留字", text)
        rc, text = self.run_main(["--data", self.tmp, "--user", "root"], passwords=[])   # 不會走到問密碼
        self.assertEqual(rc, 1)
        self.assertIn("保留字", text)

    def test_mismatched_passwords_retry_then_give_up(self):
        rc, text = self.run_main(["--data", self.tmp, "--user", "zoltan"],
                                 passwords=[PW, "nope-1234", PW, "nope-5678", PW, "nope-0000"])
        self.assertEqual(rc, 1)
        self.assertIn("沒有設定", text)
        self.assertFalse(admin_cli.has_admin(self.tmp))

    def test_ctrl_c_and_eof_do_not_crash(self):
        rc, _ = self.run_main(["--data", self.tmp, "--user", "zoltan"], passwords=[KeyboardInterrupt()])
        self.assertEqual(rc, 1)
        rc, _ = self.run_main(["--data", self.tmp], inputs=[EOFError()])
        self.assertEqual(rc, 1)

    def test_missing_data_dir(self):
        rc, text = self.run_main(["--data", os.path.join(self.tmp, "nope"), "--check"])
        self.assertEqual(rc, 2)
        self.assertIn("找不到資料夾", text)

    def test_panel_login_page_uses_the_cli_admin(self):
        """建好之後：/api/ping 不再要求登入碼設定，一般登入（帳號密碼）拿到管理員身分。"""
        import json
        import threading
        import urllib.request
        import server
        admin_cli.set_admin(self.tmp, "zoltan", PW)
        cfg = {"host": "127.0.0.1", "port": 0, "token": "master-token-0123456789", "terminal": False,
               "data_dir": self.tmp, "ollama": "http://127.0.0.1:9"}
        srv = server.make_server(cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            with urllib.request.urlopen(base + "/api/ping", timeout=10) as r:
                self.assertFalse(json.load(r)["setup"])
            req = urllib.request.Request(base + "/api/login", data=json.dumps({"user": "zoltan", "password": PW}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.load(r)
            self.assertEqual((body["user"], body["role"]), ("zoltan", "admin"))
            self.assertNotIn("master", body)
            me = urllib.request.Request(base + "/api/users", headers={"Authorization": "Bearer " + body["token"]})
            with urllib.request.urlopen(me, timeout=10) as r:
                self.assertIn("zoltan", [u["name"] for u in json.load(r)["users"]])
            # 主機面板（/）也是帳號密碼登入，登入碼欄位預設藏起來
            with urllib.request.urlopen(base + "/", timeout=10) as r:
                page = r.read().decode("utf-8")
            self.assertIn('id="login-user"', page)
            self.assertIn('id="login-pass"', page)
            self.assertRegex(page, r'<input id="login-token"[^>]*\bhidden\b')
            # 帳號憑證能用面板的管理員功能（/api/jobs 只有管理員能用）
            jobs = urllib.request.Request(base + "/api/jobs", headers={"Authorization": "Bearer " + body["token"]})
            with urllib.request.urlopen(jobs, timeout=10) as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_password_is_not_stored_in_plain_text(self):
        admin_cli.set_admin(self.tmp, "zoltan", PW)
        with open(os.path.join(self.tmp, "users.json"), encoding="utf-8") as f:
            self.assertNotIn(PW, f.read())


if __name__ == "__main__":
    unittest.main()
