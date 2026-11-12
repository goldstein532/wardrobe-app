"""Каталог: состояние витрины и правила его изменения.

Все входящие сообщения каналов проходят через `Catalog.ingest`. Метод
идемпотентен: повторный апдейт или правка без изменений не создаёт дублей
и не порождает событий. События (`Event`) дальше используются для
уведомлений подписчикам и сообщений админу.

Ключи:
- сообщение: "<chat_id>:<message_id>";
- пост: "<chat_id>:g<media_group_id>" для альбома, иначе "<chat_id>:m<message_id>".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .parser import ParsedPost, Status, parse_post, plan_items


# ---------------------------------------------------------------- модели

@dataclass
class Photo:
    file_id: str
    file_unique_id: str
    width: int = 0
    height: int = 0
    message_id: int = 0


@dataclass
class Message:
    """Сообщение канала, уже приведённое к простому виду (из Bot API или Telethon)."""
    chat_id: int
    message_id: int
    date: int
    text: str | None = None
    entities: list[dict[str, Any]] = field(default_factory=list)
    photo: Photo | None = None
    media_group_id: str | None = None
    chat_username: str | None = None
    forward_from: tuple[int, int] | None = None  # (chat_id, message_id) исходного поста


@dataclass
class Post:
    key: str
    chat_id: int
    chat_username: str | None
    date: int
    message_ids: list[int] = field(default_factory=list)
    text: str | None = None
    entities: list[dict[str, Any]] = field(default_factory=list)
    text_message_id: int | None = None
    photos: list[Photo] = field(default_factory=list)
    alias_of: str | None = None          # пост — пересылка другого известного поста
    item_ids: list[int] = field(default_factory=list)
    last_status: str | None = None       # статус по хэштегам при прошлом разборе
    retired: bool = False                # товар объединён с другим постом, правки игнорируются


@dataclass
class Item:
    id: int
    post_key: str
    part: int
    status: str
    created_at: int
    updated_at: int
    hidden: bool = False
    title: str | None = None
    brand: str | None = None
    name: str | None = None
    season: str | None = None
    category: str | None = None
    description: str | None = None
    size: str | None = None
    size_note: str | None = None
    condition: str | None = None
    condition_note: str | None = None
    price: int | None = None
    currency: str = "RUB"
    authentic: bool = False
    delivery: str | None = None
    buy_link: str | None = None
    photos: list[Photo] = field(default_factory=list)
    extra_photos: list[Photo] = field(default_factory=list)
    refs: list[list[Any]] = field(default_factory=list)  # [[chat_username, message_id], ...]
    status_changed_at: int = 0

    @property
    def no(self) -> str:
        return f"{self.id:03d}"


@dataclass
class Event:
    kind: str  # created | status | updated | hidden | review | merge_candidate
    item_id: int
    status: str | None = None
    prev_status: str | None = None
    other_id: int | None = None


# Поля товара, которые берутся из разбора поста.
_PARSED_FIELDS = (
    "title", "brand", "name", "season", "category", "description", "size_note",
    "condition", "condition_note", "price", "currency", "authentic", "delivery", "buy_link",
)


def _norm(s: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


# ---------------------------------------------------------------- каталог

class Catalog:
    def __init__(self, main_username: str | None = None) -> None:
        self.main_username = (main_username or "").lstrip("@").lower() or None
        self.posts: dict[str, Post] = {}
        self.msg_index: dict[str, str] = {}
        self.items: dict[int, Item] = {}
        self.next_id = 1

    # ------------------------------------------------------------ входящие

    def ingest(self, m: Message) -> list[Event]:
        mkey = f"{m.chat_id}:{m.message_id}"
        pkey = self.msg_index.get(mkey) or (
            f"{m.chat_id}:g{m.media_group_id}" if m.media_group_id else f"{m.chat_id}:m{m.message_id}"
        )
        post = self.posts.get(pkey)
        if post is None:
            post = Post(key=pkey, chat_id=m.chat_id, chat_username=m.chat_username, date=m.date)
            self.posts[pkey] = post
        self.msg_index[mkey] = pkey

        if m.message_id not in post.message_ids:
            post.message_ids.append(m.message_id)
            post.message_ids.sort()
        post.date = min(post.date, m.date)
        post.chat_username = m.chat_username or post.chat_username

        if m.text and m.text.strip():
            post.text, post.entities, post.text_message_id = m.text, list(m.entities), m.message_id
        elif post.text_message_id == m.message_id:  # подпись удалили
            post.text, post.entities = "", []

        if m.photo:
            photo = Photo(**{**asdict(m.photo), "message_id": m.message_id})
            post.photos = [p for p in post.photos if p.message_id != m.message_id] + [photo]
            post.photos.sort(key=lambda p: p.message_id)

        if m.forward_from and post.alias_of is None:
            origin = self.msg_index.get(f"{m.forward_from[0]}:{m.forward_from[1]}")
            if origin and origin != pkey:
                post.alias_of = origin

        return self._evaluate(post, now=m.date)

    def add_extra_photo(self, chat_id: int, message_id: int, photo: Photo) -> bool:
        """Доп. фото из комментариев к посту (только от админов — проверяет бот)."""
        pkey = self.msg_index.get(f"{chat_id}:{message_id}")
        if not pkey:
            return False
        post = self.posts[pkey]
        target = self.posts.get(post.alias_of, post) if post.alias_of else post
        added = False
        for iid in target.item_ids:
            item = self.items[iid]
            if all(p.file_unique_id != photo.file_unique_id for p in item.photos + item.extra_photos):
                item.extra_photos.append(photo)
                added = True
        return added

    # ------------------------------------------------------------ разбор поста

    def _evaluate(self, post: Post, now: int) -> list[Event]:
        if post.retired:
            return []
        target = self.posts[post.alias_of] if post.alias_of else post
        existing = [self.items[i] for i in target.item_ids if i in self.items]
        parsed = parse_post(post.text, post.entities, known_item=bool(existing))
        if not parsed.is_product:
            return []

        photos = post.photos or target.photos
        drafts, needs_review = plan_items(parsed, len(photos))
        status_changed = parsed.status.value != post.last_status
        post.last_status = parsed.status.value
        ref = [post.chat_username, post.message_ids[0]] if post.message_ids else None

        events: list[Event] = []
        created: list[Item] = []
        for d in drafts:
            fields = self._fields(parsed, d.size, [photos[i] for i in d.photo_indices])
            item = existing[d.index] if d.index < len(existing) else None
            if item is None:
                item = Item(
                    id=self.next_id, post_key=target.key, part=d.index,
                    status=parsed.status.value, created_at=post.date, updated_at=now,
                    status_changed_at=now, **fields,
                )
                self.next_id += 1
                self.items[item.id] = item
                target.item_ids.append(item.id)
                created.append(item)
                events.append(Event("created", item.id, status=item.status))
            else:
                # Пересланный пост несёт те же фото под другими message_id — это не изменение.
                if [p.file_unique_id for p in fields["photos"]] == [p.file_unique_id for p in item.photos]:
                    fields["photos"] = item.photos
                changed = any(getattr(item, k) != v for k, v in fields.items())
                for k, v in fields.items():
                    setattr(item, k, v)
                if item.hidden:
                    item.hidden, changed = False, True
                # Статус из поста применяется, только если он изменился в самом посте:
                # ручная смена статуса админом не сбрасывается правкой опечатки.
                if status_changed and item.status != parsed.status.value:
                    events.append(Event("status", item.id, parsed.status.value, item.status))
                    item.status, item.status_changed_at = parsed.status.value, now
                elif changed:
                    events.append(Event("updated", item.id, status=item.status))
                if changed or events:
                    item.updated_at = now
            if ref and ref not in item.refs:
                item.refs.append(ref)

        for extra in existing[len(drafts):]:  # размеров стало меньше
            if not extra.hidden:
                extra.hidden = True
                events.append(Event("hidden", extra.id, status=extra.status))

        if needs_review and created:
            events.append(Event("review", created[0].id))
        for item in created:
            other = self._merge_candidate(item)
            if other:
                events.append(Event("merge_candidate", item.id, other_id=other.id))
        return events

    @staticmethod
    def _fields(parsed: ParsedPost, size: str | None, photos: list[Photo]) -> dict[str, Any]:
        fields = {k: getattr(parsed, k) for k in _PARSED_FIELDS}
        fields["size"] = size
        fields["photos"] = list(photos)
        return fields

    def _merge_candidate(self, item: Item) -> Item | None:
        """Новая вещь «в наличии», совпадающая с вещью «в пути» → вероятно, та же."""
        if item.status != Status.AVAILABLE.value:
            return None
        key = (_norm(item.brand), _norm(item.name), _norm(item.size))
        for other in self.items.values():
            if (
                other.id != item.id and not other.hidden
                and other.status == Status.TRANSIT.value
                and other.post_key != item.post_key
                and (_norm(other.brand), _norm(other.name), _norm(other.size)) == key
            ):
                return other
        return None

    # ------------------------------------------------------------ действия админа

    def items_for_message(self, chat_id: int, message_id: int) -> list[Item]:
        pkey = self.msg_index.get(f"{chat_id}:{message_id}")
        if not pkey:
            return []
        post = self.posts[pkey]
        target = self.posts.get(post.alias_of, post) if post.alias_of else post
        return [self.items[i] for i in target.item_ids if i in self.items]

    def set_status(self, item_id: int, status: Status, now: int) -> Event | None:
        item = self.items[item_id]
        if item.status == status.value and not item.hidden:
            return None
        prev, item.status, item.hidden = item.status, status.value, False
        item.status_changed_at = item.updated_at = now
        return Event("status", item_id, status.value, prev)

    def hide(self, item_id: int, now: int) -> Event | None:
        item = self.items[item_id]
        if item.hidden:
            return None
        item.hidden, item.updated_at = True, now
        return Event("hidden", item_id, status=item.status)

    def merge(self, keep_id: int, drop_id: int, now: int) -> Event | None:
        """Вещь из «в пути» приехала: старый номер сохраняется, данные берутся из нового поста."""
        keep, drop = self.items[keep_id], self.items.pop(drop_id)
        old_post = self.posts[keep.post_key]
        old_post.item_ids = [i for i in old_post.item_ids if i != keep_id]
        if not old_post.item_ids:
            old_post.retired = True  # старый пост «в пути» больше не управляет товаром
        post = self.posts[drop.post_key]
        post.item_ids = [keep_id if i == drop_id else i for i in post.item_ids]
        prev = keep.status
        for k in (*_PARSED_FIELDS, "size", "photos", "status"):
            setattr(keep, k, getattr(drop, k))
        keep.post_key, keep.part = drop.post_key, drop.part
        keep.extra_photos += [p for p in drop.extra_photos if p not in keep.extra_photos]
        keep.refs += [r for r in drop.refs if r not in keep.refs]
        keep.hidden, keep.updated_at = False, now
        if keep.status != prev:
            keep.status_changed_at = now
            return Event("status", keep_id, keep.status, prev)
        return None

    # ------------------------------------------------------------ экспорт для витрины

    def post_url(self, item: Item) -> str | None:
        refs = [r for r in item.refs if r[0]]
        if not refs:
            return None
        main = [r for r in refs if self.main_username and r[0].lower() == self.main_username]
        username, message_id = (main or refs)[-1]
        return f"https://t.me/{username}/{message_id}"

    def export(self, photo_urls: Callable[[Photo], dict[str, Any]] | None = None) -> dict[str, Any]:
        photo_urls = photo_urls or default_photo_urls

        def public(item: Item, full: bool) -> dict[str, Any]:
            data: dict[str, Any] = {
                "id": item.id, "no": item.no, "status": item.status,
                "brand": item.brand, "name": item.name, "title": item.title,
                "size": item.size, "category": item.category,
                "photos": [photo_urls(p) for p in (item.photos[:1] if not full else item.photos)],
                "post_url": self.post_url(item),
            }
            if full:
                data.update(
                    season=item.season, description=item.description, size_note=item.size_note,
                    condition=item.condition, condition_note=item.condition_note,
                    price=item.price, currency=item.currency, authentic=item.authentic,
                    delivery=item.delivery, buy_url=item.buy_link,
                    extra_photos=[photo_urls(p) for p in item.extra_photos],
                    created_at=item.created_at, status_changed_at=item.status_changed_at,
                )
            return data

        visible = [i for i in self.items.values() if not i.hidden]
        active = sorted((i for i in visible if i.status != Status.SOLD.value), key=lambda i: -i.created_at)
        sold = sorted((i for i in visible if i.status == Status.SOLD.value), key=lambda i: -i.status_changed_at)
        # Для deep link startapp=post_<message_id>: "<канал>/<message_id>" → товары.
        posts: dict[str, list[int]] = {}
        for p in self.posts.values():
            ids = self._post_items(p)
            if p.chat_username and ids:
                for mid in p.message_ids:
                    posts[f"{p.chat_username}/{mid}"] = ids
        return {
            "items": [public(i, full=True) for i in active],
            "archive": [public(i, full=False) for i in sold],
            "posts": posts,
        }

    def _post_items(self, post: Post) -> list[int]:
        target = self.posts.get(post.alias_of, post) if post.alias_of else post
        return [i for i in target.item_ids if i in self.items]

    # ------------------------------------------------------------ сохранение

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "next_id": self.next_id,
                "main_username": self.main_username,
                "posts": [asdict(p) for p in self.posts.values()],
                "msg_index": self.msg_index,
                "items": [asdict(i) for i in self.items.values()],
            },
            ensure_ascii=False, indent=1,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Catalog":
        data = json.loads(raw)
        cat = cls(data.get("main_username"))
        cat.next_id = data["next_id"]
        cat.msg_index = data["msg_index"]
        for p in data["posts"]:
            p["photos"] = [Photo(**x) for x in p["photos"]]
            cat.posts[p["key"]] = Post(**p)
        for i in data["items"]:
            i["photos"] = [Photo(**x) for x in i["photos"]]
            i["extra_photos"] = [Photo(**x) for x in i["extra_photos"]]
            cat.items[i["id"]] = Item(**i)
        return cat


def default_photo_urls(p: Photo) -> dict[str, Any]:
    """Сетка: 720 px по ширине (чётко на экранах 3x при двух колонках).
    Карточка: оригинал из Telegram без уменьшения (до 1280 px, у HD-фото до 2560 px)."""
    return {
        "thumb": f"media/{p.file_unique_id}-720.webp",
        "full": f"media/{p.file_unique_id}-full.webp",
        "w": p.width, "h": p.height,
    }
