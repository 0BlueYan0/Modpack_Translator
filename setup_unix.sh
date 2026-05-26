#!/usr/bin/env sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    echo "找不到 uv。請先安裝 uv：https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "正在同步 Python 環境..."
uv python install 3.12
uv sync --managed-python --python 3.12 --inexact --no-install-package llama-cpp-python

echo "正在偵測硬體並初始化本機模型後端..."
uv run python scripts/setup_backend.py "$@"

echo "初始化完成。請使用以下指令啟動程式："
echo "uv run python main.py"
