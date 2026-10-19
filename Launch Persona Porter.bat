@echo off
rem Double-click to start Persona Porter on Windows.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 persona_porter_gui.py) || (python persona_porter_gui.py)
if errorlevel 1 (
  echo.
  echo Persona Porter needs Python 3.10 or newer from https://www.python.org
  echo Tick "Add python.exe to PATH" when installing.
  pause
)
