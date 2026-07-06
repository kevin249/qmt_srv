@echo off
setlocal
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "VENV_DIR=%CD%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

call :ensure_venv
if errorlevel 1 goto failed

"%VENV_PY%" -c "import encodings" >nul 2>nul
if errorlevel 1 (
    echo .venv Python is broken or points to a missing base Python.
    echo Do not run: uv pip install encodings
    echo Recreating .venv now...
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
    call :ensure_venv
    if errorlevel 1 goto failed
    "%VENV_PY%" -c "import encodings" >nul 2>nul
    if errorlevel 1 (
        echo .venv Python is still broken after recreation.
        echo Check whether uv selected a complete CPython 3.13 runtime:
        uv python find 3.13
        pause
        exit /b 1
    )
)

"%VENV_PY%" app.py
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

:ensure_venv
if exist "%VENV_PY%" exit /b 0
echo Preparing qmt_srv runtime in "%VENV_DIR%"...
where uv >nul 2>nul
if errorlevel 1 (
    echo uv not found. Install uv or create .venv manually.
    exit /b 1
)
uv venv --python 3.13 "%VENV_DIR%"
if errorlevel 1 exit /b 1
uv pip install --python "%VENV_PY%" -r requirements.txt
if errorlevel 1 exit /b 1
exit /b 0

:failed
echo Failed to prepare qmt_srv runtime.
pause
exit /b 1
