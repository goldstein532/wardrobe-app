"""Преобразование апдейтов Bot API в простые структуры каталога."""

from __future__ import annotations

from typing import Any

from .catalog import Message, Photo


def largest_photo(sizes: list[dict[str, Any]] | None) -> Photo | None:
    if not sizes:
        return None
    big = max(sizes, key=lambda p: (p.get("width", 0) * p.get("height", 0), p.get("file_size", 0)))
    return Photo(big["file_id"], big["file_unique_id"], big.get("width", 0), big.get("height", 0))


def channel_message(post: dict[str, Any]) -> Message:
    """channel_post / edited_channel_post → Message."""
    origin = post.get("forward_origin") or {}
    forward = None
    if origin.get("type") == "channel" and origin.get("chat"):
        forward = (origin["chat"]["id"], origin["message_id"])
    text = post["text"] if "text" in post else post.get("caption")
    entities = post.get("entities") if "text" in post else post.get("caption_entities")
    return Message(
        chat_id=post["chat"]["id"],
        message_id=post["message_id"],
        date=post.get("edit_date") or post["date"],
        text=text,
        entities=list(entities or []),
        photo=largest_photo(post.get("photo")),
        media_group_id=post.get("media_group_id"),
        chat_username=(post["chat"].get("username") or None),
        forward_from=forward,
    )


def automatic_forward(msg: dict[str, Any]) -> tuple[int, tuple[int, int]] | None:
    """Копия поста канала в группе обсуждения → (id сообщения в группе, (канал, id поста))."""
    if not msg.get("is_automatic_forward"):
        return None
    origin = msg.get("forward_origin") or {}
    if origin.get("type") != "channel":
        return None
    return msg["message_id"], (origin["chat"]["id"], origin["message_id"])


def comment_thread(msg: dict[str, Any]) -> int | None:
    """id сообщения-копии поста, к которому относится комментарий."""
    reply = msg.get("reply_to_message") or {}
    if reply.get("is_automatic_forward"):
        return reply["message_id"]
    return msg.get("message_thread_id")
