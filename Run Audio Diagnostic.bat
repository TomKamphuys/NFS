@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\harmonic-drive-qt.exe" (
    ".venv\Scripts\harmonic-drive-qt.exe" --audio-diagnostic
) else (
    uv run harmonic-drive-qt --audio-diagnostic
)

if errorlevel 1 (
    echo.
    echo The audio diagnostic did not start or finish normally.
    echo Please take a screenshot of this window.
    pause
)

