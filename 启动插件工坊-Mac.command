#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print '请先安装 Python 3.9 或更新版本：https://www.python.org/downloads/'
  read '?按回车关闭窗口。'
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  print '需要 Python 3.9 或更新版本。'
  read '?按回车关闭窗口。'
  exit 1
fi
python3 local-manager/server.py
if [[ $? -ne 0 ]]; then
  read '?启动未完成，按回车关闭窗口。'
fi
