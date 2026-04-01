@echo off
REM ============================================================
REM  build_exe.bat
REM  Packages ui.py into a standalone Windows .exe using PyInstaller.
REM
REM  Usage:
REM    1. Open a Command Prompt in this folder
REM    2. Run:   build_exe.bat
REM    3. Find the finished .exe in:   dist\NewsScraper.exe
REM ============================================================

echo.
echo ==========================================
echo  News Scraper -- Build EXE
echo ==========================================
echo.

REM Step 1: make sure PyInstaller is installed
echo [1/4] Closing any running NewsScraper instance...
taskkill /F /IM NewsScraper.exe 2>nul
if %ERRORLEVEL% EQU 0 (
    echo       Closed running NewsScraper.exe.
) else (
    echo       NewsScraper.exe was not running.
)
echo.

echo [2/4] Checking dependencies...
pip install pyinstaller --quiet
pip install requests beautifulsoup4 botasaurus feedparser --quiet
echo       Done.
echo.

REM Step 3: run PyInstaller
echo [3/4] Running PyInstaller...
echo.

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "NewsScraper" ^
    --add-data "sites_config.py;." ^
    --hidden-import "botasaurus" ^
    --hidden-import "bs4" ^
    --hidden-import "requests" ^
    --hidden-import "feedparser" ^
    --clean ^
    ui.py

echo.

REM Step 4: check result using PyInstaller exit code (not file existence)
if %ERRORLEVEL% EQU 0 (
    echo [4/4] Build successful!
    echo.
    echo   Your executable is ready at:
    echo   %cd%\dist\NewsScraper.exe
    echo.
    echo   You can send dist\NewsScraper.exe to anyone on Windows.
    echo   They do NOT need Python installed.
    echo.
) else (
    echo [4/4] Build FAILED -- check the output above for errors.
    echo.
)

pause
