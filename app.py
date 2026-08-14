# 라벨 생성 웹 UI 서버.

from __future__ import annotations

import io
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_parser import CatalogItem, load_catalog, refresh_catalog
from brands import DEFAULT_BRAND_ID, catalog_slot, get_brand, list_brands
from label_engine import (
    SHEET_COLS,
    SHEET_ROWS,
    LabelItem,
    export_selected_sheet_zip,
    export_selected_zip,
    render_preview_thumbnail,
    validate_barcode,
    validate_code,
    validate_name,
)

app = FastAPI(title="Label Generator", version="2.0.0")
api = APIRouter(prefix="/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from paths import get_static_dir

STATIC_DIR = get_static_dir()

_sheets: dict[str, dict[str, LabelItem]] = {b.id: {} for b in list_brands()}
_catalogs: dict[str, list[CatalogItem]] = {}


class LabelCreateRequest(BaseModel):
    code: str
    name: str
    barcode: str


class LabelResponse(BaseModel):
    id: str
    code: str
    name: str
    barcode: str
    row: int
    col: int
    brand: str
    selected: bool = False


class CatalogResponse(BaseModel):
    code: str
    name: str
    barcode: str
    row: int
    col: int
    brand: str
    source: str
    valid: bool


class BrandResponse(BaseModel):
    id: str
    label: str
    brand_text: str


class ExportRequest(BaseModel):
    ids: list[str] = Field(min_length=1)
    mode: str = Field(default="individual", pattern="^(individual|sheet)$")
    brand: str = DEFAULT_BRAND_ID


class ImportCatalogRequest(BaseModel):
    codes: list[str] = Field(min_length=1)
    brand: str = DEFAULT_BRAND_ID


def _resolve_brand(brand: str | None) -> str:
    # API brand 쿼리 → 유효 brand_id. 없으면 HTTP 400.
    try:
        return get_brand(brand or DEFAULT_BRAND_ID).id
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"알 수 없는 브랜드: {brand}") from exc


def _sheet(brand_id: str) -> dict[str, LabelItem]:
    # 브랜드별 in-memory 시트 (label_id → LabelItem).
    return _sheets[brand_id]


def _is_valid_catalog_item(item: CatalogItem) -> bool:
    # 코드·EAN-13 바코드가 모두 유효한 카탈로그 항목인지.
    if not item.code or len(item.barcode) != 13:
        return False
    try:
        validate_barcode(item.barcode)
        return True
    except ValueError:
        return False


def _to_response(item: LabelItem, selected: bool = False) -> LabelResponse:
    # LabelItem → API 응답 DTO.
    return LabelResponse(
        id=item.id,
        code=item.code,
        name=item.name,
        barcode=item.barcode,
        row=item.row,
        col=item.col,
        brand=item.brand,
        selected=selected,
    )


def _catalog_to_response(item: CatalogItem) -> CatalogResponse:
    # CatalogItem → API 응답 DTO (valid 플래그 포함).
    return CatalogResponse(
        code=item.code,
        name=item.name,
        barcode=item.barcode,
        row=item.row,
        col=item.col,
        brand=item.brand,
        source=item.source,
        valid=_is_valid_catalog_item(item),
    )


def _next_slot(brand_id: str) -> tuple[int, int]:
    # 시트에서 비어 있는 다음 (row, col) — 좌→우, 위→아래.
    sheet = _sheet(brand_id)
    occupied = {(item.row, item.col) for item in sheet.values()}
    for row in range(SHEET_ROWS):
        for col in range(SHEET_COLS):
            if (row, col) not in occupied:
                return row, col
    raise HTTPException(status_code=400, detail="시트가 가득 찼습니다. (최대 200장)")


def _slot_for_code(brand_id: str, code: str) -> tuple[int, int]:
    # 브랜드·제품코드에 맞는 (row, col). 네일플라워/칼라디움은 번호 기반 배치.
    sheet = _sheet(brand_id)
    occupied = {(item.row, item.col) for item in sheet.values()}

    catalog_map = {item.code: item for item in _catalogs.get(brand_id, []) if item.code}
    cat = catalog_map.get(code)
    if cat:
        row, col = catalog_slot(brand_id, code, row=cat.row, col=cat.col)
    elif brand_id in ("nailflower", "coloridium") and code.isdigit():
        row, col = catalog_slot(brand_id, code, row=0, col=0)
    else:
        return _next_slot(brand_id)

    if row >= SHEET_ROWS or col >= SHEET_COLS:
        raise HTTPException(
            status_code=400,
            detail=f"코드 {code}에 해당하는 시트 위치가 범위를 벗어났습니다.",
        )
    if (row, col) in occupied:
        raise HTTPException(
            status_code=400,
            detail=f"시트 {row + 1}행 {col + 1}열에 이미 라벨이 있습니다.",
        )
    return row, col


def _sync_catalog_to_sheet(brand_id: str, *, replace: bool = False) -> int:
    # 카탈로그의 유효 항목을 AI 원본 row/col 위치에 시트에 배치.
    sheet = _sheet(brand_id)
    catalog = _catalogs.get(brand_id, [])

    if replace:
        sheet.clear()

    added = 0
    occupied = {(item.row, item.col) for item in sheet.values()}
    used_codes = {item.code for item in sheet.values()}

    for cat in catalog:
        if cat.brand != brand_id:
            continue
        if not _is_valid_catalog_item(cat):
            continue
        if cat.code in used_codes:
            continue
        try:
            vcode = validate_code(cat.code)
            vname = validate_name(cat.name) if cat.name else cat.code
            vbarcode = validate_barcode(cat.barcode)
        except ValueError:
            continue

        row, col = catalog_slot(brand_id, vcode, row=cat.row, col=cat.col)
        if row >= SHEET_ROWS or col >= SHEET_COLS:
            continue
        if (row, col) in occupied:
            continue

        label_id = uuid.uuid4().hex[:12]
        sheet[label_id] = LabelItem(
            id=label_id,
            code=vcode,
            name=vname,
            barcode=vbarcode,
            row=row,
            col=col,
            brand=brand_id,
        )
        occupied.add((row, col))
        used_codes.add(vcode)
        added += 1

    return added


@api.get("/health")
def health() -> dict[str, str | bool | int]:
    return {"status": "ok", "version": "3", "catalog": True, "brands": len(list_brands())}


@api.get("/brands", response_model=list[BrandResponse])
def api_list_brands() -> list[BrandResponse]:
    return [
        BrandResponse(id=b.id, label=b.label, brand_text=b.brand_text)
        for b in list_brands()
    ]


@api.get("/sheet/meta")
def sheet_meta(brand: str = Query(DEFAULT_BRAND_ID)) -> dict[str, int | str]:
    brand_id = _resolve_brand(brand)
    return {
        "brand": brand_id,
        "cols": SHEET_COLS,
        "rows": SHEET_ROWS,
        "capacity": SHEET_COLS * SHEET_ROWS,
        "count": len(_sheet(brand_id)),
    }


@api.get("/catalog", response_model=list[CatalogResponse])
def list_catalog(brand: str = Query(DEFAULT_BRAND_ID)) -> list[CatalogResponse]:
    brand_id = _resolve_brand(brand)
    return [_catalog_to_response(item) for item in _catalogs.get(brand_id, [])]


@api.post("/catalog/refresh", response_model=list[CatalogResponse])
def catalog_refresh(brand: str = Query(DEFAULT_BRAND_ID)) -> list[CatalogResponse]:
    brand_id = _resolve_brand(brand)
    global _catalogs
    try:
        _catalogs[brand_id] = refresh_catalog(brand_id)
        _sync_catalog_to_sheet(brand_id, replace=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI 추출 실패: {exc}") from exc
    return [_catalog_to_response(item) for item in _catalogs[brand_id]]


@api.post("/catalog/import")
def import_catalog_items(payload: ImportCatalogRequest) -> dict[str, object]:
    brand_id = _resolve_brand(payload.brand)
    sheet = _sheet(brand_id)
    imported: list[LabelResponse] = []
    errors: list[str] = []

    catalog_map = {item.code: item for item in _catalogs.get(brand_id, []) if item.code}
    for code in payload.codes:
        item = catalog_map.get(code)
        if not item:
            errors.append(f"{code}: 카탈로그에 없습니다.")
            continue
        if not _is_valid_catalog_item(item):
            errors.append(f"{code}: 바코드/코드 정보가 불완전합니다.")
            continue
        try:
            vcode = validate_code(item.code)
            vname = validate_name(item.name) if item.name else item.code
            vbarcode = validate_barcode(item.barcode)
        except ValueError as exc:
            errors.append(f"{code}: {exc}")
            continue

        if any(s.code == vcode for s in sheet.values()):
            errors.append(f"{code}: 이미 시트에 있습니다.")
            continue

        row, col = catalog_slot(brand_id, vcode, row=item.row, col=item.col)
        if row >= SHEET_ROWS or col >= SHEET_COLS or any(
            s.row == row and s.col == col for s in sheet.values()
        ):
            row, col = _next_slot(brand_id)

        label_id = uuid.uuid4().hex[:12]
        label = LabelItem(
            id=label_id,
            code=vcode,
            name=vname,
            barcode=vbarcode,
            row=row,
            col=col,
            brand=brand_id,
        )
        sheet[label_id] = label
        imported.append(_to_response(label))

    if not imported and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    return {"imported": imported, "errors": errors}


@api.get("/labels", response_model=list[LabelResponse])
def list_labels(brand: str = Query(DEFAULT_BRAND_ID)) -> list[LabelResponse]:
    brand_id = _resolve_brand(brand)
    return [
        _to_response(item)
        for item in sorted(_sheet(brand_id).values(), key=lambda i: (i.row, i.col))
    ]


@api.post("/labels", response_model=LabelResponse)
def add_label(payload: LabelCreateRequest, brand: str = Query(DEFAULT_BRAND_ID)) -> LabelResponse:
    brand_id = _resolve_brand(brand)
    try:
        code = validate_code(payload.code)
        name = validate_name(payload.name)
        barcode = validate_barcode(payload.barcode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row, col = _slot_for_code(brand_id, code)
    item_id = uuid.uuid4().hex[:12]
    item = LabelItem(
        id=item_id,
        code=code,
        name=name,
        barcode=barcode,
        row=row,
        col=col,
        brand=brand_id,
    )
    _sheet(brand_id)[item_id] = item
    return _to_response(item)


@api.delete("/labels/{item_id}")
def delete_label(item_id: str, brand: str = Query(DEFAULT_BRAND_ID)) -> dict[str, str]:
    brand_id = _resolve_brand(brand)
    sheet = _sheet(brand_id)
    if item_id not in sheet:
        raise HTTPException(status_code=404, detail="라벨을 찾을 수 없습니다.")
    del sheet[item_id]
    return {"status": "deleted"}


@api.delete("/labels")
def clear_labels(brand: str = Query(DEFAULT_BRAND_ID)) -> dict[str, str]:
    brand_id = _resolve_brand(brand)
    _sheet(brand_id).clear()
    return {"status": "cleared"}


@lru_cache(maxsize=512)
def _cached_preview_png(code: str, name: str, barcode: str, brand: str, size: str) -> bytes:
    if size == "large":
        from label_engine import render_label_png

        return render_label_png(code, name, barcode, dpi=300, brand=brand)
    return render_preview_thumbnail(code, name, barcode, brand=brand)


@api.get("/labels/{item_id}/preview")
def label_preview(
    item_id: str,
    size: str = "thumb",
    brand: str = Query(DEFAULT_BRAND_ID),
) -> Response:
    brand_id = _resolve_brand(brand)
    item = _sheet(brand_id).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="라벨을 찾을 수 없습니다.")
    png = _cached_preview_png(item.code, item.name, item.barcode, item.brand, size)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@api.post("/export")
def export_labels(payload: ExportRequest) -> StreamingResponse:
    brand_id = _resolve_brand(payload.brand)
    sheet = _sheet(brand_id)
    selected: list[LabelItem] = []
    for item_id in payload.ids:
        item = sheet.get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"라벨을 찾을 수 없습니다: {item_id}")
        selected.append(item)

    selected.sort(key=lambda i: (i.row, i.col))

    if payload.mode == "sheet":
        data = export_selected_sheet_zip(selected)
        filename = f"selected_labels_{brand_id}_sheet.zip"
    else:
        data = export_selected_zip(selected)
        filename = f"selected_labels_{brand_id}.zip"

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


app.include_router(api)


@app.on_event("startup")
def startup_load() -> None:
    # 서버 기동 시 카탈로그 JSON 로드 및 시트 초기 배치.
    global _catalogs
    for brand in list_brands():
        if brand.catalog_path.exists():
            _catalogs[brand.id] = load_catalog(brand.id)
            _sync_catalog_to_sheet(brand.id, replace=True)


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
