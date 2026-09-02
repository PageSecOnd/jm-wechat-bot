from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    api_base_url: str = "https://ilinkai.weixin.qq.com"
    cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c"
    ilink_app_id: str = "bot"
    ilink_protocol_version: str = "2.4.6"
    bot_agent: str = "JMWeixinBot/1.0.0"
    long_poll_timeout_ms: int = 40_000
    request_timeout_s: float = 20.0
    per_account_download_concurrency: int = 2
    jm_option_file: Path | None = None
    search_limit: int = 8
    max_send_mb: int = 500
    cache_ttl_days: int = 7
    cache_max_gb: float = 20.0
    cache_min_free_percent: float = 20.0
    cache_cleanup_interval_hours: float = 6.0
    daily_signin_hour: int = 8
    daily_signin_check_interval_minutes: int = 30
    daily_signin_max_attempts: int = 3
    admin_token: str = ""
    admin_host: str = "0.0.0.0"
    admin_port: int = 8787
    admin_secure_cookie: bool = False
    admin_timezone: str = "Asia/Shanghai"

    @classmethod
    def from_env(cls) -> "Settings":
        option = os.getenv("JMWXBOT_JM_OPTION_FILE", "").strip()
        return cls(
            data_dir=Path(os.getenv("JMWXBOT_DATA_DIR", "./data")).expanduser().resolve(),
            api_base_url=os.getenv("JMWXBOT_API_BASE_URL", "https://ilinkai.weixin.qq.com").rstrip("/"),
            cdn_base_url=os.getenv("JMWXBOT_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c").rstrip("/"),
            ilink_app_id=os.getenv("JMWXBOT_ILINK_APP_ID", "bot"),
            ilink_protocol_version=os.getenv("JMWXBOT_ILINK_PROTOCOL_VERSION", "2.4.6"),
            bot_agent=os.getenv("JMWXBOT_BOT_AGENT", "JMWeixinBot/1.0.0"),
            long_poll_timeout_ms=_env_int("JMWXBOT_LONG_POLL_TIMEOUT_MS", 40_000),
            request_timeout_s=_env_float("JMWXBOT_REQUEST_TIMEOUT_S", 20.0),
            per_account_download_concurrency=max(1, _env_int("JMWXBOT_DOWNLOAD_CONCURRENCY", 2)),
            jm_option_file=Path(option).expanduser().resolve() if option else None,
            search_limit=max(1, min(20, _env_int("JMWXBOT_SEARCH_LIMIT", 8))),
            max_send_mb=max(0, _env_int("JMWXBOT_MAX_SEND_MB", 500)),
            cache_ttl_days=max(0, _env_int("JMWXBOT_CACHE_TTL_DAYS", 7)),
            cache_max_gb=max(0.0, _env_float("JMWXBOT_CACHE_MAX_GB", 20.0)),
            cache_min_free_percent=max(0.0, min(99.0, _env_float("JMWXBOT_CACHE_MIN_FREE_PERCENT", 20.0))),
            cache_cleanup_interval_hours=max(0.25, _env_float("JMWXBOT_CACHE_CLEANUP_INTERVAL_HOURS", 6.0)),
            daily_signin_hour=max(0, min(23, _env_int("JMWXBOT_DAILY_SIGNIN_HOUR", 8))),
            daily_signin_check_interval_minutes=max(5, _env_int("JMWXBOT_DAILY_SIGNIN_CHECK_INTERVAL_MINUTES", 30)),
            daily_signin_max_attempts=max(1, min(10, _env_int("JMWXBOT_DAILY_SIGNIN_MAX_ATTEMPTS", 3))),
            admin_token=os.getenv("JMWXBOT_ADMIN_TOKEN", "").strip(),
            admin_host=os.getenv("JMWXBOT_ADMIN_HOST", "0.0.0.0").strip() or "0.0.0.0",
            admin_port=max(1, min(65535, _env_int("JMWXBOT_ADMIN_PORT", 8787))),
            admin_secure_cookie=_env_bool("JMWXBOT_ADMIN_SECURE_COOKIE", False),
            admin_timezone=os.getenv("JMWXBOT_ADMIN_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.sqlite3"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def jm_profiles_dir(self) -> Path:
        return self.data_dir / "jm_profiles"
