import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.models import Account, JmDailyResult, JmDailyStatus
from jmwxbot.runtime import AccountRuntime
from jmwxbot.settings import Settings
from jmwxbot.store import Store
from jmwxbot.util import peer_jm_profile


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_text(self, *args):
        self.sent.append(args)


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def auth_profile_is_current(self, path):
        return True

    async def daily_checkin(self, uid, *, option_file):
        self.calls += 1
        return JmDailyResult("签到成功", False, JmDailyStatus("67", "每日签到", "1/7"))


class DailySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_logged_in_user_is_auto_signed_once_without_wechat_notification(self):
        with tempfile.TemporaryDirectory() as td:
            settings = Settings(
                data_dir=Path(td),
                daily_signin_hour=8,
                daily_signin_check_interval_minutes=30,
            )
            store = Store(settings.db_path)
            try:
                account = Account("a1", "token", "https://example.test", "bot-user")
                store.save_account(account)
                login = SimpleNamespace(
                    uid="9988", username="alice", display_name="Alice", coin=1, level=1,
                    level_name="Lv.1", exp=0, next_level_exp=10, exp_percent=0.0, favorite_count=2,
                )
                store.save_jm_login("a1", "peer1", login)
                profile = peer_jm_profile(settings.jm_profiles_dir, "a1", "peer1")
                profile.parent.mkdir(parents=True, exist_ok=True)
                profile.write_text("# jmwxbot-auth-profile: 2\nversion: '2.1'\n", encoding="utf-8")

                client = FakeClient()
                runtime = AccountRuntime(settings, store, account, client)
                provider = FakeProvider()
                runtime.provider = provider
                fixed = datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=8)))
                with patch("jmwxbot.runtime.beijing_now", return_value=fixed):
                    await runtime.run_daily_signin_once()
                    await runtime.run_daily_signin_once()

                self.assertEqual(provider.calls, 1)
                self.assertEqual(client.sent, [])
                state = store.get_jm_state("a1", "peer1")
                self.assertEqual(state["last_daily_date"], "2026-09-01")
                self.assertEqual(state["last_daily_result"], "signed")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
