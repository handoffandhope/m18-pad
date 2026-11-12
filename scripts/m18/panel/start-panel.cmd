@echo off
rem Starts the M18 panel hidden and detached: http://127.0.0.1:8725
rem A second start exits on its own (the port is taken). Log: %USERPROFILE%\tools\m18\state\panel\panel.log
setlocal
if "%M18_HOME%"=="" set "M18_HOME=%USERPROFILE%\tools\m18"
set "PYW=%M18_HOME%\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%USERPROFILE%\tools\m18\.venv\Scripts\pythonw.exe"
start "" "%PYW%" "%~dp0server.py"
endlocal
