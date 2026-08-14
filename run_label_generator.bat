@echo off
cd /d "%~dp0"

REM Prefer dist\LabelGenerator after build; also works when copied next to exe.
set "APP_DIR=%~dp0dist\LabelGenerator"
if exist "%APP_DIR%\LabelGenerator.exe" (
  cd /d "%APP_DIR%"
  goto :run
)

if exist "%~dp0LabelGenerator.exe" (
  set "APP_DIR=%~dp0"
  cd /d "%APP_DIR%"
  goto :run
)

echo ERROR: LabelGenerator.exe not found.
echo.
echo 1. Run build_windows.bat first to create the exe.
echo 2. Then run this file again, or open:
echo    dist\LabelGenerator\run_label_generator.bat
pause
exit /b 1

:run
if not exist "_internal" (
  echo ERROR: _internal folder not found in %APP_DIR%
  echo Copy the entire LabelGenerator folder, not just the exe.
  pause
  exit /b 1
)

LabelGenerator.exe
echo.
echo Server stopped.
pause
