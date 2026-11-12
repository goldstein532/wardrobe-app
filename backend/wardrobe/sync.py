"""Один проход синхронизации: забрать апдейты у бота → обновить каталог → подготовить фото
→ записать файлы витрины. Запускается GitHub Actions каждые 5 минут.

    python -m wardrobe.sync <папка-данных>

Папка данных (ветка `data` репозитория):
    state.json        — состояние каталога
    sync.json         — offset апдейтов, связи с группой обсуждения, очередь событий
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
from .catalog import Catalog, Event, Item, default_photo_urls
from .media import build_variants, has_variants
from .parser import Status

STATUS_WORDS = {
    "наличие": Status.AVAILABLE, "вналичии": Status.AVAILABLE, "available": Status.AVAILABLE,
    "едет": Status.TRANSIT, "впути": Status.TRANSIT, "transit": Status.TRANSIT,
    "продано": Status.SOLD, "sold": Status.SOLD,
}
STATUS_RU = {"available": "в наличии", "transit": "в пути", "sold": "продано"}
MAX_PENDING = 1000


class Api(Protocol):
    def get_updates(self, offset: int | None) -> list[dict[str, Any]]: ...
    def download(self, file_id: str) -> bytes: ...
    def send_message(self, chat_id: int, text: str, **extra: Any) -> Any: ...


@dataclass
class Config:
    main_channel: str
    transit_channel: str | None = None
    admin_username: str = ""
    admin_ids: set[int] = field(default_factory=set)
    webapp_url: str | None = None

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
        )

    def channels(self) -> set[str]:
        return {c for c in (self.main_channel, self.transit_channel) if c}


@dataclass
class SyncResult:
    updates: int = 0
    changed: bool = False
    new_media: int = 0
    media_errors: int = 0
    log: list[str] = field(default_factory=list)


class Sync:
    def __init__(self, api: Api, cfg: Config, data_dir: Path) -> None:
        self.api, self.cfg, self.dir = api, cfg, data_dir
        self.site = data_dir / "site"
        self.media = self.site / "media"
        state = data_dir / "state.json"
        self.cat = Catalog.from_json(state.read_text("utf-8")) if state.exists() else Catalog(cfg.main_channel)
        meta = data_dir / "sync.json"
        self.meta: dict[str, Any] = json.loads(meta.read_text("utf-8")) if meta.exists() else {}
        self.meta.setdefault("offset", None)
        self.meta.setdefault("threads", {})
        self.meta.setdefault("pending", [])
        self.result = SyncResult()

    # ------------------------------------------------------------ основной проход

    def run(self) -> SyncResult:
        while True:
            updates = self.api.get_updates(self.meta["offset"])
            if not updates:
                break
            for u in updates:
                self.meta["offset"] = u["update_id"] + 1
                self.result.updates += 1
                try:
                    self.handle(u)
                except Exception as e:  # один битый апдейт не должен останавливать синхронизацию
                    self.log(f"ошибка в апдейте {u.get('update_id')}: {type(e).__name__}: {e}")
        self.ensure_media()
        self.write()
        return self.result

    def log(self, line: str) -> None:
        self.result.log.append(line)
        print(line, flush=True)

    def handle(self, u: dict[str, Any]) -> None:
        post = u.get("channel_post") or u.get("edited_channel_post")
        if post:
            username = (post["chat"].get("username") or "").lower()
            if username in self.cfg.channels():
                self.on_channel(post, edited="edited_channel_post" in u)
            return
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            return
        chat_type = msg["chat"].get("type")
        if chat_type == "private":
            self.on_private(msg)
        elif chat_type in ("group", "supergroup"):
            self.on_group(msg)

    # ------------------------------------------------------------ канал

    def on_channel(self, post: dict[str, Any], edited: bool) -> None:
        m = channel_message(post)
        events = self.cat.ingest(m)
        where = f"{m.chat_username}/{m.message_id}{' (правка)' if edited else ''}"
        if events:
            for e in events:
                self.log(f"{where}: {self.describe(e)}")
        elif m.text and not self.cat.items_for_message(m.chat_id, m.message_id):
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
        now = int(time.time())
        for e in events:
            if e.kind in ("review", "merge_candidate"):
                self.notify_admins(e)
            if e.kind in ("created", "status"):
                self.meta["pending"].append({**asdict(e), "ts": now})
        self.meta["pending"] = self.meta["pending"][-MAX_PENDING:]

    def notify_admins(self, e: Event) -> None:
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

    # ------------------------------------------------------------ группа обсуждения: доп. фото

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
                ev = self.cat.set_status(item.id, status, now=int(time.time()))
                self.queue([ev] if ev else [])
                return self.safe_send(chat_id, f"N° {item.no}: {STATUS_RU[status.value]}")
        if cmd == "/hide" and len(args) == 1:
            item = self.find_item(args[0])
            if item:
                self.cat.hide(item.id, now=int(time.time()))
                return self.safe_send(chat_id, f"N° {item.no} скрыт из витрины")
        if cmd == "/merge" and len(args) == 2:
            keep, drop = self.find_item(args[0]), self.find_item(args[1])
            if keep and drop and keep.id != drop.id:
                ev = self.cat.merge(keep.id, drop.id, now=int(time.time()))
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
        items = self.cat.items_for_message(origin["chat"]["id"], origin["message_id"])
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
        try:
            self.api.send_message(chat_id, text, **extra)
        except Exception as e:
            self.log(f"не удалось отправить сообщение в {chat_id}: {e}")

    # ------------------------------------------------------------ фото и файлы

    def ensure_media(self) -> None:
        for item in self.cat.items.values():
            if item.hidden:
                continue
            photos = item.photos[:1] if item.status == Status.SOLD.value else item.photos + item.extra_photos
            for p in photos:
                if has_variants(self.media, p.file_unique_id):
                    continue
                try:
                    w, h = build_variants(self.api.download(p.file_id), self.media, p.file_unique_id)
                    p.width, p.height = w, h
                    self.result.new_media += 1
                except Exception as e:
                    self.result.media_errors += 1
                    self.log(f"фото {p.file_unique_id} для N° {item.no}: {e}")

    def write(self) -> None:
        self.site.mkdir(parents=True, exist_ok=True)
        export = self.cat.export(default_photo_urls)
        export["meta"] = {"channel": self.cfg.main_channel, "admin_username": self.cfg.admin_username}
        path = self.site / "catalog.json"
        old = json.loads(path.read_text("utf-8")) if path.exists() else None
        if old is not None:
            old.get("meta", {}).pop("generated_at", None)
        if old != export or self.result.new_media:
            self.result.changed = True
            export["meta"]["generated_at"] = int(time.time())
            path.write_text(json.dumps(export, ensure_ascii=False, separators=(",", ":")), "utf-8")
        (self.dir / "state.json").write_text(self.cat.to_json(), "utf-8")
        (self.dir / "sync.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), "utf-8")


ADMIN_HELP = (
    "<b>Команды админа</b>\n"
    "Перешлите сюда пост из канала — покажу, как он распознан.\n"
    "/status N продано | наличие | едет\n"
    "/hide N — скрыть из витрины\n"
    "/merge СТАРЫЙ НОВЫЙ — объединить вещь «в пути» с новым постом\n"
    "/whoami — ваш Telegram id\n\n"
    "Команды выполняются при следующей синхронизации (до 5–15 минут)."
)


def main(argv: list[str]) -> int:
    from .telegram_api import BotAPI

    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN не задан", file=sys.stderr)
        return 2
    data_dir = Path(argv[1] if len(argv) > 1 else "data")
    result = Sync(BotAPI(token), Config.from_env(), data_dir).run()
    print(f"апдейтов: {result.updates}, новых фото: {result.new_media}, "
          f"ошибок фото: {result.media_errors}, витрина изменилась: {result.changed}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if result.changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
