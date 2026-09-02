from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from .models import (
    ComicInfo,
    CommentEntry,
    CommentResults,
    FavoriteResults,
    JmDailyResult,
    JmDailyStatus,
    JmLoginResult,
    SearchHit,
    SearchResults,
)
from .settings import Settings

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], Awaitable[None] | None]


class ProviderError(RuntimeError):
    pass


class JmComicProvider:
    SUPPORTED_FORMATS = {"pdf", "zip", "long"}
    CATEGORY_ALIASES = {
        "all": "0",
        "全部": "0",
        "doujin": "doujin",
        "同人": "doujin",
        "single": "single",
        "单本": "single",
        "單本": "single",
        "short": "short",
        "短篇": "short",
        "another": "another",
        "其他": "another",
        "hanman": "hanman",
        "韩漫": "hanman",
        "韓漫": "hanman",
        "meiman": "meiman",
        "美漫": "meiman",
        "cosplay": "doujin_cosplay",
        "3d": "3D",
        "english": "english_site",
        "英文": "english_site",
        "english_site": "english_site",
    }
    SEARCH_SORTS = {
        "likes": ("ORDER_BY_LIKE", "爱心"),
        "views": ("ORDER_BY_VIEW", "阅读量"),
        "latest": ("ORDER_BY_LATEST", "最新"),
        "pages": ("ORDER_BY_PICTURE", "页数"),
    }

    CATEGORY_NAMES = {
        "0": "全部",
        "doujin": "同人",
        "single": "单本",
        "short": "短篇",
        "another": "其他",
        "hanman": "韩漫",
        "meiman": "美漫",
        "doujin_cosplay": "Cosplay",
        "3D": "3D",
        "english_site": "英文站",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self._favorite_locks: dict[str, asyncio.Lock] = {}

    def _create_option(self, option_file: Path | None = None):
        try:
            from jmcomic import JmOption, create_option_by_file
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("缺少 jmcomic，请先安装项目依赖") from exc

        selected = option_file or self.settings.jm_option_file
        if selected:
            if not selected.is_file():
                raise ProviderError(f"JM option 文件不存在: {selected}")
            return create_option_by_file(str(selected))
        return JmOption.default()

    @staticmethod
    def _strings(value) -> tuple[str, ...]:
        if not value:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        return tuple(str(x).strip() for x in value if str(x).strip())

    @staticmethod
    def _get(value, name: str, default=None):
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(name, default)
        try:
            return value[name]
        except (KeyError, TypeError, AttributeError):
            return getattr(value, name, default)

    @staticmethod
    def _int_or_none(value):
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float_or_none(value):
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _daily_status_from_data(cls, data) -> JmDailyStatus:
        return JmDailyStatus(
            daily_id=str(cls._get(data, "daily_id", cls._get(data, "dailyId", "")) or ""),
            event_name=str(cls._get(data, "event_name", cls._get(data, "eventName", "")) or ""),
            current_progress=str(cls._get(data, "current_progress", cls._get(data, "currentProgress", "")) or ""),
            three_days_coin=str(cls._get(data, "three_days_coin", cls._get(data, "threeDaysCoin", "")) or ""),
            three_days_exp=str(cls._get(data, "three_days_exp", cls._get(data, "threeDaysExp", "")) or ""),
            seven_days_coin=str(cls._get(data, "seven_days_coin", cls._get(data, "sevenDaysCoin", "")) or ""),
            seven_days_exp=str(cls._get(data, "seven_days_exp", cls._get(data, "sevenDaysExp", "")) or ""),
        )

    @classmethod
    def _page_to_results(
        cls,
        label: str,
        page_obj,
        page: int,
        limit: int,
        *,
        sort_by: str = "",
        sort_label: str = "",
    ) -> SearchResults:
        items: list[SearchHit] = []
        if hasattr(page_obj, "iter_id_title_tag"):
            for aid, title, tags in page_obj.iter_id_title_tag():
                items.append(SearchHit(str(aid), str(title).strip(), cls._strings(tags)))
                if len(items) >= limit:
                    break
        else:
            for aid, title in page_obj:
                items.append(SearchHit(str(aid), str(title).strip(), ()))
                if len(items) >= limit:
                    break
        page_count = getattr(page_obj, "page_count", None)
        return SearchResults(
            query=label,
            page=int(getattr(page_obj, "page_number", None) or page),
            total=int(getattr(page_obj, "total", 0) or 0),
            page_count=int(page_count) if page_count is not None else None,
            items=tuple(items),
            sort_by=sort_by,
            sort_label=sort_label,
        )

    @classmethod
    def _enriched_hit(cls, base: SearchHit, album) -> SearchHit:
        episodes = getattr(album, "episode_list", None) or []
        return SearchHit(
            jm_id=str(getattr(album, "id", None) or getattr(album, "album_id", None) or base.jm_id),
            title=str(getattr(album, "name", "") or base.title).strip(),
            tags=cls._strings(getattr(album, "tags", None)) or base.tags,
            authors=cls._strings(getattr(album, "authors", None)),
            views=str(getattr(album, "views", "") or "").strip(),
            likes=str(getattr(album, "likes", "") or "").strip(),
            comment_count=cls._int_or_none(getattr(album, "comment_count", None)),
            page_count=cls._int_or_none(getattr(album, "page_count", None)),
            chapter_count=len(episodes) if episodes else None,
        )

    async def _enrich_search_results(self, option, results: SearchResults) -> SearchResults:
        # Search pages expose IDs/titles/tags reliably, but statistics are detail fields.
        # Enrich only the visible page and cap concurrency to avoid hammering JM.
        sem = asyncio.Semaphore(4)

        async def one(hit: SearchHit) -> SearchHit:
            async with sem:
                def _fetch():
                    client = option.new_jm_client()
                    return client.get_album_detail(hit.jm_id)
                try:
                    album = await asyncio.to_thread(_fetch)
                    return self._enriched_hit(hit, album)
                except Exception as exc:
                    log.debug("search detail enrichment skipped jm=%s: %s", hit.jm_id, exc)
                    return hit

        items = await asyncio.gather(*(one(hit) for hit in results.items))
        return SearchResults(
            query=results.query,
            page=results.page,
            total=results.total,
            page_count=results.page_count,
            items=tuple(items),
            sort_by=results.sort_by,
            sort_label=results.sort_label,
        )

    async def fetch_info(self, jm_id: str) -> ComicInfo:
        option = self._create_option()

        def _fetch():
            client = option.new_jm_client()
            return client.get_album_detail(jm_id)

        try:
            album = await asyncio.to_thread(_fetch)
        except Exception as exc:
            raise ProviderError(f"JM{jm_id} 详情获取失败: {exc}") from exc

        try:
            episodes = getattr(album, "episode_list", None) or []
            return ComicInfo(
                jm_id=str(getattr(album, "id", None) or getattr(album, "album_id", jm_id)),
                title=str(getattr(album, "name", "") or f"JM{jm_id}").strip(),
                authors=self._strings(getattr(album, "authors", None)),
                works=self._strings(getattr(album, "works", None)),
                actors=self._strings(getattr(album, "actors", None)),
                tags=self._strings(getattr(album, "tags", None)),
                page_count=int(getattr(album, "page_count", 0) or 0),
                chapter_count=len(episodes),
                pub_date=str(getattr(album, "pub_date", "") or "").strip(),
                update_date=str(getattr(album, "update_date", "") or "").strip(),
                views=str(getattr(album, "views", "") or "").strip(),
                likes=str(getattr(album, "likes", "") or "").strip(),
                comment_count=int(getattr(album, "comment_count", 0) or 0),
                description=str(getattr(album, "description", "") or "").strip(),
            )
        except Exception as exc:
            raise ProviderError(f"JM{jm_id} 详情解析失败: {exc}") from exc

    async def search(self, query: str, page: int = 1, limit: int = 8, sort_by: str = "likes") -> SearchResults:
        sort_by = (sort_by or "likes").lower()
        sort_spec = self.SEARCH_SORTS.get(sort_by)
        if sort_spec is None:
            raise ProviderError("搜索排序只支持 likes / views / latest / pages")
        constant_name, sort_label = sort_spec
        option = self._create_option()

        def _search():
            from jmcomic import JmMagicConstants

            client = option.new_jm_client()
            order_by = getattr(JmMagicConstants, constant_name)
            return client.search_site(search_query=query, page=page, order_by=order_by)

        try:
            page_obj = await asyncio.to_thread(_search)
            results = self._page_to_results(
                query, page_obj, page, limit, sort_by=sort_by, sort_label=sort_label
            )
            return await self._enrich_search_results(option, results)
        except Exception as exc:
            raise ProviderError(f"搜索失败：{exc}") from exc

    async def ranking(self, period: str = "week", page: int = 1, limit: int = 8) -> SearchResults:
        period = period.lower()
        method_name = {"day": "day_ranking", "week": "week_ranking", "month": "month_ranking"}.get(period)
        label = {"day": "日排行", "week": "周排行", "month": "月排行"}.get(period)
        if not method_name or not label:
            raise ProviderError("排行类型只支持 day / week / month（日 / 周 / 月）")
        option = self._create_option()

        def _fetch():
            client = option.new_jm_client()
            return getattr(client, method_name)(page)

        try:
            result = await asyncio.to_thread(_fetch)
            return self._page_to_results(label, result, page, limit)
        except Exception as exc:
            raise ProviderError(f"{label}获取失败：{exc}") from exc

    async def category(self, category: str, page: int = 1, limit: int = 8) -> SearchResults:
        key = category.strip()
        normalized = self.CATEGORY_ALIASES.get(key.lower(), self.CATEGORY_ALIASES.get(key))
        if normalized is None:
            supported = " / ".join(self.CATEGORY_NAMES.values())
            raise ProviderError(f"未知分类：{category}。可用分类：{supported}")
        option = self._create_option()

        def _fetch():
            from jmcomic import JmMagicConstants

            client = option.new_jm_client()
            return client.categories_filter(
                page=page,
                time=JmMagicConstants.TIME_ALL,
                category=normalized,
                order_by=JmMagicConstants.ORDER_BY_LATEST,
            )

        label = f"分类：{self.CATEGORY_NAMES.get(normalized, category)}"
        try:
            result = await asyncio.to_thread(_fetch)
            return self._page_to_results(label, result, page, limit)
        except Exception as exc:
            raise ProviderError(f"{label}获取失败：{exc}") from exc

    AUTH_PROFILE_MARKER = "# jmwxbot-auth-profile: 2"
    LOGIN_DOMAIN_ATTEMPTS = 3

    @classmethod
    def auth_profile_is_current(cls, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            with path.open("r", encoding="utf-8") as fh:
                return fh.readline().strip() == cls.AUTH_PROFILE_MARKER
        except OSError:
            return False

    @classmethod
    def _require_current_auth_profile(cls, path: Path) -> None:
        if not path.is_file():
            raise ProviderError("尚未登录 JM。请先使用 /login JM用户名 JM密码。")
        if not cls.auth_profile_is_current(path):
            raise ProviderError(
                "当前 JM 登录态来自 v0.6.1 或更早版本，只保存了 AVS 且未绑定登录域名。"
                "为避免跨域 Cookie 失效，请重新执行一次 /login JM用户名 JM密码。"
            )

    @staticmethod
    def _safe_cookie_dict(value) -> dict[str, str]:
        if not value:
            return {}
        try:
            raw = dict(value)
        except Exception:
            return {}
        result: dict[str, str] = {}
        for key, item in raw.items():
            if key is None or item is None:
                continue
            result[str(key)] = str(item)
        return result

    @classmethod
    def _write_login_profile(cls, path: Path, domain: str, cookies: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass

        clean_domain = str(domain).strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        if not clean_domain:
            raise ProviderError("JM 登录成功但无法确定认证域名，已拒绝保存登录态")
        clean_cookies = {str(k): str(v) for k, v in cookies.items() if str(k) and v is not None}
        if not clean_cookies.get("AVS"):
            raise ProviderError("JM 登录成功但 Cookie 中没有 AVS，已拒绝保存登录态")

        lines = [
            cls.AUTH_PROFILE_MARKER,
            "version: '2.1'",
            "",
            "client:",
            "  impl: api",
            "  # Authentication cookies are domain-bound. Keep authenticated",
            "  # requests on the exact API host that accepted this login.",
            "  domain:",
            "    api:",
            f"      - {json.dumps(clean_domain, ensure_ascii=False)}",
            "  retry_times: 2",
            "  postman:",
            "    meta_data:",
            "      cookies:",
        ]
        for key in sorted(clean_cookies):
            lines.append(
                f"        {json.dumps(key, ensure_ascii=False)}: "
                f"{json.dumps(clean_cookies[key], ensure_ascii=False)}"
            )
        content = "\n".join(lines) + "\n"

        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(content, encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @classmethod
    def _discover_login_domains(cls):
        # Create an isolated unauthenticated probe. The non-AVS cookie prevents
        # JmApiClient.ensure_have_cookies() from consulting process-global
        # JmModuleConfig.APP_COOKIES, while after_init() can still refresh the
        # current API domain list.
        try:
            from jmcomic import JmOption
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("缺少 jmcomic，请先安装项目依赖") from exc

        probe_option = JmOption.construct({
            "client": {
                "postman": {
                    "meta_data": {
                        "cookies": {"jmwxbot_domain_probe": uuid.uuid4().hex},
                    }
                }
            }
        })
        probe = probe_option.new_jm_client(impl="api", cache=None)
        domains = []
        for value in probe.get_domain_list() or []:
            domain = str(value).strip().removeprefix("https://").removeprefix("http://").rstrip("/")
            if domain and domain not in domains:
                domains.append(domain)
        if not domains:
            raise ProviderError("没有获取到可用的 JM API 域名")
        return domains

    async def login(self, username: str, password: str, *, option_file: Path) -> JmLoginResult:
        username = username.strip()
        if not username or not password:
            raise ProviderError("用法：/login JM用户名 JM密码")

        def _login_once(domain: str):
            try:
                from jmcomic import JmOption
            except ImportError as exc:  # pragma: no cover
                raise ProviderError("缺少 jmcomic，请先安装项目依赖") from exc

            # SECURITY: never bootstrap authenticated clients from the process-
            # global APP_COOKIES cache. Pin this login attempt to one domain so a
            # retry cannot silently move credentials/session state across hosts.
            option = JmOption.construct({
                "client": {
                    "retry_times": 1,
                    "postman": {
                        "meta_data": {
                            "cookies": {"jmwxbot_login": uuid.uuid4().hex},
                        }
                    },
                }
            })
            client = option.new_jm_client(impl="api", cache=None, domain_list=[domain])
            resp = client.login(username, password)
            cookies = self._safe_cookie_dict(client.get_meta_data("cookies"))
            return resp, cookies

        try:
            domains = await asyncio.to_thread(self._discover_login_domains)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"JM API 域名发现失败：{exc}") from exc

        errors: list[str] = []
        resp = None
        login_domain = ""
        login_cookies: dict[str, str] = {}
        for domain in domains[: self.LOGIN_DOMAIN_ATTEMPTS]:
            try:
                resp, login_cookies = await asyncio.to_thread(_login_once, domain)
                login_domain = domain
                break
            except Exception as exc:
                # Some JM API nodes intermittently answer 401 for credentials
                # accepted by another current node. Try a small bounded set of
                # domains, but never carry cookies from one attempt to the next.
                errors.append(f"{domain}: {exc}")
                log.warning("JM login attempt failed domain=%s: %s", domain, exc)

        if resp is None:
            detail = errors[-1] if errors else "没有可用的 JM API 登录节点"
            raise ProviderError(f"JM 登录失败：{detail}")

        try:
            data = getattr(resp, "res_data", None)
            avs = None
            if data is not None:
                try:
                    avs = data["s"]
                except (KeyError, TypeError):
                    avs = getattr(data, "s", None)
            if not avs:
                raise ProviderError("JM 登录响应中没有 AVS，登录可能未成功")

            # Upstream login() normally writes response cookies + AVS back into
            # the client's postman metadata. Keep all of them; AVS-only profiles
            # are too weak when JM routes authenticated traffic across hosts.
            login_cookies["AVS"] = str(avs)

            uid = str(self._get(data, "uid", "") or "").strip()
            if not uid:
                raise ProviderError("JM 登录响应中没有 UID，无法启用签到功能")

            returned_username = str(self._get(data, "username", "") or "").strip()
            if not returned_username:
                raise ProviderError("JM 登录响应中没有用户名，已拒绝保存登录态")
            if returned_username.casefold() != username.casefold():
                raise ProviderError(
                    "安全检查失败：JM 返回账号 "
                    f"{returned_username!r}，但本次请求登录的是 {username!r}。"
                    "登录态未保存，请重试。"
                )

            self._write_login_profile(option_file, login_domain, login_cookies)
            return JmLoginResult(
                uid=uid,
                username=returned_username,
                display_name=str(self._get(data, "fname", "") or "").strip(),
                level_name=str(self._get(data, "level_name", "") or "").strip(),
                favorite_count=self._int_or_none(self._get(data, "album_favorites", None)),
                coin=self._int_or_none(self._get(data, "coin", None)),
                level=self._int_or_none(self._get(data, "level", None)),
                exp=self._int_or_none(self._get(data, "exp", None)),
                next_level_exp=self._int_or_none(self._get(data, "nextLevelExp", None)),
                exp_percent=self._float_or_none(self._get(data, "expPercent", None)),
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"JM 登录失败：{exc}") from exc

    async def get_daily_status(self, uid: str, *, option_file: Path) -> JmDailyStatus:
        self._require_current_auth_profile(option_file)
        option = self._create_option(option_file)

        def _fetch():
            client = option.new_jm_client(impl="api")
            return client.req_api("/daily", params={"user_id": uid})

        try:
            resp = await asyncio.to_thread(_fetch)
            return self._daily_status_from_data(getattr(resp, "res_data", None))
        except Exception as exc:
            raise ProviderError(f"JM 每日签到状态获取失败：{exc}") from exc

    async def daily_checkin(self, uid: str, *, option_file: Path) -> JmDailyResult:
        status = await self.get_daily_status(uid, option_file=option_file)
        if not status.daily_id:
            raise ProviderError("JM 签到状态里没有 daily_id，暂时无法签到")
        option = self._create_option(option_file)

        def _checkin():
            client = option.new_jm_client(impl="api")
            return client.req_api(
                "/daily_chk",
                False,
                data={"user_id": uid, "daily_id": status.daily_id},
            )

        try:
            resp = await asyncio.to_thread(_checkin)
            data = getattr(resp, "res_data", None)
            message = str(self._get(data, "msg", "") or "签到请求已完成")
            already = any(token in message.lower() for token in ("已签到", "签到过", "已簽到", "簽到過", "already"))
            try:
                refreshed = await self.get_daily_status(uid, option_file=option_file)
            except ProviderError:
                refreshed = status
            return JmDailyResult(message=message, already_signed=already, status=refreshed)
        except Exception as exc:
            # Some server/client combinations surface duplicate sign-in as an exception.
            text = str(exc)
            if any(token in text.lower() for token in ("已签到", "签到过", "已簽到", "簽到過", "already")):
                return JmDailyResult(message=text, already_signed=True, status=status)
            raise ProviderError(f"JM 每日签到失败：{exc}") from exc

    @classmethod
    def _favorite_page_hits(cls, page_obj) -> list[SearchHit]:
        hits: list[SearchHit] = []
        if hasattr(page_obj, "iter_id_title_tag"):
            for aid, title, tags in page_obj.iter_id_title_tag():
                hits.append(SearchHit(str(aid), str(title).strip(), cls._strings(tags)))
        else:
            for aid, title in page_obj:
                hits.append(SearchHit(str(aid), str(title).strip(), ()))
        return hits

    @classmethod
    def _favorite_contains_with_client(cls, client, jm_id: str, folder_id: str = "0") -> bool:
        target = str(jm_id)
        first = client.favorite_folder(page=1, folder_id=folder_id)
        if any(hit.jm_id == target for hit in cls._favorite_page_hits(first)):
            return True

        total = int(getattr(first, "total", 0) or 0)
        upstream_size = int(getattr(first, "page_size", 20) or 20)
        upstream_pages = getattr(first, "page_count", None)
        if upstream_pages is None:
            upstream_pages = (total + upstream_size - 1) // upstream_size if total else 1
        for upstream_page in range(2, int(upstream_pages) + 1):
            result = client.favorite_folder(page=upstream_page, folder_id=folder_id)
            if any(hit.jm_id == target for hit in cls._favorite_page_hits(result)):
                return True
        return False

    async def add_favorite(self, jm_id: str, *, option_file: Path | None = None) -> bool:
        """Ensure an album is present in favorites.

        JM's mobile /favorite endpoint is a toggle, not an idempotent add endpoint.
        The upstream Python client also assumes a ``status`` field that is not
        present in every valid response.  To preserve /fav-add semantics we
        check first, issue the raw toggle only when absent, then verify by
        re-reading the favorites list.  Returns True when newly added and
        False when it was already present.
        """
        if option_file is None:
            raise ProviderError("尚未登录 JM。请先使用 /login JM用户名 JM密码。")
        self._require_current_auth_profile(option_file)
        option = self._create_option(option_file)
        lock_key = str(option_file.resolve())
        lock = self._favorite_locks.setdefault(lock_key, asyncio.Lock())

        async with lock:
            def _add_and_verify():
                import time

                client = option.new_jm_client(impl="api")
                if self._favorite_contains_with_client(client, jm_id):
                    return False

                # Do not call client.add_favorite_album(): current jmcomic
                # unconditionally reads response.model_data.status and can raise
                # KeyError('status') even though /favorite accepted the toggle.
                client.req_api("/favorite", False, data={"aid": str(jm_id)})

                # Favorite list updates can lag the toggle response briefly.
                for attempt in range(3):
                    if self._favorite_contains_with_client(client, jm_id):
                        return True
                    if attempt < 2:
                        time.sleep(0.35)
                raise ProviderError(
                    f"JM{jm_id} 收藏请求已发送，但复查收藏夹仍未发现该项目；未将其误报为收藏成功。"
                )

            try:
                return await asyncio.to_thread(_add_and_verify)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"JM{jm_id} 收藏失败：{exc}") from exc

    async def favorites(
        self, folder_id: str = "0", page: int = 1, limit: int = 8, *, option_file: Path | None = None
    ) -> FavoriteResults:
        if option_file is None:
            raise ProviderError("收藏夹未绑定独立 JM 登录配置。请先发送 /profile 查看自己的配置路径。")
        self._require_current_auth_profile(option_file)
        if page < 1:
            raise ProviderError("收藏夹页码必须从 1 开始。")
        if limit < 1:
            raise ProviderError("收藏夹每页数量必须大于 0。")
        option = self._create_option(option_file)

        def _fetch():
            # JM's API page size is currently 20, while the bot intentionally
            # displays fewer rows (default 8). Fetch the upstream pages needed
            # for the bot-level slice instead of truncating one JM page and
            # reusing its page_count.
            client = option.new_jm_client(impl="api")
            first = client.favorite_folder(page=1, folder_id=folder_id)
            total = int(getattr(first, "total", 0) or 0)
            upstream_size = int(getattr(first, "page_size", 20) or 20)
            bot_page_count = (total + limit - 1) // limit if total else 1
            start = (page - 1) * limit
            end = min(start + limit, total)

            items: list[SearchHit] = []
            if start < total:
                first_needed = start // upstream_size + 1
                last_needed = (end - 1) // upstream_size + 1
                for upstream_page in range(first_needed, last_needed + 1):
                    result = first if upstream_page == 1 else client.favorite_folder(
                        page=upstream_page, folder_id=folder_id
                    )
                    hits = self._favorite_page_hits(result)
                    base = (upstream_page - 1) * upstream_size
                    local_start = max(start - base, 0)
                    local_end = min(end - base, len(hits))
                    if local_end > local_start:
                        items.extend(hits[local_start:local_end])

            folders: tuple[tuple[str, str], ...] = ()
            if hasattr(first, "iter_folder_id_name"):
                folders = tuple((str(fid), str(name)) for fid, name in first.iter_folder_id_name())
            return FavoriteResults(
                folder_id=str(folder_id),
                page=page,
                total=total,
                page_count=bot_page_count,
                items=tuple(items),
                folders=folders,
            )

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            msg = str(exc)
            if '"code":401' in msg or "請先登入會員" in msg or "请先登入会员" in msg:
                raise ProviderError(
                    "收藏夹认证失败：当前 JM 会话已失效，或该认证域名不再接受这组 Cookie。"
                    "请重新执行 /login JM用户名 JM密码；机器人不会再拿登录 Cookie 自动跨域重试。"
                ) from exc
            raise ProviderError(f"收藏夹获取失败：{exc}") from exc

    @classmethod
    def _comment_entry(cls, comment, depth: int = 0) -> CommentEntry:
        replies = ()
        if depth < 3:
            replies = tuple(cls._comment_entry(x, depth + 1) for x in (getattr(comment, "replies", None) or []))
        return CommentEntry(
            comment_id=str(getattr(comment, "comment_id", "") or ""),
            author=str(getattr(comment, "nickname", None) or getattr(comment, "username", None) or "匿名").strip(),
            content=str(getattr(comment, "content", "") or "").strip(),
            likes=getattr(comment, "likes", None),
            created_at=str(getattr(comment, "created_at", "") or "").strip(),
            spoiler=bool(getattr(comment, "is_spoiler", False)),
            replies=replies,
        )

    async def comments(self, jm_id: str, page: int = 1, limit: int = 10) -> CommentResults:
        option = self._create_option()

        def _fetch():
            # API comment pagination includes total/page_count and likes when available.
            client = option.new_jm_client(impl="api")
            return client.album_pagination(jm_id, page=page)

        try:
            result = await asyncio.to_thread(_fetch)
            page_count = getattr(result, "page_count", None)
            total = getattr(result, "total", None)
            items = tuple(self._comment_entry(x) for x in list(result)[:limit])
            return CommentResults(
                jm_id=jm_id,
                page=int(getattr(result, "page_number", None) or page),
                total=int(total) if total is not None else None,
                page_count=int(page_count) if page_count is not None else None,
                items=items,
            )
        except Exception as exc:
            raise ProviderError(f"JM{jm_id} 评论获取失败：{exc}") from exc

    COVER_MAX_EDGE = 2048
    COVER_JPEG_QUALITY = 90

    @classmethod
    def _cover_is_normalized(cls, path: Path) -> bool:
        """Return True only for a decodable, WeChat-friendly cached JPEG."""
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        try:
            from PIL import Image

            # verify() checks the encoded stream without trusting the extension.
            with Image.open(path) as probe:
                probe.verify()
            # Re-open after verify() and force a real decode.  Some truncated files
            # can pass header parsing but fail when pixel data is loaded.
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                return (
                    image.format == "JPEG"
                    and image.mode == "RGB"
                    and width > 0
                    and height > 0
                    and max(width, height) <= cls.COVER_MAX_EDGE
                )
        except Exception:
            return False

    @classmethod
    def _normalize_cover(cls, source: Path, destination: Path) -> None:
        """Decode arbitrary JM cover media and re-encode a conservative RGB JPEG."""
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("缺少 Pillow，无法校验和标准化封面") from exc

        try:
            with Image.open(source) as image:
                image.load()
                image = ImageOps.exif_transpose(image)

                # Flatten transparency on white instead of letting RGBA->RGB turn
                # transparent pixels black.
                if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")

                image.thumbnail(
                    (cls.COVER_MAX_EDGE, cls.COVER_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                )
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("invalid image dimensions")

                destination.parent.mkdir(parents=True, exist_ok=True)
                image.save(
                    destination,
                    format="JPEG",
                    quality=cls.COVER_JPEG_QUALITY,
                    optimize=True,
                    progressive=False,
                )

            if not cls._cover_is_normalized(destination):
                raise ValueError("normalized JPEG failed verification")
        except ProviderError:
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise ProviderError(f"封面不是可用图片或标准化失败: {exc}") from exc

    async def fetch_cover(self, jm_id: str, workspace: Path) -> Path:
        cover_dir = workspace / "cover"
        cover_dir.mkdir(parents=True, exist_ok=True)
        expected = cover_dir / f"JM{jm_id}.jpg"

        # v0.5.1 and earlier accepted any non-empty file as a valid cover.
        # Validate cached files now; if they are decodable but non-standard,
        # normalize them in place.  If they are corrupt, discard and redownload.
        if expected.is_file() and expected.stat().st_size > 0:
            if await asyncio.to_thread(self._cover_is_normalized, expected):
                expected.touch()
                return expected
            normalized_cached = cover_dir / f".JM{jm_id}.cache-normalized.jpg"
            try:
                await asyncio.to_thread(self._normalize_cover, expected, normalized_cached)
                normalized_cached.replace(expected)
                expected.touch()
                return expected
            except Exception as exc:
                log.warning("invalid cached cover removed jm=%s path=%s: %s", jm_id, expected, exc)
                normalized_cached.unlink(missing_ok=True)
                expected.unlink(missing_ok=True)

        option = self._create_option()
        downloaded = cover_dir / f".JM{jm_id}.download.jpg"
        normalized = cover_dir / f".JM{jm_id}.normalized.jpg"

        def _download() -> None:
            client = option.new_jm_client()
            client.download_album_cover(jm_id, str(downloaded))

        try:
            downloaded.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
            await asyncio.to_thread(_download)
            if not downloaded.is_file() or downloaded.stat().st_size <= 0:
                raise ProviderError(f"JM{jm_id} 封面下载完成，但文件为空")
            await asyncio.to_thread(self._normalize_cover, downloaded, normalized)
            normalized.replace(expected)
            expected.touch()
            return expected
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"JM{jm_id} 封面获取失败: {exc}") from exc
        finally:
            downloaded.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)

    @staticmethod
    def cached_export(workspace: Path, jm_id: str, export_format: str) -> Path:
        ext = "png" if export_format == "long" else export_format
        return workspace / export_format / f"JM{jm_id}.{ext}"

    async def fetch_export(
        self,
        jm_id: str,
        workspace: Path,
        export_format: str = "pdf",
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[Path, float | None]:
        export_format = export_format.lower()
        if export_format not in self.SUPPORTED_FORMATS:
            raise ProviderError(f"不支持的导出格式: {export_format}")

        workspace.mkdir(parents=True, exist_ok=True)
        expected = self.cached_export(workspace, jm_id, export_format)
        expected.parent.mkdir(parents=True, exist_ok=True)
        if expected.is_file() and expected.stat().st_size > 0:
            expected.touch()
            return expected, 0.0

        try:
            from jmcomic import DirRule, Feature, JmAsyncDownloader, download_album_async
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("缺少 jmcomic，请先安装项目依赖") from exc

        option = self._create_option()
        job_root = workspace / "tmp" / uuid.uuid4().hex
        image_dir = job_root / "images"
        output_dir = job_root / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        option.dir_rule = DirRule("Bd / Aid / Pindex", base_dir=str(image_dir))

        if export_format == "pdf":
            feature = Feature.export_pdf(pdf_dir=str(output_dir), filename_rule="JM{Aid}")
            suffix = "pdf"
        elif export_format == "zip":
            feature = Feature.export_zip(zip_dir=str(output_dir), filename_rule="JM{Aid}")
            suffix = "zip"
        else:
            feature = Feature.export_long_img(img_dir=str(output_dir), filename_rule="JM{Aid}")
            suffix = "png"

        async def _emit(stage: str, done: int = 0, total: int = 0) -> None:
            if progress is None:
                return
            result = progress(stage, done, total)
            if inspect.isawaitable(result):
                await result

        class StatusDownloader(JmAsyncDownloader):
            def __init__(self, opt):
                super().__init__(opt)
                self._jmwx_done = 0
                self._jmwx_total = 0

            async def before_album(self, album):
                self._jmwx_total = int(getattr(album, "page_count", 0) or 0)
                await _emit("downloading", 0, self._jmwx_total)
                await super().before_album(album)

            async def after_image(self, image, img_save_path: str):
                await super().after_image(image, img_save_path)
                self._jmwx_done += 1
                await _emit("downloading", self._jmwx_done, self._jmwx_total)

            async def after_album(self, album):
                await _emit("exporting", self._jmwx_done, self._jmwx_total)
                await super().after_album(album)
                await _emit("exported", self._jmwx_done, self._jmwx_total)

        try:
            result = await download_album_async(jm_id, option, downloader=StatusDownloader, extra=feature)
            paths = [Path(p) for p in result.manifest.get_export_filepath_list(suffix)]
            paths = [p for p in paths if p.is_file() and p.stat().st_size > 0]
            if not paths:
                paths = sorted(output_dir.glob(f"*.{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not paths:
                raise ProviderError(f"JM{jm_id} 下载完成，但没有找到导出的 {suffix.upper()} 文件")

            produced = paths[0]
            if expected.exists():
                expected.unlink()
            shutil.move(str(produced), str(expected))
            return expected, getattr(result, "duration", None)
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"JM{jm_id} 下载/导出失败: {exc}") from exc
        finally:
            try:
                shutil.rmtree(job_root, ignore_errors=True)
            except Exception:
                pass

    async def fetch_pdf(self, jm_id: str, workspace: Path) -> Path:
        path, _ = await self.fetch_export(jm_id, workspace, "pdf")
        return path
