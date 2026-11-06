@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Probably Stolen Save Editor

rem ---------------------------------------------------------------
rem  Find a windowed Python 3 interpreter and run app.py with it.
rem  This launcher is deliberately ASCII-only: cmd.exe interprets
rem  .bat files with the OEM code page, so non-ASCII text here would
rem  corrupt the script itself.
rem ---------------------------------------------------------------

set "PYW="

rem 1) pythonw.exe on PATH
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"

rem 2) per-user installs
if not defined PYW for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PYW if exist "%%~fD\pythonw.exe" set "PYW=%%~fD\pythonw.exe"

rem 3) machine-wide installs
if not defined PYW for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PYW if exist "%%~fD\pythonw.exe" set "PYW=%%~fD\pythonw.exe"

rem 4) old-style install root
if not defined PYW for /d %%D in ("C:\Python3*") do if not defined PYW if exist "%%~fD\pythonw.exe" set "PYW=%%~fD\pythonw.exe"

if defined PYW (
    start "" "%PYW%" "%~dp0app.py"
    exit /b 0
)

rem 5) the py launcher, in case pythonw is not on PATH
if exist "%SystemRoot%\pyw.exe" (
    start "" "%SystemRoot%\pyw.exe" -3 "%~dp0app.py"
    exit /b 0
)

echo.
echo    Python 3 was not found on this computer.
echo.
echo    This save editor needs Python 3 with tkinter (standard installer,
echo    no extra packages).
echo.
echo    https://www.python.org/downloads/windows/
echo    During setup, tick "Add python.exe to PATH".
echo.
echo    More details: README.md in this folder.
echo.
pause
