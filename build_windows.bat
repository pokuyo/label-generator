@echo off
cd /d "%~dp0"

echo ========================================
echo  Label Generator - Windows Build
echo ========================================

where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.11+ is required.
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
echo Running PyInstaller... (may take several minutes)

REM Stop running exe so dist folder is not locked
echo Closing LabelGenerator if running...
taskkill /F /IM LabelGenerator.exe >nul 2>&1
timeout /t 3 /nobreak >nul

REM Remove old dist output (PyInstaller fails if files are locked)
if exist "dist\LabelGenerator" (
  echo Removing old dist\LabelGenerator ...
  rmdir /s /q "dist\LabelGenerator" 2>nul
  timeout /t 2 /nobreak >nul
)
if exist "dist\LabelGenerator" (
  echo.
  echo ERROR: dist\LabelGenerator is locked.
  echo - Close LabelGenerator.exe and this folder in File Explorer
  echo - Pause OneDrive sync if the project is in OneDrive
  echo - Then run build_windows.bat again
  pause
  exit /b 1
)

pyinstaller label-generator.spec --noconfirm
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

if not exist "dist\LabelGenerator\assets" mkdir "dist\LabelGenerator\assets"
copy /Y run_label_generator.bat dist\LabelGenerator\ >nul

echo.
echo ========================================
echo  Done: dist\LabelGenerator\
echo  Run:   dist\LabelGenerator\run_label_generator.bat
echo         (or LabelGenerator.exe)
echo.
echo  Put brand source files (.ai / .png) in assets\ next to the exe.
echo  e.g. assets\sexybam.png, assets\coloridium.png ...
echo ========================================
pause
