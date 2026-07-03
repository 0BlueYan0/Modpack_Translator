@echo off
setlocal

where uv >nul 2>nul
if errorlevel 1 (
    set "MSG_B64=5om+5LiN5YiwIHV244CC6KuL5YWI5a6J6KOdIHV277yaaHR0cHM6Ly9kb2NzLmFzdHJhbC5zaC91di8="
    call :msg
    exit /b 1
)

set "MSG_B64=5q2j5Zyo5ZCM5q2lIFB5dGhvbiDnkrDlooMuLi4="
call :msg
uv python install 3.12
if errorlevel 1 exit /b 1

REM 自癒：.venv 存在但缺 pyvenv.cfg（更新中斷或刪除被檔案鎖擋下的半殘環境），
REM uv sync 會死在 "No pyvenv.cfg file" 且不會自行重建，必須先整個移除
if exist ".venv\" if not exist ".venv\pyvenv.cfg" (
    set "MSG_B64=5YG15ris5Yiw5pCN5aOe55qEIFB5dGhvbiDnkrDlooPvvIjnvLogcHl2ZW52LmNmZ++8ie+8jOato+WcqOenu+mZpOmHjeW7ui4uLg=="
    call :msg
    rd /s /q ".venv"
)
if exist ".venv\" if not exist ".venv\pyvenv.cfg" (
    set "MSG_B64=54Sh5rOV56e76Zmk5pCN5aOe55qEIC52ZW5277yM6KuL6Zec6ZaJ5omA5pyJ55u46Zec56iL5byP5b6M6YeN5paw5Z+36KGMIHNldHVwX3dpbmRvd3MuYmF044CC"
    call :msg
    exit /b 1
)

uv sync --managed-python --python 3.12 --inexact --no-install-package llama-cpp-python
if errorlevel 1 exit /b 1

set "MSG_B64=5q2j5Zyo5YG15ris56Gs6auU5Lim5Yid5aeL5YyW5pys5qmf5qih5Z6L5b6M56uvLi4u"
call :msg
uv run python scripts\setup_backend.py %*
if errorlevel 1 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_launcher.ps1 >nul 2>nul

set "MSG_B64=5Yid5aeL5YyW5a6M5oiQ44CC6KuL5L2/55So5Lul5LiL5oyH5Luk5ZWf5YuV56iL5byP77ya"
call :msg
echo uv run python main.py
echo Or double-click the versioned launcher EXE in this folder.
exit /b 0

:msg
powershell -NoProfile -Command "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Host ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MSG_B64)))"
exit /b 0
