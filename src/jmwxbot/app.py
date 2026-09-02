from __future__ import annotations

import asyncio
import logging

from .admin import AdminConsole
from .cache import cleanup_cache
from .ilink import ILinkClient, ILinkTransport
from .runtime import AccountRuntime
from .settings import Settings
from .store import Store

log = logging.getLogger(__name__)


async def _cache_loop(settings: Settings) -> None:
    while True:
        try:
            result = await asyncio.to_thread(cleanup_cache, settings)
            if result.removed_files:
                log.info(
                    "cache cleanup removed %d files (%.1f MiB), remaining %.1f MiB",
                    result.removed_files,
                    result.removed_bytes / 1024**2,
                    result.remaining_bytes / 1024**2,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("periodic cache cleanup failed")
        await asyncio.sleep(settings.cache_cleanup_interval_hours * 3600)


async def _daily_signin_loop(settings: Settings, runtimes: list[AccountRuntime]) -> None:
    while True:
        try:
            for runtime in runtimes:
                await runtime.run_daily_signin_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("periodic JM daily sign-in check failed")
        await asyncio.sleep(settings.daily_signin_check_interval_minutes * 60)


async def run_all(settings: Settings, store: Store) -> None:
    accounts = store.list_accounts()
    if not accounts:
        raise RuntimeError("没有已绑定微信账号。先执行：jmwxbot login")
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    store.prune_processed()
    await asyncio.to_thread(cleanup_cache, settings, grace_seconds=0)

    async with ILinkTransport(settings) as transport:
        runtimes = [
            AccountRuntime(settings, store, account, ILinkClient(transport, settings, account))
            for account in accounts
        ]
        tasks = [asyncio.create_task(rt.run(), name=f"account-{rt.account.account_id}") for rt in runtimes]
        cleanup_task = asyncio.create_task(_cache_loop(settings), name="cache-cleanup")
        daily_task = asyncio.create_task(_daily_signin_loop(settings, runtimes), name="jm-daily-signin")
        admin = AdminConsole(settings, store, runtimes)
        await admin.start()
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        finally:
            await admin.stop()
            cleanup_task.cancel()
            daily_task.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, cleanup_task, daily_task, return_exceptions=True)
