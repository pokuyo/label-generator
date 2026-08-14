# 라벨 PDF(.ai) 및 PNG 생성 엔진.

from __future__ import annotations

import io
import math
import re
import zipfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from pymupdf import mupdf
from PIL import Image

from brands import DEFAULT_BRAND_ID, get_brand

from paths import get_app_dir, get_font_dir

BASE_DIR = get_app_dir()
FONT_DIR = get_font_dir()
FONT_LIGHT = FONT_DIR / "NotoSansCJKkr-Light.otf"
FONT_BOLD = FONT_DIR / "NotoSansCJKkr-Bold.otf"

# 시트/라벨 규격 (섹시밤.ai MediaBox 기준 — 시트 배치 공통)
SHEET_COLS = 10
SHEET_ROWS = 20
LABEL_W_PT = 612.332 / SHEET_COLS
LABEL_H_PT = 1218.0 / SHEET_ROWS
SHEET_W_PT = LABEL_W_PT * SHEET_COLS
SHEET_H_PT = LABEL_H_PT * SHEET_ROWS

# 디자인 상수
BLACK = (0, 0, 0)

# EAN-13 바코드 레이아웃
BARCODE_DATA_HEIGHT_R = 45 / 62
BARCODE_GUARD_EXTEND_SCALE = 1 / 3
CORNER_RADIUS_PT = 3.0
BORDER_WIDTH_PT = 0.35

# 텍스트 bbox — baseline 대비 ascent/descent 비율 (Noto CJK 측정)
TEXT_DESCENT_RATIO = 0.28
TEXT_HEIGHT_RATIO = 1.45
FRAME_TEXT_PAD = 2.0

# 폰트 크기 (pt)
FONT_BRAND = 4
FONT_PRODUCT = 8
FONT_BARCODE_NUM = 4
FONT_FOOTER = 3

LABEL_EXPORT_MARGIN = 2.0

BRAND_TEXT = "섹시밤 네일 칼라"
FOOTER_LINES = (
    "화장품책임판매업자 : 차밍코리아",
    "화장품제조업자 : (주)비엔씨코리아",
)


@dataclass(frozen=True)
class BrandLayout:
    # 브랜드별 라벨 프레임·요소 배치 (원본 PNG/AI 측정).

    shape: str  # "rect" | "circle"
    frame_px: float
    cell_px_w: float
    cell_px_h: float
    layout_ref: float
    y_brand_top_r: float
    y_product_top_r: float
    y_product_bottom_r: float
    y_barcode_top_r: float
    y_barcode_bottom_r: float
    y_num_top_r: float
    y_footer1_top_r: float
    y_footer2_top_r: float


# 섹시밤·칼라디움 — 둥근 사각형 테두리 (섹시밤.png S54 셀 204×249px)
_DEFAULT_LAYOUT = BrandLayout(
    shape="rect",
    frame_px=166,
    cell_px_w=204,
    cell_px_h=249,
    layout_ref=166,
    y_brand_top_r=10 / 166,
    y_product_top_r=24 / 166,
    y_product_bottom_r=57 / 166,
    y_barcode_top_r=57 / 166,
    y_barcode_bottom_r=121 / 166,
    y_num_top_r=118 / 166,
    y_footer1_top_r=135 / 166,
    y_footer2_top_r=148 / 166,
)

# 네일플라워 — 원형 테두리 (네일플라워.png 셀 238×211px, 원 202px)
_NAILFLOWER_LAYOUT = BrandLayout(
    shape="circle",
    frame_px=202,
    cell_px_w=238,
    cell_px_h=211,
    layout_ref=202,
    y_brand_top_r=14 / 202,
    y_product_top_r=21 / 202,
    y_product_bottom_r=55 / 202,
    y_barcode_top_r=52 / 202,
    y_barcode_bottom_r=145 / 202,
    y_num_top_r=127 / 202,
    y_footer1_top_r=153 / 202,
    y_footer2_top_r=167 / 202,
)

_BRAND_LAYOUTS: dict[str, BrandLayout] = {
    "nailflower": _NAILFLOWER_LAYOUT,
}

# 네일플라워 — 원형 라벨 전용 줄바꿈·배치 (네일플라워.png 기준)
_NAILFLOWER_BRAND_LINES = ("인터칼라 프리티", "네일플라워")
_NAILFLOWER_Y = {
    "brand1": 10 / 202,
    "brand2": 24 / 202,
    "product": 38 / 202,
    "product_bottom": 72 / 202,
    "barcode_top": 80 / 202,
    "barcode_bottom": 130 / 202,
    "num": 128 / 202,
    "footer1": 152 / 202,
    "footer2": 166 / 202,
}


def _circle_chord_width(frame: pymupdf.Rect, y: float, *, pad: float = 3.0) -> float:
    # 원형 프레임에서 y 높이의 최대 가로 폭(현).
    r = frame.width / 2
    cy = (frame.y0 + frame.y1) / 2
    dy = abs(y - cy)
    if dy >= r:
        return 0.0
    return max(0.0, 2 * math.sqrt(r * r - dy * dy) - pad)


def _inner_rect(frame: pymupdf.Rect) -> pymupdf.Rect:
    # 테두리 안쪽 텍스트·바코드 배치 영역 (여백 제외).
    return pymupdf.Rect(
        frame.x0 + FRAME_TEXT_PAD,
        frame.y0 + FRAME_TEXT_PAD,
        frame.x1 - FRAME_TEXT_PAD,
        frame.y1 - FRAME_TEXT_PAD,
    )


def _layout_for(brand: str) -> BrandLayout:
    # 브랜드별 프레임 형태·요소 Y 비율. 미등록 브랜드는 섹시밤(사각) 레이아웃.
    return _BRAND_LAYOUTS.get(brand, _DEFAULT_LAYOUT)


def _frame_size_pt(layout: BrandLayout) -> float:
    # 원본 PNG 픽셀 비율 → PDF 포인트로 환산한 프레임(테두리) 한 변 길이.
    return LABEL_W_PT * (layout.frame_px / layout.cell_px_w)


def _label_frame(
    x: float,
    y: float,
    *,
    standalone: bool,
    layout: BrandLayout,
) -> tuple[pymupdf.Rect, float]:
    # 시트 셀 또는 단일 export 기준 (x,y)에 프레임 사각형·크기(s) 반환.
    s = _frame_size_pt(layout)
    if standalone:
        lx = x
        frame_y = y
    else:
        lx = x + (LABEL_W_PT - s) / 2
        frame_y = y + (LABEL_H_PT - s) / 2
    return pymupdf.Rect(lx, frame_y, lx + s, frame_y + s), s


def _draw_label_border(page: pymupdf.Page, frame: pymupdf.Rect, layout: BrandLayout) -> None:
    # 브랜드별 테두리 — 원형(circle) 또는 둥근 사각형(rect).
    if layout.shape == "circle":
        center = pymupdf.Point((frame.x0 + frame.x1) / 2, (frame.y0 + frame.y1) / 2)
        page.draw_circle(center, frame.width / 2, color=BLACK, width=BORDER_WIDTH_PT)
        return
    radius = CORNER_RADIUS_PT / frame.width
    page.draw_rect(frame, color=BLACK, fill=None, width=BORDER_WIDTH_PT, radius=radius)


def _export_size(layout: BrandLayout) -> float:
    # 개별 라벨 PDF/PNG 한 페이지 크기 (프레임 + 여백).
    return _frame_size_pt(layout) + 2 * LABEL_EXPORT_MARGIN


@dataclass
class LabelItem:
    id: str
    code: str
    name: str
    barcode: str
    row: int
    col: int
    brand: str = DEFAULT_BRAND_ID


def validate_barcode(value: str) -> str:
    # EAN-13 식별번호 검증 및 정규화.
    digits = re.sub(r"\D", "", value.strip())
    if len(digits) != 13:
        raise ValueError("식별번호는 13자리 EAN-13이어야 합니다.")
    if not digits.isdigit():
        raise ValueError("식별번호는 숫자만 입력 가능합니다.")

    body, check = digits[:12], int(digits[12])
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
    expected = (10 - (total % 10)) % 10
    if check != expected:
        raise ValueError(f"EAN-13 체크디지it가 올바르지 않습니다. (기대값: {expected})")
    return digits


def validate_code(value: str) -> str:
    # 상품코드 공백 제거·대문자화 및 형식 검증.
    code = value.strip().upper()
    if not code:
        raise ValueError("상품코드를 입력해 주세요.")
    if not re.fullmatch(r"[A-Z0-9\-_]+", code):
        raise ValueError("상품코드는 영문, 숫자, -, _ 만 사용 가능합니다.")
    return code


def validate_name(value: str) -> str:
    # 상품명 공백 제거 및 길이(30자) 검증.
    name = value.strip()
    if not name:
        raise ValueError("상품명을 입력해 주세요.")
    if len(name) > 30:
        raise ValueError("상품명은 30자 이내로 입력해 주세요.")
    return name


def format_barcode_text(value: str) -> str:
    # EAN-13을 'X XXXXXX XXXXXX' 표시 형식으로 변환.
    return f"{value[0]} {value[1:7]} {value[7:13]}"


def _barcode_run_pattern(gray_image: Image.Image) -> list[tuple[int, int]]:
    # 바코드 이미지 중앙 행 run-length 패턴 (0=흰, 1=검).
    row = np.array(gray_image.convert("L"))[gray_image.height // 2]
    binary = (row < 128).astype(int)
    runs: list[tuple[int, int]] = []
    if len(binary) == 0:
        return runs
    cur = int(binary[0])
    count = 1
    for val in binary[1:]:
        if int(val) == cur:
            count += 1
        else:
            runs.append((cur, count))
            cur = int(val)
            count = 1
    runs.append((cur, count))
    return runs


def _ean13_module_pattern(ean13: str) -> str:
    # EAN-13 가드 바 포함 모듈 패턴 (0=흰, 1=데이터, G=가드).
    from barcode.ean import EAN13_GUARD

    return EAN13_GUARD(ean13).build()[0]


def _packed_barcode_modules(code_line: str) -> list[tuple[int, bool, bool]]:
    # (연속 모듈 수, 검정 여부, 가드 바 여부).
    line = code_line + " "
    modules: list[tuple[int, bool, bool]] = []
    run = 1
    for i in range(len(line) - 1):
        if line[i] == line[i + 1]:
            run += 1
        else:
            ch = line[i]
            if ch == "1":
                modules.append((run, True, False))
            elif ch == "G":
                modules.append((run, True, True))
            else:
                modules.append((run, False, False))
            run = 1
    return modules


def _barcode_data_height(total_height: float) -> float:
    # 데이터 막대 높이 — 가드 연장 비율 반영.
    d = BARCODE_DATA_HEIGHT_R
    s = BARCODE_GUARD_EXTEND_SCALE
    return total_height * d / (d + (1 - d) * s)


def _draw_barcode_vector(page: pymupdf.Page, rect: pymupdf.Rect, ean13: str) -> None:
    # EAN-13 바코드를 벡터 막대로 그림 — 가드/데이터 동일 두께.
    modules = _packed_barcode_modules(_ean13_module_pattern(ean13))
    total = sum(count for count, _, _ in modules)
    if total <= 0 or rect.width <= 0 or rect.height <= 0:
        return

    data_h = _barcode_data_height(rect.height)
    x0 = rect.x0
    for count, is_black, is_guard in modules:
        x1 = x0 + rect.width * count / total
        if is_black:
            bar_h = rect.height if is_guard else data_h
            page.draw_rect(
                pymupdf.Rect(x0, rect.y0, x1, rect.y0 + bar_h),
                color=BLACK,
                fill=BLACK,
                width=0,
            )
        x0 = x1


def _render_barcode_array(width_px: int, height_px: int, ean13: str) -> np.ndarray:
    # 벡터와 동일 규칙으로 바코드 비트맵 생성 (패턴 검증·PNG용).
    modules = _packed_barcode_modules(_ean13_module_pattern(ean13))
    total = sum(count for count, _, _ in modules)
    arr = np.full((max(1, height_px), max(1, width_px)), 255, dtype=np.uint8)
    if total <= 0:
        return arr

    data_h = max(1, int(round(_barcode_data_height(float(height_px)))))
    x0 = 0.0
    for count, is_black, is_guard in modules:
        x1 = x0 + width_px * count / total
        ix0, ix1 = int(round(x0)), int(round(x1))
        if is_black:
            bar_h = height_px if is_guard else data_h
            arr[:bar_h, ix0:ix1] = 0
        x0 = x1
    return arr


def _make_barcode_image(ean13: str, width_px: int, height_px: int) -> bytes:
    # 바코드 비트맵 PNG bytes (OCR 패턴 검증용).
    arr = _render_barcode_array(width_px, height_px, ean13)
    img = Image.fromarray(arr).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _png_grid_bounds(height: int, rows: int, row: int) -> tuple[int, int]:
    # PNG 시트 행 경계 (누적 반올림 오차 방지).
    y0 = round(row * height / rows)
    y1 = round((row + 1) * height / rows)
    return y0, y1


def _extract_png_barcode_crop(
    png_path: Path | str,
    row: int,
    col: int,
    cols: int = SHEET_COLS,
    rows: int = SHEET_ROWS,
) -> Image.Image | None:
    # 원본 PNG에서 바코드 막대 영역 crop.
    png_path = Path(png_path)
    if not png_path.exists():
        return None
    img = Image.open(png_path)
    width, height = img.size
    label_width = width // cols
    y0, y1 = _png_grid_bounds(height, rows, row)
    cell = img.crop((col * label_width + 8, y0 + 8, (col + 1) * label_width - 8, y1 - 8))
    if np.array(cell.convert("L")).std() < 15:
        return None
    cw, ch = cell.size
    return cell.crop((int(cw * 0.06), int(ch * 0.26), int(cw * 0.96), int(ch * 0.53))).convert("L")


def barcode_pattern_matches_png(
    ean13: str,
    png_path: Path | str,
    row: int,
    col: int,
) -> bool:
    # PNG 바코드 막대 패턴과 EAN-13 생성 패턴이 동일한지 검증.
    crop = _extract_png_barcode_crop(png_path, row, col)
    if crop is None:
        return False
    try:
        ean13 = validate_barcode(ean13)
    except ValueError:
        return False

    orig_runs = _barcode_run_pattern(crop)
    if not orig_runs:
        return False

    gen_bytes = _make_barcode_image(ean13, crop.width, crop.height)
    gen_img = Image.open(io.BytesIO(gen_bytes)).convert("L")
    gen_runs = _barcode_run_pattern(gen_img)

    if len(orig_runs) != len(gen_runs):
        return False
    return all(o[0] == g[0] for o, g in zip(orig_runs, gen_runs))


FONT_LIGHT_OBJ = pymupdf.Font(fontfile=str(FONT_LIGHT))
FONT_BOLD_OBJ = pymupdf.Font(fontfile=str(FONT_BOLD))

# .ai 내보내기: 텍스트를 벡터 패스(아웃라인)로 변환 — Illustrator Create Outlines.
_outline_text = False


@contextmanager
def _text_as_outlines():
    global _outline_text
    prev = _outline_text
    _outline_text = True
    try:
        yield
    finally:
        _outline_text = prev


class _GlyphPathWalker(mupdf.FzPathWalker2):
    # fz_outline_glyph 경로는 PDF 좌표 — Shape.ipctm 변환 없이 그대로 기록.

    def __init__(self, shape: pymupdf.Shape) -> None:
        super().__init__()
        self.shape = shape
        self.use_virtual_moveto()
        self.use_virtual_lineto()
        self.use_virtual_curveto()
        self.use_virtual_closepath()

    def _fmt(self, point: pymupdf.Point) -> str:
        return pymupdf._format_g(pymupdf.JM_TUPLE(point))

    def moveto(self, ctx, x, y) -> None:
        point = pymupdf.Point(x, y)
        if self.shape.last_point != point:
            self.shape.draw_cont += self._fmt(point) + " m\n"
            self.shape.last_point = point

    def lineto(self, ctx, x, y) -> None:
        point = pymupdf.Point(x, y)
        self.shape.draw_cont += self._fmt(point) + " l\n"
        self.shape.last_point = point
        self.shape.updateRect(point)

    def curveto(self, ctx, x1, y1, x2, y2, x3, y3) -> None:
        p1 = pymupdf.Point(x1, y1)
        p2 = pymupdf.Point(x2, y2)
        p3 = pymupdf.Point(x3, y3)
        args = pymupdf.JM_TUPLE(list(p1) + list(p2) + list(p3))
        self.shape.draw_cont += pymupdf._format_g(args) + " c\n"
        self.shape.last_point = p3
        self.shape.updateRect(p1)
        self.shape.updateRect(p2)
        self.shape.updateRect(p3)

    def closepath(self, ctx) -> None:
        self.shape.draw_cont += "h\n"


def _text_writer_point(page: pymupdf.Page, point: pymupdf.Point) -> pymupdf.Point:
    # TextWriter.append() 와 동일한 좌표계로 변환 (상하 반전 방지).
    ictm = ~pymupdf.Matrix(1, 0, 0, -1, 0, page.rect.height)
    return point * ictm


def _insert_text_outlined(
    page: pymupdf.Page,
    point: pymupdf.Point,
    text: str,
    font: pymupdf.Font,
    fontsize: float,
) -> None:
    if not text:
        return
    shape = page.new_shape()
    p = _text_writer_point(page, point)
    x, y = p.x, p.y
    for ch in text:
        cp = ord(ch)
        gid, fzfont = mupdf.fz_encode_character_with_fallback(font.this, cp, 0, 0)
        trm = mupdf.fz_make_matrix(fontsize, 0, 0, fontsize, x, y)
        path = mupdf.fz_outline_glyph(fzfont, gid, trm)
        walker = _GlyphPathWalker(shape)
        mupdf.fz_walk_path(path, walker, walker.m_internal)
        x += font.glyph_advance(cp) * fontsize
    # 채우기만 적용 (width=0). color+fill 동시 지정 시 외곽선이 두꺼워짐.
    shape.finish(fill=BLACK, width=0, even_odd=True, closePath=False)
    shape.commit()


def _text_width(text: str, font: pymupdf.Font, fontsize: float) -> float:
    # 주어진 폰트·크기에서 텍스트 가로 폭(pt).
    return font.text_length(text, fontsize=fontsize)


def _fit_font_size(
    text: str,
    font: pymupdf.Font,
    max_size: float,
    min_size: float,
    max_width: float,
) -> float:
    # max_width 안에 들어가도록 폰트 크기를 줄임.
    size = max_size
    while size > min_size and _text_width(text, font, size) > max_width:
        size -= 0.2
    return max(size, min_size)


def _insert_text(page: pymupdf.Page, point: pymupdf.Point, text: str, font: pymupdf.Font, fontsize: float) -> None:
    # PDF 페이지에 단일 텍스트 span 삽입.
    if _outline_text:
        _insert_text_outlined(page, point, text, font, fontsize)
        return
    tw = pymupdf.TextWriter(page.rect, color=BLACK)
    tw.append(point, text, font=font, fontsize=fontsize)
    tw.write_text(page)


def _insert_text_baseline(
    page: pymupdf.Page,
    frame: pymupdf.Rect,
    baseline_y: float,
    text: str,
    font: pymupdf.Font,
    fontsize: float,
    *,
    min_size: float = 2.0,
    max_width: float | None = None,
) -> float:
    # baseline 기준 텍스트 — bbox 하단 y 반환.
    inner = _inner_rect(frame)
    width = min(max_width, inner.width) if max_width is not None else inner.width
    size = _fit_font_size(text, font, fontsize, min_size, width)
    text_w = _text_width(text, font, size)
    cx = (inner.x0 + inner.x1) / 2
    x = cx - text_w / 2
    x = max(inner.x0, min(x, inner.x1 - text_w))
    _insert_text(page, pymupdf.Point(x, baseline_y), text, font, size)
    return baseline_y + size * TEXT_DESCENT_RATIO


def _insert_line_at_top_ratio(
    page: pymupdf.Page,
    frame: pymupdf.Rect,
    top_ratio: float,
    text: str,
    font: pymupdf.Font,
    fontsize: float,
    *,
    min_size: float = 2.0,
    circle: bool = False,
    max_bottom_ratio: float | None = None,
) -> float:
    # 프레임 top 비율 위치에 한 줄 텍스트 배치. circle=True면 원형 chord 폭 제한.
    top_y = frame.y0 + frame.height * top_ratio
    inner = _inner_rect(frame)
    est_baseline = top_y + _text_ascent_for_top(fontsize)
    max_w = _circle_chord_width(frame, est_baseline) if circle else inner.width
    size = _fit_font_size(text, font, fontsize, min_size, max_w)
    if max_bottom_ratio is not None:
        max_bottom_y = frame.y0 + frame.height * max_bottom_ratio
        while size > min_size:
            baseline = top_y + _text_ascent_for_top(size)
            bottom_y = baseline + size * TEXT_DESCENT_RATIO
            if bottom_y <= max_bottom_y:
                break
            size -= 0.2
        size = max(size, min_size)
    baseline = top_y + _text_ascent_for_top(size)
    if circle:
        max_w = _circle_chord_width(frame, baseline - size * 0.35)
        size = _fit_font_size(text, font, fontsize, min_size, max_w)
        if max_bottom_ratio is not None:
            max_bottom_y = frame.y0 + frame.height * max_bottom_ratio
            while size > min_size:
                baseline = top_y + _text_ascent_for_top(size)
                bottom_y = baseline + size * TEXT_DESCENT_RATIO
                if bottom_y <= max_bottom_y:
                    break
                size -= 0.2
            size = max(size, min_size)
        baseline = top_y + _text_ascent_for_top(size)
        max_w = _circle_chord_width(frame, baseline - size * 0.35)
        size = _fit_font_size(text, font, fontsize, min_size, max_w)
        baseline = top_y + _text_ascent_for_top(size)
    _insert_text_baseline(
        page,
        frame,
        baseline,
        text,
        font,
        size,
        min_size=min_size,
        max_width=max_w if circle else None,
    )
    return size


def _text_ascent(size: float) -> float:
    # baseline → ascent 높이 (바코드 숫자 baseline 계산용).
    return size * (1.16 if size >= 4.0 else 1.0)


def _text_ascent_for_top(size: float) -> float:
    # 텍스트 상단을 inner.y0 에 맞출 때 baseline 오프셋.
    return size * 1.16


def _insert_at_top_ratio(
    page: pymupdf.Page,
    frame: pymupdf.Rect,
    top_ratio: float,
    text: str,
    font: pymupdf.Font,
    fontsize: float,
    *,
    min_size: float = 2.0,
    max_bottom_ratio: float | None = None,
) -> float:
    # 원본 bbox top 비율에 맞춰 텍스트 배치.
    inner = pymupdf.Rect(
        frame.x0 + FRAME_TEXT_PAD,
        frame.y0 + FRAME_TEXT_PAD,
        frame.x1 - FRAME_TEXT_PAD,
        frame.y1 - FRAME_TEXT_PAD,
    )
    size = _fit_font_size(text, font, fontsize, min_size, inner.width)
    if max_bottom_ratio is not None:
        max_bottom_y = frame.y0 + frame.height * max_bottom_ratio
        while size > min_size:
            top_y = frame.y0 + frame.height * top_ratio
            baseline = top_y + _text_ascent_for_top(size)
            bottom_y = baseline + size * TEXT_DESCENT_RATIO
            if bottom_y <= max_bottom_y:
                break
            size -= 0.2
        size = max(size, min_size)
    top_y = frame.y0 + frame.height * top_ratio
    top_y = max(top_y, inner.y0)
    baseline = top_y + _text_ascent_for_top(size)
    _insert_text_baseline(page, frame, baseline, text, font, size, min_size=min_size)
    return size


def _find_text_bottom(page: pymupdf.Page, *needles: str) -> float:
    # 페이지에서 needle 텍스트 span의 최대 하단 y (블록 병합에도 동작).
    bottom = 0.0
    found = False
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"]
                for needle in needles:
                    if not needle:
                        continue
                    normalized = needle.replace(" ", "\xa0")
                    if needle in text or normalized in text:
                        bottom = max(bottom, span["bbox"][3])
                        found = True
                        break
    if not found:
        raise RuntimeError(f"텍스트 bbox를 찾을 수 없습니다: {needles[0]!r}")
    return bottom


def _draw_nailflower_label(
    page: pymupdf.Page,
    frame: pymupdf.Rect,
    s: float,
    code: str,
    name: str,
    ean13: str,
) -> None:
    # 네일플라워 원형 라벨 — 줄바꿈·원형 chord 폭 제한.
    y = _NAILFLOWER_Y
    _insert_line_at_top_ratio(
        page, frame, y["brand1"], _NAILFLOWER_BRAND_LINES[0], FONT_LIGHT_OBJ, FONT_BRAND, min_size=2.5, circle=True
    )
    _insert_line_at_top_ratio(
        page, frame, y["brand2"], _NAILFLOWER_BRAND_LINES[1], FONT_LIGHT_OBJ, FONT_BRAND, min_size=2.5, circle=True
    )
    _insert_line_at_top_ratio(
        page,
        frame,
        y["product"],
        f"{code} {name}",
        FONT_BOLD_OBJ,
        FONT_PRODUCT,
        min_size=4.5,
        circle=True,
        max_bottom_ratio=y["product_bottom"],
    )

    barcode_top = frame.y0 + s * y["barcode_top"]
    barcode_bottom = frame.y0 + s * y["barcode_bottom"]
    bc_center_y = (barcode_top + barcode_bottom) / 2
    bc_width = _circle_chord_width(frame, bc_center_y, pad=4.0)
    cx = (frame.x0 + frame.x1) / 2
    barcode_rect = pymupdf.Rect(cx - bc_width / 2, barcode_top, cx + bc_width / 2, barcode_bottom)
    _draw_barcode_vector(page, barcode_rect, ean13)

    num_top = frame.y0 + s * y["num"]
    est_num_baseline = num_top + _text_ascent(FONT_BARCODE_NUM)
    num_max_w = _circle_chord_width(frame, est_num_baseline)
    num_size = _fit_font_size(
        format_barcode_text(ean13), FONT_LIGHT_OBJ, FONT_BARCODE_NUM, 2.2, num_max_w
    )
    num_baseline = num_top + _text_ascent(num_size)
    _insert_text_baseline(
        page,
        frame,
        num_baseline,
        format_barcode_text(ean13),
        FONT_LIGHT_OBJ,
        num_size,
        min_size=2.2,
        max_width=_circle_chord_width(frame, num_baseline - num_size * 0.35),
    )

    _insert_line_at_top_ratio(
        page,
        frame,
        y["footer1"],
        FOOTER_LINES[0],
        FONT_LIGHT_OBJ,
        FONT_FOOTER,
        min_size=1.8,
        circle=True,
    )
    _insert_line_at_top_ratio(
        page,
        frame,
        y["footer2"],
        FOOTER_LINES[1],
        FONT_LIGHT_OBJ,
        FONT_FOOTER,
        min_size=1.8,
        circle=True,
    )


def draw_label(
    page: pymupdf.Page,
    x: float,
    y: float,
    code: str,
    name: str,
    ean13: str,
    *,
    standalone: bool = False,
    brand: str = DEFAULT_BRAND_ID,
) -> None:
    # 단일 라벨을 PDF 페이지에 그립니다. (x, y = 배치 기준 좌상단)
    brand_text = get_brand(brand).brand_text
    layout = _layout_for(brand)
    frame, s = _label_frame(x, y, standalone=standalone, layout=layout)

    _draw_label_border(page, frame, layout)

    if brand == "nailflower":
        _draw_nailflower_label(page, frame, s, code, name, ean13)
        return

    inner = _inner_rect(frame)
    _insert_at_top_ratio(
        page,
        frame,
        layout.y_brand_top_r,
        brand_text,
        FONT_LIGHT_OBJ,
        FONT_BRAND,
        min_size=2.5,
    )

    product_text = f"{code} {name}"
    _insert_at_top_ratio(
        page,
        frame,
        layout.y_product_top_r,
        product_text,
        FONT_BOLD_OBJ,
        FONT_PRODUCT,
        min_size=4.5,
        max_bottom_ratio=layout.y_product_bottom_r,
    )

    barcode_top = frame.y0 + s * layout.y_barcode_top_r
    barcode_bottom = frame.y0 + s * layout.y_barcode_bottom_r
    pad_x = s * 0.06
    barcode_rect = pymupdf.Rect(frame.x0 + pad_x, barcode_top, frame.x1 - pad_x, barcode_bottom)
    _draw_barcode_vector(page, barcode_rect, ean13)

    num_size = _fit_font_size(format_barcode_text(ean13), FONT_LIGHT_OBJ, FONT_BARCODE_NUM, 2.2, inner.width)
    num_top = frame.y0 + s * layout.y_num_top_r
    num_baseline = num_top + _text_ascent(num_size)
    barcode_num_text = format_barcode_text(ean13)
    _insert_text_baseline(
        page,
        frame,
        num_baseline,
        barcode_num_text,
        FONT_LIGHT_OBJ,
        FONT_BARCODE_NUM,
        min_size=2.2,
    )

    footer_size = _fit_font_size(FOOTER_LINES[0], FONT_LIGHT_OBJ, FONT_FOOTER, 2.0, inner.width)
    footer1_top = frame.y0 + s * layout.y_footer1_top_r
    footer1_baseline = footer1_top + _text_ascent_for_top(footer_size)
    _insert_text_baseline(
        page,
        frame,
        footer1_baseline,
        FOOTER_LINES[0],
        FONT_LIGHT_OBJ,
        FONT_FOOTER,
        min_size=2.0,
    )

    footer2_top = frame.y0 + s * layout.y_footer2_top_r
    footer2_baseline = footer2_top + _text_ascent_for_top(footer_size)
    _insert_text_baseline(
        page,
        frame,
        footer2_baseline,
        FOOTER_LINES[1],
        FONT_LIGHT_OBJ,
        FONT_FOOTER,
        min_size=2.0,
    )


def _new_page(size: tuple[float, float]) -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    return doc, page


def render_label_pdf(
    code: str,
    name: str,
    ean13: str,
    *,
    brand: str = DEFAULT_BRAND_ID,
    outline_text: bool = False,
) -> bytes:
    # 단일 라벨 PDF bytes (.ai 호환).
    layout = _layout_for(brand)
    export_size = _export_size(layout)
    doc, page = _new_page((export_size, export_size))
    ctx = _text_as_outlines() if outline_text else nullcontext()
    with ctx:
        draw_label(page, LABEL_EXPORT_MARGIN, LABEL_EXPORT_MARGIN, code, name, ean13, standalone=True, brand=brand)
    pdf = doc.tobytes()
    doc.close()
    return pdf


def render_sheet_pdf(items: list[LabelItem], *, outline_text: bool = False) -> bytes:
    # 선택 라벨을 10×20 시트 PDF로 렌더.
    doc, page = _new_page((SHEET_W_PT, SHEET_H_PT))
    ctx = _text_as_outlines() if outline_text else nullcontext()
    with ctx:
        for item in items:
            x = item.col * LABEL_W_PT
            y = item.row * LABEL_H_PT
            draw_label(page, x, y, item.code, item.name, item.barcode, brand=item.brand)
    pdf = doc.tobytes()
    doc.close()
    return pdf


def pdf_to_png(pdf_bytes: bytes, dpi: int = 300) -> bytes:
    # PDF 1페이지 → PNG bytes 변환.
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("png")
    doc.close()
    return data


def render_label_png(code: str, name: str, ean13: str, dpi: int = 300, *, brand: str = DEFAULT_BRAND_ID) -> bytes:
    # 단일 라벨 PNG bytes.
    return pdf_to_png(render_label_pdf(code, name, ean13, brand=brand), dpi=dpi)


def render_preview_thumbnail(code: str, name: str, ean13: str, *, brand: str = DEFAULT_BRAND_ID) -> bytes:
    # 웹 UI 썸네일용 작은 PNG (~120px). 필요한 해상도만 렌더링.
    doc = pymupdf.open(stream=render_label_pdf(code, name, ean13, brand=brand), filetype="pdf")
    page = doc[0]
    target_w = 120
    zoom = target_w / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("png")
    doc.close()
    return data


def render_sheet_preview_png(items: list[LabelItem], dpi: int = 120) -> bytes:
    return pdf_to_png(render_sheet_pdf(items), dpi=dpi)


def export_selected_zip(items: list[LabelItem]) -> bytes:
    # 선택 라벨을 개별 .ai + .png ZIP으로 묶음.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            base = f"{item.code}_{item.name}"
            pdf = render_label_pdf(item.code, item.name, item.barcode, brand=item.brand, outline_text=True)
            png = pdf_to_png(pdf, dpi=600)
            zf.writestr(f"{base}.ai", pdf)
            zf.writestr(f"{base}.png", png)
    buffer.seek(0)
    return buffer.getvalue()


def export_selected_sheet_zip(items: list[LabelItem]) -> bytes:
    # 선택 라벨을 시트 1장(.ai + .png) ZIP으로 묶음.
    buffer = io.BytesIO()
    pdf = render_sheet_pdf(items, outline_text=True)
    png = pdf_to_png(pdf, dpi=300)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("selected_sheet.ai", pdf)
        zf.writestr("selected_sheet.png", png)
    buffer.seek(0)
    return buffer.getvalue()
