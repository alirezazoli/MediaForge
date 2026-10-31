@echo off
rem Launch MediaForge Studio using the project venv.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%"
set "PY=%HERE%..\.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=python"
start "" "%PY%" -m mediaforge
endlocal
