@echo off
rem Runs MediaForge Studio from source. The installer is the normal way to run it;
rem this is for development.
rem
rem Uses the first Python that has PySide6: MEDIAFORGE_PYTHON, a .venv beside this file,
rem the "py" launcher, then python on PATH. pythonw has no console, so when none of them
rem had PySide6 the app used to fail in silence -- now it says so.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%"
set "PY="

for %%P in ("%MEDIAFORGE_PYTHON%" "%HERE%.venv\Scripts\python.exe") do (
    if not defined PY if exist "%%~P" (
        "%%~P" -c "import PySide6" >nul 2>&1 && set "PY=%%~P"
    )
)
if not defined PY (
    py -3 -c "import PySide6" >nul 2>&1 && for /f "delims=" %%E in ('py -3 -c "import sys; print(sys.executable)"') do set "PY=%%E"
)
if not defined PY (
    python -c "import PySide6" >nul 2>&1 && for /f "delims=" %%E in ('python -c "import sys; print(sys.executable)"') do set "PY=%%E"
)

if not defined PY (
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('No Python with PySide6 was found, so MediaForge cannot start from source.' + [char]10 + [char]10 + 'Install MediaForge Studio with its installer, or run:' + [char]10 + '    pip install -r requirements.txt' + [char]10 + 'or set MEDIAFORGE_PYTHON to a python.exe that has PySide6.', 'MediaForge Studio')"
    exit /b 1
)

set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" -m mediaforge
endlocal
