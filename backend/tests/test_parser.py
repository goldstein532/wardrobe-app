from pathlib import Path

import pytest

from wardrobe.parser import (
    Status,
    parse_condition,
    parse_post,
    parse_price,
    plan_items,
    resolve_status,
)

POSTS = Path(__file__).parent / "fixtures" / "posts"


def load(name: str) -> str:
    return (POSTS / f"{name}.txt").read_text(encoding="utf-8")


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


# ------------------------------------------------------------ реальные посты

def test_margiela_evolution_low():
    p = parse_post(load("margiela_evolution_low"))
    assert p.is_product and p.status is Status.AVAILABLE
    assert p.title == "Maison Margiela Evolution Low"
    assert p.brand == "Maison Margiela"
    assert p.name == "Evolution Low"
    assert p.season is None
    assert p.category == "Обувь"  # из описания: «Эти кеды…»
    assert p.sizes == ["40"] and p.size_note == "40-41"
    assert p.condition == "10/10" and p.condition_note == "NEW, КП С БУТИКА"
    assert p.price == 30900 and not p.price_each
    assert p.authentic
    assert p.delivery == "СПБ/ДОСТАВКА"
    assert p.buy_link == "https://t.me/nasonchikh_mngr"
    assert p.has_extra_photos
    assert p.description.startswith("Эти кеды воплощают")
    assert "Size" not in p.description


def test_slp_savage_zebra():
    p = parse_post(load("slp_savage_zebra"))
    assert p.status is Status.AVAILABLE
    assert p.brand == "Saint Laurent"
    assert p.season == "SS14"
    assert p.name == "“Savage Zebra” Textured Sweatshirt"
    assert p.category == "Верх"
    assert p.sizes == ["M"] and p.size_note is None
    assert p.condition == "9.9/10"
    assert p.price == 17900
    assert p.description.startswith("SAINT LAURENT PARIS- рок-н-ролльная")


def test_givenchy_lock_boots():
    p = parse_post(load("givenchy_lock_boots"))
    assert p.brand == "Givenchy"
    assert p.name == "Lock Ankle Boots"
    assert p.category == "Обувь"
    assert p.sizes == ["41"] and p.size_note == "41-42"
    assert p.condition == "9/10" and p.condition_note is None
    assert p.price == 27900
    assert not p.has_extra_photos


def test_slp_jeans_multi_item_in_transit():
    p = parse_post(load("slp_jeans_hedi_multi"))
    assert p.status is Status.TRANSIT
    assert p.brand == "Saint Laurent"
    assert p.name == "Jeans by HEDI SLIMANE"
    assert p.category == "Низ"
    assert p.price == 13900 and p.price_each
    assert p.sizes == ["31", "32"] and p.multi_item
    assert p.condition == "9/10"
    assert p.description is None
    assert p.buy_link == "https://t.me/nasonchikh_mngr"


def test_nofaith_bomber_sold():
    p = parse_post(load("nofaith_bomber_sold"))
    assert p.status is Status.SOLD
    assert p.brand == "No/Faith Studios"
    assert p.name == "Fencing Bomber Jacket"
    assert p.category == "Верхняя одежда"
    assert p.sizes == ["XL"]
    assert p.condition == "10/10" and p.condition_note == "NEW"
    assert p.price == 25500
    assert "SOLD" not in (p.description or "")


def test_announcement_without_status_is_ignored():
    p = parse_post(load("new_drop_announcement"))
    assert not p.is_product and p.status is None


# ------------------------------------------------------------ статусы

@pytest.mark.parametrize(
    "text, known, expected",
    [
        ("Кофта\n#наличие", False, Status.AVAILABLE),
        ("Кофта\n#едет", False, Status.TRANSIT),
        ("Кофта\n#ВНаличии", False, Status.AVAILABLE),
        ("Кофта\n#едет\n#продано", False, Status.SOLD),
        ("Кофта\nПРОДАНО\n#наличие", False, Status.SOLD),
        ("Кофта\nне продано, ещё ждёт\n#наличие", False, Status.AVAILABLE),
        # слово SOLD без хэштега — только для товара, уже известного каталогу
        ("Кофта ❗️SOLD❗️", False, None),
        ("Кофта ❗️SOLD❗️", True, Status.SOLD),
        ("Новости канала, всё продано!", False, None),
    ],
)
def test_resolve_status(text, known, expected):
    tags = [t.lower() for t in __import__("re").findall(r"#(\w+)", text)]
    assert resolve_status(tags, text, known_item=known) is expected


def test_edit_removing_hashtag_keeps_known_item_sold():
    p = parse_post("Raf Simons Bomber\n❗️SOLD❗️", known_item=True)
    assert p.is_product and p.status is Status.SOLD
    assert p.brand == "Raf Simons"


# ------------------------------------------------------------ поля

@pytest.mark.parametrize(
    "value, amount, each",
    [
        ("30.900₽", 30900, False),
        ("30 900 ₽", 30900, False),
        ("30,900 руб", 30900, False),
        ("30 900₽", 30900, False),
        ("30к", 30000, False),
        ("4500", 4500, False),
        ("13.900 (for each)", 13900, True),
        ("13 900 за каждую", 13900, True),
        ("договорная", None, False),
    ],
)
def test_parse_price(value, amount, each):
    assert parse_price(value) == (amount, each)


@pytest.mark.parametrize(
    "value, score, note",
    [
        ("10/10 (NEW, КП С БУТИКА)", "10/10", "NEW, КП С БУТИКА"),
        ("9,5/10", "9.5/10", None),
        ("отличное", None, "отличное"),
    ],
)
def test_parse_condition(value, score, note):
    assert parse_condition(value) == (score, note)


def test_waist_length_size_is_single_item_without_for_each():
    p = parse_post("Helmut Lang Jeans\nSize: 31/32\nPrice: 9.900₽\n#наличие")
    assert p.sizes == ["31/32"] and not p.multi_item


def test_unknown_brand_keeps_title_as_name():
    p = parse_post("Some Obscure Label Coat\nSize: L\n#наличие")
    assert p.brand is None
    assert p.name == "Some Obscure Label Coat"
    assert p.category == "Верхняя одежда"


def test_missing_fields_still_product():
    p = parse_post("Rick Owens Geobasket\n#наличие")
    assert p.is_product
    assert p.brand == "Rick Owens"
    assert p.price is None and p.sizes == []


def test_longest_brand_alias_wins():
    assert parse_post("MM6 Maison Margiela Tabi\n#наличие").brand == "MM6 Maison Margiela"
    assert parse_post("Rick Owens DRKSHDW Ramones\n#наличие").brand == "Rick Owens DRKSHDW"


# ------------------------------------------------------------ entities (Bot API)

def test_blockquote_entity_with_utf16_offsets():
    title = "Givenchy Lock Ankle Boots 🔒\n\n"  # эмодзи = 2 UTF-16 единицы
    quote = "Описание из цитаты."
    text = title + quote + "\n\n•Size: 41\n\nКУПИТЬ\n\n#наличие"
    entities = [
        {"type": "expandable_blockquote", "offset": u16(title), "length": u16(quote)},
        {"type": "text_link", "offset": u16(text[: text.index("КУПИТЬ")]), "length": 6,
         "url": "https://t.me/nasonchikh_mngr"},
    ]
    p = parse_post(text, entities)
    assert p.description == "Описание из цитаты."
    assert p.buy_link == "https://t.me/nasonchikh_mngr"


def test_blockquote_with_fields_is_not_description():
    title = "Saint Laurent Paris Jeans\n\n"
    quote = "Cond - 9/10\nSize - 31\nPrice - 13.900"
    text = title + quote + "\n\n#едет"
    p = parse_post(text, [{"type": "blockquote", "offset": u16(title), "length": u16(quote)}])
    assert p.description is None
    assert p.price == 13900 and p.sizes == ["31"]


# ------------------------------------------------------------ деление на товары

def test_plan_single_item_gets_all_photos():
    drafts, review = plan_items(parse_post(load("givenchy_lock_boots")), photo_count=6)
    assert len(drafts) == 1 and drafts[0].photo_indices == [0, 1, 2, 3, 4, 5]
    assert drafts[0].size == "41" and not review


def test_plan_multi_pairs_photos_with_sizes():
    drafts, review = plan_items(parse_post(load("slp_jeans_hedi_multi")), photo_count=2)
    assert [(d.size, d.photo_indices) for d in drafts] == [("31", [0]), ("32", [1])]
    assert review


def test_plan_multi_mismatch_shares_photos():
    drafts, review = plan_items(parse_post(load("slp_jeans_hedi_multi")), photo_count=5)
    assert [d.size for d in drafts] == ["31", "32"]
    assert all(d.photo_indices == [0, 1, 2, 3, 4] for d in drafts)
    assert review
