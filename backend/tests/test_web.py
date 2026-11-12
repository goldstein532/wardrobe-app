import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from wardrobe.parser import Status, parse_post, reply_status
from wardrobe.sync import Config, Sync
from wardrobe.web import WebPost, chat_id_for, parse_page

WEB = Path(__file__).parent / "fixtures" / "web"
POSTS = Path(__file__).parent / "fixtures" / "posts"


def page(name):
    return (WEB / f"{name}.html").read_text(encoding="utf-8")


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (120, 110, 100)).save(buf, "JPEG")
    return buf.getvalue()


# ------------------------------------------------------------ разбор настоящей страницы ленты

def test_real_page_album_posts():
    posts, prev = parse_page(page("wardrobe_latest"), "nasonchikh_wardrobe")
    assert prev == 1576
    by_id = {p.message_id: p for p in posts}

    zebra = by_id[1576]
    assert [ph.message_id for ph in zebra.photos] == [1576, 1577, 1578, 1579, 1580, 1581]
    assert all(ph.url.startswith("https://cdn") for ph in zebra.photos)
    parsed = parse_post(zebra.text, zebra.entities)
    assert parsed.status is Status.AVAILABLE
    assert parsed.brand == "Saint Laurent" and parsed.season == "SS14"
    assert parsed.sizes == ["M"] and parsed.price == 17900
    assert parsed.description.startswith("SAINT LAURENT PARIS- рок-н-ролльная")
    assert parsed.buy_link == "https://t.me/nasonchikh_mngr"

    boots = by_id[1582]
    assert boots.edited and len(boots.photos) == 6
    assert parse_post(boots.text, boots.entities).name == "Lock Ankle Boots"


def test_real_page_replies_and_forwards():
    posts, _ = parse_page(page("wardrobe_latest"), "nasonchikh_wardrobe")
    by_id = {p.message_id: p for p in posts}
    # ответ «SOLD» на старый пост: текст цитаты не попадает в текст ответа
    assert (by_id[1607].reply_to, by_id[1607].text) == (1324, "SOLD")
    assert (by_id[1608].reply_to, by_id[1608].text) == (1375, "бронь")
    # пересылка из express: в веб-ленте содержимого нет
    assert by_id[1593].forwarded_from == ("nasoncikh_express", 11)
    assert not by_id[1593].text and not by_id[1593].photos


@pytest.mark.parametrize("text, status", [
    ("SOLD", Status.SOLD), ("❗️SOLD❗️", Status.SOLD), ("Продано", Status.SOLD),
    ("бронь", Status.RESERVED), ("Забронировано", Status.RESERVED),
    ("снова в наличии", Status.AVAILABLE), ("бронь снята", Status.AVAILABLE),
    ("По секрету, в пятницу дроп", None), ("", None),
    ("очень длинный ответ про то что вещь продано уже давно", None),
])
def test_reply_status(text, status):
    assert reply_status(text) is status


def test_album_to_messages():
    posts, _ = parse_page(page("wardrobe_latest"), "nasonchikh_wardrobe")
    msgs = next(p for p in posts if p.message_id == 1576).to_messages(seen_at=999)
    assert len(msgs) == 6 and msgs[0].text and msgs[1].text is None
    assert {m.media_group_id for m in msgs} == {"w1576"}
    assert msgs[0].photo.file_unique_id == "nasonchikh_wardrobe-1576"
    assert msgs[0].chat_id == chat_id_for("nasonchikh_wardrobe") < 0


# ------------------------------------------------------------ синхронизация из ленты

def block(mid, text="", photos=(), reply_to=None, channel="shop"):
    ph = "".join(
        f'<a class="tgme_widget_message_photo_wrap grouped_media_wrap" data-ratio="0.75" '
        f'href="https://t.me/{channel}/{pid}?single" style="width:1px;background-image:url(\'https://cdn.example/{pid}.jpg\')"></a>'
        for pid in photos
    )
    reply = (f'<a class="tgme_widget_message_reply" href="https://t.me/{channel}/{reply_to}">'
             f'<div class="tgme_widget_message_text js-message_reply_text">цитата #наличие</div></a>') if reply_to else ""
    body = text.replace("\n", "<br/>")
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
            f'data-post="{channel}/{mid}">{reply}{ph}<div class="tgme_widget_message_text js-message_text">{body}</div>'
            f'<span class="tgme_widget_message_meta"><time datetime="2026-11-0{1 + mid % 8}T10:00:00+00:00"></time></span>'
            f'</div></div>')


class FakeWeb:
    """Лента канала из списка блоков; по 3 поста на страницу, как ?before= в Telegram."""

    def __init__(self, blocks, per_page=3):
        self.blocks, self.per_page, self.calls, self.downloads = blocks, per_page, [], 0

    def page(self, channel, before=None):
        self.calls.append(before)
        items = sorted(((mid, html) for mid, html in self.blocks.items() if before is None or mid < before))
        chunk = items[-self.per_page:]
        prev = f'<link rel="prev" href="/s/{channel}?before={chunk[0][0]}">' if len(items) > len(chunk) else ""
        return prev + "".join(h for _, h in chunk)

    def download(self, url):
        self.downloads += 1
        return jpeg()


def product(name, price, tag="#наличие"):
    return f"{name}\n•Size: M\n•Price: {price}₽\n{tag}"


@pytest.fixture
def setup(tmp_path):
    blocks = {
        10: block(10, product("Givenchy Lock Ankle Boots", "27.900"), photos=[10, 11]),
        12: block(12, product("Rick Owens Geobasket", "45.000"), photos=[12]),
        13: block(13, "Новости канала"),
        14: block(14, product("Raf Simons Bomber", "60.000", "#едет"), photos=[14, 15]),
        16: block(16, "SOLD", reply_to=12),
        17: block(17, product("Helmut Lang Jeans", "9.900"), photos=[17]),
    }
    web = FakeWeb(blocks)
    cfg = Config(main_channel="shop", admin_username="mngr")
    return web, cfg, tmp_path


def cat_json(d):
    return json.loads((d / "site" / "catalog.json").read_text("utf-8"))


def test_first_run_reads_whole_history(setup):
    web, cfg, d = setup
    r = Sync(None, cfg, d, web).run()
    assert r.pages == 2 and web.calls == [None, 14]  # пролистал до начала
    cat = cat_json(d)
    names = {i["name"]: i["status"] for i in cat["items"]}
    assert names == {"Lock Ankle Boots": "available", "Bomber": "transit", "Jeans": "available"}
    assert [a["name"] for a in cat["archive"]] == ["Geobasket"]  # продан ответом «SOLD»
    # номера по дате публикации: Jeans (2 ноя) → Boots (3) → Geobasket (5, продан) → Bomber (7)
    assert {i["name"]: i["no"] for i in cat["items"]} == {"Jeans": "001", "Lock Ankle Boots": "002", "Bomber": "004"}
    assert web.downloads == 6  # 5 фото товаров + 1 обложка проданного


def test_second_run_is_quiet_and_rolls(setup):
    web, cfg, d = setup
    Sync(None, cfg, d, web).run()
    state_before = (d / "state.json").read_text("utf-8")
    web.calls.clear()
    r = Sync(None, cfg, d, web).run()
    assert not r.changed
    assert web.calls[:2] == [None, 14]
    assert (d / "state.json").read_text("utf-8") == state_before


def test_edit_and_delete_detected(setup):
    web, cfg, d = setup
    Sync(None, cfg, d, web).run()
    web.blocks[17] = block(17, product("Helmut Lang Jeans", "9.900", "#продано"), photos=[17])
    del web.blocks[10]  # альбом 10–11 удалён целиком
    r = Sync(None, cfg, d, web).run()
    cat = cat_json(d)
    assert "Jeans" in [a["name"] for a in cat["archive"]]
    assert "Lock Ankle Boots" not in [i["name"] for i in cat["items"]]
    assert any("пост удалён" in line for line in r.log)


def test_reply_applied_once(setup):
    web, cfg, d = setup
    s = Sync(None, cfg, d, web)
    s.run()
    # админ потом вернул вещь в продажу правкой поста: старый ответ SOLD не должен перебить
    web.blocks[12] = block(12, product("Rick Owens Geobasket", "45.000", "#наличие #бронь"), photos=[12])
    Sync(None, cfg, d, web).run()
    statuses = {i["name"]: i["status"] for i in cat_json(d)["items"]}
    assert statuses["Geobasket"] == "reserved"


def test_web_errors_do_not_hide_items(setup, monkeypatch):
    web, cfg, d = setup
    Sync(None, cfg, d, web).run()

    def broken(channel, before=None):
        raise OSError("timeout")

    web.page = broken
    r = Sync(None, cfg, d, web).run()
    assert len(cat_json(d)["items"]) == 3 and not r.changed
    assert any("ошибка загрузки" in line for line in r.log)
