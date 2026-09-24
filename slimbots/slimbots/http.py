"""The async REST side of `Bot`, on httpx rather than `Client`'s urllib; see docs/framework.md for why."""

from __future__ import annotations

import asyncio
import urllib.parse
import uuid
from typing import Any, Awaitable, Callable

import httpx

from .client import socket_url
from .models import Attachment, DmConversation, Message

DEFAULT_TIMEOUT = 15.0


class ApiError(Exception):
    """An HTTP error response (`status` set), or a network failure (`status` None)."""

    def __init__(self, status: int | None, body: Any = None) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}" if status else f"network error: {body}")

    @property
    def reason(self) -> str | None:
        """The server's short `error` string, when the body is the usual `{"error": ...}` shape."""
        return self.body.get("error") if isinstance(self.body, dict) else None


def is_token_revoked(err: BaseException) -> bool:
    return isinstance(err, ApiError) and err.status == 401


def is_forbidden(err: BaseException) -> bool:
    return isinstance(err, ApiError) and err.status == 403


def is_not_found(err: BaseException) -> bool:
    return isinstance(err, ApiError) and err.status == 404


def is_rate_limited(err: BaseException) -> bool:
    return isinstance(err, ApiError) and err.status == 429


def is_retryable(err: BaseException) -> bool:
    return isinstance(err, ApiError) and (err.status is None or err.status == 429 or err.status >= 500)


def _error_from_response(response: httpx.Response) -> ApiError:
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    return ApiError(response.status_code, detail)


class AsyncClient:
    """An authenticated async slim-m REST client for one bot token."""

    def __init__(self, base: str, token: str, user_agent: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not base or not token:
            raise ValueError("an AsyncClient needs both a base URL and a token")
        self.base = base.rstrip("/")
        self.token = token
        self.user_agent = user_agent
        self._http = httpx.AsyncClient(
            base_url=self.base,
            timeout=timeout,
            headers={"authorization": f"Bearer {token}", "user-agent": user_agent},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    def socket_url(self) -> str:
        return socket_url(self.base)

    async def _send_once(
        self, method: str, path: str, body: Any, params: dict[str, Any] | None,
        headers: dict[str, str] | None, raw_body: bytes | None,
    ) -> httpx.Response:
        if raw_body is not None:
            return await self._http.request(method, path, content=raw_body, params=params, headers=headers)
        return await self._http.request(method, path, json=body, params=params, headers=headers)

    async def call(
        self, method: str, path: str, body: Any = None, *, params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None, raw_body: bytes | None = None,
        retries: int = 5, base_delay: float = 0.5, max_delay: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> Any:
        """One authenticated call, retrying a genuinely uncertain outcome; `raw_body` sends bytes as-is (an attachment)."""
        attempt = 0
        while True:
            try:
                response = await self._send_once(method, path, body, params, headers, raw_body)
            except httpx.HTTPError as err:
                if attempt >= retries:
                    raise ApiError(None, str(err)) from err
                await sleep(min(base_delay * (2**attempt), max_delay))
                attempt += 1
                continue
            if response.status_code >= 400:
                err = _error_from_response(response)
                if attempt >= retries or not is_retryable(err):
                    raise err
                await sleep(min(base_delay * (2**attempt), max_delay))
                attempt += 1
                continue
            return response.json() if response.content else None

    async def me(self) -> Any:
        return await self.call("GET", "/me")

    async def ws_ticket(self) -> str:
        ticket = await self.call("POST", "/auth/ws-ticket")
        return ticket["ticket"]

    async def get_user(self, user_id: str) -> Any:
        return await self.call("GET", f"/users/{user_id}")

    async def list_members(self, *, after: str | None = None, limit: int = 200) -> Any:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        return await self.call("GET", "/members", params=params)

    async def list_channels(self) -> Any:
        return await self.call("GET", "/channels")

    async def list_roles(self) -> Any:
        return await self.call("GET", "/roles")

    async def assign_role(self, user_id: str, role_id: str) -> None:
        await self.call("PUT", f"/members/{user_id}/roles/{role_id}")

    async def unassign_role(self, user_id: str, role_id: str) -> None:
        await self.call("DELETE", f"/members/{user_id}/roles/{role_id}")

    async def send(
        self, channel_id: str, content: str, *, message_id: str | None = None, reply_to_id: str | None = None,
        attachment_ids: list[str] | None = None, embeds: list[dict[str, Any]] | None = None,
        fallback_content: str | None = None,
    ) -> Message:
        """Posts a message under a stable id; on rejection with `embeds` set, retries once as plain `fallback_content`."""
        message_id = message_id or str(uuid.uuid4())
        body: dict[str, Any] = {"id": message_id, "content": content}
        if reply_to_id:
            body["reply_to_id"] = reply_to_id
        if attachment_ids:
            body["attachment_ids"] = attachment_ids
        if embeds:
            body["embeds"] = embeds
        try:
            data = await self.call("POST", f"/channels/{channel_id}/messages", body)
        except ApiError:
            if not embeds or fallback_content is None:
                raise
            body = {"id": message_id, "content": fallback_content}
            if reply_to_id:
                body["reply_to_id"] = reply_to_id
            if attachment_ids:
                body["attachment_ids"] = attachment_ids
            data = await self.call("POST", f"/channels/{channel_id}/messages", body)
        return Message(data, client=self, channel_id=channel_id)

    async def edit_message(self, channel_id: str, message_id: str, content: str) -> Any:
        return await self.call("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"content": content})

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        await self.call("DELETE", f"/channels/{channel_id}/messages/{message_id}")

    async def add_reaction(self, message_id: str, emoji: str) -> None:
        await self.call("PUT", f"/messages/{message_id}/reactions/{urllib.parse.quote(emoji, safe='')}")

    async def remove_reaction(self, message_id: str, emoji: str) -> None:
        await self.call("DELETE", f"/messages/{message_id}/reactions/{urllib.parse.quote(emoji, safe='')}")

    async def pin_message(self, channel_id: str, message_id: str) -> None:
        await self.call("PUT", f"/channels/{channel_id}/messages/{message_id}/pin")

    async def unpin_message(self, channel_id: str, message_id: str) -> None:
        await self.call("DELETE", f"/channels/{channel_id}/messages/{message_id}/pin")

    async def list_pinned_messages(self, channel_id: str, *, limit: int | None = None) -> Any:
        params = {"limit": limit} if limit else None
        return await self.call("GET", f"/channels/{channel_id}/pins", params=params)

    async def open_thread(self, channel_id: str, message_id: str) -> Any:
        return await self.call("POST", f"/channels/{channel_id}/messages/{message_id}/thread")

    async def vote_poll(self, message_id: str, option: int) -> None:
        await self.call("PUT", f"/messages/{message_id}/polls/vote", {"option": option})

    async def upload_attachment(self, data: bytes, *, filename: str | None = None) -> Attachment:
        """Uploads raw bytes and returns an `Attachment` whose `.id` `send`'s `attachment_ids` accepts."""
        path = "/attachments"
        if filename:
            path += f"?filename={urllib.parse.quote(filename, safe='')}"
        response = await self.call(
            "POST", path, raw_body=data, headers={"content-type": "application/octet-stream"}
        )
        return Attachment(response)

    async def list_dms(self) -> list[DmConversation]:
        return [DmConversation(d) for d in await self.call("GET", "/dms")]

    async def open_dm(self, user_id: str) -> DmConversation:
        return DmConversation(await self.call("POST", f"/dms/{user_id}"))

    async def close_dm(self, user_id: str) -> None:
        await self.call("DELETE", f"/dms/{user_id}")
