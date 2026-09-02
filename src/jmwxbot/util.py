from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path


def protocol_client_version(version: str) -> int:
    parts = []
    for token in version.split(".")[:3]:
        m = re.match(r"(\d+)", token)
        parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def random_wechat_uin() -> str:
    # Tencent implementation: random uint32 -> decimal UTF-8 string -> base64.
    n = int.from_bytes(os.urandom(4), "big", signed=False)
    return base64.b64encode(str(n).encode("utf-8")).decode("ascii")


def stable_component(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def peer_workspace(root: Path, account_id: str, peer_id: str) -> Path:
    return root / stable_component(account_id) / stable_component(peer_id)



def peer_jm_profile(root: Path, account_id: str, peer_id: str) -> Path:
    return root / stable_component(account_id) / f"{stable_component(peer_id)}.yml"


def mask_secret(value: str | None, keep: int = 5) -> str:
    if not value:
        return "-"
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-keep:]}"
