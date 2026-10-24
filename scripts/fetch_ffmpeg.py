#!/usr/bin/env python
"""按需下载 FFmpeg 二进制（**不进入本仓库**）。

## 为什么要这个脚本

OpenNoter 以 MIT 协议发布，而官方预编译的 FFmpeg 二进制通常是 **GPL** 构建
（含 libx264 / libx265 等 GPL 组件）。把 GPL 二进制提交进 MIT 仓库会造成
许可证污染，也会让仓库膨胀数百 MB。所以 FFmpeg 一律**按需下载到本地目录**，
由用户自己的机器持有，仓库里只保留这个脚本。

## 许可证提醒

- 本脚本只负责下载，**不改变 FFmpeg 自身的许可证**。
- 下载到的二进制按其自身许可证（GPL / LGPL，视构建而定）分发与使用；
  若你要把它随产品再分发，必须一并提供对应许可证文本与源码获取方式。
- 商业闭源分发请改用 **LGPL 构建**（需自行编译或选择明确标注 LGPL 的来源），
  或改为调用系统已安装的 FFmpeg（只依赖其命令行接口，不做静态链接）。

## 用法

    # 下载到 ./vendor/ffmpeg（默认，已被 .gitignore 排除）
    python scripts/fetch_ffmpeg.py

    # 指定目录
    python scripts/fetch_ffmpeg.py --dest /opt/ffmpeg

    # 只打印下载地址，不实际下载（用于审计）
    python scripts/fetch_ffmpeg.py --print-url

下载完成后按提示设置 FFMPEG_BIN_PATH 即可；也可以把目录加入系统 PATH。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 下载源：优先官方与发行版维护的稳定地址
# ---------------------------------------------------------------------------
# 说明：
#   - Windows 用 gyan.dev 的 essentials 构建（社区最常用，默认 GPL）
#   - macOS 建议用 brew（见下方提示），不在这里硬编码第三方二进制
#   - Linux 建议用发行版包管理器；
#     下列直链仅作为「无包管理器」场景的兜底
SOURCES = {
    "windows": {
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "note": "gyan.dev essentials build（默认 GPL，含 libx264/libx265）",
        "archive": "zip",
        "bins": ["ffmpeg.exe", "ffprobe.exe"],
    },
    "linux": {
        # johnvansickle 提供静态构建；amd64 与 arm64 分开
        "url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        "note": "johnvansickle 静态构建（GPL）",
        "archive": "tar.xz",
        "bins": ["ffmpeg", "ffprobe"],
    },
    "darwin": {
        # macOS 不提供官方静态包，优先引导用户用 Homebrew
        "url": None,
        "note": "macOS 推荐 `brew install ffmpeg`；如需自带二进制请自行编译",
        "archive": None,
        "bins": ["ffmpeg", "ffprobe"],
    },
}

HOMEBREW_HINT = """\
macOS 上没有官方静态构建，推荐直接：

    brew install ffmpeg

Homebrew 的 ffmpeg 会装到 /opt/homebrew/bin（Apple Silicon）或
/usr/local/bin（Intel），通常已在 PATH 里，无需本脚本。
若你确实需要一份独立的二进制，可自行编译 LGPL 版本：

    ./configure --disable-gpl --disable-nonfree --enable-shared
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_dest() -> Path:
    return _repo_root() / "vendor" / "ffmpeg"


def _platform_key() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "darwin"
    if system == "linux":
        return "linux"
    raise SystemExit(f"暂不支持的平台：{platform.system()}")


def _download(url: str, target: Path) -> None:
    print(f"下载中… {url}")
    target.parent.mkdir(parents=True, exist_ok=True)

    def _hook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            done = min(block_num * block_size, total_size)
            pct = done * 100 // total_size
            mb = done / 1024 / 1024
            total_mb = total_size / 1024 / 1024
            sys.stdout.write(f"\r  {pct:3d}%  {mb:7.1f} / {total_mb:.1f} MB")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, target, reporthook=_hook)  # noqa: S310
    sys.stdout.write("\n")


def _extract(archive: Path, dest: Path, kind: str) -> None:
    print(f"解包… {archive.name}")
    tmp = dest / ".extract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    elif kind == "tar.xz":
        import tarfile

        with tarfile.open(archive, "r:xz") as tf:
            # Python 3.12+ 的 filter 参数可防路径穿越
            try:
                tf.extractall(tmp, filter="data")  # type: ignore[call-arg]
            except TypeError:
                tf.extractall(tmp)  # noqa: S202
    else:
        raise SystemExit(f"未知归档类型：{kind}")


def _place_binaries(tmp: Path, dest: Path, bins: list[str]) -> list[Path]:
    """从解包目录里把需要的可执行文件复制到 dest 根下。"""
    placed: list[Path] = []
    for name in bins:
        hits = list(tmp.rglob(name))
        if not hits:
            print(f"  ⚠️ 未在归档中找到 {name}")
            continue
        src = hits[0]
        out = dest / name
        shutil.copy2(src, out)
        if os.name != "nt":
            mode = out.stat().st_mode
            out.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        placed.append(out)
        print(f"  ✓ {out}")
    return placed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按需下载 FFmpeg 二进制（不进入版本库）",
    )
    parser.add_argument("--dest", default=None, help="下载目录，默认 ./vendor/ffmpeg")
    parser.add_argument("--print-url", action="store_true", help="只打印下载地址，不下载")
    parser.add_argument("--keep-archive", action="store_true", help="保留下载的压缩包")
    args = parser.parse_args()

    key = _platform_key()
    src = SOURCES[key]
    dest = Path(args.dest).expanduser().resolve() if args.dest else _default_dest()

    print("=" * 68)
    print("OpenNoter FFmpeg 按需下载")
    print("=" * 68)
    print(f"平台      : {platform.system()} / {platform.machine()}")
    print(f"目标目录  : {dest}")
    print(f"来源说明  : {src['note']}")
    print()
    print("⚠️ 许可证提醒：")
    print("   FFmpeg 按其自身许可（常见为 GPL / LGPL）分发，与 OpenNoter 的 MIT 无关。")
    print("   若你要随产品再分发这些二进制，必须遵守 FFmpeg 的许可证（含提供源码获取方式）。")
    print("   商业闭源分发请使用 LGPL 构建，或改为依赖系统已安装的 ffmpeg。")
    print()

    if src["url"] is None:
        print(HOMEBREW_HINT)
        return 0

    if args.print_url:
        print(f"下载地址: {src['url']}")
        return 0

    archive = dest / Path(src["url"]).name
    try:
        _download(src["url"], archive)
        _extract(archive, dest, src["archive"])
        placed = _place_binaries(dest / ".extract", dest, src["bins"])
        if not args.keep_archive:
            archive.unlink(missing_ok=True)
    finally:
        shutil.rmtree(dest / ".extract", ignore_errors=True)

    if not placed:
        print("\n✗ 没有取到任何可执行文件，请手动安装 FFmpeg。")
        return 1

    print()
    print("=" * 68)
    print("完成。接下来二选一：")
    print()
    print(f"  A) 在 .env 里指定（推荐，不影响系统 PATH）：")
    print(f"       FFMPEG_BIN_PATH={dest}")
    print()
    print(f"  B) 把目录加进系统 PATH：")
    print(f"       export PATH=\"{dest}:$PATH\"")
    print("=" * 68)
    print()
    print("提示：该目录已被 .gitignore 排除，不会被提交进仓库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
