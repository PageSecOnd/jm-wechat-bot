from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheStats:
    files: int
    bytes: int


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed_files: int
    removed_bytes: int
    remaining_files: int
    remaining_bytes: int


def cache_stats(root: Path) -> CacheStats:
    files = 0
    total = 0
    if not root.exists():
        return CacheStats(0, 0)
    for p in root.rglob("*"):
        try:
            if p.is_file():
                files += 1
                total += p.stat().st_size
        except OSError:
            continue
    return CacheStats(files, total)


def cleanup_cache(settings: Settings, *, grace_seconds: float = 3600.0) -> CleanupResult:
    root = settings.workspaces_dir
    if not root.exists():
        return CleanupResult(0, 0, 0, 0)

    now = time.time()
    files: list[tuple[float, int, Path]] = []
    for p in root.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue

    removed_files = 0
    removed_bytes = 0

    def remove(p: Path, size: int) -> bool:
        nonlocal removed_files, removed_bytes
        try:
            p.unlink(missing_ok=True)
            removed_files += 1
            removed_bytes += size
            return True
        except OSError as exc:
            log.warning("cache cleanup failed for %s: %s", p, exc)
            return False

    keep: list[tuple[float, int, Path]] = []
    ttl_s = settings.cache_ttl_days * 86400
    for mtime, size, p in files:
        old_enough = (now - mtime) >= grace_seconds
        ttl_expired = settings.cache_ttl_days > 0 and (now - mtime) > ttl_s
        if old_enough and ttl_expired and remove(p, size):
            continue
        keep.append((mtime, size, p))

    keep.sort(key=lambda x: x[0])
    total = sum(size for _, size, _ in keep)
    max_bytes = int(settings.cache_max_gb * 1024**3) if settings.cache_max_gb > 0 else 0

    def low_disk() -> bool:
        try:
            usage = shutil.disk_usage(settings.data_dir)
            if usage.total <= 0:
                return False
            return (usage.free / usage.total * 100) < settings.cache_min_free_percent
        except OSError:
            return False

    survivors: list[tuple[float, int, Path]] = []
    for mtime, size, p in keep:
        over_limit = max_bytes > 0 and total > max_bytes
        disk_low = settings.cache_min_free_percent > 0 and low_disk()
        old_enough = (now - mtime) >= grace_seconds
        if old_enough and (over_limit or disk_low) and remove(p, size):
            total -= size
        else:
            survivors.append((mtime, size, p))

    # Best effort removal of empty directories, deepest first.
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        try:
            d.rmdir()
        except OSError:
            pass

    return CleanupResult(removed_files, removed_bytes, len(survivors), sum(x[1] for x in survivors))
