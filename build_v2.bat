@echo off
cd /d C:\Users\patricthomas\Desktop\legaltechnewsscraper
taskkill /F /IM NewsScraperv2.exe 2>nul

C:\Python314\python.exe -m PyInstaller ^
  --onefile --windowed ^
  --name NewsScraperv2 ^
  "--add-data=sites_config.py;." ^
  "--add-data=C:\Users\patricthomas\AppData\Roaming\Python\Python314\site-packages\customtkinter;customtkinter" ^
  --hidden-import=customtkinter ^
  --hidden-import=darkdetect ^
  --hidden-import=bs4 ^
  --hidden-import=requests ^
  --hidden-import=feedparser ^
  --clean ^
  ui2.py

if %ERRORLEVEL% EQU 0 (
    echo BUILD SUCCESS
    copy /Y dist\NewsScraperv2.exe "%USERPROFILE%\Desktop\NewsScraperv2.exe" >nul
    echo Copied to Desktop!
) else (
    echo BUILD FAILED
)
