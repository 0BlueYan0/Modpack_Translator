#!/usr/bin/env sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    echo "找不到 uv。請先安裝 uv：https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "正在同步 Python 環境..."
uv python install 3.12

# 自癒：.venv 存在但缺 pyvenv.cfg（更新中斷留下的半殘環境），
# uv sync 會死在 "No pyvenv.cfg file" 且不會自行重建，必須先整個移除
if [ -d .venv ] && [ ! -f .venv/pyvenv.cfg ]; then
    echo "偵測到損壞的 Python 環境（缺 pyvenv.cfg），正在移除重建..."
    rm -rf .venv
fi

uv sync --managed-python --python 3.12 --inexact --no-install-package llama-cpp-python

echo "正在偵測硬體並初始化本機模型後端..."
uv run python scripts/setup_backend.py "$@"

echo "初始化完成。請使用以下指令啟動程式："
echo "uv run python main.py"
