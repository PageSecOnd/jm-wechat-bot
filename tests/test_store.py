import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from jmwxbot.models import Account
from jmwxbot.store import Store


class StoreTests(unittest.TestCase):
    def test_account_peer_and_dedupe_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            try:
                store.save_account(Account("a1", "t1", "https://x", "u1", "one"))
                store.save_account(Account("a2", "t2", "https://x", "u2", "two"))
                store.update_peer_context("a1", "p", "ctx-a1")
                store.update_peer_context("a2", "p", "ctx-a2")
                self.assertEqual(store.get_peer_context("a1", "p"), "ctx-a1")
                self.assertEqual(store.get_peer_context("a2", "p"), "ctx-a2")
                self.assertTrue(store.claim_message("a1", "m1"))
                self.assertFalse(store.claim_message("a1", "m1"))
                self.assertTrue(store.claim_message("a2", "m1"))
            finally:
                store.close()

    def test_same_scanner_replaces_old_binding(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            try:
                store.save_account(Account("old", "t1", "https://x", "same-user"))
                replaced = store.save_account(Account("new", "t2", "https://x", "same-user"))
                self.assertEqual(replaced, ["old"])
                self.assertIsNone(store.get_account("old"))
                self.assertIsNotNone(store.get_account("new"))
            finally:
                store.close()

    def test_history_is_scoped_per_account_and_peer(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            try:
                store.save_account(Account("a1", "t1", "https://x", "u1"))
                store.save_account(Account("a2", "t2", "https://x", "u2"))
                hid = store.start_history("a1", "p1", "123", "pdf")
                store.finish_history(hid, status="sent", title="标题", file_size=100, duration=1.2)
                store.start_history("a1", "p2", "456", "zip")
                store.start_history("a2", "p1", "789", "pdf")
                rows = store.list_history("a1", "p1")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["jm_id"], "123")
                self.assertEqual(rows[0]["status"], "sent")
            finally:
                store.close()

    def test_jm_signin_state_is_isolated_and_enrolled_on_login(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            try:
                store.save_account(Account("a1", "t1", "https://x", "u1"))
                store.save_account(Account("a2", "t2", "https://x", "u2"))
                login = SimpleNamespace(
                    uid="9988", username="alice", display_name="Alice", coin=10, level=2,
                    level_name="Lv.2", exp=20, next_level_exp=30, exp_percent=66.7, favorite_count=4,
                )
                store.save_jm_login("a1", "p1", login)
                store.save_jm_login("a2", "p1", SimpleNamespace(**{**login.__dict__, "uid": "7788"}))
                users = store.list_jm_signin_users("a1")
                self.assertEqual(len(users), 1)
                self.assertEqual(users[0]["uid"], "9988")
                self.assertEqual(users[0]["login_verified"], 1)
                store.record_daily_attempt("a1", "p1", "2026-09-01", "signed", "签到成功")
                state = store.get_jm_state("a1", "p1")
                self.assertEqual(state["last_daily_result"], "signed")
                self.assertIsNone(store.get_jm_state("a1", "missing"))
                self.assertIsNone(store.get_jm_state("a2", "missing"))
            finally:
                store.close()

    def test_v053_migration_marks_preexisting_jm_login_unverified(self):
        import sqlite3
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE accounts (
                    account_id TEXT PRIMARY KEY, bot_token TEXT NOT NULL, base_url TEXT NOT NULL,
                    user_id TEXT, name TEXT, sync_buf TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE jm_peer_state (
                    account_id TEXT NOT NULL, peer_id TEXT NOT NULL, uid TEXT, username TEXT,
                    display_name TEXT, coin INTEGER, level INTEGER, level_name TEXT, exp INTEGER,
                    next_level_exp INTEGER, exp_percent REAL, favorite_count INTEGER,
                    last_daily_date TEXT, last_daily_result TEXT, last_daily_message TEXT,
                    last_daily_at TEXT, daily_attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL, PRIMARY KEY (account_id, peer_id)
                );
                INSERT INTO accounts(account_id, bot_token, base_url, created_at, updated_at)
                    VALUES('a1','t','https://x','now','now');
                INSERT INTO jm_peer_state(account_id, peer_id, uid, username, updated_at)
                    VALUES('a1','p1','111','old-user','now');
            """)
            conn.commit()
            conn.close()

            store = Store(db)
            try:
                state = store.get_jm_state("a1", "p1")
                self.assertEqual(state["login_verified"], 0)
                self.assertEqual(store.list_jm_signin_users("a1"), [])

                login = SimpleNamespace(
                    uid="222", username="new-user", display_name="", coin=0, level=1,
                    level_name="Lv.1", exp=0, next_level_exp=1, exp_percent=0.0, favorite_count=0,
                )
                store.save_jm_login("a1", "p1", login)
                state = store.get_jm_state("a1", "p1")
                self.assertEqual(state["login_verified"], 1)
                self.assertEqual(state["uid"], "222")
            finally:
                store.close()

    def test_admin_aggregates_are_scoped_and_secret_free(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            try:
                store.save_account(Account("a1", "secret-token", "https://x", "wx1", "one"))
                store.save_account(Account("a2", "secret-token-2", "https://x", "wx2", "two"))
                store.update_peer_context("a1", "p1", "secret-context")
                store.record_usage("a1", "p1", "search")
                store.record_usage("a1", "p1", "download_pdf")
                store.record_usage("a2", "p1", "search")
                login = SimpleNamespace(
                    uid="100", username="alice", display_name="Alice", coin=3, level=2,
                    level_name="Lv.2", exp=1, next_level_exp=2, exp_percent=50.0, favorite_count=4,
                )
                store.save_jm_login("a1", "p1", login)
                hid = store.start_history("a1", "p1", "123", "pdf")
                store.finish_history(hid, status="sent", title="title", file_size=4096, duration=1.0)

                accounts = {r["account_id"]: r for r in store.admin_accounts()}
                self.assertEqual(accounts["a1"]["peer_count"], 1)
                self.assertEqual(accounts["a1"]["jm_logged_in"], 1)
                self.assertEqual(accounts["a1"]["command_count"], 2)
                self.assertEqual(accounts["a1"]["sent_count"], 1)
                self.assertEqual(accounts["a1"]["sent_bytes"], 4096)
                self.assertNotIn("bot_token", accounts["a1"])

                peers = store.admin_peer_stats("a1")
                self.assertEqual(len(peers), 1)
                self.assertEqual(peers[0]["peer_id"], "p1")
                self.assertEqual(peers[0]["username"], "alice")
                self.assertEqual(peers[0]["search_count"], 1)
                self.assertNotIn("context_token", peers[0])

                usage = {r["event_type"]: r["count"] for r in store.admin_usage_summary("a1", days=7)}
                self.assertEqual(usage, {"download_pdf": 1, "search": 1})
                recent = store.admin_recent_jobs("a1", limit=10)
                self.assertEqual(recent[0]["jm_id"], "123")
                self.assertEqual(recent[0]["file_size"], 4096)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
