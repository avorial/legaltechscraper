@echo off
cd /d C:\Users\patricthomas\Desktop\legaltechnewsscraper
taskkill /F /IM NewsScraper.exe 2>nul
C:\Python314\python.exe -m PyInstaller --onefile --windowed --name NewsScraper "--add-data=sites_config.py;." --hidden-import=botasaurus --hidden-import=bs4 --hidden-import=requests --hidden-import=feedparser --clean ui.py
if %ERRORLEVEL% EQU 0 (
    echo BUILD SUCCESS
    copy /Y dist\NewsScraper.exe "%USERPROFILE%\Desktop\NewsScraper.exe" >nul
    echo Copied to Desktop!
) else (
    echo BUILD FAILED
)
