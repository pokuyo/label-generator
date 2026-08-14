# Windows exe / CLI 진입점.

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import traceback
import webbrowser


def _pause_if_frozen(message: str = "") -> None:
    # exe 더블클릭 시 오류 메시지를 읽을 수 있게 대기.
    from paths import is_frozen

    if not is_frozen():
        return
    if message:
        print(message)
    input("Press Enter to exit...")


def _ensure_frozen_layout() -> None:
    # onedir 빌드: exe만 복사하면 _internal 이 없어 즉시 종료됨.
    from paths import get_app_dir, is_frozen

    if not is_frozen():
        return

    os.chdir(get_app_dir())
    internal = get_app_dir() / "_internal"
    if internal.is_dir():
        return

    print("ERROR: _internal folder not found next to LabelGenerator.exe")
    print("Copy the entire dist\\LabelGenerator\\ folder, not just the exe file.")
    _pause_if_frozen()
    sys.exit(1)


def _ensure_port_free(host: str, port: int) -> None:
    # 포트가 이미 사용 중이면 uvicorn 이 조용히 종료됨.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        print(f"ERROR: Port {port} is already in use on {host}.")
        print("Close other LabelGenerator windows, or run:")
        print("  taskkill /F /IM LabelGenerator.exe")
        _pause_if_frozen()
        sys.exit(1)
    finally:
        probe.close()


def main() -> int:
    # uvicorn 서버 기동 및 브라우저 자동 열기.
    _ensure_frozen_layout()

    parser = argparse.ArgumentParser(description="Label Generator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"Label Generator: {url}")
    print("Close this window to stop the server (or press Ctrl+C)")

    _ensure_port_free(args.host, args.port)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    # PyInstaller exe에서는 "app:app" 문자열 import가 실패하므로 객체를 직접 전달.
    from app import app as fastapi_app

    uvicorn.run(
        fastapi_app,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except KeyboardInterrupt:
        exit_code = 0
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        if exit_code != 0:
            _pause_if_frozen()
        sys.exit(exit_code)
