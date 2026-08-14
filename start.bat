@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  라벨 생성기 (개발/테스트 실행)
echo ========================================

where python >nul 2>&1
if errorlevel 1 (
  echo Python이 설치되어 있지 않습니다.
  echo https://www.python.org/downloads/ 에서 Python 3.11+ 설치 후 다시 실행하세요.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 가상환경 생성 중...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -U pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

set PYTHONPATH=.
python main.py %*
pause
