from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Account:
    account_id: str
    bot_token: str
    base_url: str
    user_id: str | None = None
    name: str | None = None
    sync_buf: str = ""


@dataclass(slots=True)
class InboundMessage:
    account_id: str
    message_id: str
    from_user_id: str
    context_token: str
    text: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ComicInfo:
    jm_id: str
    title: str
    authors: tuple[str, ...] = ()
    works: tuple[str, ...] = ()
    actors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    page_count: int = 0
    chapter_count: int = 0
    pub_date: str = ""
    update_date: str = ""
    views: str = ""
    likes: str = ""
    comment_count: int = 0
    description: str = ""


@dataclass(frozen=True, slots=True)
class SearchHit:
    jm_id: str
    title: str
    tags: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    views: str = ""
    likes: str = ""
    comment_count: int | None = None
    page_count: int | None = None
    chapter_count: int | None = None


@dataclass(frozen=True, slots=True)
class SearchResults:
    query: str
    page: int
    total: int
    page_count: int | None
    items: tuple[SearchHit, ...]
    sort_by: str = ""
    sort_label: str = ""


@dataclass(frozen=True, slots=True)
class JmLoginResult:
    uid: str
    username: str
    display_name: str = ""
    level_name: str = ""
    favorite_count: int | None = None
    coin: int | None = None
    level: int | None = None
    exp: int | None = None
    next_level_exp: int | None = None
    exp_percent: float | None = None


@dataclass(frozen=True, slots=True)
class JmDailyStatus:
    daily_id: str
    event_name: str = ""
    current_progress: str = ""
    three_days_coin: str = ""
    three_days_exp: str = ""
    seven_days_coin: str = ""
    seven_days_exp: str = ""


@dataclass(frozen=True, slots=True)
class JmDailyResult:
    message: str
    already_signed: bool
    status: JmDailyStatus


@dataclass(frozen=True, slots=True)
class FavoriteResults:
    folder_id: str
    page: int
    total: int
    page_count: int | None
    items: tuple[SearchHit, ...]
    folders: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CommentEntry:
    comment_id: str
    author: str
    content: str
    likes: int | None = None
    created_at: str = ""
    spoiler: bool = False
    replies: tuple["CommentEntry", ...] = ()


@dataclass(frozen=True, slots=True)
class CommentResults:
    jm_id: str
    page: int
    total: int | None
    page_count: int | None
    items: tuple[CommentEntry, ...]


@dataclass(slots=True)
class DownloadJob:
    jm_id: str
    peer_id: str
    context_token: str
    export_format: str = "pdf"

    @property
    def key(self) -> tuple[str, str]:
        return self.export_format, self.jm_id
