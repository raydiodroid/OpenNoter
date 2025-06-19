#!/usr/bin/env bash
set -e
# uncomment this for debugging
# set -x

# 切到项目根（假设脚本放在 script/ 目录）
cd "$(dirname "$0")/.."

echo "当前工作目录：$(pwd)"

# 清理旧的构建
echo "清理旧的构建..."
rm -rf backend/dist backend/build ./BillNote_frontend/src-tauri/bin/*
echo "清理完成。"

TARGET_TRIPLE=$(rustc -Vv | grep host | cut -f2 -d' ')
echo "Detected target triple: $TARGET_TRIPLE"

# PyInstaller onedir 模式，直接输出到 Tauri 的 bin 目录
echo "开始 PyInstaller 打包..."
pyinstaller \
  --name BiliNoteBackend \
  --paths backend \
  --distpath ./BillNote_frontend/src-tauri/bin \
  --workpath backend/build \
  --specpath backend \
  --hidden-import uvicorn \
  --hidden-import fastapi \
  --hidden-import starlette \
  --add-data "app/db/builtin_providers.json:."\
  --add-data "../.env.env.example:.env" \
  "$(pwd)/backend/main.py" # 确保这里没有额外的空格，并使用绝对路径
mv \
 ./BillNote_frontend/src-tauri/bin/BiliNoteBackend/BiliNoteBackend\
 ./BillNote_frontend/src-tauri/bin/BiliNoteBackend/BiliNoteBackend-$TARGET_TRIPLE

echo "PyInstaller 打包完成："
ls -l  ./BillNote_frontend/src-tauri/bin/BiliNoteBackend # 这里会列出 onedir 模式下的目录内容
echo "请检查 src-tauri/bin/BiliNoteBackend 目录，以确认打包内容。"
