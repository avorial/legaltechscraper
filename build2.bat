@echo off
cd /d C:\Users\patricthomas\Desktop\legaltechnewsscraper
echo Killing old instance...
taskkill /F /IM NewsScraper.exe 2>nul
echo Running PyInstaller with C:\Python314\python.exe...
C:\Python314\python.exe -m PyInstaller --onefile --windowed --name NewsScraper "--add-data=sites_config.py;." --hidden-import=botasaurus --hidden-import=bs4 --hidden-import=requests --hidden-import=feedparser --clean ui.py
echo DONE - exit code: %ERRORLEVEL%
