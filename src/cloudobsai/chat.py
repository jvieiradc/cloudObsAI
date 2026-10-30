"""Chat API output, authenticated as the app (SPEC 6.1)."""
import asyncio
import hashlib

import aiohttp
import google.auth
import google.auth.transport.requests

SCOPES = ["https://www.googleapis.com/auth/chat.bot"]
API = "https://chat.googleapis.com/v1"
MAX_TEXT = 4000


def request_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


class ChatClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._credentials, _ = google.auth.default(scopes=SCOPES)

    async def _token(self) -> str:
        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, google.auth.transport.requests.Request())
        return self._credentials.token

    async def post(
        self, space: str, text: str, *, req_id: str, thread_name: str | None = None, thread_key: str | None = None
    ) -> dict:
        body: dict = {"text": text[:MAX_TEXT]}
        params = {"requestId": req_id}
        if thread_name or thread_key:
            body["thread"] = {"name": thread_name} if thread_name else {"threadKey": thread_key}
            params["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
        async with self._session.post(
            f"{API}/{space}/messages",
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {await self._token()}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            return await response.json()
