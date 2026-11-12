"""Разбор товарных постов канала.

Вход — текст поста (или подпись к медиа) и, по возможности, entities из Bot API.
Парсер ничего не знает о Telegram-клиенте и базе: это чистая функция,
поэтому её одинаково используют бот, сборщик каталога и импорт истории.

Правило: если поле не разобралось, оно просто остаётся пустым,
товар всё равно попадает в витрину.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .brands import match_brand_prefix


class Status(str, Enum):
    AVAILABLE = "available"
    TRANSIT = "transit"
    SOLD = "sold"


STATUS_TAGS: dict[str, Status] = {
    "наличие": Status.AVAILABLE,
    "вналичии": Status.AVAILABLE,
    "в_наличии": Status.AVAILABLE,
    "instock": Status.AVAILABLE,
    "едет": Status.TRANSIT,
    "впути": Status.TRANSIT,
    "в_пути": Status.TRANSIT,
    "продано": Status.SOLD,
    "sold": Status.SOLD,
}

_HASHTAG_RE = re.compile(r"#(\w+)")
# «SOLD», «продано», «продана», но не «не продано».
_SOLD_WORD_RE = re.compile(r"(?<![\w#])(?<!не )(?:sold|продан[оаы]?)(?!\w)", re.IGNORECASE)

_BULLETS = "•·▪▫◦‣∙*-–— \t ️"
_FIELD_RE = re.compile(
    r"^(?P<key>size|sizes|размер|cond|condition|состояние|price|цена)\s*[:\-–—]\s*(?P<val>.+)$",
    re.IGNORECASE,
)
_FIELD_KEYS = {
    "size": "size", "sizes": "size", "размер": "size",
    "cond": "cond", "condition": "cond", "состояние": "cond",
    "price": "price", "цена": "price",
}

_SEASON_RE = re.compile(r"(?<!\w)(SS|AW|FW|S/S|F/W|A/W)\s?['’]?(\d{2}|\d{4})(?!\w)", re.IGNORECASE)
_PRICE_NUM_RE = re.compile(r"(\d{1,3}(?:[ .,  ]\d{3})+|\d+)\s*([кk])?(?!\w)", re.IGNORECASE)
_EACH_RE = re.compile(r"for each|each|за (?:каждую|каждый|каждое|шт|единицу)|кажд", re.IGNORECASE)
_COND_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*/\s*10")
_PAREN_RE = re.compile(r"^(.*?)\s*\((.+)\)\s*$")
_DELIVERY_RE = re.compile(r"^(спб|мск|москва|питер|санкт|доставка)", re.IGNORECASE)
_TG_URL_RE = re.compile(r"https?://t\.me/[\w/+\-?=&.]+", re.IGNORECASE)
_TG_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Обувь", [
        "sneaker", "boot", "shoe", "loafer", "derby", "sandal", "slides", "trainers",
        "кед", "кроссовк", "ботин", "сапог", "лофер", "дерби", "туфл", "сандал", "обувь",
    ]),
    ("Верхняя одежда", [
        "jacket", "bomber", "coat", "parka", "blazer", "puffer", "vest", "gilet",
        "куртк", "бомбер", "пальто", "парка", "пуховик", "пиджак", "жилет",
    ]),
    ("Низ", [
        "jeans", "denim", "pants", "trousers", "shorts", "skirt", "cargo",
        "джинс", "брюк", "штан", "шорт", "юбк", "карго",
    ]),
    ("Верх", [
        "sweatshirt", "hoodie", "tee", "t-shirt", "shirt", "sweater", "knit", "cardigan",
        "longsleeve", "top", "polo", "jumper",
        "свитшот", "худи", "футболк", "рубашк", "свитер", "лонгслив", "кардиган", "джемпер", "поло",
    ]),
    ("Аксессуары", [
        "bag", "belt", "wallet", "cap", "hat", "beanie", "scarf", "ring", "necklace", "sunglasses",
        "сумк", "ремень", "кошел", "кепк", "шапк", "шарф", "кольц", "очки",
    ]),
]
_CATEGORY_RES = [
    (name, re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in kws) + r")", re.IGNORECASE))
    for name, kws in CATEGORIES
]


@dataclass
class ParsedPost:
    is_product: bool
    status: Status | None
    hashtags: list[str] = field(default_factory=list)
    title: str | None = None
    brand: str | None = None
    name: str | None = None
    season: str | None = None
    category: str | None = None
    description: str | None = None
    size_raw: str | None = None
    sizes: list[str] = field(default_factory=list)
    size_note: str | None = None
    condition: str | None = None
    condition_note: str | None = None
    price: int | None = None
    currency: str = "RUB"
    price_each: bool = False
    authentic: bool = False
    delivery: str | None = None
    buy_link: str | None = None
    has_extra_photos: bool = False

    @property
    def multi_item(self) -> bool:
        return len(self.sizes) > 1


@dataclass
class ItemDraft:
    """Один товар витрины, полученный из поста."""
    index: int
    size: str | None
    photo_indices: list[int]


# ---------------------------------------------------------------- helpers

def _utf16_slice(text: str, offset: int, length: int) -> str:
    """Telegram считает offset/length в UTF-16 code units."""
    raw = text.encode("utf-16-le")
    return raw[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


def _strip_decor(s: str) -> str:
    """Убирает эмодзи и прочие символы-украшения по краям строки."""
    def is_decor(c: str) -> bool:
        return c.isspace() or unicodedata.category(c) in {"So", "Sk", "Cf", "Mn", "Co", "Cs"}

    start, end = 0, len(s)
    while start < end and is_decor(s[start]):
        start += 1
    while end > start and is_decor(s[end - 1]):
        end -= 1
    return s[start:end]


def _clean_line(line: str) -> str:
    return _strip_decor(line.strip().lstrip(_BULLETS))


def _collapse(s: str) -> str:
    return re.sub(r"\s{2,}", " ", s).strip(" -–—:,")


def parse_price(value: str) -> tuple[int | None, bool]:
    """«30.900₽» → (30900, False); «13.900 (for each)» → (13900, True)."""
    each = bool(_EACH_RE.search(value))
    m = _PRICE_NUM_RE.search(value)
    if not m:
        return None, each
    digits = re.sub(r"\D", "", m.group(1))
    if not digits:
        return None, each
    amount = int(digits)
    if m.group(2):  # 30к
        amount *= 1000
    return amount, each


def parse_condition(value: str) -> tuple[str | None, str | None]:
    """«10/10 (NEW, КП С БУТИКА)» → ("10/10", "NEW, КП С БУТИКА")."""
    m = _COND_RE.search(value)
    score = f"{m.group(1).replace(',', '.')}/10" if m else None
    pm = _PAREN_RE.match(value)
    note = pm.group(2).strip() if pm else None
    if score is None and not note:
        note = value.strip() or None
    return score, note


def parse_size(value: str) -> tuple[str, str | None]:
    """«40 (40-41)» → ("40", "40-41")."""
    pm = _PAREN_RE.match(value.strip())
    if pm:
        return pm.group(1).strip(), pm.group(2).strip()
    return value.strip(), None


def split_sizes(main: str) -> list[str]:
    return [s.strip() for s in re.split(r"\s*[/,;]\s*|\s+и\s+", main) if s.strip()]


def detect_category(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        best: tuple[int, str] | None = None
        for name, rx in _CATEGORY_RES:
            m = rx.search(text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), name)
        if best:
            return best[1]
    return None


def resolve_status(hashtags: Iterable[str], text: str, known_item: bool = False) -> Status | None:
    """Статус по хэштегам. «Продано»/SOLD в тексте учитывается только у товарных постов
    (есть статусный хэштег) или у постов, которые уже есть в каталоге."""
    tagged = [STATUS_TAGS[t] for t in hashtags if t in STATUS_TAGS]
    if Status.SOLD in tagged:
        return Status.SOLD
    if (tagged or known_item) and _SOLD_WORD_RE.search(text):
        return Status.SOLD
    if Status.AVAILABLE in tagged:
        return Status.AVAILABLE
    if Status.TRANSIT in tagged:
        return Status.TRANSIT
    return None


# ---------------------------------------------------------------- main

def parse_post(
    text: str | None,
    entities: list[dict[str, Any]] | None = None,
    known_item: bool = False,
) -> ParsedPost:
    text = text or ""
    entities = entities or []
    hashtags = [h.lower() for h in _HASHTAG_RE.findall(text)]
    status = resolve_status(hashtags, text, known_item=known_item)
    post = ParsedPost(is_product=status is not None, status=status, hashtags=hashtags)
    if not post.is_product:
        return post

    lines = text.splitlines()

    # Название — первая непустая строка.
    title_idx = next((i for i, l in enumerate(lines) if _clean_line(l)), None)
    if title_idx is not None:
        post.title = _collapse(_strip_decor(lines[title_idx].strip()))
        rest = post.title
        bm = match_brand_prefix(rest)
        if bm:
            post.brand, rest = bm
        sm = _SEASON_RE.search(rest)
        if sm:
            post.season = (sm.group(1).replace("/", "").upper() + sm.group(2))
            rest = rest[:sm.start()] + " " + rest[sm.end():]
        post.name = _collapse(rest) or post.title

    # Описание — цитата (blockquote) или строки между названием и полями.
    quote = next(
        (e for e in entities if e.get("type") in ("blockquote", "expandable_blockquote")),
        None,
    )
    if quote:
        post.description = _utf16_slice(text, quote["offset"], quote["length"]).strip() or None
        # Иногда в цитату кладут не описание, а поля (Size/Price) — тогда это не описание.
        if post.description and any(
            _FIELD_RE.match(_clean_line(l)) for l in post.description.splitlines()
        ):
            post.description, quote = None, None

    desc_lines: list[str] = []
    fields_started = False
    for i, raw in enumerate(lines):
        line = _clean_line(raw)
        if not line or i == title_idx:
            continue
        fm = _FIELD_RE.match(line)
        if fm:
            fields_started = True
            key, val = _FIELD_KEYS[fm.group("key").lower()], fm.group("val").strip()
            if key == "size" and post.size_raw is None:
                post.size_raw = val
            elif key == "cond" and post.condition is None and post.condition_note is None:
                post.condition, post.condition_note = parse_condition(val)
            elif key == "price" and post.price is None:
                post.price, post.price_each = parse_price(val)
            continue
        low = line.lower()
        if "оригинал" in low or "original" in low:
            post.authentic = True
            fields_started = True
            continue
        if _DELIVERY_RE.match(line):
            post.delivery = line
            continue
        if "доп фото" in low or "доп. фото" in low:
            post.has_extra_photos = True
            continue
        if line.startswith("#") or _SOLD_WORD_RE.fullmatch(line.strip("!❗️ ")):
            continue
        if not fields_started and not quote:
            desc_lines.append(raw.strip())

    if not post.description and desc_lines:
        post.description = "\n".join(desc_lines).strip() or None

    # Размеры. Несколько вещей в посте — только если цена «за каждую»,
    # иначе «31/32» — это один размер (талия/длина).
    if post.size_raw:
        main, post.size_note = parse_size(post.size_raw)
        post.sizes = split_sizes(main) if post.price_each else [main]

    post.category = detect_category(post.title, post.description)
    post.buy_link = _find_buy_link(text, entities)
    return post


def _find_buy_link(text: str, entities: list[dict[str, Any]]) -> str | None:
    for e in entities:
        url = e.get("url") or ""
        if e.get("type") == "text_link" and "t.me/" in url:
            return url
    m = _TG_URL_RE.search(text)
    if m:
        return m.group(0).rstrip(").,")
    m = _TG_MENTION_RE.search(text)
    if m:
        return f"https://t.me/{m.group(1)}"
    return None


def plan_items(post: ParsedPost, photo_count: int) -> tuple[list[ItemDraft], bool]:
    """Делит пост на товары витрины. Возвращает (товары, нужна_проверка_админом).

    Одна вещь → один товар со всеми фото.
    Несколько вещей и фото столько же, сколько размеров → фото i к размеру i.
    Иначе → по товару на размер, у каждого все фото.
    Пост с несколькими вещами всегда отправляется админу на проверку.
    """
    all_photos = list(range(photo_count))
    if not post.multi_item:
        size = post.sizes[0] if post.sizes else None
        return [ItemDraft(0, size, all_photos)], False
    if photo_count == len(post.sizes):
        drafts = [ItemDraft(i, s, [i]) for i, s in enumerate(post.sizes)]
    else:
        drafts = [ItemDraft(i, s, all_photos) for i, s in enumerate(post.sizes)]
    return drafts, True
