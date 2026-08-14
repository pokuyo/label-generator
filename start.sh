#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH=.

PORT=${PORT:-8765}

# 기존 프로세스 종료 시도
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "포트 $PORT 사용 중 → 기존 프로세스 종료 시도..."
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

if lsof -ti:"$PORT" >/dev/null 2>&1; then
  ALT=8766
  echo "⚠️  포트 $PORT 이(가) 다른 프로세스에 의해 점유되어 있습니다."
  echo "   터미널에서 아래 명령으로 직접 종료 후 다시 실행해 주세요:"
  echo "   lsof -ti:$PORT | xargs kill -9"
  echo ""
  echo "   임시로 포트 $ALT 에서 실행합니다 → http://127.0.0.1:$ALT"
  PORT=$ALT
fi

echo "라벨 생성기: http://127.0.0.1:$PORT"
exec python3 -m uvicorn app:app --host 127.0.0.1 --port "$PORT" --reload
