import io
import json
from pathlib import Path

import pytest
from PIL import Image

from wardrobe.sync import Config, Sync

POSTS = Path(__file__).parent / "fixtures" / "posts"
MAIN, EXPRESS, GROUP, ADMIN = -100111, -100222, -100333, 42


def load(name):
    return (POSTS / f"{name}.txt").read_text(encoding="utf-8")


def jpeg(w=1280, h=1707):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 190, 170)).save(buf, "JPEG")
    return buf.getvalue()


class FakeAPI:
    def __init__(self):
        self.queue, self.sent, self.downloads = [], [], 0
        self.next_id = 1

    def push(self, kind, payload):
        self.queue.append({"update_id": self.next_id, kind: payload})
        self.next_id += 1

    def get_updates(self, offset):
        batch = [u for u in self.queue if offset is None or u["update_id"] >= offset][:100]
        return batch

    def download(self, file_id):
        self.downloads += 1
        return jpeg()

    def send_message(self, chat_id, text, **extra):
        self.sent.append((chat_id, text, extra))


def photo(uid):
    return [
        {"file_id": f"small-{uid}", "file_unique_id": f"s-{uid}", "width": 90, "height": 120},
        {"file_id": f"big-{uid}", "file_unique_id": uid, "width": 1280, "height": 1707},
    ]


def channel_post(mid, text=None, uid=None, group=None, chat=MAIN, user="nasonchikh_wardrobe", **extra):
    p = {"message_id": mid, "date": 1_700_000_000 + mid, "chat": {"id": chat, "type": "channel", "username": user}}
    if uid:
        p["photo"] = photo(uid)
        if text is not None:
            p["caption"] = text
    elif text is not None:
        p["text"] = text
    if group:
        p["media_group_id"] = group
    p.update(extra)
    return p


@pytest.fixture
def env(tmp_path):
    api = FakeAPI()
    cfg = Config(main_channel="nasonchikh_wardrobe", transit_channel="nasonchikh_express",
                 admin_username="nasonchikh_mngr", admin_ids={ADMIN}, webapp_url="https://example.github.io/shop/")
    return api, cfg, tmp_path


def catalog(tmp_path):
    return json.loads((tmp_path / "site" / "catalog.json").read_text("utf-8"))


def test_album_post_lands_in_catalog_with_media(env):
    api, cfg, d = env
    for k in range(3):
        api.push("channel_post", channel_post(10 + k, load("givenchy_lock_boots") if k == 0 else None, f"u{k}", "A"))
    r = Sync(api, cfg, d).run()
    assert r.updates == 3 and r.changed and r.new_media == 3
    cat = catalog(d)
    assert [i["no"] for i in cat["items"]] == ["001"]
    item = cat["items"][0]
    assert item["photos"][0]["thumb"] == "media/u0-720.webp"
    assert cat["meta"]["admin_username"] == "nasonchikh_mngr"
    with Image.open(d / "site" / "media" / "u0-720.webp") as im:
        assert im.width == 720
    with Image.open(d / "site" / "media" / "u0-full.webp") as im:
        assert (im.width, im.height) == (1280, 1707)  # оригинал без уменьшения


def test_second_run_is_noop(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(10, load("margiela_evolution_low"), "m0"))
    Sync(api, cfg, d).run()
    before = (d / "site" / "catalog.json").read_text("utf-8")
    r = Sync(api, cfg, d).run()  # offset сохранён — старые апдейты не придут
    assert r.updates == 0 and not r.changed and api.downloads == 1
    assert (d / "site" / "catalog.json").read_text("utf-8") == before


def test_edit_to_sold_moves_to_archive(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(10, load("margiela_evolution_low"), "m0"))
    Sync(api, cfg, d).run()
    api.push("edited_channel_post", channel_post(10, load("margiela_evolution_low") + "\n#продано", "m0", edit_date=1_800_000_000))
    r = Sync(api, cfg, d).run()
    assert r.changed
    cat = catalog(d)
    assert cat["items"] == [] and cat["archive"][0]["no"] == "001"


def test_foreign_channel_and_announcements_ignored(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(1, load("givenchy_lock_boots"), "x", chat=-1009, user="other_shop"))
    api.push("channel_post", channel_post(2, load("new_drop_announcement"), "y"))
    r = Sync(api, cfg, d).run()
    assert catalog(d)["items"] == []
    assert any("пропущен" in line for line in r.log)


def test_multi_item_post_notifies_admin(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(5, load("slp_jeans_hedi_multi"), "j0", "J", chat=EXPRESS, user="nasonchikh_express"))
    api.push("channel_post", channel_post(6, None, "j1", "J", chat=EXPRESS, user="nasonchikh_express"))
    Sync(api, cfg, d).run()
    assert sorted(i["size"] for i in catalog(d)["items"]) == ["31", "32"]
    assert any(chat == ADMIN and "несколько вещей" in text for chat, text, _ in api.sent)


def test_extra_photos_only_from_channel_or_admin(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(10, load("margiela_evolution_low"), "m0"))
    # копия поста в группе обсуждения
    api.push("message", {"message_id": 500, "date": 1, "chat": {"id": GROUP, "type": "supergroup"},
                         "is_automatic_forward": True,
                         "forward_origin": {"type": "channel", "chat": {"id": MAIN}, "message_id": 10}})
    reply = {"message_id": 500, "is_automatic_forward": True}
    # комментарий от имени канала — берём
    api.push("message", {"message_id": 501, "date": 2, "chat": {"id": GROUP, "type": "supergroup"},
                         "sender_chat": {"id": MAIN}, "photo": photo("extra1"), "reply_to_message": reply})
    # фото покупателя — не берём
    api.push("message", {"message_id": 502, "date": 3, "chat": {"id": GROUP, "type": "supergroup"},
                         "from": {"id": 777}, "photo": photo("buyer"), "reply_to_message": reply})
    # фото админа в той же ветке (без reply, по message_thread_id) — берём
    api.push("message", {"message_id": 503, "date": 4, "chat": {"id": GROUP, "type": "supergroup"},
                         "from": {"id": ADMIN}, "photo": photo("extra2"), "message_thread_id": 500})
    Sync(api, cfg, d).run()
    extra = [p["thumb"] for p in catalog(d)["items"][0]["extra_photos"]]
    assert extra == ["media/extra1-720.webp", "media/extra2-720.webp"]


def test_admin_commands(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(10, load("givenchy_lock_boots"), "g0"))
    dm = lambda text, uid=ADMIN, **kw: {"message_id": 1, "date": 1, "chat": {"id": uid, "type": "private"},
                                        "from": {"id": uid}, "text": text, **kw}
    api.push("message", dm("/status 1 продано"))
    api.push("message", dm("/status 1 продано", uid=999))  # не админ — игнор
    Sync(api, cfg, d).run()
    assert catalog(d)["archive"][0]["no"] == "001"
    assert any("продано" in t for c, t, _ in api.sent if c == ADMIN)
    assert not [1 for c, _, _ in api.sent if c == 999]

    api.push("message", dm("пересланный", forward_origin={"type": "channel", "chat": {"id": MAIN}, "message_id": 10}))
    Sync(api, cfg, d).run()
    assert "/status 1 продано" in api.sent[-1][1]


def test_start_shows_webapp_button_and_whoami(env):
    api, cfg, d = env
    api.push("message", {"message_id": 1, "date": 1, "chat": {"id": 555, "type": "private"},
                         "from": {"id": 555}, "text": "/start"})
    api.push("message", {"message_id": 2, "date": 1, "chat": {"id": 555, "type": "private"},
                         "from": {"id": 555}, "text": "/whoami"})
    Sync(api, cfg, d).run()
    (_, _, start), (_, whoami, _) = api.sent
    assert start["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == cfg.webapp_url
    assert "555" in whoami


def test_merge_flow_via_commands(env):
    api, cfg, d = env
    api.push("channel_post", channel_post(7, "Givenchy Lock Ankle Boots\nSize: 41\nPrice: 27.900₽\n#едет",
                                          "e0", chat=EXPRESS, user="nasonchikh_express"))
    api.push("channel_post", channel_post(60, load("givenchy_lock_boots"), "n0"))
    Sync(api, cfg, d).run()
    assert any("/merge 1 2" in t for _, t, _ in api.sent)
    api.push("message", {"message_id": 3, "date": 1, "chat": {"id": ADMIN, "type": "private"},
                         "from": {"id": ADMIN}, "text": "/merge 1 2"})
    Sync(api, cfg, d).run()
    items = catalog(d)["items"]
    assert [(i["no"], i["status"]) for i in items] == [("001", "available")]
    assert items[0]["post_url"] == "https://t.me/nasonchikh_wardrobe/60"


def test_config_from_env():
    cfg = Config.from_env({"CHANNEL_MAIN": "@Nasonchikh_Wardrobe", "ADMIN_IDS": "1, 2,x", "CHANNEL_TRANSIT": ""})
    assert cfg.main_channel == "nasonchikh_wardrobe" and cfg.admin_ids == {1, 2}
    assert cfg.transit_channel is None
