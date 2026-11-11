@echo off
chcp 65001 >nul
set PYTHONUTF8=1
pushd "%~dp0" || exit /b 1
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 (
    py -3 local-manager\server.py
    goto finished
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 (
    python local-manager\server.py
    goto finished
  )
)
echo 请先安装 Python 3.9 或更新版本：https://www.python.org/downloads/windows/
echo 安装时勾选 Add Python to PATH，然后重新打开此文件。
:finished
popd
pause
