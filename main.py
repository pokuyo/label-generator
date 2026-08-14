# Windows exe / CLI 진입점.

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser


def main() -> None:
    # uvicorn 서버 기동 및 브라우저 자동 열기.
    parser = argparse.ArgumentParser(description="라벨 생성기")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"라벨 생성기: {url}")
    print("종료: 이 창을 닫거나 Ctrl+C")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
