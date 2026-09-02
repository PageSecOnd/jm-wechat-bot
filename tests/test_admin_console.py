from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from jmwxbot.admin import AdminConsole, _session_value
from jmwxbot.models import Account
from jmwxbot.settings import Settings
from jmwxbot.store import Store


class _Runtime:
    def __init__(self, account):
        self.account = account

    def admin_snapshot(self):
        return {
            "account_id": self.account.account_id,
            "active_count": 1,
            "queued_count": 2,
            "peer_workers": 1,
            "active_items": [
                {"peer_id": "peer-a", "jm_id": "123", "format": "pdf", "stage": "下载", "done": 3, "total": 10, "started_at": 0},
            ],
        }


class AdminConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.settings = Settings(data_dir=data, admin_token="test-secret", admin_host="127.0.0.1", admin_port=8787)
        self.store = Store(self.settings.db_path)
        self.account = Account(
            account_id="acc-a",
            bot_token="BOT_TOKEN_MUST_NOT_LEAK",
            base_url="https://ilink.example.invalid",
            user_id="wx-user-a",
            name="微信A",
            sync_buf="",
        )
        self.store.save_account(self.account)
        self.store.update_peer_context("acc-a", "peer-a", "CONTEXT_TOKEN_MUST_NOT_LEAK")
        self.store.record_usage("acc-a", "peer-a", "search")
        self.store.save_jm_login(
            "acc-a",
            "peer-a",
            SimpleNamespace(
                uid="1001", username="jm-user-a", display_name="JM A", coin=1, level=2,
                level_name="L2", exp=3, next_level_exp=4, exp_percent=75.0, favorite_count=5,
            ),
        )
        console = AdminConsole(self.settings, self.store, [_Runtime(self.account)])
        self.server = TestServer(console.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()
        self.tmp.cleanup()

    async def test_overview_requires_auth(self):
        resp = await self.client.get("/", allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers.get("Location"), "/login")

    async def test_authenticated_pages_show_stats_but_not_secrets(self):
        self.client.session.cookie_jar.update_cookies({"jmwxbot_admin": _session_value("test-secret")})
        resp = await self.client.get("/")
        text = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertIn("微信A", text)
        self.assertIn("wx-user-a", text)
        self.assertIn("search", text)
        self.assertNotIn("BOT_TOKEN_MUST_NOT_LEAK", text)
        self.assertNotIn("CONTEXT_TOKEN_MUST_NOT_LEAK", text)

        resp = await self.client.get("/account/acc-a")
        text = await resp.text()
        self.assertEqual(resp.status, 200)
        self.assertIn("jm-user-a", text)
        self.assertIn("1001", text)
        self.assertIn("3 / 10", text)
        self.assertNotIn("BOT_TOKEN_MUST_NOT_LEAK", text)
        self.assertNotIn("CONTEXT_TOKEN_MUST_NOT_LEAK", text)

    async def test_login_sets_http_only_cookie(self):
        resp = await self.client.post("/login", data={"token": "test-secret"}, allow_redirects=False)
        self.assertEqual(resp.status, 302)
        cookie = resp.headers.get("Set-Cookie", "")
        self.assertIn("jmwxbot_admin=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
