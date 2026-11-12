"""Подготовка фото для витрины.

- `<uid>-720.webp`  — для сетки (720 px по ширине, чётко на экранах 3x);
- `<uid>-full.webp` — для карточки, исходный размер из Telegram без уменьшения.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

THUMB_WIDTH = 720


def variant_paths(media_dir: Path, uid: str) -> tuple[Path, Path]:
    return media_dir / f"{uid}-720.webp", media_dir / f"{uid}-full.webp"


def has_variants(media_dir: Path, uid: str) -> bool:
    return all(p.exists() for p in variant_paths(media_dir, uid))


def build_variants(data: bytes, media_dir: Path, uid: str) -> tuple[int, int]:
    """Сохраняет обе версии. Возвращает (ширина, высота) оригинала."""
    media_dir.mkdir(parents=True, exist_ok=True)
    thumb_path, full_path = variant_paths(media_dir, uid)
    with Image.open(io.BytesIO(data)) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")
    im.save(full_path, "WEBP", quality=88, method=6)
    thumb = im
    if im.width > THUMB_WIDTH:
        thumb = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)), Image.LANCZOS)
    thumb.save(thumb_path, "WEBP", quality=82, method=6)
    return im.width, im.height
