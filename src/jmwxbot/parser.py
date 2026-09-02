from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


_ID_RE = re.compile(r"^(?:(?:jm)\s*|#\s*)?(\d{1,12})$", re.IGNORECASE)
_EXPORT_RE = re.compile(r"^/(pdf|zip|long)\s+(?:(?:jm)\s*|#\s*)?(\d{1,12})$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^/cancel(?:\s+(?:(?:jm)\s*|#\s*)?(\d{1,12}))?$", re.IGNORECASE)
_HISTORY_RE = re.compile(r"^/history(?:\s+(\d{1,2}))?$", re.IGNORECASE)
_COMMENTS_RE = re.compile(
    r"^/comments?\s+(?:(?:jm)\s*|#\s*)?(\d{1,12})(?:\s+--page\s+(\d+))?$",
    re.IGNORECASE,
)
_RANK_RE = re.compile(r"^/rank(?:\s+(day|week|month|日|周|月))?(?:\s+--page\s+(\d+))?$", re.IGNORECASE)
_FAV_RE = re.compile(r"^/(?:fav|favorites?)(?:\s+(\d+))?(?:\s+--page\s+(\d+))?$", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"^/category(?:\s+([^\s]+))?(?:\s+--page\s+(\d+))?$", re.IGNORECASE)
_LOGIN_RE = re.compile(r"^/login(?:\s+(\S+)\s+(.+))?$", re.IGNORECASE)
_FAV_ADD_RE = re.compile(r"^/(?:fav-add|collect)\s+(?:(?:jm)\s*|#\s*)?(\d{1,12})$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Command:
    kind: str
    value: str | None = None
    arg: str | None = None
    option: str | None = None


_SEARCH_SORT_ALIASES = {
    "likes": "likes", "like": "likes", "heart": "likes", "爱心": "likes", "愛心": "likes", "爱心数": "likes", "愛心數": "likes", "喜欢": "likes", "喜歡": "likes",
    "views": "views", "view": "views", "read": "views", "阅读": "views", "閱讀": "views", "阅读量": "views", "閱讀量": "views", "浏览": "views", "瀏覽": "views", "浏览量": "views", "瀏覽量": "views",
    "latest": "latest", "new": "latest", "最新": "latest",
    "pages": "pages", "page": "pages", "pictures": "pages", "图片": "pages", "圖片": "pages", "页数": "pages", "頁數": "pages",
}


def _parse_search(raw: str) -> Command | None:
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    if not tokens:
        return None
    query_parts: list[str] = []
    page = 1
    sort_by = "likes"
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.lower() == "--page":
            if i + 1 >= len(tokens):
                return None
            try:
                page = max(1, int(tokens[i + 1]))
            except ValueError:
                return None
            i += 2
            continue
        if tok.lower() in {"--sort", "--order"}:
            if i + 1 >= len(tokens):
                return None
            key = tokens[i + 1].lower()
            sort_by = _SEARCH_SORT_ALIASES.get(key, _SEARCH_SORT_ALIASES.get(tokens[i + 1], ""))
            if not sort_by:
                return Command("search_sort_help")
            i += 2
            continue
        query_parts.append(tok)
        i += 1
    query = " ".join(query_parts).strip()
    if not query:
        return None
    return Command("search", query, str(page), sort_by)


def parse_command(text: str) -> Command | None:
    text = text.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"/help", "help", "帮助"}:
        return Command("help")
    if lowered in {"/status", "status", "状态"}:
        return Command("status")
    if lowered in {"/cache", "cache", "缓存"}:
        return Command("cache")
    if lowered in {"/profile", "profile"}:
        return Command("profile")
    if lowered in {"/logout", "logout"}:
        return Command("logout")
    if lowered in {"/daily", "daily", "签到"}:
        return Command("daily")

    m = _LOGIN_RE.fullmatch(text)
    if m:
        return Command("login", m.group(1), m.group(2))

    m = _FAV_ADD_RE.fullmatch(text)
    if m:
        return Command("fav_add", m.group(1))

    m = _CANCEL_RE.fullmatch(text)
    if m:
        return Command("cancel", m.group(1))

    m = _HISTORY_RE.fullmatch(text)
    if m:
        return Command("history", m.group(1) or "10")

    m = _COMMENTS_RE.fullmatch(text)
    if m:
        return Command("comments", m.group(1), str(max(1, int(m.group(2) or "1"))))

    m = _RANK_RE.fullmatch(text)
    if m:
        period = (m.group(1) or "week").lower()
        period = {"日": "day", "周": "week", "月": "month"}.get(period, period)
        return Command("rank", period, str(max(1, int(m.group(2) or "1"))))

    m = _FAV_RE.fullmatch(text)
    if m:
        return Command("fav", m.group(1) or "0", str(max(1, int(m.group(2) or "1"))))

    m = _CATEGORY_RE.fullmatch(text)
    if m:
        return Command("category", m.group(1), str(max(1, int(m.group(2) or "1"))))

    if lowered.startswith("/search "):
        return _parse_search(text[8:].strip())

    m = _EXPORT_RE.fullmatch(text)
    if m:
        return Command("download", m.group(2), m.group(1).lower())

    m = _ID_RE.fullmatch(text)
    if m:
        return Command("download", m.group(1), "pdf")
    return None
