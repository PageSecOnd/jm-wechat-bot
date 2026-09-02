from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys

from .app import run_all
from .cache import cleanup_cache
from .ilink import ILinkLogin, ILinkTransport
from .settings import Settings
from .store import Store
from .util import mask_secret, stable_component


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jmwxbot", description="jm-wechat-bot: multi-account Weixin iLink JMComic bot")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="扫码新增/更新一个微信绑定")
    login.add_argument("--name", help="本地备注名，仅用于区分账号")
    sub.add_parser("accounts", help="列出已绑定账号")
    remove = sub.add_parser("remove", help="移除一个绑定账号")
    remove.add_argument("account_id")
    remove.add_argument("--purge-files", action="store_true", help="同时删除该账号的隔离工作目录")
    sub.add_parser("run", help="并发运行全部已绑定账号")
    sub.add_parser("clean-cache", help="立即执行一次缓存清理")
    return p


async def _login(settings: Settings, store: Store, name: str | None) -> int:
    async with ILinkTransport(settings) as transport:
        account = await ILinkLogin(transport, settings).login(store.recent_tokens(10), name=name)
    if account is None:
        return 0
    replaced = store.save_account(account)
    print(f"绑定成功：{name or '(未命名)'}  account_id={account.account_id}")
    print(f"token={mask_secret(account.bot_token)}（已写入 {settings.db_path}，不会在后续列表中显示）")
    if replaced:
        print("同一微信用户的旧绑定已从状态库移除：" + ", ".join(replaced))
    return 0


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    try:
        if args.command == "login":
            raise SystemExit(asyncio.run(_login(settings, store, args.name)))
        if args.command == "accounts":
            accounts = store.list_accounts()
            if not accounts:
                print("暂无绑定账号")
            for i, a in enumerate(accounts, 1):
                print(f"{i}. {a.name or '(未命名)'}  account_id={a.account_id}  user_id={a.user_id or '-'}  base={a.base_url}")
            return
        if args.command == "remove":
            account = store.get_account(args.account_id)
            if not account:
                print("未找到该 account_id", file=sys.stderr)
                raise SystemExit(2)
            store.delete_account(args.account_id)
            if args.purge_files:
                root = settings.workspaces_dir / stable_component(args.account_id)
                shutil.rmtree(root, ignore_errors=True)
            print("已移除绑定")
            return
        if args.command == "clean-cache":
            result = cleanup_cache(settings, grace_seconds=0)
            print(
                f"缓存清理完成：删除 {result.removed_files} 个文件 / {result.removed_bytes / 1024**2:.1f} MiB；"
                f"剩余 {result.remaining_files} 个文件 / {result.remaining_bytes / 1024**2:.1f} MiB"
            )
            return
        if args.command == "run":
            try:
                asyncio.run(run_all(settings, store))
            except KeyboardInterrupt:
                pass
            return
    finally:
        store.close()


if __name__ == "__main__":
    main()
