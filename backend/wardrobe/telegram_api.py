"""Минимальный клиент Bot API на стандартной библиотеке (без зависимостей).

Токен не попадает в текст исключений и логи.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ALLOWED_UPDATES = ["channel_post", "edited_channel_post", "message", "edited_message", "callback_query"]


class TelegramError(Exception):
    def __init__(self, method: str, code: int, description: str, retry_after: int | None = None):
        super().__init__(f"{method}: {code} {description}")
        self.code, self.description, self.retry_after = code, description, retry_after


class BotAPI:
    def __init__(self, token: str, timeout: int = 40) -> None:
        self._base = f"https://api.telegram.org/bot{token}/"
        self._files = f"https://api.telegram.org/file/bot{token}/"
        self.timeout = timeout

    def call(self, method: str, **params: Any) -> Any:
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        for attempt in range(3):
            req = Request(self._base + method, data=body, headers={"Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=self.timeout) as r:
                    payload = json.load(r)
            except HTTPError as e:
                try:
                    payload = json.load(e)
                except Exception:
                    raise TelegramError(method, e.code, "HTTP error") from None
            except URLError as e:
                if attempt == 2:
                    raise TelegramError(method, 0, f"network: {e.reason}") from None
                time.sleep(2)
                continue
            if payload.get("ok"):
                return payload["result"]
            retry = (payload.get("parameters") or {}).get("retry_after")
            if payload.get("error_code") == 429 and retry and attempt < 2:
                time.sleep(min(int(retry), 30))
                continue
            raise TelegramError(method, payload.get("error_code", 0), payload.get("description", ""), retry)
        raise TelegramError(method, 0, "retries exhausted")

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        return self.call("getUpdates", offset=offset, timeout=0, limit=100, allowed_updates=ALLOWED_UPDATES)

    def download(self, file_id: str) -> bytes:
        path = self.call("getFile", file_id=file_id)["file_path"]
        try:
            with urlopen(self._files + path, timeout=self.timeout) as r:
                return r.read()
        except (HTTPError, URLError):
            raise TelegramError("download", 0, "file download failed") from None

    def send_message(self, chat_id: int, text: str, **extra: Any) -> Any:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         link_preview_options={"is_disabled": True}, **extra)
