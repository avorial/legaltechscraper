@echo off
setlocal

echo ============================================================
echo  NetDocuments LegalTech News Blast - Task Scheduler Setup
echo ============================================================
echo.

REM --- Resolve the folder this .bat lives in
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "PYTHON_SCRIPT=%SCRIPT_DIR%\generate_blast.py"

REM --- Find python
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] python not found on PATH.
    echo         Make sure Python is installed and on your PATH, then re-run.
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i" & goto :found_python
:found_python

REM --- Install required packages
echo [1/3] Installing Python dependencies...
pip install requests beautifulsoup4 feedparser lxml pywin32 --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] pip install reported an error. Continuing anyway.
)
echo       Done.
echo.

REM --- Task settings (edit these if needed)
set "TASK_NAME=ND LegalTech News Blast"
set "SCHEDULE=WEEKLY"
set "DAY=MON"
set "TIME=07:00"

echo [2/3] Registering scheduled task...
echo.
echo   Task name : %TASK_NAME%
echo   Schedule  : Every %DAY% at %TIME%
echo   Script    : %PYTHON_SCRIPT%
echo.

REM Delete any existing task with same name first (ignore error if not found)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create the task
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_EXE%\" \"%PYTHON_SCRIPT%\"" ^
  /sc %SCHEDULE% ^
  /d %DAY% ^
  /st %TIME% ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Task creation failed. Try running this .bat as Administrator.
    pause
    exit /b 1
)

echo.
echo [3/3] Done! Verifying task...
schtasks /query /tn "%TASK_NAME%" /fo LIST

echo.
echo ============================================================
echo  Setup complete.
echo.
echo  The blast will run every Monday at 7:00 AM and send to
echo  the recipients configured in generate_blast.py.
echo.
echo  To test it right now (sends the email):
echo    python "%PYTHON_SCRIPT%"
echo.
echo  To preview without sending:
echo    python "%PYTHON_SCRIPT%" --preview
echo.
echo  To change schedule: edit this .bat and re-run it.
echo  To change recipients: edit RECIPIENTS in generate_blast.py
echo ============================================================
echo.
pause
