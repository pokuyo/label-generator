"""실행 경로 — 개발 / PyInstaller 빌드(exe) 공통."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_app_dir() -> Path:
    """exe 또는 프로젝트 루트 (설정·데이터 저장 위치)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_bundle_dir() -> Path:
    """읽기 전용 리소스 (static, fonts, 내장 catalog)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return get_app_dir()


def get_data_dir() -> Path:
    """카탈로그 JSON 등 — exe 옆 data/ (최초 실행 시 번들에서 복사)."""
    data = get_app_dir() / "data"
    data.mkdir(parents=True, exist_ok=True)
    if is_frozen():
        bundle_data = get_bundle_dir() / "data"
        if bundle_data.is_dir():
            for src in bundle_data.glob("catalog_*.json"):
                dest = data / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
    return data


def get_static_dir() -> Path:
    return get_bundle_dir() / "static"


def get_font_dir() -> Path:
    return get_bundle_dir() / "fonts"


def get_label_root() -> Path:
    """브랜드 .ai / .png — exe 옆 assets/ 우선, 없으면 개발용 상위 폴더."""
    app = get_app_dir()
    assets = app / "assets"
    if assets.is_dir():
        return assets
    parent = app.parent
    if any(parent.glob("*.ai")) or any(parent.glob("*.png")):
        return parent
    assets.mkdir(parents=True, exist_ok=True)
    return assets
