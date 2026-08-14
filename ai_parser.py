# 브랜드별 .ai / PNG 시트에서 라벨 항목 추출.

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from brands import BrandConfig, DEFAULT_BRAND_ID, catalog_slot, get_brand

logger = logging.getLogger(__name__)

from paths import get_data_dir


def _cv2():
    # OpenCV 지연 import — exe 빌드 시 OCR 미포함 가능.
    import cv2

    return cv2

CODE_RE = re.compile(r"(SMT\d{2}|SB\d{2}|SG\d{2}|SM\d{2}|SP\d{2}|SV\d{2}|S\d{2,3})", re.I)
EAN13_RE = re.compile(r"880\d{10}")

# OCR에서 자주 틀리는 상품코드 보정
_CODE_FIXES = (
    (re.compile(r"\bSOI\b", re.I), "S01"),
    (re.compile(r"\bSOl\b", re.I), "S01"),
    (re.compile(r"\bSO([2-9])\b", re.I), r"S0\1"),
    (re.compile(r"\bS0O(\d)\b", re.I), r"S0\1"),
)

# 상품명 흔한 OCR 오타 보정
_NAME_FIXES = {
    "규티클": "큐티클",
    "규티클오일": "큐티클오일",
    "크리스틱": "크리스탈",
    "베이스콧": "베이스코트",
    "베이스코콧": "베이스코트",
    "손톱강화재": "손톱강화제",
    "브론즈카델": "브론즈카멜",
    "브론즈기델": "브론즈카멜",
}


@dataclass
class CatalogItem:
    code: str
    name: str
    barcode: str
    row: int
    col: int
    brand: str = DEFAULT_BRAND_ID
    source: str = ""


def _ean13_check_digit(body12: str) -> int:
    # EAN-13 체크디지it 계산.
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body12))
    return (10 - (total % 10)) % 10


def _is_valid_ean13(digits: str) -> bool:
    # 13자리 EAN-13 유효성(체크디지it) 검사.
    if len(digits) != 13 or not digits.isdigit():
        return False
    return int(digits[12]) == _ean13_check_digit(digits[:12])


def normalize_barcode(raw: str, *, prefixes: tuple[str, ...] = ("880921369", "880")) -> str:
    # EAN-13 정규화 및 체크디지it 보정.
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""

    candidates: list[str] = []
    for match in EAN13_RE.finditer(digits):
        candidates.append(match.group())

    idx = digits.find("880")
    while idx >= 0:
        chunk = digits[idx : idx + 13]
        if len(chunk) == 13:
            candidates.append(chunk)
        elif len(chunk) >= 12:
            body = chunk[:12]
            candidates.append(body + str(_ean13_check_digit(body)))
        idx = digits.find("880", idx + 1)

    if len(digits) == 12 and digits.startswith("880"):
        candidates.append(digits + str(_ean13_check_digit(digits)))

    seen: set[str] = set()
    valid: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_valid_ean13(candidate):
            valid.append(candidate)
    if valid:
        valid.sort(
            key=lambda c: (
                not any(c.startswith(p) for p in prefixes),
                prefixes.index(next(p for p in prefixes if c.startswith(p))) if any(c.startswith(p) for p in prefixes) else 99,
                c,
            )
        )
        for prefix in prefixes:
            preferred = [c for c in valid if c.startswith(prefix)]
            if preferred:
                return preferred[0]
        return valid[0]

    if len(digits) >= 13:
        fallback = digits[:13]
        if _is_valid_ean13(fallback):
            return fallback
    return ""


def _fix_code_token(token: str) -> str:
    # OCR 흔한 상품코드 오타 보정 (SOI→S01 등).
    result = token.upper().strip()
    for pattern, repl in _CODE_FIXES:
        result = pattern.sub(repl, result)
    result = result.replace("O", "0").replace("I", "1").replace("L", "1")
    return result


def normalize_code(raw: str, brand: BrandConfig | None = None) -> str:
    # 브랜드 code_pattern에 맞게 상품코드 추출·정규화.
    token = raw.strip().split()[0] if raw.strip() else ""
    token = _fix_code_token(token)
    pattern = brand.code_pattern if brand else CODE_RE
    m = pattern.search(token)
    if m:
        return m.group(1).upper() if brand and brand.id == "sexybam" else m.group(1)
    m = CODE_RE.search(token)
    return m.group(1).upper() if m else ""


def normalize_name(raw: str, code: str) -> str:
    # 상품명에서 코드·skip_words 제거 및 OCR 오타 보정.
    name = raw.strip()
    parts = name.split(maxsplit=1)
    if parts:
        first = _fix_code_token(parts[0])
        if CODE_RE.fullmatch(first) or CODE_RE.search(first):
            name = parts[1] if len(parts) > 1 else ""
    if code:
        name = re.sub(re.escape(code), "", name, flags=re.I).strip(" :-_")
    name = re.sub(r"[^\uAC00-\uD7A3A-Za-z0-9\s\-]", "", name).strip()
    for wrong, right in _NAME_FIXES.items():
        name = name.replace(wrong, right)
    return name


def _get_reader():
    # EasyOCR Reader 싱글턴 (한·영). 미설치 시 ImportError.
    try:
        import easyocr
    except ImportError as exc:
        raise ImportError(
            "AI 재추출(OCR)은 easyocr 패키지가 필요합니다. "
            "개발 환경에서는 pip install -r requirements.txt 를 실행하세요."
        ) from exc

    return easyocr.Reader(["ko", "en"], gpu=False, verbose=False)


def _preprocess_header(crop: Image.Image, scale: int = 8) -> np.ndarray:
    # 브랜드·상품명 영역 OCR 전처리 (확대·대비).
    cv2 = _cv2()
    enlarged = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    gray = cv2.cvtColor(np.array(enlarged), cv2.COLOR_RGB2GRAY)
    return cv2.convertScaleAbs(gray, alpha=1.35, beta=12)


def _preprocess_digits(crop: Image.Image, scale: int = 10) -> list[np.ndarray]:
    # 바코드 숫자 OCR용 이진화 변형 목록.
    cv2 = _cv2()
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    variants: list[np.ndarray] = []
    for thresh_type in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
        _, bw = cv2.threshold(gray, 0, 255, thresh_type + cv2.THRESH_OTSU)
        variants.append(bw)
    return variants


def _extract_barcode_from_texts(texts: list[str], *, prefixes: tuple[str, ...]) -> str:
    # OCR 텍스트 목록에서 EAN-13 후보 추출·우선순위 선택.
    joined = " ".join(texts)
    candidates: list[str] = []

    direct = normalize_barcode(joined, prefixes=prefixes)
    if direct:
        candidates.append(direct)

    digits = re.sub(r"\D", "", joined)
    for i in range(len(digits)):
        if not digits[i:].startswith("880"):
            continue
        for length in (13, 12):
            chunk = digits[i : i + length]
            if len(chunk) < 12:
                continue
            candidate = chunk if len(chunk) == 13 else chunk + str(_ean13_check_digit(chunk))
            if _is_valid_ean13(candidate):
                candidates.append(candidate)

    if not candidates:
        return ""

    seen: set[str] = set()
    valid: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            valid.append(c)

    valid.sort(
        key=lambda c: (
            not any(c.startswith(p) for p in prefixes),
            prefixes.index(next(p for p in prefixes if c.startswith(p))) if any(c.startswith(p) for p in prefixes) else 99,
            c,
        )
    )
    for prefix in prefixes:
        preferred = [c for c in valid if c.startswith(prefix)]
        if preferred:
            return preferred[0]
    return valid[0] if valid else ""


def _parse_label_crop(crop: Image.Image, reader, brand: BrandConfig) -> tuple[str, str, str]:
    # 라벨 1칸 crop → (code, name, barcode) 추출.
    w, h = crop.size

    # 전체 셀 OCR (코드/명/바코드 통합 fallback)
    full = crop.resize((crop.width * 6, crop.height * 6), Image.Resampling.LANCZOS)
    full_lines = reader.readtext(np.array(full), detail=0, paragraph=False)

    # 상단 28%: 브랜드 + 상품코드/명
    header = crop.crop((0, 0, w, int(h * 0.28)))
    header_img = _preprocess_header(header)
    header_lines = reader.readtext(header_img, detail=0, paragraph=False)

    skip_words = brand.skip_words

    code = ""
    name = ""
    for line in header_lines + full_lines:
        if any(k in line for k in skip_words):
            continue
        found = normalize_code(line, brand)
        if found:
            code = found
            name = normalize_name(line, code)
            break

    # 바코드 숫자 영역 (48~76%)
    digit_region = crop.crop((0, int(h * 0.48), w, int(h * 0.76)))
    digit_texts: list[str] = []
    for variant in _preprocess_digits(digit_region):
        digit_texts.extend(reader.readtext(variant, detail=0, allowlist="0123456789 "))

    all_barcode_texts = digit_texts + full_lines
    barcode = _extract_barcode_from_texts(all_barcode_texts, prefixes=brand.barcode_prefix_priority)

    return code, name, barcode


def _grid_bounds(height: int, rows: int, row: int) -> tuple[int, int]:
    # PNG 시트 행 Y 경계 (누적 반올림 오차 방지).
    y0 = round(row * height / rows)
    y1 = round((row + 1) * height / rows)
    return y0, y1


def extract_from_png(
    png_path: Path | str,
    brand: BrandConfig,
    cols: int = 10,
    rows: int = 20,
) -> list[CatalogItem]:
    # PNG 시트에서 OCR로 항목 추출.
    png_path = Path(png_path)
    if not png_path.exists():
        raise FileNotFoundError(f"PNG 파일을 찾을 수 없습니다: {png_path}")

    img = Image.open(png_path)
    width, height = img.size
    label_width = width // cols
    reader = _get_reader()
    source = brand.ai_path.name

    items: list[CatalogItem] = []
    for row in range(rows):
        y0, y1 = _grid_bounds(height, rows, row)
        for col in range(cols):
            x0 = col * label_width

            crop = img.crop((x0 + 8, y0 + 8, x0 + label_width - 8, y1 - 8))
            gray = np.array(crop.convert("L"))
            if gray.std() < 15:
                continue

            code, name, barcode = _parse_label_crop(crop, reader, brand)
            if not code and not barcode:
                continue

            items.append(
                CatalogItem(
                    code=code,
                    name=name,
                    barcode=barcode,
                    row=row,
                    col=col,
                    brand=brand.id,
                    source=source,
                )
            )

    logger.info("[%s] PNG 추출 완료: %d개 (유효 EAN-13: %d)", brand.id, len(items), sum(1 for i in items if _is_valid_ean13(i.barcode)))
    return items


def extract_from_ai(brand_id: str = DEFAULT_BRAND_ID) -> list[CatalogItem]:
    # AI/PDF 파일을 PNG로 렌더링 후 추출. PNG가 있으면 PNG 우선.
    brand = get_brand(brand_id)
    ai_path = brand.ai_path
    png_path = brand.png_path
    if png_path.exists():
        return extract_from_png(png_path, brand)
    if not ai_path.exists():
        raise FileNotFoundError(f"AI 파일을 찾을 수 없습니다: {ai_path}")

    import io

    import pymupdf

    doc = pymupdf.open(str(ai_path))
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=False)
    doc.close()

    tmp = get_data_dir() / f"_render_cache_{brand.id}.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(pix.tobytes("png"))
    return extract_from_png(tmp, brand)


def _apply_brand_slot(item: CatalogItem) -> CatalogItem:
    # 칼라디움·네일플라워 등 브랜드별 (row,col) 규칙 적용.
    row, col = catalog_slot(item.brand, item.code, row=item.row, col=item.col)
    if row == item.row and col == item.col:
        return item
    return CatalogItem(
        code=item.code,
        name=item.name,
        barcode=item.barcode,
        row=row,
        col=col,
        brand=item.brand,
        source=item.source,
    )


def load_catalog(brand_id: str = DEFAULT_BRAND_ID) -> list[CatalogItem]:
    # data/catalog_*.json 로드 및 브랜드 슬롯 보정.
    brand = get_brand(brand_id)
    cache_path = brand.catalog_path
    if not cache_path.exists():
        return []
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    items: list[CatalogItem] = []
    for item in data:
        item.setdefault("brand", brand.id)
        item.setdefault("source", brand.ai_path.name)
        items.append(_apply_brand_slot(CatalogItem(**item)))
    return items


def save_catalog(items: list[CatalogItem], brand_id: str = DEFAULT_BRAND_ID) -> None:
    # 카탈로그 항목을 JSON 파일로 저장.
    brand = get_brand(brand_id)
    cache_path = brand.catalog_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "code": i.code,
            "name": i.name,
            "barcode": i.barcode,
            "row": i.row,
            "col": i.col,
            "brand": i.brand,
            "source": i.source,
        }
        for i in items
    ]
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_verified_barcodes(
    items: list[CatalogItem],
    brand: BrandConfig,
) -> list[CatalogItem]:
    # PNG OCR 바코드와 EAN-13 검증값이 일치하면 정규화된 EAN-13을 적용.
    from label_engine import barcode_pattern_matches_png, validate_barcode

    png_path = brand.png_path
    prefixes = brand.barcode_prefix_priority
    verified: list[CatalogItem] = []
    pattern_ok = 0
    for item in items:
        if not item.barcode:
            verified.append(item)
            continue
        normalized = normalize_barcode(item.barcode, prefixes=prefixes)
        if not _is_valid_ean13(normalized):
            logger.warning("유효하지 않은 EAN-13 [%d,%d] %s", item.row, item.col, item.barcode)
            verified.append(
                CatalogItem(
                    code=item.code,
                    name=item.name,
                    barcode="",
                    row=item.row,
                    col=item.col,
                    brand=item.brand,
                    source=item.source,
                )
            )
            continue

        try:
            ean = validate_barcode(normalized)
        except ValueError:
            verified.append(
                CatalogItem(
                    code=item.code,
                    name=item.name,
                    barcode="",
                    row=item.row,
                    col=item.col,
                    brand=item.brand,
                    source=item.source,
                )
            )
            continue

        if barcode_pattern_matches_png(ean, png_path, item.row, item.col):
            pattern_ok += 1

        verified.append(
            CatalogItem(
                code=item.code,
                name=item.name,
                barcode=ean,
                row=item.row,
                col=item.col,
                brand=item.brand,
                source=item.source,
            )
        )

    logger.info("[%s] EAN-13 적용: %d개 / PNG 패턴 일치: %d개", brand.id, len([i for i in verified if i.barcode]), pattern_ok)
    return verified


def refresh_catalog(brand_id: str = DEFAULT_BRAND_ID) -> list[CatalogItem]:
    # AI/PNG OCR 재추출 → 검증 → JSON 저장 → 목록 반환.
    brand = get_brand(brand_id)
    items = extract_from_ai(brand_id)
    items = apply_verified_barcodes(items, brand)
    items = [_apply_brand_slot(i) for i in items]
    save_catalog(items, brand_id)
    return items


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    brand_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BRAND_ID
    items = refresh_catalog(brand_id)
    valid = [i for i in items if _is_valid_ean13(i.barcode)]
    print(f"[{brand_id}] 추출 완료: {len(items)}개 / 유효 EAN-13: {len(valid)}개")
    for item in items[:10]:
        mark = "✓" if _is_valid_ean13(item.barcode) else "✗"
        print(f"  {mark} [{item.row},{item.col}] {item.code} {item.name} {item.barcode}")
