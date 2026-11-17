"""Один проход синхронизации: получить посты → обновить каталог → подготовить фото
→ записать файлы витрины. Запускается GitHub Actions каждые 5 минут.

    python -m wardrobe.sync <папка-данных>

Источник постов (переменная SOURCE):
- web (по умолчанию) — публичная веб-лента t.me/s/<канал>, боту не нужны права в канале;
- bot — апдейты Bot API, бот должен быть админом канала.
Если задан BOT_TOKEN, в обоих режимах работают команды админа в личке бота.

Папка данных (ветка `data` репозитория):
    state.json        — состояние каталога
    sync.json         — offset апдейтов, курсоры ленты, связи с группой обсуждения, очередь событий
    site/catalog.json — то, что читает мини-приложение
    site/media/       — фото
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Protocol

from .adapters import automatic_forward, channel_message, comment_thread, largest_photo
from .catalog import Catalog, Event, Item, Message, default_photo_urls
from .media import build_variants, has_variants
from .parser import Status, reply_status
from .web import WebClient, WebPost, chat_id_for, parse_page

STATUS_WORDS = {
    "наличие": Status.AVAILABLE, "вналичии": Status.AVAILABLE, "available": Status.AVAILABLE,
    "едет": Status.TRANSIT, "впути": Status.TRANSIT, "transit": Status.TRANSIT,
    "бронь": Status.RESERVED, "reserved": Status.RESERVED,
    "продано": Status.SOLD, "sold": Status.SOLD,
}
STATUS_RU = {"available": "в наличии", "transit": "в пути", "reserved": "бронь", "sold": "продано"}
MAX_PENDING = 1000
WEB_NEW_PAGES = 2      # свежие страницы ленты — каждый прогон
WEB_ROLL_PAGES = 10    # плюс столько старых по кругу (ловим правки старых постов и удаления)
# Сменился разбор постов или ответов → один раз перечитываем всю ленту, чтобы применить его к истории.
RESCAN_VERSION = 2
WEB_MAX_PAGES = 400    # предохранитель для первого полного прохода


class Api(Protocol):
    def get_updates(self, offset: int | None) -> list[dict[str, Any]]: ...
    def download(self, file_id: str) -> bytes: ...
    def send_message(self, chat_id: int, text: str, **extra: Any) -> Any: ...


class Web(Protocol):
    def page(self, channel: str, before: int | None = None) -> str: ...
    def download(self, url: str) -> bytes: ...


@dataclass
class Config:
    main_channel: str
    transit_channel: str | None = None
    admin_username: str = ""
    admin_ids: set[int] = field(default_factory=set)
    webapp_url: str | None = None
    source: str = "web"
    # Какие статусы показывать в витрине. По просьбе заказчика — только «в наличии».
    visible: tuple[str, ...] = ("available",)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if env is None else env)
        ids = {int(x) for x in env.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}
        return cls(
            main_channel=env.get("CHANNEL_MAIN", "").lstrip("@").lower(),
            transit_channel=(env.get("CHANNEL_TRANSIT", "").lstrip("@").lower() or None),
            admin_username=env.get("ADMIN_USERNAME", "").lstrip("@"),
            admin_ids=ids,
            webapp_url=env.get("WEBAPP_URL") or None,
            source=(env.get("SOURCE") or "web").lower(),
        )

    def channels(self) -> list[str]:
        return [c for c in (self.main_channel, self.transit_channel) if c]


@dataclass
class SyncResult:
    updates: int = 0
    pages: int = 0
    changed: bool = False
    new_media: int = 0
    media_errors: int = 0
    log: list[str] = field(default_factory=list)


class Sync:
    def __init__(self, api: Api | None, cfg: Config, data_dir: Path, web: Web | None = None) -> None:
        self.api, self.cfg, self.dir, self.web = api, cfg, data_dir, web
        self.site = data_dir / "site"
        self.media = self.site / "media"
        state = data_dir / "state.json"
        self.cat = Catalog.from_json(state.read_text("utf-8")) if state.exists() else Catalog(cfg.main_channel)
        meta = data_dir / "sync.json"
        self.meta: dict[str, Any] = json.loads(meta.read_text("utf-8")) if meta.exists() else {}
        for key, default in (("offset", None), ("threads", {}), ("pending", []), ("web", {}), ("replies", []),
                             ("reply_dates", {})):
            self.meta.setdefault(key, default)
        if self.meta.get("rescan") != RESCAN_VERSION:
            for st in self.meta["web"].values():
                st["full_done"], st["cursor"] = False, None
            self.meta["rescan"] = RESCAN_VERSION
        self.result = SyncResult()
        self.now = int(time.time())

    # ------------------------------------------------------------ основной проход

    def run(self) -> SyncResult:
        if self.web:
            self.scan_web()
        if self.api:
            self.poll_bot()
        self.ensure_media()
        self.write()
        return self.result

    def log(self, line: str) -> None:
        self.result.log.append(line)
        print(line, flush=True)

    def poll_bot(self) -> None:
        while True:
            try:
                updates = self.api.get_updates(self.meta["offset"])
            except Exception as e:
                self.log(f"бот недоступен: {e}")
                return
            if not updates:
                return
            for u in updates:
                self.meta["offset"] = u["update_id"] + 1
                self.result.updates += 1
                try:
                    self.handle(u)
                except Exception as e:  # один битый апдейт не должен останавливать синхронизацию
                    self.log(f"ошибка в апдейте {u.get('update_id')}: {type(e).__name__}: {e}")

    def handle(self, u: dict[str, Any]) -> None:
        post = u.get("channel_post") or u.get("edited_channel_post")
        if post:
            username = (post["chat"].get("username") or "").lower()
            if username in self.cfg.channels():
                m = channel_message(post)
                self.ingest([m], f"{m.chat_username}/{m.message_id}")
            return
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            return
        chat_type = msg["chat"].get("type")
        if chat_type == "private":
            self.on_private(msg)
        elif chat_type in ("group", "supergroup"):
            self.on_group(msg)

    # ------------------------------------------------------------ веб-лента

    def scan_web(self) -> None:
        posts: list[WebPost] = []
        for channel in self.cfg.channels():
            posts += self.scan_channel(channel)
        posts.sort(key=lambda p: (p.date, p.message_id))
        replies: list[WebPost] = []
        for p in posts:
            if p.reply_to and reply_status(p.text):
                replies.append(p)
                continue
            if not p.text and not p.photos:
                continue  # Telegram не показывает этот пост в веб-ленте
            self.ingest(p.to_messages(self.now), f"{p.channel}/{p.message_id}", has_photo=bool(p.photos))
        self.apply_replies(replies)

    def scan_channel(self, channel: str) -> list[WebPost]:
        st = self.meta["web"].setdefault(channel, {"cursor": None, "full_done": False})
        befores: list[int | None] = [None]
        limit = WEB_MAX_PAGES if not st["full_done"] else WEB_NEW_PAGES
        posts: list[WebPost] = []
        ranges: list[tuple[int, int]] = []
        prev: int | None = None

        def fetch(before: int | None) -> int | None:
            html = self.web.page(channel, before)  # type: ignore[union-attr]
            page_posts, nxt = parse_page(html, channel)
            self.result.pages += 1
            if page_posts:
                ids = [i for p in page_posts for i in p.message_ids]
                lo = 0 if nxt is None else min(ids)  # последняя страница — всё, что старше, тоже на ней
                ranges.append((lo, max(ids) if before is None else before - 1))
                posts.extend(page_posts)
            return nxt

        try:
            for _ in range(limit):
                prev = fetch(befores[-1])
                if prev is None:
                    break
                befores.append(prev)
            if not st["full_done"]:
                st["full_done"] = prev is None
                st["cursor"] = prev
            else:
                cursor = st["cursor"] or prev
                for _ in range(WEB_ROLL_PAGES):
                    if not cursor:
                        break
                    cursor = fetch(cursor)
                st["cursor"] = cursor  # None → следующий круг начнётся заново
        except Exception as e:
            self.log(f"лента {channel}: ошибка загрузки ({type(e).__name__}: {e}), проверка удалений пропущена")
            return posts
        self.detect_deleted(channel, posts, ranges)
        return posts

    def detect_deleted(self, channel: str, posts: list[WebPost], ranges: list[tuple[int, int]]) -> None:
        """Пост, который должен быть на просмотренной странице, но его там нет, — удалён."""
        seen = {i for p in posts for i in p.message_ids}
        cid = chat_id_for(channel)
        for post in self.cat.posts.values():
            if post.chat_id != cid or not post.item_ids or post.retired:
                continue
            ids = post.message_ids
            if seen.intersection(ids):
                continue
            if any(lo <= min(ids) and max(ids) <= hi for lo, hi in ranges):
                for iid in post.item_ids:
                    if self.cat.hide(iid, self.now):
                        self.log(f"{channel}/{ids[0]}: пост удалён — N° {self.cat.items[iid].no} скрыт")

    def apply_replies(self, replies: list[WebPost]) -> None:
        done = set(self.meta["replies"])
        for r in replies:
            key = f"{r.channel}/{r.message_id}"
            if key in done:
                continue
            items = self.cat.items_for_message(chat_id_for(r.channel), r.reply_to or 0)
            if not items:
                continue  # исходный пост ещё не в каталоге — попробуем при следующем проходе
            status = reply_status(r.text)
            # Ответы применяем по порядку: старый «SOLD», найденный позже, не перебивает более новый «снова в наличии».
            dates = self.meta["reply_dates"]
            fresh = [i for i in items if r.date >= dates.get(str(i.id), 0)]
            for i in fresh:
                dates[str(i.id)] = r.date
            events = [e for i in fresh if (e := self.cat.set_status(i.id, status, self.now))]  # type: ignore[arg-type]
            for e in events:
                self.log(f"{key}: ответ «{r.text}» → {self.describe(e)}")
            self.queue(events)
            done.add(key)
        self.meta["replies"] = sorted(done)[-5000:]

    # ------------------------------------------------------------ общий приём постов

    def ingest(self, messages: list[Message], where: str, has_photo: bool = True) -> None:
        events: list[Event] = []
        for m in messages:
            events += self.cat.ingest(m)
        if events:
            for e in events:
                self.log(f"{where}: {self.describe(e)}")
        elif has_photo and messages[0].text and not self.cat.items_for_message(messages[0].chat_id, messages[0].message_id):
            self.log(f"{where}: пропущен — нет #наличие / #едет / #продано")
        self.queue(events)

    def describe(self, e: Event) -> str:
        item = self.cat.items.get(e.item_id)
        name = f"N° {item.no} {item.brand or ''} {item.name or ''}".strip() if item else f"#{e.item_id}"
        if e.kind == "status":
            return f"{name}: {STATUS_RU.get(e.prev_status or '', e.prev_status)} → {STATUS_RU.get(e.status or '', e.status)}"
        if e.kind == "merge_candidate":
            return f"{name}: похоже на N° {e.other_id:03d} (в пути)"
        return f"{name}: {e.kind}"

    def queue(self, events: list[Event]) -> None:
        for e in events:
            if e.kind in ("review", "merge_candidate"):
                self.notify_admins(e)
            if e.kind in ("created", "status"):
                self.meta["pending"].append({**asdict(e), "ts": self.now})
        self.meta["pending"] = self.meta["pending"][-MAX_PENDING:]

    def notify_admins(self, e: Event) -> None:
        if not self.api:
            return
        item = self.cat.items.get(e.item_id)
        if not item:
            return
        if e.kind == "review":
            siblings = [self.cat.items[i] for i in self.cat.posts[item.post_key].item_ids if i in self.cat.items]
            lines = "\n".join(f"• N° {i.no} — размер {escape(i.size or '?')}, фото: {len(i.photos)}" for i in siblings)
            text = (
                f"В посте несколько вещей, я разделил его на {len(siblings)}:\n{lines}\n\n"
                f"Если что-то не так: /hide N — скрыть товар.\nЛучше публиковать по одной вещи в посте."
            )
        else:
            other = self.cat.items.get(e.other_id or 0)
            if not other:
                return
            text = (
                f"Похоже, это одна и та же вещь:\n"
                f"• N° {other.no} — {escape(other.title or '')} (в пути)\n"
                f"• N° {item.no} — {escape(item.title or '')} (новый пост, в наличии)\n\n"
                f"Объединить (останется N° {other.no}): /merge {other.id} {item.id}\n"
                f"Если это разные вещи — ничего не делайте."
            )
        for admin in self.cfg.admin_ids:
            self.safe_send(admin, text)

    # ------------------------------------------------------------ группа обсуждения: доп. фото (режим bot)

    def on_group(self, msg: dict[str, Any]) -> None:
        threads: dict[str, list[int]] = self.meta["threads"]
        chat_id = msg["chat"]["id"]
        fwd = automatic_forward(msg)
        if fwd:
            threads[f"{chat_id}:{fwd[0]}"] = list(fwd[1])
            return
        photo = largest_photo(msg.get("photo"))
        thread = comment_thread(msg)
        if not photo or thread is None:
            return
        target = threads.get(f"{chat_id}:{thread}")
        if not target:
            return
        sender_chat = (msg.get("sender_chat") or {}).get("id")
        sender = (msg.get("from") or {}).get("id")
        if sender_chat != target[0] and sender not in self.cfg.admin_ids:
            return  # фото от покупателей в витрину не попадают
        if self.cat.add_extra_photo(target[0], target[1], photo):
            self.log(f"доп. фото к посту {target[1]}")

    # ------------------------------------------------------------ личка с ботом

    def on_private(self, msg: dict[str, Any]) -> None:
        chat_id = msg["chat"]["id"]
        user_id = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        is_admin = user_id in self.cfg.admin_ids
        cmd, *args = text.split() if text else [""]
        cmd = cmd.split("@")[0].lower()

        if cmd == "/start":
            return self.welcome(chat_id)
        if cmd == "/whoami":
            return self.safe_send(chat_id, f"Ваш Telegram id: <code>{user_id}</code>")
        if not is_admin:
            return
        if msg.get("forward_origin", {}).get("type") == "channel":
            return self.describe_forward(chat_id, msg["forward_origin"])
        if cmd in ("/help", "/admin"):
            return self.safe_send(chat_id, ADMIN_HELP)
        if cmd == "/status" and len(args) == 2:
            item, status = self.find_item(args[0]), STATUS_WORDS.get(args[1].lower().lstrip("#"))
            if item and status:
                ev = self.cat.set_status(item.id, status, now=self.now)
                self.queue([ev] if ev else [])
                return self.safe_send(chat_id, f"N° {item.no}: {STATUS_RU[status.value]}")
        if cmd == "/hide" and len(args) == 1:
            item = self.find_item(args[0])
            if item:
                self.cat.hide(item.id, now=self.now)
                return self.safe_send(chat_id, f"N° {item.no} скрыт из витрины")
        if cmd == "/merge" and len(args) == 2:
            keep, drop = self.find_item(args[0]), self.find_item(args[1])
            if keep and drop and keep.id != drop.id:
                ev = self.cat.merge(keep.id, drop.id, now=self.now)
                self.queue([ev] if ev else [])
                return self.safe_send(chat_id, f"Готово: N° {keep.no}, {STATUS_RU[keep.status]}")
        if cmd.startswith("/"):
            self.safe_send(chat_id, "Не понял команду.\n\n" + ADMIN_HELP)

    def welcome(self, chat_id: int) -> None:
        text = (
            "<b>NASONCHIKH — архив</b>\n"
            "Наличие архивной дизайнерской одежды из канала. Обновляется автоматически."
        )
        markup = None
        if self.cfg.webapp_url:
            markup = {"inline_keyboard": [[{"text": "Открыть витрину", "web_app": {"url": self.cfg.webapp_url}}]]}
        self.safe_send(chat_id, text, reply_markup=markup)

    def describe_forward(self, chat_id: int, origin: dict[str, Any]) -> None:
        chat = origin.get("chat") or {}
        items = self.cat.items_for_message(chat["id"], origin["message_id"])
        if not items and chat.get("username"):  # в режиме web id канала — из username
            items = self.cat.items_for_message(chat_id_for(chat["username"]), origin["message_id"])
        if not items:
            return self.safe_send(chat_id, "Этот пост не в витрине: в нём нет #наличие / #едет / #продано.")
        lines = "\n".join(
            f"• N° {i.no} — {escape(i.title or '')}{f', размер {escape(i.size)}' if i.size else ''}: "
            f"{'скрыт' if i.hidden else STATUS_RU[i.status]}"
            for i in items
        )
        first = items[0].id
        self.safe_send(
            chat_id,
            f"{lines}\n\n/status {first} продано\n/status {first} наличие\n/status {first} едет\n/hide {first}",
        )

    def find_item(self, ref: str) -> Item | None:
        ref = ref.lstrip("N°№#").strip()
        return self.cat.items.get(int(ref)) if ref.isdigit() else None

    def safe_send(self, chat_id: int, text: str, **extra: Any) -> None:
        if not self.api:
            return
        try:
            self.api.send_message(chat_id, text, **extra)
        except Exception as e:
            self.log(f"не удалось отправить сообщение в {chat_id}: {e}")

    # ------------------------------------------------------------ фото и файлы

    def fetch_file(self, file_id: str) -> bytes:
        if file_id.startswith("http"):
            if not self.web:
                raise RuntimeError("нет веб-клиента для загрузки фото")
            return self.web.download(file_id)
        if not self.api:
            raise RuntimeError("нет бота для загрузки фото")
        return self.api.download(file_id)

    def ensure_media(self) -> None:
        for item in self.cat.items.values():
            if item.hidden or item.status not in self.cfg.visible:
                continue  # фото скачиваем только для того, что видно в витрине
            photos = item.photos[:1] if item.status == Status.SOLD.value else item.photos + item.extra_photos
            for p in photos:
                if has_variants(self.media, p.file_unique_id):
                    continue
                try:
                    w, h = build_variants(self.fetch_file(p.file_id), self.media, p.file_unique_id)
                    p.width, p.height = w, h
                    self.result.new_media += 1
                except Exception as e:
                    self.result.media_errors += 1
                    self.log(f"фото {p.file_unique_id} для N° {item.no}: {type(e).__name__}: {e}")

    def write(self) -> None:
        self.site.mkdir(parents=True, exist_ok=True)
        export = self.cat.export(default_photo_urls, visible=self.cfg.visible)
        export["meta"] = {"channel": self.cfg.main_channel, "admin_username": self.cfg.admin_username}
        path = self.site / "catalog.json"
        old = json.loads(path.read_text("utf-8")) if path.exists() else None
        if old is not None:
            old.get("meta", {}).pop("generated_at", None)
        if old != export or self.result.new_media:
            self.result.changed = True
            export["meta"]["generated_at"] = self.now
            path.write_text(json.dumps(export, ensure_ascii=False, separators=(",", ":")), "utf-8")
        (self.dir / "state.json").write_text(self.cat.to_json(), "utf-8")
        (self.dir / "sync.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), "utf-8")


ADMIN_HELP = (
    "<b>Команды админа</b>\n"
    "Перешлите сюда пост из канала — покажу, как он распознан.\n"
    "/status N продано | наличие | едет | бронь\n"
    "/hide N — скрыть из витрины\n"
    "/merge СТАРЫЙ НОВЫЙ — объединить вещь «в пути» с новым постом\n"
    "/whoami — ваш Telegram id\n\n"
    "Команды выполняются при следующей синхронизации (до 5–15 минут)."
)


def main(argv: list[str]) -> int:
    from .telegram_api import BotAPI

    cfg = Config.from_env()
    token = os.environ.get("BOT_TOKEN")
    if cfg.source == "bot" and not token:
        print("SOURCE=bot, но BOT_TOKEN не задан", file=sys.stderr)
        return 2
    if not cfg.main_channel:
        print("CHANNEL_MAIN не задан", file=sys.stderr)
        return 2
    api = BotAPI(token) if token else None
    web = WebClient() if cfg.source == "web" else None
    data_dir = Path(argv[1] if len(argv) > 1 else "data")
    result = Sync(api, cfg, data_dir, web).run()
    print(f"страниц ленты: {result.pages}, апдейтов бота: {result.updates}, новых фото: {result.new_media}, "
          f"ошибок фото: {result.media_errors}, витрина изменилась: {result.changed}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if result.changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
