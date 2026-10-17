@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto fail
%PY% -m PyInstaller --noconfirm --clean --windowed --onefile --name "Desktop Gas Sign" --icon "..\assets\icon.ico" --distpath dist --workpath build --collect-all pystray --collect-all PIL --hidden-import pystray._win32 --hidden-import requests --hidden-import serial --add-data "firmware/gas_sign.hex;firmware" --add-data "firmware/avrdude.exe;firmware" --add-data "firmware/avrdude.conf;firmware" --add-data ".cache/geocode.json;cache" --add-data "..\assets\icon.png;assets" gas_sign.py
if errorlevel 1 goto fail
echo.
echo Built: dist\Desktop Gas Sign.exe
pause
exit /b 0
:fail
echo.
echo Build failed. Install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
pause
exit /b 1
