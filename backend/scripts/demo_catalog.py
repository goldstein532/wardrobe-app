"""Собирает демо-каталог из реальных постов (tests/fixtures) для разработки фронтенда.

    python scripts/demo_catalog.py ../design/img ../frontend/demo

Копирует фото в <папка>/demo/ и пишет <папка>/catalog.json (только для `npm run dev`).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from wardrobe.catalog import Catalog, Message, Photo  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "posts"
MAIN, EXPRESS = -100111, -100222

# (пост, фото, канал, дата)
POSTS = [
    ("nofaith_bomber_sold", ["nofaith-1", "nofaith-2"], MAIN, 1728300000),
    ("slp_jeans_hedi_multi", ["jeans-1", "jeans-2"], EXPRESS, 1730900000),
    ("givenchy_lock_boots", ["givenchy-1", "givenchy-2", "givenchy-3"], MAIN, 1731050000),
    ("slp_savage_zebra", ["slp-1", "slp-2", "slp-3"], MAIN, 1731060000),
    ("margiela_evolution_low", ["margiela-1", "margiela-2", "margiela-3"], MAIN, 1731070000),
    ("new_drop_announcement", ["drop"], MAIN, 1731080000),
]


def main(img_dir: Path, public: Path) -> None:
    demo = public / "demo"
    demo.mkdir(parents=True, exist_ok=True)
    cat = Catalog("nasonchikh_wardrobe")
    mid = 100
    for name, photos, chat, date in POSTS:
        text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
        user = "nasonchikh_wardrobe" if chat == MAIN else "nasonchikh_express"
        for k, photo in enumerate(photos):
            src = img_dir / f"{photo}.jpg"
            shutil.copy(src, demo / src.name)
            w, h = Image.open(src).size
            cat.ingest(Message(
                chat_id=chat, message_id=mid, date=date, text=text if k == 0 else None,
                photo=Photo(file_id=photo, file_unique_id=photo, width=w, height=h),
                media_group_id=f"{name}", chat_username=user,
            ))
            mid += 1
    # доп. фото из комментариев
    w, h = Image.open(img_dir / "drop.jpg").size
    cat.add_extra_photo(MAIN, 112, Photo("drop", "drop", w, h))

    def urls(p: Photo) -> dict:
        return {"thumb": f"demo/{p.file_unique_id}.jpg", "full": f"demo/{p.file_unique_id}.jpg",
                "w": p.width, "h": p.height}

    out = cat.export(urls)
    out["meta"] = {
        "channel": "nasonchikh_wardrobe",
        "admin_username": "nasonchikh_mngr",
        "generated_at": 1731100000,
    }
    (public / "catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"items={len(out['items'])} archive={len(out['archive'])}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
