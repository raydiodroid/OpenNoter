@echo off
setlocal enabledelayedexpansion

REM 切换到脚本所在目录的上级，也就是项目根目录
cd /d %~dp0..
echo 当前工作目录：%cd%

REM 清理旧的构建
echo 清理旧的构建...
rmdir /s /q backend\dist 2>nul
rmdir /s /q backend\build 2>nul
rmdir /s /q BillNote_frontend\src-tauri\bin 2>nul
mkdir BillNote_frontend\src-tauri\bin\BiliNoteBackend
echo 清理完成。

REM 获取 Rust 的 target triple（适配 Tauri 对应平台）
for /f "tokens=2 delims=:" %%A in ('rustc -Vv ^| findstr "host"') do (
    set "TARGET_TRIPLE=%%A"
)
set "TARGET_TRIPLE=%TARGET_TRIPLE: =%"  REM 去除多余空格
echo Detected target triple: %TARGET_TRIPLE%

REM 执行 PyInstaller 打包
echo 开始 PyInstaller 打包...
pyinstaller ^
  --name BiliNoteBackend ^
  --paths backend ^
  --distpath BillNote_frontend\src-tauri\bin ^
  --workpath backend\build ^
  --specpath backend ^
  --hidden-import uvicorn ^
  --hidden-import fastapi ^
  --hidden-import starlette ^
  --add-data "app/db/builtin_providers.json;." ^
  --add-data "..\.env.example;.env" ^
  backend\main.py

REM 重命名生成的可执行文件为符合 Tauri 要求的名称
move /Y BillNote_frontend\src-tauri\bin\BiliNoteBackend\BiliNoteBackend.exe BillNote_frontend\src-tauri\bin\BiliNoteBackend\BiliNoteBackend-%TARGET_TRIPLE%.exe

echo PyInstaller 打包完成：
dir BillNote_frontend\src-tauri\bin\BiliNoteBackend

echo 请检查 BillNote_frontend\src-tauri\bin\BiliNoteBackend 目录，以确认打包内容。
endlocal