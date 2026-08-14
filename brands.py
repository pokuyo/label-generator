"""브랜드(카테고리)별 AI/PNG·라벨 문구 설정."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from paths import get_app_dir, get_data_dir, get_label_root

BASE_DIR = get_app_dir()
LABEL_ROOT = get_label_root()


@dataclass(frozen=True)
class BrandConfig:
    id: str
    label: str
    brand_text: str
    ai_path: Path
    png_path: Path
    catalog_path: Path
    code_pattern: re.Pattern[str]
    skip_words: tuple[str, ...]
    barcode_prefix_priority: tuple[str, ...] = ("880921369", "880")


BRANDS: dict[str, BrandConfig] = {
    "sexybam": BrandConfig(
        id="sexybam",
        label="섹시밤 네일칼라",
        brand_text="섹시밤 네일 칼라",
        ai_path=LABEL_ROOT / "섹시밤.ai",
        png_path=LABEL_ROOT / "섹시밤.png",
        catalog_path=get_data_dir() / "catalog_sexybam.json",
        code_pattern=re.compile(r"(SMT\d{2}|SB\d{2}|SG\d{2}|SM\d{2}|SP\d{2}|SV\d{2}|S\d{2,3})", re.I),
        skip_words=("네일", "칼라", "갈라", "화장품", "차밍", "차망", "비엔씨", "비언", "섹시", "섹시밤"),
        barcode_prefix_priority=("880921369", "880"),
    ),
    "coloridium": BrandConfig(
        id="coloridium",
        label="칼라디움",
        brand_text="네일 칼라디움",
        ai_path=LABEL_ROOT / "칼라디움.ai",
        png_path=LABEL_ROOT / "칼라디움.png",
        catalog_path=get_data_dir() / "catalog_coloridium.json",
        code_pattern=re.compile(r"\b(\d{3})\b"),
        skip_words=("네일", "칼라", "칼라디움", "화장품", "차밍", "차망", "비엔씨", "비언"),
        barcode_prefix_priority=("880921369", "880936331", "880"),
    ),
    "nailflower": BrandConfig(
        id="nailflower",
        label="네일플라워",
        brand_text="인터칼라 프리티 네일플라워",
        ai_path=LABEL_ROOT / "네일플라워.ai",
        png_path=LABEL_ROOT / "네일플라워.png",
        catalog_path=get_data_dir() / "catalog_nailflower.json",
        code_pattern=re.compile(r"\b(\d{2})\b"),
        skip_words=("인터칼라", "프리티", "네일", "플라워", "화장품", "차밍", "차망", "비엔씨", "비언"),
        barcode_prefix_priority=("880921369", "880936331", "880"),
    ),
}

DEFAULT_BRAND_ID = "sexybam"

# 칼라디움 시트: 열 = 제품번호 끝자리(0~9), 행 = 십의 자리 블록(000·100·110…)
_COLORIDIUM_ROW_BY_TAG: dict[int, int] = {
    0: 0,
    100: 1,
    110: 2,
    200: 3,
    210: 4,
    220: 5,
    230: 6,
    300: 7,
    310: 8,
    320: 9,
    330: 10,
    340: 11,
    400: 12,
    410: 12,
    500: 13,
    510: 14,
    600: 15,
    610: 15,
    700: 16,
    710: 17,
    800: 18,
    900: 19,
    910: 20,
}


def coloridium_slot(code: str) -> tuple[int, int]:
    """3자리 제품번호 → (row, col). col은 0~9, row는 시리즈 블록."""
    n = int(code)
    if n < 0 or n > 999:
        raise ValueError(f"칼라디움 코드는 000~999 범위여야 합니다: {code}")
    col = n % 10
    tag = (n // 10) * 10 if n >= 10 else 0
    row = _COLORIDIUM_ROW_BY_TAG.get(tag)
    if row is None:
        hundred, ten = divmod(n // 10, 10)
        if n < 100:
            row = 0
        elif hundred < 4:
            row = max(1, (n // 10) - 9)
        elif hundred == 4:
            row = 12
        elif hundred == 5:
            row = 13 if ten == 0 else 14
        elif hundred == 6:
            row = 15
        elif hundred == 7:
            row = 16 if ten == 0 else 17
        elif hundred == 8:
            row = 18
        elif hundred == 9:
            row = 19 if ten == 0 else 20
        else:
            row = hundred * 2
    return row, col


# 네일플라워 시트: 열 = 제품번호 끝자리(0~9), 행 = 백·십의 자리 블록(00·100·200·210…)
_NAILFLOWER_ROW_BY_TAG: dict[int, int] = {
    0: 0,
    100: 1,
    200: 2,
    210: 3,
    300: 4,
    310: 5,
    400: 6,
    410: 7,
    500: 8,
    510: 9,
    600: 10,
    610: 11,
    700: 12,
    800: 13,
    900: 14,
    910: 15,
}


def nailflower_slot(code: str) -> tuple[int, int]:
    """제품번호 → (row, col). col은 0~9, row는 시리즈 블록."""
    n = int(code)
    if n < 0 or n > 999:
        raise ValueError(f"네일플라워 코드는 00~999 범위여야 합니다: {code}")
    col = n % 10
    if n < 10:
        return 0, col
    tag = (n // 10) * 10
    row = _NAILFLOWER_ROW_BY_TAG.get(tag)
    if row is None:
        hundred, ten = divmod(n // 10, 10)
        if hundred <= 2:
            row = hundred + ten
        elif hundred == 8 and ten == 0:
            row = 13
        elif hundred == 9 and ten == 0:
            row = 14
        elif hundred == 9 and ten == 1:
            row = 15
        else:
            row = hundred * 2 + ten - 2
    return row, col


def catalog_slot(brand_id: str, code: str, *, row: int, col: int) -> tuple[int, int]:
    """브랜드별 카탈로그 항목의 시트 (row, col) 좌표."""
    if brand_id == "coloridium" and code.isdigit():
        return coloridium_slot(code)
    if brand_id == "nailflower" and code.isdigit():
        return nailflower_slot(code)
    return row, col


def get_brand(brand_id: str) -> BrandConfig:
    key = (brand_id or DEFAULT_BRAND_ID).strip().lower()
    if key not in BRANDS:
        raise KeyError(f"Unknown brand: {brand_id}")
    return BRANDS[key]


def list_brands() -> list[BrandConfig]:
    return list(BRANDS.values())
