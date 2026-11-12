@echo off
rem pad: the macro pad for AI agents (pad.py beside this file). Uses the m18 venv when it exists.
set "_PADPY=%USERPROFILE%\tools\m18\.venv\Scripts\python.exe"
if defined M18_HOME if exist "%M18_HOME%\.venv\Scripts\python.exe" set "_PADPY=%M18_HOME%\.venv\Scripts\python.exe"
if not exist "%_PADPY%" set "_PADPY=python"
"%_PADPY%" "%~dp0pad.py" %*
