@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 goto fail
%PY% gas_sign.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Could not start. Install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
pause
