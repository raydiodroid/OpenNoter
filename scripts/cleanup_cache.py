#!/usr/bin/env python
"""缓存与孤儿文件清理（手动执行的运维脚本，**不自动运行**）。

## 为什么不做成自动清理

自动清理最容易误删：
- 任务正在写缓存时被删 → 任务失败
- 刚生成的笔记截图被当成孤儿 → 笔记里图片 404
- 用户还在排队、视频刚下载好就被清掉

所以这里做成**显式执行、默认 dry-run** 的脚本。看清楚再决定。

## 清理什么

| 目标 | 判据 | 默认保留 |
|---|---|---|
| `data/` 下的媒体缓存（mp4/m4a/webm） | mtime 超过 N 天 | 7 天 |
| `data/output_frames/`、`grid_output/` | mtime 超过 N 天 | 7 天 |
| 孤儿截图（`static/screenshots/**`） | 不被任何笔记 markdown 引用 | 保留 3 天内 |
| 孤儿状态残留（`*_markdown.status.json` 等） | 有同名正常文件才删 | — |
| 已删除任务的状态文件 | DB 里 deleted_at 非空 | — |

## 用法

    # 先看会删什么（默认）
    python scripts/cleanup_cache.py --dry-run

    # 确认后真删
    python scripts/cleanup_cache.py --apply

    # 自定义保留天数
    python scripts/cleanup_cache.py --apply --media-days 30 --orphan-shot-days 7
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# 允许直接运行（容器内 cwd 是 /app）
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if BACKEND.exists() and str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

NOTE_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
SHOT_DIR = Path(os.getenv("OUT_DIR", "./static/screenshots"))

MEDIA_EXTS = {".mp4", ".m4a", ".webm", ".mkv", ".mp3", ".wav", ".flv"}
# 已知的缓存残留后缀（旧版 bug 留下的）
RESIDUAL_SUFFIXES = ("_markdown", "_audio", "_transcript", "_meta")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        return 1e9


def _reply(action: str, path: Path, size: int, apply: bool) -> None:
    tag = "删除" if apply else "将删除"
    print(f"  [{tag}] {path}  ({_mb(size)})")


# ---------------------------------------------------------------------------
# 1. 媒体缓存
# ---------------------------------------------------------------------------
def clean_media(days: float, apply: bool) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    if not DATA_DIR.exists():
        return 0, 0
    print(f"\n[1] 媒体缓存（>{days:g} 天，{DATA_DIR}）")
    for path in DATA_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS:
            continue
        if _age_days(path) < days:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        _reply("media", path, size, apply)
        total_files += 1
        total_bytes += size
        if apply:
            try:
                path.unlink()
            except OSError as exc:
                print(f"      失败: {exc}")
    print(f"  合计: {total_files} 个文件, {_mb(total_bytes)}")
    return total_files, total_bytes


# ---------------------------------------------------------------------------
# 2. 抽帧 / 拼图中间产物
# ---------------------------------------------------------------------------
def clean_frames(days: float, apply: bool) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    targets = [DATA_DIR / "output_frames", DATA_DIR / "grid_output"]
    print(f"\n[2] 抽帧/拼图（>{days:g} 天）")
    for base in targets:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or _age_days(path) < days:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            _reply("frames", path, size, apply)
            total_files += 1
            total_bytes += size
            if apply:
                try:
                    path.unlink()
                except OSError:
                    pass
    print(f"  合计: {total_files} 个文件, {_mb(total_bytes)}")
    return total_files, total_bytes


# ---------------------------------------------------------------------------
# 3. 孤儿截图
# ---------------------------------------------------------------------------
def _referenced_screenshots() -> set[str]:
    """收集所有笔记 markdown 里引用到的截图文件名（含子目录）。"""
    refs: set[str] = set()
    if not NOTE_DIR.exists():
        return refs
    pattern = re.compile(r"screenshots/([A-Za-z0-9_.\-/]+\.(?:jpg|jpeg|png|webp))")
    for note in NOTE_DIR.glob("*.json"):
        if note.name.endswith(".status.json"):
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pattern.finditer(text):
            refs.add(m.group(1))
    return refs


def clean_orphan_screenshots(min_days: float, apply: bool) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    if not SHOT_DIR.exists():
        return 0, 0
    print(f"\n[3] 孤儿截图（未被引用，且 >{min_days:g} 天）")
    refs = _referenced_screenshots()
    print(f"  笔记引用到 {len(refs)} 张")

    for path in SHOT_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(SHOT_DIR).as_posix()
        if rel in refs or path.name in refs:
            continue
        if _age_days(path) < min_days:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        _reply("shot", path, size, apply)
        total_files += 1
        total_bytes += size
        if apply:
            try:
                path.unlink()
            except OSError:
                pass
    print(f"  合计: {total_files} 个文件, {_mb(total_bytes)}")
    return total_files, total_bytes


# ---------------------------------------------------------------------------
# 4. 状态文件残留
# ---------------------------------------------------------------------------
def clean_status_residuals(apply: bool) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    if not NOTE_DIR.exists():
        return 0, 0
    print("\n[4] 状态文件残留（有正常文件对应的 `_markdown` 等）")
    for path in NOTE_DIR.glob("*.status.json"):
        stem = path.name[: -len(".status.json")]
        hit = next((s for s in RESIDUAL_SUFFIXES if stem.lower().endswith(s)), None)
        if not hit:
            continue
        base = stem[: -len(hit)]
        # 只有对应的正常状态文件存在时才删（否则可能是唯一痕迹）
        if not (NOTE_DIR / f"{base}.status.json").exists():
            print(f"  [保留] {path.name}  ← 无正常文件对应，可能是唯一痕迹")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        _reply("residual", path, size, apply)
        total_files += 1
        total_bytes += size
        if apply:
            try:
                path.unlink()
            except OSError:
                pass
    print(f"  合计: {total_files} 个文件, {_mb(total_bytes)}")
    return total_files, total_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="清理缓存与孤儿文件（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="真正执行删除（默认只预览）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览（默认行为）")
    parser.add_argument("--media-days", type=float, default=7, help="媒体缓存保留天数")
    parser.add_argument("--frames-days", type=float, default=7, help="抽帧保留天数")
    parser.add_argument("--orphan-shot-days", type=float, default=3, help="孤儿截图保留天数")
    parser.add_argument("--skip-shots", action="store_true", help="跳过截图清理")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run

    print("=" * 70)
    print("OpenNoter 缓存清理")
    print("=" * 70)
    print(f"模式    : {'实际删除' if apply else '预览（不会删任何东西）'}")
    print(f"笔记目录: {NOTE_DIR}")
    print(f"数据目录: {DATA_DIR}")
    print(f"截图目录: {SHOT_DIR}")

    f1, b1 = clean_media(args.media_days, apply)
    f2, b2 = clean_frames(args.frames_days, apply)
    if not args.skip_shots:
        f3, b3 = clean_orphan_screenshots(args.orphan_shot_days, apply)
    else:
        f3 = b3 = 0
    f4, b4 = clean_status_residuals(apply)

    total_files = f1 + f2 + f3 + f4
    total_bytes = b1 + b2 + b3 + b4

    print()
    print("=" * 70)
    print(f"{'已删除' if apply else '可释放'}: {total_files} 个文件, {_mb(total_bytes)}")
    if not apply and total_files:
        print("确认无误后加 --apply 真正执行")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
