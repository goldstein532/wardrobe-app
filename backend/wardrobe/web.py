"""Чтение публичной веб-ленты канала (t.me/s/<канал>) — вариант без прав админа у бота.

Что видно в веб-ленте: текст постов (с форматированием), фото (до 800 px по длинной
стороне), альбомы, ответы на посты, отметку «edited». Что не видно: посты, которые
Telegram помечает «Please open Telegram to view this post» (часть пересылок и медиа),
комментарии к постам.
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .catalog import Message, Photo

USER_AGENT = "Mozilla/5.0 (compatible; wardrobe-sync/1.0)"
_BG_RE = re.compile(r"background-image:url\('([^']+)'\)")
_ID_RE = re.compile(r"/(\d+)(?:\?|$)")
_PREV_RE = re.compile(r'<link rel="prev" href="[^"]*before=(\d+)"')


def chat_id_for(username: str) -> int:
    """У веб-ленты нет числовых id каналов — делаем стабильный id из username."""
    return -(zlib.crc32(username.lower().encode()) or 1)


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


@dataclass
class WebPhoto:
    message_id: int
    url: str
    ratio: float | None = None


@dataclass
class WebPost:
    channel: str
    message_id: int
    date: int = 0
    edited: bool = False
    supported: bool = True
    text: str = ""
    entities: list[dict[str, Any]] = field(default_factory=list)
    photos: list[WebPhoto] = field(default_factory=list)
    reply_to: int | None = None
    forwarded_from: tuple[str, int] | None = None

    @property
    def message_ids(self) -> list[int]:
        return sorted({self.message_id, *(p.message_id for p in self.photos)})

    def to_messages(self, seen_at: int) -> list[Message]:
        """Пост ленты → сообщения для каталога (альбом — по сообщению на фото)."""
        cid = chat_id_for(self.channel)
        fwd = (chat_id_for(self.forwarded_from[0]), self.forwarded_from[1]) if self.forwarded_from else None
        group = f"w{self.message_id}" if len(self.photos) > 1 else None
        common = dict(chat_id=cid, date=self.date or seen_at, edit_date=seen_at,
                      media_group_id=group, chat_username=self.channel, forward_from=fwd)
        if not self.photos:
            return [Message(message_id=self.message_id, text=self.text, entities=self.entities, **common)]
        out = []
        for i, p in enumerate(self.photos):
            photo = Photo(file_id=p.url, file_unique_id=f"{self.channel}-{p.message_id}")
            out.append(Message(
                message_id=p.message_id, photo=photo,
                text=self.text if i == 0 else None, entities=self.entities if i == 0 else [],
                **common,
            ))
        return out


class _BlockParser(HTMLParser):
    """Разбирает один блок сообщения ленты."""

    def __init__(self, post: WebPost) -> None:
        super().__init__(convert_charrefs=True)
        self.p = post
        self.stack: list[str] = []      # открытые теги внутри текста
        self.text_depth = 0             # >0 — мы внутри div.js-message_text
        self.div_depth = 0
        self.skip_depth = 0             # цитата ответа (js-message_reply_text) — пропускаем
        self.buf: list[str] = []
        self.open_quote: tuple[int, str] | None = None
        self.open_link: tuple[int, str] | None = None
        self.in_time = False

    # --- текст
    def _offset(self) -> int:
        return _u16("".join(self.buf))

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs_list}
        cls = a.get("class", "")
        if tag == "div":
            self.div_depth += 1
            if self.text_depth:
                self.text_depth += 1
            elif "js-message_reply_text" in cls:
                self.skip_depth = self.div_depth
            elif "js-message_text" in cls:
                self.text_depth = 1
            elif "message_media_not_supported" in cls:
                self.p.supported = False
            return
        if self.text_depth:
            if tag == "br":
                self.buf.append("\n")
            elif tag == "blockquote":
                kind = "expandable_blockquote" if "expandable" in cls else "blockquote"
                self.open_quote = (self._offset(), kind)
            elif tag == "a":
                href = a.get("href", "")
                if href.startswith("http"):
                    self.open_link = (self._offset(), href)
            return
        if tag == "a":
            href = a.get("href", "")
            if "tgme_widget_message_photo_wrap" in cls or "tgme_widget_message_video_player" in cls:
                m = _ID_RE.search(href)
                bg = _BG_RE.search(a.get("style", ""))
                if m and bg:
                    ratio = float(a["data-ratio"]) if a.get("data-ratio") else None
                    self.p.photos.append(WebPhoto(int(m.group(1)), bg.group(1), ratio))
            elif "tgme_widget_message_reply" in cls:
                m = _ID_RE.search(href)
                if m and f"/{self.p.channel}/" in href.lower():
                    self.p.reply_to = int(m.group(1))
            elif "tgme_widget_message_forwarded_from_name" in cls:
                m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", href)
                if m:
                    self.p.forwarded_from = (m.group(1).lower(), int(m.group(2)))
        elif tag == "i" and "tgme_widget_message_video_thumb" in cls:
            bg = _BG_RE.search(a.get("style", ""))
            if bg and not self.p.photos:
                self.p.photos.append(WebPhoto(self.p.message_id, bg.group(1)))
        elif tag == "time" and a.get("datetime") and not self.p.date:
            try:
                self.p.date = int(datetime.fromisoformat(a["datetime"]).timestamp())
            except ValueError:
                pass
        elif tag == "span" and "tgme_widget_message_meta" in cls:
            self.in_time = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self.skip_depth == self.div_depth:
                self.skip_depth = 0
            if self.text_depth:
                self.text_depth -= 1
            self.div_depth -= 1
            return
        if not self.text_depth:
            if tag == "span":
                self.in_time = False
            return
        if tag == "blockquote" and self.open_quote:
            start, kind = self.open_quote
            self.p.entities.append({"type": kind, "offset": start, "length": self._offset() - start})
            self.open_quote = None
        elif tag == "a" and self.open_link:
            start, href = self.open_link
            length = self._offset() - start
            if length:
                self.p.entities.append({"type": "text_link", "offset": start, "length": length, "url": href})
            self.open_link = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.text_depth and not self.skip_depth:
            self.buf.append(data)
        elif self.in_time and "edited" in data:
            self.p.edited = True

    def finish(self) -> WebPost:
        self.p.text = "".join(self.buf).strip("\n ")
        return self.p


def parse_page(html: str, channel: str) -> tuple[list[WebPost], int | None]:
    """HTML страницы ленты → (посты, id для следующей, более старой страницы)."""
    channel = channel.lower()
    posts: list[WebPost] = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', html)[1:]:
        m = re.search(r'data-post="([^"/]+)/(\d+)"', block)
        if not m or m.group(1).lower() != channel:
            continue
        parser = _BlockParser(WebPost(channel=channel, message_id=int(m.group(2))))
        parser.feed(block)
        parser.close()
        posts.append(parser.finish())
    prev = _PREV_RE.search(html)
    return posts, int(prev.group(1)) if prev else None


class WebClient:
    def __init__(self, delay: float = 1.0, timeout: int = 30) -> None:
        self.delay, self.timeout = delay, timeout
        self._last = 0.0

    def _get(self, url: str, throttle: bool = True) -> bytes:
        wait = self.delay - (time.monotonic() - self._last)
        if throttle and wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=self.timeout) as r:
                    self._last = time.monotonic()
                    return r.read()
            except HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise
            except URLError:
                if attempt == 2:
                    raise
                time.sleep(3)
        raise RuntimeError("unreachable")

    def page(self, channel: str, before: int | None = None) -> str:
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        return self._get(url).decode("utf-8", errors="replace")

    def download(self, url: str) -> bytes:
        return self._get(url, throttle=False)  # фото с CDN, страницы ленты не нагружает
