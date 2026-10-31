@echo off
rem Runs MediaForge Studio from source. The installer is the normal way to run it;
rem this is for development. Uses a .venv beside this file if there is one.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%"
set "PY=%HERE%.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=pythonw"
start "" "%PY%" -m mediaforge
endlocal
