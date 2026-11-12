"""Словарь брендов: каноническое имя → варианты написания в постах.

Пополняйте список по мере появления новых брендов в канале.
Сравнение без учёта регистра; более длинные варианты проверяются первыми,
поэтому «MM6 Maison Margiela» не превратится в «Maison Margiela».
"""

from __future__ import annotations

import re

BRANDS: list[tuple[str, list[str]]] = [
    ("Maison Margiela", ["Maison Margiela", "Maison Martin Margiela", "Margiela"]),
    ("MM6 Maison Margiela", ["MM6 Maison Margiela", "MM6"]),
    ("Saint Laurent", ["Saint Laurent Paris", "Saint Laurent", "SLP"]),
    ("Yves Saint Laurent", ["Yves Saint Laurent", "YSL"]),
    ("Givenchy", ["Givenchy"]),
    ("Dior Homme", ["Dior Homme"]),
    ("Dior", ["Christian Dior", "Dior"]),
    ("Rick Owens DRKSHDW", ["Rick Owens DRKSHDW", "DRKSHDW"]),
    ("Rick Owens", ["Rick Owens"]),
    ("Raf Simons", ["Raf Simons"]),
    ("Helmut Lang", ["Helmut Lang"]),
    ("Yohji Yamamoto", ["Yohji Yamamoto", "Yohji"]),
    ("Y's", ["Y's"]),
    ("Y-3", ["Y-3"]),
    ("Comme des Garçons", ["Comme des Garçons", "Comme des Garcons", "CDG"]),
    ("Junya Watanabe", ["Junya Watanabe"]),
    ("Undercover", ["Undercover"]),
    ("Number (N)ine", ["Number (N)ine", "Number Nine"]),
    ("Issey Miyake", ["Issey Miyake"]),
    ("Homme Plissé Issey Miyake", ["Homme Plissé Issey Miyake", "Homme Plisse", "Homme Plissé"]),
    ("Ann Demeulemeester", ["Ann Demeulemeester"]),
    ("Carol Christian Poell", ["Carol Christian Poell", "CCP"]),
    ("Julius", ["Julius"]),
    ("Boris Bidjan Saberi", ["Boris Bidjan Saberi", "BBS"]),
    ("Guidi", ["Guidi"]),
    ("Prada", ["Prada"]),
    ("Miu Miu", ["Miu Miu"]),
    ("Jil Sander", ["Jil Sander"]),
    ("Balenciaga", ["Balenciaga"]),
    ("Vetements", ["Vetements"]),
    ("Acne Studios", ["Acne Studios", "Acne"]),
    ("Our Legacy", ["Our Legacy"]),
    ("Kapital", ["Kapital"]),
    ("Visvim", ["Visvim"]),
    ("Hysteric Glamour", ["Hysteric Glamour"]),
    ("Jean Paul Gaultier", ["Jean Paul Gaultier", "JPG"]),
    ("Vivienne Westwood", ["Vivienne Westwood"]),
    ("Alexander McQueen", ["Alexander McQueen", "McQueen"]),
    ("Dries Van Noten", ["Dries Van Noten"]),
    ("Martine Rose", ["Martine Rose"]),
    ("Chrome Hearts", ["Chrome Hearts"]),
    ("Enfants Riches Déprimés", ["Enfants Riches Déprimés", "Enfants Riches Deprimes", "ERD"]),
    ("Celine", ["Celine", "Céline"]),
    ("Bottega Veneta", ["Bottega Veneta"]),
    ("Gucci", ["Gucci"]),
    ("Louis Vuitton", ["Louis Vuitton"]),
    ("Burberry", ["Burberry"]),
    ("Stone Island", ["Stone Island"]),
    ("C.P. Company", ["C.P. Company", "CP Company"]),
    ("Moncler", ["Moncler"]),
    ("Diesel", ["Diesel"]),
    ("1017 ALYX 9SM", ["1017 ALYX 9SM", "ALYX"]),
    ("Kiko Kostadinov", ["Kiko Kostadinov"]),
    ("Craig Green", ["Craig Green"]),
    ("Lemaire", ["Lemaire"]),
    ("Mastermind Japan", ["Mastermind Japan", "Mastermind"]),
    ("No/Faith Studios", ["No/Faith Studios", "No Faith Studios", "No/Faith"]),
    ("Takahiromiyashita The Soloist", ["Takahiromiyashita The Soloist", "The Soloist"]),
    ("Thom Browne", ["Thom Browne"]),
    ("Song for the Mute", ["Song for the Mute"]),
    ("Entire Studios", ["Entire Studios"]),
    ("Arc'teryx Veilance", ["Arc'teryx Veilance", "Veilance"]),
    ("Arc'teryx", ["Arc'teryx", "Arcteryx"]),
    ("Converse", ["Converse"]),
    ("Nike", ["Nike"]),
    ("Adidas", ["Adidas"]),
    ("Salomon", ["Salomon"]),
    ("Asics", ["Asics"]),
    ("New Balance", ["New Balance"]),
]

# Конец варианта: конец строки, пробел или разделитель.
_END = r"(?=$|[\s\-–—:,.;!?“”\"«»'’()])"

_ALIASES: list[tuple[re.Pattern[str], str]] = sorted(
    (
        (re.compile(re.escape(alias) + _END, re.IGNORECASE), canonical)
        for canonical, aliases in BRANDS
        for alias in aliases
    ),
    key=lambda pair: -len(pair[0].pattern),
)


def match_brand_prefix(title: str) -> tuple[str, str] | None:
    """Бренд в начале названия → (каноническое имя, остаток названия)."""
    for pattern, canonical in _ALIASES:
        m = pattern.match(title)
        if m:
            return canonical, title[m.end():]
    return None
