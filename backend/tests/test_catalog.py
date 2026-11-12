from pathlib import Path

from wardrobe.catalog import Catalog, Message, Photo
from wardrobe.parser import Status

POSTS = Path(__file__).parent / "fixtures" / "posts"
MAIN, EXPRESS = -100111, -100222


def load(name: str) -> str:
    return (POSTS / f"{name}.txt").read_text(encoding="utf-8")


def ph(uid: str) -> Photo:
    return Photo(file_id=f"fid-{uid}", file_unique_id=uid, width=1280, height=1707)


def msg(mid, text=None, photo=None, group=None, chat=MAIN, user="nasonchikh_wardrobe",
        date=1000, forward=None):
    return Message(chat_id=chat, message_id=mid, date=date, text=text,
                   photo=ph(photo) if photo else None, media_group_id=group,
                   chat_username=user, forward_from=forward)


def kinds(events):
    return [e.kind for e in events]


def album(cat, first_mid, text, n, group="G1", chat=MAIN, user="nasonchikh_wardrobe", order=None):
    events = []
    for k in (order or range(n)):
        events += cat.ingest(msg(first_mid + k, text if k == 0 else None, f"{group}-{k}",
                                 group, chat=chat, user=user))
    return events


# ------------------------------------------------------------ альбомы и идемпотентность

def test_album_becomes_one_item_with_ordered_photos():
    cat = Catalog("nasonchikh_wardrobe")
    album(cat, 10, load("givenchy_lock_boots"), 6)
    assert len(cat.items) == 1
    item = cat.items[1]
    assert item.brand == "Givenchy" and item.status == "available"
    assert [p.file_unique_id for p in item.photos] == [f"G1-{k}" for k in range(6)]
    assert cat.post_url(item) == "https://t.me/nasonchikh_wardrobe/10"


def test_album_messages_in_any_order():
    cat = Catalog()
    album(cat, 10, load("givenchy_lock_boots"), 4, order=[3, 1, 0, 2])
    assert len(cat.items) == 1
    assert [p.message_id for p in cat.items[1].photos] == [10, 11, 12, 13]


def test_repeated_updates_do_not_duplicate():
    cat = Catalog()
    album(cat, 10, load("givenchy_lock_boots"), 3)
    again = album(cat, 10, load("givenchy_lock_boots"), 3)
    assert again == [] and len(cat.items) == 1


def test_announcement_creates_nothing():
    cat = Catalog()
    assert cat.ingest(msg(5, load("new_drop_announcement"), "d")) == []
    assert cat.items == {}


# ------------------------------------------------------------ статусы при правке поста

def test_edit_transit_to_available_emits_status_event():
    cat = Catalog()
    text = load("givenchy_lock_boots").replace("#наличие", "#едет")
    album(cat, 10, text, 2)
    assert cat.items[1].status == "transit"
    events = cat.ingest(msg(10, text.replace("#едет", "#наличие"), "G1-0", "G1", date=2000))
    assert [(e.kind, e.prev_status, e.status) for e in events] == [("status", "transit", "available")]
    assert cat.items[1].status_changed_at == 2000


def test_sold_item_moves_to_archive():
    cat = Catalog("nasonchikh_wardrobe")
    album(cat, 10, load("givenchy_lock_boots"), 2)
    cat.ingest(msg(10, load("givenchy_lock_boots") + "\n#продано", "G1-0", "G1", date=2000))
    out = cat.export()
    assert out["items"] == []
    assert [a["no"] for a in out["archive"]] == ["001"]
    assert cat.items[1].status == "sold"  # запись остаётся


def test_typo_fix_keeps_manual_status():
    cat = Catalog()
    album(cat, 10, load("givenchy_lock_boots"), 2)
    cat.set_status(1, Status.SOLD, now=1500)
    fixed = load("givenchy_lock_boots").replace("Price: 27.900₽", "Price: 26.900₽")
    events = cat.ingest(msg(10, fixed, "G1-0", "G1", date=2000))
    assert kinds(events) == ["updated"]
    assert cat.items[1].status == "sold" and cat.items[1].price == 26900


def test_hashtag_change_overrides_manual_status():
    cat = Catalog()
    text = load("givenchy_lock_boots").replace("#наличие", "#едет")
    album(cat, 10, text, 1)
    cat.set_status(1, Status.SOLD, now=1500)
    cat.ingest(msg(10, text.replace("#едет", "#наличие"), "G1-0", "G1", date=2000))
    assert cat.items[1].status == "available"


def test_removed_caption_does_not_delete_item():
    cat = Catalog()
    album(cat, 10, load("givenchy_lock_boots"), 2)
    assert cat.ingest(msg(10, "", "G1-0", "G1", date=2000)) == []
    assert not cat.items[1].hidden


# ------------------------------------------------------------ несколько вещей в посте

def test_multi_item_post_splits_and_asks_review():
    cat = Catalog()
    events = album(cat, 20, load("slp_jeans_hedi_multi"), 2, group="J")
    assert kinds(events).count("created") == 2 and "review" in kinds(events)
    a, b = cat.items[1], cat.items[2]
    assert (a.size, b.size) == ("31", "32")
    assert [p.file_unique_id for p in a.photos] == ["J-0"]
    assert [p.file_unique_id for p in b.photos] == ["J-1"]
    assert a.status == b.status == "transit"


def test_fewer_sizes_after_edit_hides_extra_item():
    cat = Catalog()
    album(cat, 20, load("slp_jeans_hedi_multi"), 2, group="J")
    edited = load("slp_jeans_hedi_multi").replace("31 / 32", "31")
    events = cat.ingest(msg(20, edited, "J-0", "J", date=2000))
    assert ("hidden", 2) in [(e.kind, e.item_id) for e in events]
    assert len(cat.export()["items"]) == 1


# ------------------------------------------------------------ express ↔ wardrobe

def test_forward_from_express_links_to_same_item():
    cat = Catalog("nasonchikh_wardrobe")
    text = load("slp_jeans_hedi_multi").replace("31 / 32", "31").replace(" (for each)", "")
    cat.ingest(msg(7, text, "E-0", chat=EXPRESS, user="nasonchikh_express"))
    events = cat.ingest(msg(50, text, "E-0", forward=(EXPRESS, 7)))
    assert len(cat.items) == 1 and kinds(events) == []
    assert cat.post_url(cat.items[1]) == "https://t.me/nasonchikh_wardrobe/50"
    assert cat.export()["posts"]["nasonchikh_wardrobe/50"] == [1]


def test_arrived_item_merge_keeps_number():
    cat = Catalog("nasonchikh_wardrobe")
    transit = "Givenchy Lock Ankle Boots\nSize: 41\nPrice: 27.900₽\n#едет"
    cat.ingest(msg(7, transit, "E-0", chat=EXPRESS, user="nasonchikh_express"))
    events = album(cat, 60, load("givenchy_lock_boots"), 3, group="N")
    cand = [e for e in events if e.kind == "merge_candidate"]
    assert len(cand) == 1 and (cand[0].item_id, cand[0].other_id) == (2, 1)

    ev = cat.merge(keep_id=1, drop_id=2, now=3000)
    assert (ev.kind, ev.prev_status, ev.status) == ("status", "transit", "available")
    assert list(cat.items) == [1]
    item = cat.items[1]
    assert len(item.photos) == 3 and item.description
    assert cat.post_url(item) == "https://t.me/nasonchikh_wardrobe/60"
    # правка старого поста в express больше ничего не меняет
    assert cat.ingest(msg(7, transit + "\n#продано", "E-0", chat=EXPRESS, user="nasonchikh_express")) == []
    # а правка нового поста управляет товаром N° 001
    cat.ingest(msg(60, load("givenchy_lock_boots") + "\n#продано", "N-0", "N", date=4000))
    assert cat.items[1].status == "sold"


# ------------------------------------------------------------ прочее

def test_extra_photos_from_comments():
    cat = Catalog()
    album(cat, 10, load("margiela_evolution_low"), 2)
    assert cat.add_extra_photo(MAIN, 10, ph("x1"))
    assert not cat.add_extra_photo(MAIN, 10, ph("x1"))  # без дублей
    assert not cat.add_extra_photo(MAIN, 999, ph("x2"))  # неизвестный пост
    assert [p.file_unique_id for p in cat.items[1].extra_photos] == ["x1"]


def test_hide_removes_from_export():
    cat = Catalog()
    album(cat, 10, load("margiela_evolution_low"), 1)
    assert cat.hide(1, now=2000).kind == "hidden"
    assert cat.hide(1, now=2001) is None
    assert cat.export()["items"] == []


def test_state_roundtrip():
    cat = Catalog("nasonchikh_wardrobe")
    album(cat, 10, load("margiela_evolution_low"), 3)
    album(cat, 20, load("slp_jeans_hedi_multi"), 2, group="J")
    restored = Catalog.from_json(cat.to_json())
    assert restored.export() == cat.export()
    assert restored.next_id == 4
    # и продолжает работать идемпотентно
    assert album(restored, 10, load("margiela_evolution_low"), 3) == []


def test_export_shape_for_frontend():
    cat = Catalog("nasonchikh_wardrobe")
    album(cat, 10, load("margiela_evolution_low"), 2)
    item = cat.export()["items"][0]
    assert item["no"] == "001"
    assert item["price"] == 30900 and item["size"] == "40" and item["size_note"] == "40-41"
    assert item["photos"][0] == {"thumb": "media/G1-0-720.webp", "full": "media/G1-0-full.webp",
                                 "w": 1280, "h": 1707}
    assert item["buy_url"] == "https://t.me/nasonchikh_mngr"
