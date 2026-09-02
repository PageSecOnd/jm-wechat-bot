from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .models import Account


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    bot_token TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    user_id TEXT,
                    name TEXT,
                    sync_buf TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_user_id
                    ON accounts(user_id) WHERE user_id IS NOT NULL AND user_id <> '';

                CREATE TABLE IF NOT EXISTS peer_contexts (
                    account_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    context_token TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, peer_id),
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS processed_messages (
                    account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, message_id),
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    jm_id TEXT NOT NULL,
                    export_format TEXT NOT NULL,
                    title TEXT,
                    status TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    duration REAL,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_history_peer
                    ON job_history(account_id, peer_id, id DESC);

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_usage_events_account_time
                    ON usage_events(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_usage_events_peer_time
                    ON usage_events(account_id, peer_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS jm_peer_state (
                    account_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    uid TEXT,
                    username TEXT,
                    display_name TEXT,
                    coin INTEGER,
                    level INTEGER,
                    level_name TEXT,
                    exp INTEGER,
                    next_level_exp INTEGER,
                    exp_percent REAL,
                    favorite_count INTEGER,
                    login_verified INTEGER NOT NULL DEFAULT 0,
                    last_daily_date TEXT,
                    last_daily_result TEXT,
                    last_daily_message TEXT,
                    last_daily_at TEXT,
                    daily_attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, peer_id),
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
                );
                """
            )
            # v0.5.3 security migration: existing JM sessions were created before
            # strict cross-user login verification. Mark them unverified so they
            # cannot be used for favorites or automatic sign-in until the user
            # explicitly logs in again with the isolated login flow.
            columns = {
                str(row[1]) for row in self._conn.execute("PRAGMA table_info(jm_peer_state)").fetchall()
            }
            if "login_verified" not in columns:
                self._conn.execute(
                    "ALTER TABLE jm_peer_state ADD COLUMN login_verified INTEGER NOT NULL DEFAULT 0"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_account(self, account: Account) -> list[str]:
        replaced: list[str] = []
        with self._lock, self._conn:
            if account.user_id:
                rows = self._conn.execute(
                    "SELECT account_id FROM accounts WHERE user_id=? AND account_id<>?",
                    (account.user_id, account.account_id),
                ).fetchall()
                replaced = [str(r[0]) for r in rows]
                for old in replaced:
                    self._conn.execute("DELETE FROM accounts WHERE account_id=?", (old,))
            existing = self._conn.execute(
                "SELECT created_at FROM accounts WHERE account_id=?", (account.account_id,)
            ).fetchone()
            created_at = existing[0] if existing else _now()
            self._conn.execute(
                """
                INSERT INTO accounts(account_id, bot_token, base_url, user_id, name, sync_buf, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    bot_token=excluded.bot_token,
                    base_url=excluded.base_url,
                    user_id=excluded.user_id,
                    name=COALESCE(excluded.name, accounts.name),
                    updated_at=excluded.updated_at
                """,
                (
                    account.account_id,
                    account.bot_token,
                    account.base_url,
                    account.user_id,
                    account.name,
                    account.sync_buf,
                    created_at,
                    _now(),
                ),
            )
        return replaced

    def list_accounts(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT account_id, bot_token, base_url, user_id, name, sync_buf FROM accounts ORDER BY created_at"
            ).fetchall()
        return [Account(**dict(r)) for r in rows]

    def get_account(self, account_id: str) -> Account | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT account_id, bot_token, base_url, user_id, name, sync_buf FROM accounts WHERE account_id=?",
                (account_id,),
            ).fetchone()
        return Account(**dict(row)) if row else None

    def delete_account(self, account_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
            return cur.rowcount > 0

    def recent_tokens(self, limit: int = 10) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT bot_token FROM accounts ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [str(r[0]) for r in rows]

    def update_sync_buf(self, account_id: str, sync_buf: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE accounts SET sync_buf=?, updated_at=? WHERE account_id=?",
                (sync_buf, _now(), account_id),
            )

    def update_peer_context(self, account_id: str, peer_id: str, context_token: str) -> None:
        if not context_token:
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO peer_contexts(account_id, peer_id, context_token, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(account_id, peer_id) DO UPDATE SET
                    context_token=excluded.context_token,
                    updated_at=excluded.updated_at
                """,
                (account_id, peer_id, context_token, _now()),
            )

    def get_peer_context(self, account_id: str, peer_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT context_token FROM peer_contexts WHERE account_id=? AND peer_id=?",
                (account_id, peer_id),
            ).fetchone()
        return str(row[0]) if row else None

    def claim_message(self, account_id: str, message_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO processed_messages(account_id, message_id, processed_at) VALUES(?,?,?)",
                (account_id, message_id, _now()),
            )
            return cur.rowcount == 1

    def prune_processed(self, keep_days: int = 14) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM processed_messages WHERE processed_at < datetime('now', ?)",
                (f"-{keep_days} days",),
            )

    def start_history(self, account_id: str, peer_id: str, jm_id: str, export_format: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO job_history(account_id, peer_id, jm_id, export_format, status, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (account_id, peer_id, jm_id, export_format, "running", _now()),
            )
            return int(cur.lastrowid)

    def finish_history(
        self,
        history_id: int,
        *,
        status: str,
        title: str | None = None,
        file_size: int = 0,
        duration: float | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE job_history
                SET status=?, title=COALESCE(?, title), file_size=?, duration=?, message=?, completed_at=?
                WHERE id=?
                """,
                (status, title, int(file_size), duration, message, _now(), history_id),
            )

    def list_history(self, account_id: str, peer_id: str, limit: int = 10) -> list[dict]:
        limit = max(1, min(20, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, jm_id, export_format, title, status, file_size, duration, message, created_at, completed_at
                FROM job_history
                WHERE account_id=? AND peer_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (account_id, peer_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    def save_jm_login(self, account_id: str, peer_id: str, result) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jm_peer_state(
                    account_id, peer_id, uid, username, display_name, coin, level, level_name,
                    exp, next_level_exp, exp_percent, favorite_count, login_verified, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account_id, peer_id) DO UPDATE SET
                    uid=excluded.uid,
                    username=excluded.username,
                    display_name=excluded.display_name,
                    coin=excluded.coin,
                    level=excluded.level,
                    level_name=excluded.level_name,
                    exp=excluded.exp,
                    next_level_exp=excluded.next_level_exp,
                    exp_percent=excluded.exp_percent,
                    favorite_count=excluded.favorite_count,
                    login_verified=1,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id, peer_id, result.uid, result.username, result.display_name,
                    result.coin, result.level, result.level_name, result.exp, result.next_level_exp,
                    result.exp_percent, result.favorite_count, 1, _now(),
                ),
            )

    def get_jm_state(self, account_id: str, peer_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jm_peer_state WHERE account_id=? AND peer_id=?",
                (account_id, peer_id),
            ).fetchone()
        return dict(row) if row else None

    def clear_jm_state(self, account_id: str, peer_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM jm_peer_state WHERE account_id=? AND peer_id=?",
                (account_id, peer_id),
            )

    def list_jm_signin_users(self, account_id: str) -> list[dict]:
        """Return JM users eligible for automatic daily sign-in for one WeChat bot account."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jm_peer_state
                WHERE account_id=? AND uid IS NOT NULL AND uid<>'' AND login_verified=1
                ORDER BY peer_id
                """,
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_daily_attempt(
        self, account_id: str, peer_id: str, local_date: str, result: str, message: str
    ) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_daily_date, daily_attempt_count FROM jm_peer_state WHERE account_id=? AND peer_id=?",
                (account_id, peer_id),
            ).fetchone()
            if not row:
                return
            attempts = int(row[1] or 0) + 1 if row[0] == local_date else 1
            self._conn.execute(
                """
                UPDATE jm_peer_state
                SET last_daily_date=?, last_daily_result=?, last_daily_message=?,
                    last_daily_at=?, daily_attempt_count=?, updated_at=?
                WHERE account_id=? AND peer_id=?
                """,
                (local_date, result, message[:1000], _now(), attempts, _now(), account_id, peer_id),
            )
    def record_usage(self, account_id: str, peer_id: str, event_type: str) -> None:
        """Record a privacy-minimal usage event. Raw command text and arguments are never stored."""
        event_type = str(event_type or "unknown")[:64]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO usage_events(account_id, peer_id, event_type, created_at) VALUES(?,?,?,?)",
                (account_id, peer_id, event_type, _now()),
            )

    def admin_accounts(self) -> list[dict]:
        """Safe account metadata and aggregate usage for the read-only admin console."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT a.account_id, a.base_url, a.user_id, a.name, a.created_at, a.updated_at,
                       (SELECT COUNT(*) FROM peer_contexts p WHERE p.account_id=a.account_id) AS peer_count,
                       (SELECT COUNT(*) FROM jm_peer_state j WHERE j.account_id=a.account_id AND j.login_verified=1) AS jm_logged_in,
                       (SELECT COUNT(*) FROM usage_events u WHERE u.account_id=a.account_id) AS command_count,
                       (SELECT COUNT(*) FROM usage_events u WHERE u.account_id=a.account_id AND datetime(u.created_at) >= datetime('now','-7 days')) AS command_count_7d,
                       (SELECT COUNT(*) FROM job_history h WHERE h.account_id=a.account_id) AS job_count,
                       (SELECT COUNT(*) FROM job_history h WHERE h.account_id=a.account_id AND h.status='sent') AS sent_count,
                       (SELECT COUNT(*) FROM job_history h WHERE h.account_id=a.account_id AND h.status='failed') AS failed_count,
                       (SELECT COALESCE(SUM(file_size),0) FROM job_history h WHERE h.account_id=a.account_id AND h.status='sent') AS sent_bytes,
                       (SELECT MAX(updated_at) FROM peer_contexts p WHERE p.account_id=a.account_id) AS last_seen
                FROM accounts a
                ORDER BY a.created_at
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def admin_peer_stats(self, account_id: str) -> list[dict]:
        """Per-peer aggregate statistics without exposing context tokens or JM AVS values."""
        with self._lock:
            peer_rows = self._conn.execute(
                """
                SELECT peer_id FROM peer_contexts WHERE account_id=?
                UNION SELECT peer_id FROM jm_peer_state WHERE account_id=?
                UNION SELECT peer_id FROM job_history WHERE account_id=?
                UNION SELECT peer_id FROM usage_events WHERE account_id=?
                """,
                (account_id, account_id, account_id, account_id),
            ).fetchall()
            peers = [str(r[0]) for r in peer_rows]
            result: list[dict] = []
            for peer_id in peers:
                context = self._conn.execute(
                    "SELECT updated_at FROM peer_contexts WHERE account_id=? AND peer_id=?",
                    (account_id, peer_id),
                ).fetchone()
                jm = self._conn.execute(
                    "SELECT uid, username, display_name, level, level_name, coin, favorite_count, login_verified, last_daily_date, last_daily_result, updated_at FROM jm_peer_state WHERE account_id=? AND peer_id=?",
                    (account_id, peer_id),
                ).fetchone()
                jobs = self._conn.execute(
                    """SELECT COUNT(*) AS total,
                                      SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                                      COALESCE(SUM(CASE WHEN status='sent' THEN file_size ELSE 0 END),0) AS sent_bytes,
                                      MAX(created_at) AS last_job
                               FROM job_history WHERE account_id=? AND peer_id=?""",
                    (account_id, peer_id),
                ).fetchone()
                usage = self._conn.execute(
                    """SELECT COUNT(*) AS total,
                                      SUM(CASE WHEN datetime(created_at) >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS d7,
                                      SUM(CASE WHEN event_type='search' THEN 1 ELSE 0 END) AS searches,
                                      MAX(created_at) AS last_command
                               FROM usage_events WHERE account_id=? AND peer_id=?""",
                    (account_id, peer_id),
                ).fetchone()
                row = {
                    'peer_id': peer_id,
                    'last_seen': context[0] if context else None,
                    'uid': jm['uid'] if jm else None,
                    'username': jm['username'] if jm else None,
                    'display_name': jm['display_name'] if jm else None,
                    'level': jm['level'] if jm else None,
                    'level_name': jm['level_name'] if jm else None,
                    'coin': jm['coin'] if jm else None,
                    'favorite_count': jm['favorite_count'] if jm else None,
                    'login_verified': int(jm['login_verified']) if jm else 0,
                    'last_daily_date': jm['last_daily_date'] if jm else None,
                    'last_daily_result': jm['last_daily_result'] if jm else None,
                    'jm_updated_at': jm['updated_at'] if jm else None,
                    'job_count': int(jobs['total'] or 0),
                    'sent_count': int(jobs['sent'] or 0),
                    'failed_count': int(jobs['failed'] or 0),
                    'sent_bytes': int(jobs['sent_bytes'] or 0),
                    'last_job': jobs['last_job'],
                    'command_count': int(usage['total'] or 0),
                    'command_count_7d': int(usage['d7'] or 0),
                    'search_count': int(usage['searches'] or 0),
                    'last_command': usage['last_command'],
                }
                result.append(row)
        result.sort(key=lambda r: r.get('last_seen') or r.get('last_command') or r.get('last_job') or '', reverse=True)
        return result

    def admin_usage_summary(self, account_id: str | None = None, days: int = 7) -> list[dict]:
        days = max(1, min(3650, int(days)))
        with self._lock:
            if account_id:
                rows = self._conn.execute(
                    """SELECT event_type, COUNT(*) AS count FROM usage_events
                               WHERE account_id=? AND datetime(created_at) >= datetime('now', ?)
                               GROUP BY event_type ORDER BY count DESC, event_type""",
                    (account_id, f'-{days} days'),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT event_type, COUNT(*) AS count FROM usage_events
                               WHERE datetime(created_at) >= datetime('now', ?)
                               GROUP BY event_type ORDER BY count DESC, event_type""",
                    (f'-{days} days',),
                ).fetchall()
        return [dict(r) for r in rows]

    def admin_recent_jobs(self, account_id: str | None = None, limit: int = 30, failures_only: bool = False) -> list[dict]:
        limit = max(1, min(200, int(limit)))
        where: list[str] = []
        args: list[object] = []
        if account_id:
            where.append('h.account_id=?')
            args.append(account_id)
        if failures_only:
            where.append("h.status IN ('failed','too_large')")
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        sql = f"""
            SELECT h.id, h.account_id, a.name AS account_name, h.peer_id, h.jm_id, h.export_format,
                   h.title, h.status, h.file_size, h.duration, h.message, h.created_at, h.completed_at
            FROM job_history h JOIN accounts a ON a.account_id=h.account_id
            {clause}
            ORDER BY h.id DESC LIMIT ?
        """
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]

