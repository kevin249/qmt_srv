@echo off
setlocal
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo .venv not found. Creating qmt_srv runtime...
    where uv >nul 2>nul
    if errorlevel 1 (
        echo uv not found. Install uv or create .venv manually.
        pause
        exit /b 1
    )
    uv venv --python 3.13 .venv
    if errorlevel 1 goto failed
    uv pip install --python "%VENV_PY%" -r requirements.txt
    if errorlevel 1 goto failed
)

"%VENV_PY%" -c "import encodings" >nul 2>nul
if errorlevel 1 (
    echo .venv Python is broken or points to a missing base Python.
    echo Do not run: uv pip install encodings
    echo Recreate it with:
    echo   rmdir /s /q .venv
    echo   uv venv --python 3.13 .venv
    echo   uv pip install --python .venv\Scripts\python.exe -r requirements.txt
    pause
    exit /b 1
)

"%VENV_PY%" app.py
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

:failed
echo Failed to prepare qmt_srv runtime.
pause
exit /b 1
