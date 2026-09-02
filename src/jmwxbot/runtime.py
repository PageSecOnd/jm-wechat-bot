from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .cache import cache_stats
from .ilink import ITEM_TEXT, MESSAGE_TYPE_USER, ILinkClient, StaleAccountTokenError
from .models import (
    Account,
    ComicInfo,
    CommentEntry,
    CommentResults,
    DownloadJob,
    FavoriteResults,
    InboundMessage,
    JmDailyResult,
    SearchResults,
)
from .markup import md_bold, md_code, md_code_block, md_safe
from .parser import parse_command
from .provider import JmComicProvider, ProviderError
from .settings import Settings
from .store import Store
from .util import peer_jm_profile, peer_workspace

log = logging.getLogger(__name__)

FORMAT_LABELS = {"pdf": "PDF", "zip": "ZIP", "long": "长图"}
CATEGORY_HELP = (
    "**可用分类**\n"
    "`全部` / `同人` / `单本` / `短篇` / `其他` / `韩漫` / `美漫` / `Cosplay` / `3D` / `英文`"
)
HELP_TEXT = (
    "**JM 微信机器人**\n\n"
    "直接发送 JM 号，默认导出 PDF。\n\n"
    + md_code_block("JM123456\n/pdf 123456\n/zip 123456\n/long 123456")
    + "\n\n---\n\n**查找与浏览**\n"
    "`/search 关键词 [--sort likes|views|latest|pages] [--page 2]` 搜索（默认按爱心）\n"
    "`/rank [day|week|month] [--page 2]` 排行\n"
    "`/category 分类 [--page 2]` 分类\n"
    "`/comments 123456 [--page 2]` 评论\n\n"
    "**JM 账户**\n"
    "`/login 用户名 密码` 登录，并自动启用每日签到\n"
    "`/daily` 立即检查/执行今日签到\n"
    "`/logout` 清除本地登录态并停止自动签到\n"
    "`/fav [收藏夹ID] [--page 2]` 收藏夹\n"
    "`/fav-add 123456` 加入收藏（`/collect` 同义）\n\n"
    "**任务与缓存**\n"
    "`/status` 当前下载状态\n"
    "`/cancel [JM号]` 取消任务\n"
    "`/history [数量]` 历史\n"
    "`/cache` 缓存占用\n"
    "`/profile` 登录与自动签到状态\n"
    "`/help` 帮助\n\n"
    + CATEGORY_HELP
)


def _join(values: tuple[str, ...], limit: int = 12, sep: str = " / ") -> str:
    if not values:
        return "-"
    shown = values[:limit]
    text = sep.join(md_safe(x) for x in shown)
    if len(values) > limit:
        text += f" 等 {len(values)} 项"
    return text


def format_comic_info(info: ComicInfo, max_chars: int = 1800) -> str:
    title = f"JM{info.jm_id}｜《{md_safe(info.title or '-')}》"
    lines = [
        md_bold(title),
        "",
        f"作者：{_join(info.authors)}",
        f"页数：{info.page_count or '-'} · 章节：{info.chapter_count or '-'}",
        f"标签：{_join(info.tags, 16)}",
    ]
    stats = []
    if info.views:
        stats.append(f"浏览：{md_safe(info.views)}")
    if info.likes:
        stats.append(f"喜欢：{md_safe(info.likes)}")
    if info.comment_count:
        stats.append(f"评论：{info.comment_count}")
    if stats:
        lines.append(" · ".join(stats))
    if info.works:
        lines.append(f"作品：{_join(info.works, 8)}")
    if info.actors:
        lines.append(f"角色：{_join(info.actors, 8)}")
    dates = []
    if info.pub_date:
        dates.append(f"上架：{md_safe(info.pub_date)}")
    if info.update_date:
        dates.append(f"更新：{md_safe(info.update_date)}")
    if dates:
        lines.append(" · ".join(dates))
    if info.description:
        desc = md_safe(" ".join(info.description.split()))
        lines.extend(["", "**简介**", desc])

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _format_result_list(results: SearchResults, header: str, max_chars: int = 1800) -> str:
    if not results.items:
        return f"{md_bold(header)}\n\n没有结果。"
    page_part = f"第 {results.page} 页"
    if results.page_count:
        page_part += f" / 共 {results.page_count} 页"
    total_part = f"，共 {results.total} 项" if results.total else ""
    lines = [md_bold(header), f"{page_part}{total_part}", ""]
    for idx, item in enumerate(results.items, 1):
        lines.append(f"{idx}. {md_code('JM' + item.jm_id)}｜{md_safe(item.title)}")
        if item.tags:
            lines.append(f"   标签：{_join(item.tags, 6)}")
    lines.extend(["", f"发送 {md_code('JM号')} 下载 PDF；用 {md_code('/zip')}、{md_code('/long')} 切换格式。"])
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def format_search_results(results: SearchResults, max_chars: int = 1800) -> str:
    if not results.items:
        return f"{md_bold('搜索：' + md_safe(results.query))}\n\n没有结果。"

    page_part = f"第 {results.page} 页"
    if results.page_count:
        page_part += f" / 共 {results.page_count} 页"
    total_part = f" · 共 {results.total} 项" if results.total else ""
    sort_part = f" · 排序：{md_safe(results.sort_label)}" if results.sort_label else ""
    lines = [md_bold(f"搜索：{md_safe(results.query)}"), f"{page_part}{total_part}{sort_part}", ""]

    for idx, item in enumerate(results.items, 1):
        lines.append(f"{idx}. {md_code('JM' + item.jm_id)}｜{md_safe(item.title)}")
        if item.authors:
            lines.append(f"   作者：{_join(item.authors, 3)}")

        stats: list[str] = []
        if item.views:
            stats.append(f"阅读 {md_safe(item.views)}")
        if item.likes:
            stats.append(f"爱心 {md_safe(item.likes)}")
        if item.comment_count is not None:
            stats.append(f"评论 {item.comment_count}")
        if stats:
            lines.append("   " + " · ".join(stats))

        structure: list[str] = []
        if item.page_count is not None:
            structure.append(f"{item.page_count} 页")
        if item.chapter_count is not None:
            structure.append(f"{item.chapter_count} 章节")
        if structure:
            lines.append("   " + " · ".join(structure))
        if item.tags:
            lines.append(f"   标签：{_join(item.tags, 4)}")
        lines.append("")

    lines.extend([
        f"排序：{md_code('--sort likes')} 爱心 · {md_code('--sort views')} 阅读 · {md_code('--sort latest')} 最新 · {md_code('--sort pages')} 页数",
        f"翻页：在原命令后加 {md_code('--page 2')}；发送 {md_code('JM号')} 下载 PDF。",
    ])
    text = "\n".join(lines).rstrip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def format_browse_results(results: SearchResults, max_chars: int = 1800) -> str:
    return _format_result_list(results, md_safe(results.query), max_chars)


def format_favorites(results: FavoriteResults, max_chars: int = 1800) -> str:
    page_part = f"第 {results.page} 页"
    if results.page_count:
        page_part += f" / 共 {results.page_count} 页"
    lines = [md_bold(f"收藏夹 {results.folder_id}"), f"{page_part} · 共 {results.total} 项", ""]
    if results.items:
        for idx, item in enumerate(results.items, 1):
            lines.append(f"{idx}. {md_code('JM' + item.jm_id)}｜{md_safe(item.title)}")
            if item.tags:
                lines.append(f"   标签：{_join(item.tags, 6)}")
    else:
        lines.append("当前页没有内容。")
    if results.folders:
        lines.extend(["", "**收藏夹目录**"])
        for fid, name in results.folders[:12]:
            lines.append(f"{md_code(fid)}｜{md_safe(name)}")
        lines.append(f"使用 {md_code('/fav 收藏夹ID --page 页码')} 查看。")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _append_comment(lines: list[str], item: CommentEntry, indent: str = "", max_reply_depth: int = 2) -> None:
    meta = md_safe(item.author or "匿名")
    if item.spoiler:
        meta += " [剧透]"
    if item.likes is not None:
        meta += f" · {item.likes}赞"
    if item.created_at:
        meta += f" · {md_safe(item.created_at)}"
    lines.append(f"{indent}**{meta}**")
    content = md_safe(" ".join((item.content or "").split()) or "（无文字内容）")
    lines.append(f"{indent}{content}")
    if max_reply_depth > 0:
        for reply in item.replies[:3]:
            _append_comment(lines, reply, indent + "  ↳ ", max_reply_depth - 1)


def format_comments(results: CommentResults, max_chars: int = 1800) -> str:
    page_part = f"第 {results.page} 页"
    if results.page_count:
        page_part += f" / 共 {results.page_count} 页"
    total_part = f" · 共 {results.total} 条主评论" if results.total is not None else ""
    lines = [md_bold(f"JM{results.jm_id} 评论"), f"{page_part}{total_part}", ""]
    if not results.items:
        lines.append("当前页暂无评论。")
    else:
        for idx, item in enumerate(results.items, 1):
            lines.append(f"{idx}.")
            _append_comment(lines, item)
            lines.append("")
    text = "\n".join(lines).rstrip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def format_daily(result: JmDailyResult, max_chars: int = 1800) -> str:
    state = "今日已签到" if result.already_signed else (result.message or "签到完成")
    lines = [md_bold("每日签到"), "", f"状态：{md_safe(state)}"]
    st = result.status
    if st.event_name:
        lines.append(f"活动：{md_safe(st.event_name)}")
    if st.current_progress:
        lines.append(f"进度：{md_safe(st.current_progress)}")
    if st.three_days_coin or st.three_days_exp:
        lines.append(f"3 日累计：金币 {md_safe(st.three_days_coin or '-')} · 经验 {md_safe(st.three_days_exp or '-')}")
    if st.seven_days_coin or st.seven_days_exp:
        lines.append(f"7 日累计：金币 {md_safe(st.seven_days_coin or '-')} · 经验 {md_safe(st.seven_days_exp or '-')}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def human_size(size: int) -> str:
    n = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{size} B"


def beijing_now() -> datetime:
    # JM's daily boundary is treated as China Standard Time for this bot.
    return datetime.now(timezone(timedelta(hours=8), name="CST"))


class PeerJobQueue:
    def __init__(self):
        self._items: deque[DownloadJob] = deque()
        self._condition = asyncio.Condition()

    def qsize(self) -> int:
        return len(self._items)

    async def put(self, job: DownloadJob) -> None:
        async with self._condition:
            self._items.append(job)
            self._condition.notify(1)

    async def get(self) -> DownloadJob:
        async with self._condition:
            while not self._items:
                await self._condition.wait()
            return self._items.popleft()

    async def snapshot(self) -> list[DownloadJob]:
        async with self._condition:
            return list(self._items)

    async def remove(self, predicate: Callable[[DownloadJob], bool]) -> list[DownloadJob]:
        async with self._condition:
            removed: list[DownloadJob] = []
            kept: deque[DownloadJob] = deque()
            while self._items:
                item = self._items.popleft()
                if predicate(item):
                    removed.append(item)
                else:
                    kept.append(item)
            self._items = kept
            return removed


@dataclass(slots=True)
class ActiveState:
    job: DownloadJob
    stage: str = "准备中"
    done: int = 0
    total: int = 0
    started_at: float = 0.0


class AccountRuntime:
    def __init__(self, settings: Settings, store: Store, account: Account, client: ILinkClient):
        self.settings = settings
        self.store = store
        self.account = account
        self.client = client
        self.provider = JmComicProvider(settings)
        self._queues: dict[str, PeerJobQueue] = {}
        self._peer_tasks: dict[str, asyncio.Task] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._active: dict[str, ActiveState] = {}
        self._pending_keys: dict[str, set[tuple[str, str]]] = {}
        self._download_slots = asyncio.Semaphore(settings.per_account_download_concurrency)
        self._stop = asyncio.Event()

    def admin_snapshot(self) -> dict[str, Any]:
        """Return a read-only, secret-free live snapshot for the admin console."""
        queued = sum(q.qsize() for q in self._queues.values())
        active_items = []
        for peer_id, state in list(self._active.items()):
            active_items.append({
                "peer_id": peer_id,
                "jm_id": state.job.jm_id,
                "format": state.job.export_format,
                "stage": state.stage,
                "done": state.done,
                "total": state.total,
                "started_at": state.started_at,
            })
        return {
            "account_id": self.account.account_id,
            "active_count": len(active_items),
            "queued_count": queued,
            "peer_workers": len(self._peer_tasks),
            "active_items": active_items,
        }

    async def run(self) -> None:
        log.info("account started: %s (%s)", self.account.name or "unnamed", self.account.account_id)
        await self.client.notify_start()
        cursor = self.account.sync_buf or ""
        try:
            while not self._stop.is_set():
                try:
                    resp = await self.client.get_updates(cursor)
                except StaleAccountTokenError:
                    log.error("account token stale, re-login required: %s", self.account.account_id)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("getupdates failed for %s: %s", self.account.account_id, exc)
                    await asyncio.sleep(3)
                    continue

                new_cursor = resp.get("get_updates_buf")
                if isinstance(new_cursor, str):
                    cursor = new_cursor
                    self.store.update_sync_buf(self.account.account_id, cursor)

                for raw in resp.get("msgs") or []:
                    try:
                        msg = self._parse_inbound(raw)
                        if msg is not None:
                            await self._dispatch(msg)
                    except Exception:
                        log.exception("failed to process inbound on %s", self.account.account_id)
        finally:
            await self.client.notify_stop()
            for task in list(self._active_tasks.values()) + list(self._peer_tasks.values()):
                task.cancel()
            await asyncio.gather(*self._active_tasks.values(), *self._peer_tasks.values(), return_exceptions=True)

    def _parse_inbound(self, raw: dict[str, Any]) -> InboundMessage | None:
        if raw.get("message_type") != MESSAGE_TYPE_USER:
            return None
        peer_id = str(raw.get("from_user_id") or "")
        if not peer_id:
            return None
        context = str(raw.get("context_token") or "")
        if context:
            self.store.update_peer_context(self.account.account_id, peer_id, context)

        texts: list[str] = []
        for item in raw.get("item_list") or []:
            if item.get("type") == ITEM_TEXT:
                text = (item.get("text_item") or {}).get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        if not texts:
            return None
        text = "\n".join(texts)

        raw_id = raw.get("message_id") or raw.get("client_id")
        if raw_id is None:
            material = json.dumps([peer_id, raw.get("create_time_ms"), text], ensure_ascii=False, separators=(",", ":"))
            raw_id = hashlib.sha256(material.encode()).hexdigest()
        message_id = str(raw_id)
        if not self.store.claim_message(self.account.account_id, message_id):
            return None
        return InboundMessage(
            account_id=self.account.account_id,
            message_id=message_id,
            from_user_id=peer_id,
            context_token=context,
            text=text,
            raw=raw,
        )

    def _context(self, peer_id: str, fallback: str = "") -> str:
        return self.store.get_peer_context(self.account.account_id, peer_id) or fallback

    async def _dispatch(self, msg: InboundMessage) -> None:
        cmd = parse_command(msg.text)
        if cmd is None:
            return
        peer = msg.from_user_id
        event_type = f"download_{cmd.arg or 'pdf'}" if cmd.kind == "download" else cmd.kind
        self.store.record_usage(self.account.account_id, peer, event_type)
        context = self._context(peer, msg.context_token)

        if cmd.kind == "help":
            await self.client.send_text(peer, context, HELP_TEXT)
            return
        if cmd.kind == "status":
            await self.client.send_text(peer, context, await self._status_text(peer))
            return
        if cmd.kind == "cache":
            workspace = peer_workspace(self.settings.workspaces_dir, self.account.account_id, peer)
            stats = await asyncio.to_thread(cache_stats, workspace)
            text = (
                f"{md_bold('个人缓存')}\n\n"
                f"文件：{stats.files} 个\n"
                f"占用：{human_size(stats.bytes)}\n"
                f"自动清理：{self.settings.cache_ttl_days} 天\n"
                f"发送上限：" + (f"{self.settings.max_send_mb} MB" if self.settings.max_send_mb else "未启用")
            )
            await self.client.send_text(peer, context, text)
            return
        if cmd.kind == "profile":
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            rel = profile.relative_to(self.settings.data_dir)
            state = self.store.get_jm_state(self.account.account_id, peer)
            lines = [md_bold("JM Profile"), ""]
            profile_current = self.provider.auth_profile_is_current(profile)
            verified = bool(state and state.get("login_verified") and profile_current)
            if profile.is_file() and verified:
                lines.append("登录状态：已验证")
            elif profile.is_file():
                lines.append("登录状态：需要重新登录")
            else:
                lines.append("登录状态：未登录")
            if state and state.get("username"):
                lines.append(f"账号：{md_code(state['username'])}")
            if state and state.get("uid"):
                lines.append(f"UID：{md_code(state['uid'])}")
            if profile.is_file() and verified:
                lines.append(f"自动签到：已启用 · 每天 {self.settings.daily_signin_hour:02d}:00 后检查（北京时间）")
                if state.get("last_daily_date"):
                    lines.append(
                        f"最近签到：{state['last_daily_date']} · {md_safe(state.get('last_daily_result') or '-')}"
                    )
            elif profile.is_file():
                lines.append("认证升级：旧 profile 未保存完整 Cookie/登录域名，请重新执行一次 `/login 用户名 密码`。")
            lines.extend(["", "独立配置：", md_code_block("/data/" + rel.as_posix())])
            await self.client.send_text(peer, context, "\n".join(lines)[:1800])
            return
        if cmd.kind == "login":
            if not cmd.value or cmd.arg is None:
                await self.client.send_text(
                    peer,
                    context,
                    f"{md_bold('JM 登录')}\n\n用法：{md_code('/login JM用户名 JM密码')}\n"
                    "密码不会落盘；但这条命令本身会留在你的微信聊天记录里。",
                )
                return
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            try:
                result = await self.provider.login(cmd.value, cmd.arg, option_file=profile)
                self.store.save_jm_login(self.account.account_id, peer, result)
                details = [md_bold("JM 登录成功"), "", f"账号：{md_code(result.username)}", f"UID：{md_code(result.uid)}"]
                if result.display_name and result.display_name != result.username:
                    details.append(f"昵称：{md_safe(result.display_name)}")
                if result.level_name or result.level is not None:
                    details.append(f"等级：{md_safe(result.level_name or ('Lv.' + str(result.level)))}")
                if result.coin is not None:
                    details.append(f"金币：{result.coin}")
                if result.favorite_count is not None:
                    details.append(f"收藏：{result.favorite_count}")
                details.extend([
                    "",
                    f"自动签到：已启用 · 每天 {self.settings.daily_signin_hour:02d}:00 后检查（北京时间）",
                    "完整 JM 会话 Cookie 与认证域名已保存到你的独立配置；密码未保存。",
                    "正在检查今天的签到状态…",
                ])
                await self.client.send_text(peer, self._context(peer, context), "\n".join(details))
                await self._daily_for_peer(peer, self._context(peer, context), automatic=False)
            except ProviderError as exc:
                await self.client.send_text(peer, self._context(peer, context), f"{md_bold('JM 登录失败')}\n\n{md_safe(str(exc))[:1400]}")
            return
        if cmd.kind == "logout":
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            existed = profile.is_file() or self.store.get_jm_state(self.account.account_id, peer) is not None
            try:
                profile.unlink(missing_ok=True)
                self.store.clear_jm_state(self.account.account_id, peer)
            except OSError as exc:
                await self.client.send_text(peer, context, f"{md_bold('清除失败')}\n\n{md_safe(exc)}")
                return
            await self.client.send_text(
                peer,
                context,
                f"{md_bold('JM 登录态已清除')}\n\n自动签到已停止。" if existed else "当前没有已保存的 JM 登录态。",
            )
            return
        if cmd.kind == "daily":
            await self._daily_for_peer(peer, context, automatic=False)
            return
        if cmd.kind == "history":
            rows = self.store.list_history(self.account.account_id, peer, int(cmd.value or "10"))
            await self.client.send_text(peer, context, self._format_history(rows))
            return
        if cmd.kind == "search_sort_help":
            await self.client.send_text(
                peer,
                context,
                "**搜索排序**\n\n"
                + f"{md_code('likes')} 爱心（默认）\n"
                + f"{md_code('views')} 阅读量\n"
                + f"{md_code('latest')} 最新\n"
                + f"{md_code('pages')} 页数\n\n"
                + f"例如：{md_code('/search 甘雨 --sort views --page 2')}",
            )
            return
        if cmd.kind == "search" and cmd.value:
            await self._provider_reply(
                peer,
                context,
                self.provider.search(
                    cmd.value,
                    int(cmd.arg or "1"),
                    self.settings.search_limit,
                    cmd.option or "likes",
                ),
                format_search_results,
            )
            return
        if cmd.kind == "rank" and cmd.value:
            await self._provider_reply(peer, context, self.provider.ranking(cmd.value, int(cmd.arg or "1"), self.settings.search_limit), format_browse_results)
            return
        if cmd.kind == "category":
            if not cmd.value:
                await self.client.send_text(peer, context, CATEGORY_HELP)
                return
            await self._provider_reply(peer, context, self.provider.category(cmd.value, int(cmd.arg or "1"), self.settings.search_limit), format_browse_results)
            return
        if cmd.kind == "comments" and cmd.value:
            await self._provider_reply(peer, context, self.provider.comments(cmd.value, int(cmd.arg or "1")), format_comments)
            return
        if cmd.kind == "fav":
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            state = self.store.get_jm_state(self.account.account_id, peer)
            if not profile.is_file() or not state or not state.get("login_verified"):
                await self.client.send_text(
                    peer,
                    context,
                    f"JM 登录态尚未通过安全验证。请重新使用 {md_code('/login 用户名 密码')} 登录后再查看收藏。",
                )
                return
            await self._provider_reply(
                peer,
                context,
                self.provider.favorites(
                    cmd.value or "0",
                    int(cmd.arg or "1"),
                    self.settings.search_limit,
                    option_file=profile,
                ),
                format_favorites,
            )
            return
        if cmd.kind == "fav_add" and cmd.value:
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            state = self.store.get_jm_state(self.account.account_id, peer)
            if not profile.is_file() or not state or not state.get("login_verified"):
                await self.client.send_text(
                    peer,
                    context,
                    f"JM 登录态尚未通过安全验证。请重新使用 {md_code('/login 用户名 密码')} 登录后再收藏。",
                )
                return
            try:
                added = await self.provider.add_favorite(cmd.value, option_file=profile)
                title = "收藏成功" if added else "已在收藏夹"
                detail = "已加入" if added else "无需重复收藏"
                await self.client.send_text(
                    peer,
                    self._context(peer, context),
                    f"{md_bold(title)}\n\n{detail}：{md_code('JM' + cmd.value)}\n使用 {md_code('/fav')} 查看收藏夹。",
                )
            except ProviderError as exc:
                await self.client.send_text(peer, self._context(peer, context), md_safe(str(exc))[:1500])
            return
        if cmd.kind == "cancel":
            await self._cancel(peer, context, cmd.value)
            return
        if cmd.kind == "download" and cmd.value:
            await self._enqueue(peer, context, cmd.value, cmd.arg or "pdf")

    async def _daily_for_peer(self, peer: str, context: str, *, automatic: bool) -> bool:
        state = self.store.get_jm_state(self.account.account_id, peer)
        profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
        if (
            not state
            or not state.get("uid")
            or not profile.is_file()
            or not state.get("login_verified")
            or not self.provider.auth_profile_is_current(profile)
        ):
            if not automatic:
                await self.client.send_text(
                    peer,
                    context,
                    f"JM 登录态尚未通过安全验证。请重新使用 {md_code('/login 用户名 密码')} 登录；验证后会自动启用每日签到。",
                )
            return False
        today = beijing_now().date().isoformat()
        try:
            result = await self.provider.daily_checkin(str(state["uid"]), option_file=profile)
            result_code = "already" if result.already_signed else "signed"
            self.store.record_daily_attempt(self.account.account_id, peer, today, result_code, result.message)
            if automatic:
                log.info(
                    "JM daily sign-in success account=%s peer=%s result=%s",
                    self.account.account_id, peer, result_code,
                )
            else:
                await self.client.send_text(peer, self._context(peer, context), format_daily(result))
            return True
        except ProviderError as exc:
            self.store.record_daily_attempt(self.account.account_id, peer, today, "failed", str(exc))
            if automatic:
                log.warning("JM daily sign-in failed account=%s peer=%s: %s", self.account.account_id, peer, exc)
            else:
                await self.client.send_text(peer, context, f"{md_bold('签到失败')}\n\n{md_safe(str(exc))[:1400]}")
            return False

    async def run_daily_signin_once(self) -> None:
        now = beijing_now()
        if now.hour < self.settings.daily_signin_hour:
            return
        today = now.date().isoformat()
        for state in self.store.list_jm_signin_users(self.account.account_id):
            if state.get("last_daily_date") == today:
                if state.get("last_daily_result") in {"signed", "already"}:
                    continue
                if int(state.get("daily_attempt_count") or 0) >= self.settings.daily_signin_max_attempts:
                    continue
            peer = str(state["peer_id"])
            profile = peer_jm_profile(self.settings.jm_profiles_dir, self.account.account_id, peer)
            if not profile.is_file():
                log.info("skip JM daily sign-in: profile missing account=%s peer=%s", self.account.account_id, peer)
                continue
            # Automatic sign-in intentionally sends no WeChat notification: a cached
            # context_token may be stale, and the sign-in should not depend on chat activity.
            await self._daily_for_peer(peer, "", automatic=True)
            await asyncio.sleep(0.5)

    async def _provider_reply(self, peer: str, context: str, awaitable, formatter) -> None:
        try:
            result = await awaitable
            await self.client.send_text(peer, self._context(peer, context), formatter(result))
        except ProviderError as exc:
            await self.client.send_text(peer, self._context(peer, context), md_safe(str(exc))[:1500])

    async def _ensure_peer_queue(self, peer: str) -> PeerJobQueue:
        q = self._queues.get(peer)
        if q is None:
            q = PeerJobQueue()
            self._queues[peer] = q
            self._peer_tasks[peer] = asyncio.create_task(self._peer_worker(peer, q), name=f"peer-{self.account.account_id}-{peer}")
        return q

    async def _enqueue(self, peer: str, context: str, jm_id: str, export_format: str) -> None:
        export_format = export_format.lower()
        if export_format not in self.provider.SUPPORTED_FORMATS:
            await self.client.send_text(peer, context, f"不支持的格式：{md_code(export_format)}")
            return
        key = (export_format, jm_id)
        pending = self._pending_keys.setdefault(peer, set())
        if key in pending:
            await self.client.send_text(peer, context, f"{md_code('JM' + jm_id)} 的 {FORMAT_LABELS[export_format]} 任务已经在处理中或队列中。")
            return

        q = await self._ensure_peer_queue(peer)
        active_count = 1 if peer in self._active else 0
        position = q.qsize() + active_count + 1
        job = DownloadJob(jm_id, peer, context, export_format)
        pending.add(key)
        await q.put(job)
        suffix = f"（当前第 {position} 个）" if position > 1 else ""
        await self.client.send_text(peer, context, f"收到 {md_code('JM' + jm_id)}，格式：**{FORMAT_LABELS[export_format]}**{suffix}。正在查询本子详情…")

    async def _cancel(self, peer: str, context: str, jm_id: str | None) -> None:
        cancelled: list[str] = []
        active = self._active.get(peer)
        task = self._active_tasks.get(peer)
        if active and (jm_id is None or active.job.jm_id == jm_id):
            if task and not task.done():
                task.cancel()
                cancelled.append(f"当前 JM{active.job.jm_id} {FORMAT_LABELS[active.job.export_format]} 任务")

        q = self._queues.get(peer)
        if q is not None and jm_id is not None:
            removed = await q.remove(lambda j: j.jm_id == jm_id)
            for job in removed:
                self._pending_keys.setdefault(peer, set()).discard(job.key)
            if removed:
                cancelled.append(f"队列中的 {len(removed)} 个 JM{jm_id} 任务")
        elif q is not None and jm_id is None and not cancelled:
            snap = await q.snapshot()
            if snap:
                target = snap[0]
                removed = await q.remove(lambda j: j is target)
                for job in removed:
                    self._pending_keys.setdefault(peer, set()).discard(job.key)
                if removed:
                    cancelled.append(f"队列中的 JM{target.jm_id} {FORMAT_LABELS[target.export_format]} 任务")

        if cancelled:
            await self.client.send_text(
                peer,
                self._context(peer, context),
                f"{md_bold('已请求取消')}\n\n" + "、".join(md_safe(x) for x in cancelled) + "。",
            )
        else:
            target = f" JM{jm_id}" if jm_id else ""
            await self.client.send_text(peer, context, f"没有找到可取消的{target}任务。")

    async def _status_text(self, peer: str) -> str:
        lines = [md_bold("下载状态")]
        state = self._active.get(peer)
        if state:
            elapsed = max(0, int(time.monotonic() - state.started_at))
            progress = ""
            if state.total > 0:
                pct = min(100, int(state.done * 100 / state.total))
                progress = f" · {state.done}/{state.total}（{pct}%）"
            lines.append(
                f"当前：{md_code('JM' + state.job.jm_id)} · **{FORMAT_LABELS[state.job.export_format]}** · "
                f"{md_safe(state.stage)}{progress} · {elapsed}s"
            )
        else:
            lines.append("当前：空闲")
        q = self._queues.get(peer)
        queued = await q.snapshot() if q else []
        if queued:
            lines.append("等待：" + "，".join(f"{md_code('JM' + x.jm_id)}/{FORMAT_LABELS[x.export_format]}" for x in queued[:8]))
            if len(queued) > 8:
                lines.append(f"另有 {len(queued) - 8} 个任务")
        else:
            lines.append("等待：0 个")
        lines.extend(["", "机器人不会主动刷下载进度；这里只在你查询时显示。"] )
        return "\n".join(lines)

    def _format_history(self, rows: list[dict]) -> str:
        if not rows:
            return f"{md_bold('任务历史')}\n\n暂无记录。"
        status_map = {"sent": "成功", "failed": "失败", "cancelled": "已取消", "too_large": "文件过大"}
        lines = [md_bold(f"最近 {len(rows)} 条任务"), ""]
        for row in rows:
            status = status_map.get(str(row.get("status")), str(row.get("status")))
            fmt = FORMAT_LABELS.get(str(row.get("export_format")), str(row.get("export_format")).upper())
            extra = []
            if row.get("file_size"):
                extra.append(human_size(int(row["file_size"])))
            if row.get("duration") is not None:
                extra.append(f"{float(row['duration']):.1f}s")
            tail = f" · {' · '.join(extra)}" if extra else ""
            title = md_safe(str(row.get("title") or "").strip())
            title_part = f"｜{title}" if title else ""
            lines.append(f"{md_code('JM' + str(row['jm_id']))} · {fmt} · **{md_safe(status)}**{tail}{title_part}")
        return "\n".join(lines)[:1800]

    async def _peer_worker(self, peer_id: str, queue: PeerJobQueue) -> None:
        while True:
            job = await queue.get()
            task = asyncio.create_task(self._process_job(job), name=f"job-{self.account.account_id}-{peer_id}-{job.jm_id}")
            self._active_tasks[peer_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
            finally:
                self._active_tasks.pop(peer_id, None)
                self._active.pop(peer_id, None)
                self._pending_keys.setdefault(peer_id, set()).discard(job.key)

    async def _process_job(self, job: DownloadJob) -> None:
        peer_id = job.peer_id
        context = self._context(peer_id, job.context_token)
        state = ActiveState(job=job, stage="查询详情", started_at=time.monotonic())
        self._active[peer_id] = state
        history_id = self.store.start_history(self.account.account_id, peer_id, job.jm_id, job.export_format)
        title: str | None = None
        try:
            async with self._download_slots:
                info = await self.provider.fetch_info(job.jm_id)
                title = info.title
                workspace = peer_workspace(self.settings.workspaces_dir, self.account.account_id, peer_id)

                try:
                    cover = await self.provider.fetch_cover(job.jm_id, workspace)
                    await self.client.send_image(peer_id, self._context(peer_id, context), cover)
                except Exception as exc:
                    log.warning("cover skipped account=%s peer=%s jm=%s: %s", self.account.account_id, peer_id, job.jm_id, exc)

                context = self._context(peer_id, context)
                await self.client.send_text(peer_id, context, format_comic_info(info))

                cached = self.provider.cached_export(workspace, job.jm_id, job.export_format)
                if cached.is_file() and cached.stat().st_size > 0:
                    state.stage = "读取缓存"
                    await self.client.send_text(peer_id, context, f"**{FORMAT_LABELS[job.export_format]}** 已有缓存，正在发送…")
                    path, duration = cached, 0.0
                    cached.touch()
                else:
                    await self.client.send_text(peer_id, context, f"详情获取成功，正在下载并生成 **{FORMAT_LABELS[job.export_format]}**…")

                    async def progress(stage: str, done: int, total: int) -> None:
                        # Intentionally no proactive progress messages. This only feeds /status.
                        state.done = done
                        state.total = total
                        if stage == "downloading":
                            state.stage = "下载中"
                        elif stage == "exporting":
                            state.stage = "生成文件"
                        elif stage == "exported":
                            state.stage = "生成完成"

                    path, duration = await self.provider.fetch_export(job.jm_id, workspace, job.export_format, progress=progress)

                size = path.stat().st_size
                if self.settings.max_send_mb > 0 and size > self.settings.max_send_mb * 1024**2:
                    state.stage = "文件过大"
                    self.store.finish_history(
                        history_id,
                        status="too_large",
                        title=title,
                        file_size=size,
                        duration=duration,
                        message="超过微信发送上限配置",
                    )
                    await self.client.send_text(
                        peer_id,
                        self._context(peer_id, context),
                        f"文件已生成并缓存，但大小为 {human_size(size)}，超过当前发送上限 {self.settings.max_send_mb} MB，因此没有上传微信。",
                    )
                    return

                state.stage = "上传微信"
                await self.client.send_file(peer_id, self._context(peer_id, context), path)
                state.stage = "完成"
                self.store.finish_history(history_id, status="sent", title=title, file_size=size, duration=duration)
                await self.client.send_text(
                    peer_id,
                    self._context(peer_id, context),
                    f"{md_bold('发送完成')}\n\n{md_code('JM' + job.jm_id)} · {FORMAT_LABELS[job.export_format]} · {human_size(size)}",
                )
        except asyncio.CancelledError:
            self.store.finish_history(history_id, status="cancelled", title=title, message="用户取消")
            try:
                await self.client.send_text(peer_id, self._context(peer_id, context), f"{md_code('JM' + job.jm_id)} {FORMAT_LABELS[job.export_format]} 任务已取消。")
            except Exception:
                log.exception("failed to send cancellation message")
        except ProviderError as exc:
            self.store.finish_history(history_id, status="failed", title=title, message=str(exc)[:1000])
            try:
                await self.client.send_text(peer_id, self._context(peer_id, context), md_safe(str(exc))[:1500])
            except Exception:
                log.exception("failed to send provider error")
        except Exception as exc:
            log.exception("job failed account=%s peer=%s jm=%s", self.account.account_id, peer_id, job.jm_id)
            self.store.finish_history(history_id, status="failed", title=title, message=str(exc)[:1000])
            try:
                await self.client.send_text(peer_id, self._context(peer_id, context), f"{md_bold('处理失败')}\n\n{md_code('JM' + job.jm_id)}：{md_safe(str(exc))[:800]}")
            except Exception:
                log.exception("failed to send job error")
