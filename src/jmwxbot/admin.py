from __future__ import annotations

import hashlib
import hmac
import html
import logging
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiohttp import web

from . import __version__
from .cache import cache_stats
from .runtime import AccountRuntime, human_size
from .settings import Settings
from .store import Store
from .util import stable_component

log = logging.getLogger(__name__)
_COOKIE = "jmwxbot_admin"
_SESSION_LABEL = b"jmwxbot-admin-session-v1"


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _session_value(token: str) -> str:
    return hmac.new(token.encode("utf-8"), _SESSION_LABEL, hashlib.sha256).hexdigest()


def _fmt_time(value: object, timezone_name: str) -> str:
    if not value:
        return "-"
    raw = str(value)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo(timezone_name))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw[:19].replace("T", " ")


def _status_badge(status: object) -> str:
    text = str(status or "-")
    cls = {
        "sent": "ok",
        "signed": "ok",
        "already_signed": "ok",
        "running": "run",
        "failed": "bad",
        "too_large": "warn",
        "cancelled": "muted",
    }.get(text, "muted")
    return f'<span class="badge {cls}">{_e(text)}</span>'


def _layout(title: str, body: str, *, refresh: bool = True) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{refresh_tag}
<title>{_e(title)} · jm-wechat-bot</title>
<style>
:root{{--bg:#0b0d10;--panel:#12161b;--panel2:#171c22;--line:#252c35;--text:#edf2f7;--muted:#8f9baa;--accent:#67b7ff;--ok:#62d49b;--bad:#ff7474;--warn:#f3c969}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}} .wrap{{max-width:1280px;margin:0 auto;padding:24px}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px}} .brand{{font-size:20px;font-weight:700}} .sub{{color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:18px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}}
.card .k{{color:var(--muted);font-size:12px}} .card .v{{font-size:22px;font-weight:700;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:14px 0;overflow:hidden}} .section h2{{font-size:15px;margin:0;padding:14px 16px;border-bottom:1px solid var(--line)}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted);font-size:12px;font-weight:600;background:var(--panel2)}} tr:last-child td{{border-bottom:0}} code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}}
.badge{{display:inline-block;border:1px solid var(--line);padding:2px 7px;border-radius:999px;font-size:11px}} .ok{{color:var(--ok)}} .bad{{color:var(--bad)}} .warn{{color:var(--warn)}} .run{{color:var(--accent)}} .muted{{color:var(--muted)}}
.note{{color:var(--muted);font-size:12px;padding:12px 16px}} .row{{display:flex;gap:10px;flex-wrap:wrap}} .pill{{background:var(--panel2);border:1px solid var(--line);padding:7px 10px;border-radius:8px}}
.login{{max-width:430px;margin:12vh auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px}} input{{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:11px;margin:10px 0}} button{{background:var(--accent);color:#08111a;border:0;border-radius:8px;padding:10px 14px;font-weight:700;cursor:pointer}}
.err{{color:var(--bad)}} .right{{text-align:right}} .nowrap{{white-space:nowrap}} .truncate{{max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
@media(max-width:1000px){{.grid{{grid-template-columns:repeat(3,1fr)}} .section{{overflow-x:auto}} table{{min-width:850px}}}} @media(max-width:620px){{.wrap{{padding:14px}} .grid{{grid-template-columns:repeat(2,1fr)}} .card .v{{font-size:18px}}}}
</style>
</head><body><div class="wrap">{body}</div></body></html>"""


class AdminConsole:
    def __init__(self, settings: Settings, store: Store, runtimes: list[AccountRuntime]):
        self.settings = settings
        self.store = store
        self.runtimes = {r.account.account_id: r for r in runtimes}
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

        app = web.Application(middlewares=[self._security_headers])
        app.add_routes([
            web.get("/login", self.login_page),
            web.post("/login", self.login_submit),
            web.get("/logout", self.logout),
            web.get("/", self.overview),
            web.get("/account/{account_id}", self.account_detail),
        ])
        self.app = app

    @web.middleware
    async def _security_headers(self, request: web.Request, handler):
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def _authorized(self, request: web.Request) -> bool:
        token = self.settings.admin_token
        if not token:
            return False
        got = request.cookies.get(_COOKIE, "")
        return hmac.compare_digest(got, _session_value(token))

    def _require(self, request: web.Request) -> web.Response | None:
        if self._authorized(request):
            return None
        raise web.HTTPFound("/login")

    async def start(self) -> None:
        if not self.settings.admin_token:
            log.info("admin console disabled (JMWXBOT_ADMIN_TOKEN is empty)")
            return
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.settings.admin_host, self.settings.admin_port)
        await self.site.start()
        log.info("admin console listening on http://%s:%d", self.settings.admin_host, self.settings.admin_port)

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None

    async def login_page(self, request: web.Request) -> web.Response:
        if self._authorized(request):
            raise web.HTTPFound("/")
        err = '<div class="err">Token 不正确。</div>' if request.query.get("error") else ""
        body = f"""<div class="login"><div class="brand">jm-wechat-bot 管理控制台</div>
<div class="sub">v{_e(__version__)} · 只读控制台</div>{err}
<form method="post" action="/login"><input type="password" name="token" placeholder="JMWXBOT_ADMIN_TOKEN" autofocus required><button type="submit">登录</button></form>
<div class="note" style="padding:10px 0 0">控制台不会显示 bot token、JM AVS、context token 或用户密码。</div></div>"""
        return web.Response(text=_layout("登录", body, refresh=False), content_type="text/html")

    async def login_submit(self, request: web.Request) -> web.Response:
        data = await request.post()
        got = str(data.get("token") or "")
        if not self.settings.admin_token or not hmac.compare_digest(got, self.settings.admin_token):
            raise web.HTTPFound("/login?error=1")
        resp = web.HTTPFound("/")
        resp.set_cookie(
            _COOKIE,
            _session_value(self.settings.admin_token),
            httponly=True,
            secure=self.settings.admin_secure_cookie,
            samesite="Strict",
            max_age=86400 * 30,
            path="/",
        )
        raise resp

    async def logout(self, request: web.Request) -> web.Response:
        resp = web.HTTPFound("/login")
        resp.del_cookie(_COOKIE, path="/")
        raise resp

    async def overview(self, request: web.Request) -> web.Response:
        self._require(request)
        rows = self.store.admin_accounts()
        usage = self.store.admin_usage_summary(days=7)
        jobs = self.store.admin_recent_jobs(limit=20)
        failures = self.store.admin_recent_jobs(limit=10, failures_only=True)
        live = {aid: rt.admin_snapshot() for aid, rt in self.runtimes.items()}

        peer_total = sum(int(r["peer_count"] or 0) for r in rows)
        sent_total = sum(int(r["sent_count"] or 0) for r in rows)
        failed_total = sum(int(r["failed_count"] or 0) for r in rows)
        sent_bytes = sum(int(r["sent_bytes"] or 0) for r in rows)
        active = sum(int(x["active_count"]) for x in live.values())
        queued = sum(int(x["queued_count"]) for x in live.values())
        disk = shutil.disk_usage(self.settings.data_dir)

        cards = "".join([
            f'<div class="card"><div class="k">绑定微信</div><div class="v">{len(rows)}</div></div>',
            f'<div class="card"><div class="k">已交互用户</div><div class="v">{peer_total}</div></div>',
            f'<div class="card"><div class="k">成功发送</div><div class="v">{sent_total}</div></div>',
            f'<div class="card"><div class="k">累计发送量</div><div class="v">{_e(human_size(sent_bytes))}</div></div>',
            f'<div class="card"><div class="k">任务 Active / Queue</div><div class="v">{active} / {queued}</div></div>',
            f'<div class="card"><div class="k">磁盘可用</div><div class="v">{_e(human_size(disk.free))}</div></div>',
        ])

        account_tr = []
        for r in rows:
            snap = live.get(r["account_id"], {"active_count": 0, "queued_count": 0})
            account_tr.append(
                "<tr>"
                f'<td><a href="/account/{quote(str(r["account_id"]), safe="")}"><strong>{_e(r["name"] or "未命名")}</strong></a><br><code>{_e(r["account_id"])}</code></td>'
                f'<td><code>{_e(r["user_id"] or "-")}</code></td>'
                f'<td>{int(r["peer_count"] or 0)}</td>'
                f'<td>{int(r["jm_logged_in"] or 0)}</td>'
                f'<td>{int(r["command_count_7d"] or 0)} / {int(r["command_count"] or 0)}</td>'
                f'<td>{int(r["sent_count"] or 0)} / {int(r["failed_count"] or 0)}</td>'
                f'<td>{_e(human_size(int(r["sent_bytes"] or 0)))}</td>'
                f'<td>{int(snap["active_count"])} / {int(snap["queued_count"])}</td>'
                f'<td class="nowrap">{_e(_fmt_time(r["last_seen"], self.settings.admin_timezone))}</td>'
                "</tr>"
            )
        accounts_html = "".join(account_tr) or '<tr><td colspan="9">暂无绑定账号</td></tr>'

        usage_html = "".join(
            f'<span class="pill"><code>{_e(x["event_type"])}</code> · {int(x["count"] or 0)}</span>' for x in usage
        ) or '<span class="sub">暂无 v0.6.0 之后的命令统计</span>'

        body = f"""
<div class="top"><div><div class="brand">jm-wechat-bot 管理控制台</div><div class="sub">v{_e(__version__)} · 每 15 秒刷新 · 时间：{_e(self.settings.admin_timezone)}</div></div><div><a href="/logout">退出</a></div></div>
<div class="grid">{cards}</div>
<div class="section"><h2>绑定微信账号</h2><table><thead><tr><th>备注 / Account ID</th><th>Weixin User ID</th><th>用户</th><th>JM 已登录</th><th>命令 7d / 总计</th><th>发送 / 失败</th><th>发送量</th><th>Active / Queue</th><th>最近交互</th></tr></thead><tbody>{accounts_html}</tbody></table></div>
<div class="section"><h2>最近 7 天命令分布</h2><div class="row" style="padding:14px 16px">{usage_html}</div><div class="note">命令统计从 v0.6.0 开始记录，只保存命令类型，不保存搜索关键词、登录密码或原始消息文本。</div></div>
{self._jobs_section(jobs, "最近任务")}
{self._jobs_section(failures, "最近异常", show_message=True)}
<div class="note">历史下载数据来自既有 job_history，因此升级前的任务仍会显示；命令使用量则从 v0.6.0 起累计。</div>
"""
        return web.Response(text=_layout("控制台", body), content_type="text/html")

    async def account_detail(self, request: web.Request) -> web.Response:
        self._require(request)
        account_id = request.match_info["account_id"]
        account = self.store.get_account(account_id)
        if account is None:
            raise web.HTTPNotFound(text="account not found")
        peers = self.store.admin_peer_stats(account_id)
        usage = self.store.admin_usage_summary(account_id=account_id, days=7)
        jobs = self.store.admin_recent_jobs(account_id=account_id, limit=30)
        snap = self.runtimes.get(account_id).admin_snapshot() if account_id in self.runtimes else {"active_count": 0, "queued_count": 0, "active_items": []}
        workspace = self.settings.workspaces_dir / stable_component(account_id)
        stats = cache_stats(workspace) if workspace.exists() else None

        peer_tr = []
        for p in peers:
            jm_name = p["username"] or "-"
            if p["display_name"] and p["display_name"] != p["username"]:
                jm_name += f' ({p["display_name"]})'
            login = '<span class="badge ok">已验证</span>' if p["login_verified"] else '<span class="badge muted">未登录/未验证</span>'
            daily = "-"
            if p["last_daily_date"]:
                daily = f'{_e(p["last_daily_date"])} · {_status_badge(p["last_daily_result"])}'
            peer_tr.append(
                "<tr>"
                f'<td><code>{_e(p["peer_id"])}</code></td>'
                f'<td>{_e(jm_name)}<br><span class="sub">UID {_e(p["uid"] or "-")}</span></td>'
                f'<td>{login}</td>'
                f'<td>{int(p["command_count_7d"])} / {int(p["command_count"])}</td>'
                f'<td>{int(p["search_count"])}</td>'
                f'<td>{int(p["sent_count"])} / {int(p["failed_count"])}</td>'
                f'<td>{_e(human_size(int(p["sent_bytes"])))}</td>'
                f'<td>{daily}</td>'
                f'<td class="nowrap">{_e(_fmt_time(p["last_seen"] or p["last_command"] or p["last_job"], self.settings.admin_timezone))}</td>'
                "</tr>"
            )
        peers_html = "".join(peer_tr) or '<tr><td colspan="9">暂无用户</td></tr>'
        usage_html = "".join(
            f'<span class="pill"><code>{_e(x["event_type"])}</code> · {int(x["count"] or 0)}</span>' for x in usage
        ) or '<span class="sub">暂无统计</span>'
        cache_text = f'{stats.files} 个 / {human_size(stats.bytes)}' if stats else '0 个 / 0 B'

        active_rows = "".join(
            "<tr>"
            f'<td><code>{_e(x["peer_id"])}</code></td><td><code>JM{_e(x["jm_id"])}</code></td><td>{_e(x["format"])}</td><td>{_e(x["stage"])}</td><td>{int(x["done"] or 0)} / {int(x["total"] or 0)}</td>'
            "</tr>" for x in snap.get("active_items", [])
        ) or '<tr><td colspan="5">当前没有正在执行的下载</td></tr>'

        body = f"""
<div class="top"><div><div class="brand">{_e(account.name or '未命名')}</div><div class="sub"><a href="/">← 总览</a> · <code>{_e(account.account_id)}</code></div></div><div><a href="/logout">退出</a></div></div>
<div class="grid">
<div class="card"><div class="k">Weixin User ID</div><div class="v" style="font-size:14px"><code>{_e(account.user_id or '-')}</code></div></div>
<div class="card"><div class="k">用户数</div><div class="v">{len(peers)}</div></div>
<div class="card"><div class="k">Active</div><div class="v">{int(snap.get('active_count',0))}</div></div>
<div class="card"><div class="k">Queue</div><div class="v">{int(snap.get('queued_count',0))}</div></div>
<div class="card"><div class="k">缓存</div><div class="v" style="font-size:17px">{_e(cache_text)}</div></div>
<div class="card"><div class="k">API Base</div><div class="v" style="font-size:12px">{_e(account.base_url)}</div></div>
</div>
<div class="section"><h2>当前下载</h2><table><thead><tr><th>Peer ID</th><th>JM</th><th>格式</th><th>阶段</th><th>进度</th></tr></thead><tbody>{active_rows}</tbody></table></div>
<div class="section"><h2>联系人使用数据</h2><table><thead><tr><th>Peer ID</th><th>JM 账号</th><th>登录</th><th>命令 7d / 总计</th><th>搜索</th><th>发送 / 失败</th><th>发送量</th><th>最近签到</th><th>最近交互</th></tr></thead><tbody>{peers_html}</tbody></table></div>
<div class="section"><h2>最近 7 天命令分布</h2><div class="row" style="padding:14px 16px">{usage_html}</div></div>
{self._jobs_section(jobs, "最近任务")}
<div class="note">控制台只读；不会显示 bot token、JM AVS、context token 或密码。Weixin iLink 当前没有稳定的好友昵称查询，因此联系人主要以 peer_id 标识；已登录 JM 时会额外显示 JM 用户名。</div>
"""
        return web.Response(text=_layout(account.name or "账号详情", body), content_type="text/html")

    def _jobs_section(self, jobs: list[dict], title: str, show_message: bool = False) -> str:
        rows = []
        for j in jobs:
            msg = ""
            if show_message:
                msg = f'<td class="truncate" title="{_e(j.get("message") or "")}">{_e(j.get("message") or "-")}</td>'
            rows.append(
                "<tr>"
                f'<td>{_e(j.get("account_name") or "-")}</td>'
                f'<td><code>{_e(j.get("peer_id") or "-")}</code></td>'
                f'<td><code>JM{_e(j.get("jm_id") or "-")}</code><br><span class="sub truncate">{_e(j.get("title") or "")}</span></td>'
                f'<td>{_e(j.get("export_format") or "-")}</td>'
                f'<td>{_status_badge(j.get("status"))}</td>'
                f'<td>{_e(human_size(int(j.get("file_size") or 0)))}</td>'
                f'<td>{("%.1fs" % float(j["duration"])) if j.get("duration") is not None else "-"}</td>'
                f'<td class="nowrap">{_e(_fmt_time(j.get("created_at"), self.settings.admin_timezone))}</td>'
                + msg + "</tr>"
            )
        colspan = 9 if show_message else 8
        body = "".join(rows) or f'<tr><td colspan="{colspan}">暂无记录</td></tr>'
        extra = "<th>错误信息</th>" if show_message else ""
        return f'<div class="section"><h2>{_e(title)}</h2><table><thead><tr><th>微信</th><th>Peer</th><th>JM</th><th>格式</th><th>状态</th><th>大小</th><th>耗时</th><th>时间</th>{extra}</tr></thead><tbody>{body}</tbody></table></div>'
