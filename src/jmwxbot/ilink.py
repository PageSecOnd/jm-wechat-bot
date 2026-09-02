from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import Account
from .settings import Settings
from .util import protocol_client_version, random_wechat_uin

log = logging.getLogger(__name__)

MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_FILE = 4
UPLOAD_IMAGE = 1
UPLOAD_FILE = 3


class ILinkError(RuntimeError):
    pass


class StaleContextError(ILinkError):
    pass


class StaleAccountTokenError(ILinkError):
    pass


class ILinkTransport:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._http = None

    def _httpx(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 httpx，请先安装项目依赖") from exc
        return httpx

    async def __aenter__(self) -> "ILinkTransport":
        httpx = self._httpx()
        self._http = httpx.AsyncClient(follow_redirects=True)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self):
        if self._http is None:
            raise RuntimeError("ILinkTransport must be used as an async context manager")
        return self._http

    def common_headers(self) -> dict[str, str]:
        return {
            "iLink-App-Id": self.settings.ilink_app_id,
            "iLink-App-ClientVersion": str(protocol_client_version(self.settings.ilink_protocol_version)),
        }

    def auth_headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_wechat_uin(),
            **self.common_headers(),
        }
        if token:
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    def base_info(self) -> dict[str, str]:
        return {
            "channel_version": self.settings.ilink_protocol_version,
            "bot_agent": self.settings.bot_agent,
        }

    async def post_json(
        self,
        base_url: str,
        endpoint: str,
        payload: dict[str, Any],
        *,
        token: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        res = await self.http.post(
            url,
            headers=self.auth_headers(token),
            json=payload,
            timeout=timeout_s or self.settings.request_timeout_s,
        )
        res.raise_for_status()
        if not res.content:
            return {}
        return res.json()

    async def get_json(
        self,
        base_url: str,
        endpoint: str,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        # QR status in Tencent's implementation uses only common headers.
        res = await self.http.get(
            url,
            headers=self.common_headers(),
            timeout=timeout_s or self.settings.request_timeout_s,
        )
        res.raise_for_status()
        return res.json()


class ILinkLogin:
    BOT_TYPE = "3"

    def __init__(self, transport: ILinkTransport, settings: Settings):
        self.t = transport
        self.settings = settings

    async def fetch_qr(self, local_tokens: list[str]) -> dict[str, Any]:
        return await self.t.post_json(
            self.settings.api_base_url,
            f"ilink/bot/get_bot_qrcode?bot_type={self.BOT_TYPE}",
            {"local_token_list": local_tokens[-10:]},
            timeout_s=35,
        )

    async def poll_qr(self, base_url: str, qrcode: str, verify_code: str | None = None) -> dict[str, Any]:
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code, safe='')}"
        try:
            return await self.t.get_json(base_url, endpoint, timeout_s=35)
        except Exception as exc:
            # QR status is a long poll. Retry transport timeouts/connectivity failures,
            # but propagate HTTP protocol errors instead of hiding a bad endpoint/header.
            httpx = self.t._httpx()
            if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
                log.debug("QR poll retry after network timeout/error: %s", exc)
                return {"status": "wait"}
            raise

    async def login(self, local_tokens: list[str], name: str | None = None) -> Account | None:
        current_base = self.settings.api_base_url
        qr = await self.fetch_qr(local_tokens)
        qrcode = str(qr.get("qrcode") or "")
        qr_url = str(qr.get("qrcode_img_content") or "")
        if not qrcode or not qr_url:
            raise ILinkError(f"二维码接口返回缺少字段: {qr}")
        self._print_qr(qr_url)
        pending_code: str | None = None
        refreshes = 0
        while True:
            status = await self.poll_qr(current_base, qrcode, pending_code)
            kind = status.get("status")
            if kind == "wait":
                continue
            if kind == "scaned":
                pending_code = None
                print("已扫码，等待手机确认…")
                await asyncio.sleep(1)
                continue
            if kind == "need_verifycode":
                pending_code = (await asyncio.to_thread(input, "请输入手机微信显示的数字：")).strip()
                continue
            if kind == "verify_code_blocked":
                raise ILinkError("验证码错误次数过多，请稍后重新登录")
            if kind == "scaned_but_redirect":
                host = status.get("redirect_host")
                if host:
                    current_base = f"https://{host}"
                continue
            if kind == "binded_redirect":
                print("该微信已经与现有绑定关联；未生成新凭据。")
                return None
            if kind == "expired":
                refreshes += 1
                if refreshes >= 3:
                    raise ILinkError("二维码多次过期，登录终止")
                qr = await self.fetch_qr(local_tokens)
                qrcode = str(qr.get("qrcode") or "")
                qr_url = str(qr.get("qrcode_img_content") or "")
                self._print_qr(qr_url)
                current_base = self.settings.api_base_url
                continue
            if kind == "confirmed":
                account_id = str(status.get("ilink_bot_id") or "")
                token = str(status.get("bot_token") or "")
                if not account_id or not token:
                    raise ILinkError(f"登录确认成功但凭据不完整: {status}")
                return Account(
                    account_id=account_id,
                    bot_token=token,
                    base_url=str(status.get("baseurl") or self.settings.api_base_url),
                    user_id=str(status.get("ilink_user_id") or "") or None,
                    name=name,
                )
            raise ILinkError(f"未知二维码状态: {status}")

    @staticmethod
    def _print_qr(url: str) -> None:
        print("\n请用手机微信扫码绑定：")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except Exception:
            pass
        print(f"二维码链接：{url}\n")


class ILinkClient:
    def __init__(self, transport: ILinkTransport, settings: Settings, account: Account):
        self.t = transport
        self.settings = settings
        self.account = account
        self.next_long_poll_ms = settings.long_poll_timeout_ms

    async def notify_start(self) -> None:
        try:
            await self.t.post_json(
                self.account.base_url,
                "ilink/bot/msg/notifystart",
                {"base_info": self.t.base_info()},
                token=self.account.bot_token,
            )
        except Exception as exc:
            log.warning("notify_start failed for %s: %s", self.account.account_id, exc)

    async def notify_stop(self) -> None:
        try:
            await self.t.post_json(
                self.account.base_url,
                "ilink/bot/msg/notifystop",
                {"base_info": self.t.base_info()},
                token=self.account.bot_token,
            )
        except Exception as exc:
            log.debug("notify_stop failed for %s: %s", self.account.account_id, exc)

    async def get_updates(self, cursor: str) -> dict[str, Any]:
        timeout_s = max(5.0, (self.next_long_poll_ms + 5_000) / 1000)
        try:
            resp = await self.t.post_json(
                self.account.base_url,
                "ilink/bot/getupdates",
                {"get_updates_buf": cursor or "", "base_info": self.t.base_info()},
                token=self.account.bot_token,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            httpx = self.t._httpx()
            if isinstance(exc, httpx.TimeoutException):
                return {"ret": 0, "msgs": [], "get_updates_buf": cursor}
            raise
        suggested = resp.get("longpolling_timeout_ms")
        if isinstance(suggested, int) and 5_000 <= suggested <= 120_000:
            self.next_long_poll_ms = suggested
        if resp.get("errcode") == -14:
            raise StaleAccountTokenError("iLink 返回 errcode=-14：账号 token 已失效，需要重新扫码绑定")
        ret = resp.get("ret", 0)
        if ret not in (None, 0):
            raise ILinkError(f"getupdates ret={ret} errcode={resp.get('errcode')} errmsg={resp.get('errmsg')}")
        return resp

    async def send_text(self, to_user_id: str, context_token: str, text: str) -> None:
        item = {"type": ITEM_TEXT, "text_item": {"text": text}}
        await self._send_item(to_user_id, context_token, item)

    async def _send_item(self, to_user_id: str, context_token: str, item: dict[str, Any]) -> None:
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"jmwxbot-{uuid.uuid4()}",
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "item_list": [item],
                "context_token": context_token or None,
            },
            "base_info": self.t.base_info(),
        }
        resp = await self.t.post_json(
            self.account.base_url,
            "ilink/bot/sendmessage",
            payload,
            token=self.account.bot_token,
        )
        ret = resp.get("ret", 0)
        if ret == -2:
            raise StaleContextError("context_token 已失效；请让该联系人重新发一条消息后再试")
        if ret not in (None, 0):
            raise ILinkError(f"sendmessage ret={ret} errmsg={resp.get('errmsg')}")

    async def send_image(self, to_user_id: str, context_token: str, image_path: Path) -> None:
        uploaded = await self._upload_media(to_user_id, image_path, media_type=UPLOAD_IMAGE)
        item = {
            "type": ITEM_IMAGE,
            "image_item": {
                "media": {
                    "encrypt_query_param": uploaded["download_param"],
                    "aes_key": encode_image_aes_key(uploaded["aes_key"]),
                    "encrypt_type": 1,
                },
                # Tencent's current implementation uses the AES ciphertext size here.
                "mid_size": uploaded["cipher_size"],
            },
        }
        await self._send_item(to_user_id, context_token, item)

    async def send_file(self, to_user_id: str, context_token: str, file_path: Path) -> None:
        uploaded = await self._upload_media(to_user_id, file_path, media_type=UPLOAD_FILE)
        item = {
            "type": ITEM_FILE,
            "file_item": {
                "media": {
                    "encrypt_query_param": uploaded["download_param"],
                    "aes_key": encode_media_aes_key(uploaded["aes_key"]),
                    "encrypt_type": 1,
                },
                "file_name": file_path.name,
                "len": str(uploaded["raw_size"]),
            },
        }
        await self._send_item(to_user_id, context_token, item)

    async def _upload_media(self, to_user_id: str, file_path: Path, *, media_type: int) -> dict[str, Any]:
        raw_size, raw_md5 = await asyncio.to_thread(_file_size_md5, file_path)
        cipher_size = ((raw_size + 1 + 15) // 16) * 16
        file_key = os.urandom(16).hex()
        aes_key = os.urandom(16)
        resp = await self.t.post_json(
            self.account.base_url,
            "ilink/bot/getuploadurl",
            {
                "filekey": file_key,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": cipher_size,
                "no_need_thumb": True,
                "aeskey": aes_key.hex(),
                "base_info": self.t.base_info(),
            },
            token=self.account.bot_token,
        )
        upload_full_url = str(resp.get("upload_full_url") or "").strip()
        upload_param = str(resp.get("upload_param") or "").strip()
        if upload_full_url:
            url = upload_full_url
        elif upload_param:
            url = (
                f"{self.settings.cdn_base_url}/upload"
                f"?encrypted_query_param={quote(upload_param, safe='')}"
                f"&filekey={quote(file_key, safe='')}"
            )
        else:
            raise ILinkError(f"getuploadurl 未返回上传地址: {resp}")

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(cipher_size),
                }
                res = await self.t.http.post(
                    url,
                    headers=headers,
                    content=_encrypted_file_stream(file_path, aes_key),
                    timeout=None,
                )
                if 400 <= res.status_code < 500:
                    raise ILinkError(f"CDN 上传被拒绝 HTTP {res.status_code}: {res.text[:300]}")
                if res.status_code != 200:
                    raise ILinkError(f"CDN 上传失败 HTTP {res.status_code}")
                download_param = res.headers.get("x-encrypted-param")
                if not download_param:
                    raise ILinkError("CDN 响应缺少 x-encrypted-param")
                return {
                    "download_param": download_param,
                    "aes_key": aes_key,
                    "raw_size": raw_size,
                    "cipher_size": cipher_size,
                }
            except Exception as exc:
                last_error = exc
                if isinstance(exc, ILinkError) and "被拒绝" in str(exc):
                    break
                if attempt < 3:
                    await asyncio.sleep(attempt)
        raise ILinkError(f"CDN 上传连续失败: {last_error}")



def encode_image_aes_key(raw_key: bytes) -> str:
    """Encode outbound IMAGE aes_key exactly like Tencent's current implementation.

    Tencent stores UploadedFileInfo.aeskey as a hex string and then base64-encodes
    that string when constructing CDNMedia.aes_key, i.e. base64(ASCII hex).
    """
    return encode_media_aes_key(raw_key)


def encode_media_aes_key(raw_key: bytes) -> str:
    """Encode outbound FILE aes_key exactly like Tencent's current implementation.

    Wire format: base64(ASCII hex of the raw 16-byte AES key).
    """
    if len(raw_key) != 16:
        raise ValueError("AES-128 key must be exactly 16 bytes")
    return base64.b64encode(raw_key.hex().encode("ascii")).decode("ascii")

def _file_size_md5(path: Path) -> tuple[int, str]:
    h = hashlib.md5()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


async def _encrypted_file_stream(path: Path, key: bytes) -> AsyncIterator[bytes]:
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 pycryptodome，请先安装项目依赖") from exc

    cipher = AES.new(key, AES.MODE_ECB)
    carry = b""
    with path.open("rb") as f:
        while True:
            chunk = await asyncio.to_thread(f.read, 1024 * 1024)
            if not chunk:
                break
            data = carry + chunk
            full_len = (len(data) // 16) * 16
            if full_len:
                yield cipher.encrypt(data[:full_len])
            carry = data[full_len:]
    yield cipher.encrypt(pad(carry, 16))
