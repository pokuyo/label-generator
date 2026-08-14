@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  라벨 생성기 Windows 패키지 빌드
echo ========================================

where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.11+ 가 필요합니다.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat

python -m pip install -U pip
pip install -r requirements.txt -r requirements-build.txt

echo.
echo PyInstaller 빌드 중... (수 분 소요)
pyinstaller label-generator.spec --noconfirm
if errorlevel 1 (
  echo 빌드 실패
  pause
  exit /b 1
)

if not exist "dist\LabelGenerator\assets" mkdir "dist\LabelGenerator\assets"

echo.
echo ========================================
echo  완료: dist\LabelGenerator\
echo  실행: dist\LabelGenerator\LabelGenerator.exe
echo.
echo  브랜드 원본 파일(.ai / .png)은 exe 옆 assets\ 폴더에 넣으세요.
echo  예) assets\섹시밤.png, assets\칼라디움.png ...
echo ========================================
pause
