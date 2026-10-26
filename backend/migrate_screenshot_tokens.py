"""给历史笔记里的截图 URL 补上 per-task 令牌（F9）。

## 为什么需要

F9 之前，`README` 里的截图在 markdown 里是这样的：

    ![](/static/screenshots/<task_id>/screenshot_000_x.jpg)

`/static/` 走的是 Cookie/API-Key 鉴权。Web + nginx 同源下 Cookie 能带上，所以
这条 URL 一直"能用"；但 **Tauri 桌面端**（页面源 `tauri://localhost`，跨站 +
`SameSite=Lax`）和 **浏览器扩展**（`chrome-extension://`，`<img>` 加不了
`x-api-key`）都拿不到凭据 ⇒ 截图 401。

F9 的修法是：在 URL 写进 markdown 的那一刻附带 per-task 长期令牌 `?t=`。
但它**只对新生成的笔记生效** —— 升级前已经落盘的 `note_results/<task_id>.json`
里，markdown 是历史字符串，没人会重新生成它。

于是升级后会出现：**站内旧笔记的截图全部 403**（破图，但不影响正文与登录态）。

本脚本扫描 `note_results/*.json`，把里面的 `/static/screenshots/<task_id>/...`
补上令牌。**只改 markdown 字符串，不动其它字段。**

## 幂等

可以反复执行：
  - 已经带 `?t=` 的 URL 跳过（不重签，避免无意义写入）；
  - 已经是 `?t=` 且签名正确的也跳过（`verify_token` 通过即视为已处理）；
  - 写文件前先比对内容，无变化则不落盘。

## 只处理"能安全补签"的 URL

令牌是 `HMAC(secret, "task-static:" + task_id)`。**task_id 必须来自 URL 自身**
（`screenshots/<task_id>/<file>`）。如果路径形态不是这样（例如旧格式
`screenshots/xxx.jpg` 没有子目录），无法判断归属 → **跳过并计数**，不臆造令牌。

## 用法

在 `backend/` 目录下（与 note_results 的相对路径一致）：

    python migrate_screenshot_tokens.py             # 预演：只统计，不写
    python migrate_screenshot_tokens.py --apply      # 真正写盘
    python migrate_screenshot_tokens.py --apply --note-dir /data/note_results

**默认是预演**（dry-run）是刻意的：这是改写用户历史数据的操作，必须先看统计
再决定。请先跑一次预演确认「待补签数量」符合预期。

## 无密钥时

没配 `OPENNOTER_SECRET_KEY` / `OPENNOTER_JWT_SECRET`（`AUTH_ENFORCE=0` 的单人
部署）时 `sign_payload()` 返回 None ⇒ 本脚本**什么都不改**并提示原因。
那种部署下 `/static/` 本来就不做鉴权，无需令牌。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

# 允许以 `python migrate_screenshot_tokens.py` 直接跑（把 backend/ 放进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.utils.static_signing import TOKEN_PARAM, sign_payload, verify_token  # noqa: E402

# 匹配 markdown / HTML 里的静态截图 URL。
#
# 之所以要覆盖三种形态：截图 URL 可能出现在
#   - markdown 图片语法：`![](/static/screenshots/<task>/x.jpg)`
#   - 裸 HTML：`<img src="/static/screenshots/<task>/x.jpg">`
#   - 引用式/转义后：`/static/screenshots/<task>/x.jpg?foo=bar`
#
# 这里只要求「以 /static/screenshots/ 开头、到引号/括号/空白/尖括号为止」，
# 后续由 `_patch_url()` 精确解析 task_id 与已有 query。
_URL_RE = re.compile(
    r"/static/screenshots/[^\"'()\[\]<>\s]+",
    re.IGNORECASE,
)

# 从 URL 路径里取 task_id：/static/screenshots/<task_id>/<file>（新格式）
_SCREENSHOT_PATH_RE = re.compile(r"^/static/screenshots/([^/]+)/([^/?]+)")

# 扁平格式（历史）：/static/screenshots/screenshot_000_<uuid>.jpg
# 这类反推不出 task_id，但同样需要令牌 —— 否则升级后历史笔记截图全 401。
_FLAT_SCREENSHOT_RE = re.compile(r"^/static/screenshots/(screenshot_[^/?]+)$")


def _token_for(path: str, fallback_key: str) -> "str | None":
    """按资源类型签发令牌（新格式按 task、扁平按文件名）。

    复用主程序的 `static_resource_key()`，避免两处各维护一套"怎么识别资源"
    的逻辑（那正是漂移的源头）。
    """
    from app.utils.static_signing import static_resource_key

    spec = static_resource_key(path)
    if spec is not None:
        resource, key_value = spec
        return _sign_by_resource(resource, key_value)
    # 识别不出来时退回「按 task 签名」，保持旧行为
    return sign_payload(fallback_key)


def _sign_by_resource(resource: str, key_value: str) -> "str | None":
    from app.utils import static_signing as ss

    return ss._sign(resource, key_value)  # noqa: SLF001 - 同一签名的内部入口


def _patch_url(url: str) -> tuple[str, str]:
    """给单个 URL 补令牌。

    返回 (新 URL, 状态)，状态取值：
      - ``"signed"``   —— 已补上令牌
      - ``"already"``  —— 已经带有效令牌，跳过
      - ``"resigned"`` —— 带令牌但校验失败（密钥轮换等），重签
      - ``"skip"``     —— 无法解析 task_id，跳过
      - ``"nokey"``    —— 无密钥，无法签发
    """
    # 拆出 path 与 query（不引入 urllib.parse 以保持对相对路径的宽松处理）
    path, _, query = url.partition("?")
    m = _SCREENSHOT_PATH_RE.match(path)
    if m:
        # 新格式：按 task_id 绑定
        key_value = m.group(1)
    else:
        # 扁平格式（历史）：按文件名绑定
        flat = _FLAT_SCREENSHOT_RE.match(path)
        if not flat:
            return url, "skip"
        key_value = flat.group(1)

    task_id = key_value  # 变量名沿用；对扁平截图它就是文件名
    if not task_id or ".." in task_id:
        return url, "skip"

    # 已有 ?t= ：校验一下，有效就不动
    if query:
        for part in query.split("&"):
            if part.startswith(f"{TOKEN_PARAM}="):
                token = part[len(TOKEN_PARAM) + 1:]
                if verify_token(task_id, token):
                    return url, "already"
                # 令牌对不上（可能换了密钥）→ 重签
                token_new = _token_for(path, key_value)
                if token_new is None:
                    return url, "nokey"
                rest = [p for p in query.split("&") if not p.startswith(f"{TOKEN_PARAM}=")]
                rest.append(f"{TOKEN_PARAM}={token_new}")
                return f"{path}?{'&'.join(rest)}", "resigned"
        # 有 query 但没有 t
        token = _token_for(path, key_value)
        if token is None:
            return url, "nokey"
        return f"{path}?{query}&{TOKEN_PARAM}={token}", "signed"

    token = _token_for(path, key_value)
    if token is None:
        return url, "nokey"
    return f"{path}?{TOKEN_PARAM}={token}", "signed"


def _patch_text(text: str) -> tuple[str, dict[str, int]]:
    """给一段文本（markdown）里所有截图 URL 补令牌。返回 (新文本, 统计)。"""
    stats = {"signed": 0, "already": 0, "resigned": 0, "skip": 0, "nokey": 0}

    def repl(m: re.Match) -> str:
        new_url, status = _patch_url(m.group(0))
        stats[status] = stats.get(status, 0) + 1
        return new_url

    return _URL_RE.sub(repl, text), stats


def _iter_note_files(root: Path) -> Iterable[Path]:
    """遍历 note_results 下的笔记 JSON。

    排除 ``*.status.json``（任务状态）与 ``*.json.tmp``（写入中间态）。
    """
    if not root.exists():
        return
    for f in sorted(root.glob("*.json")):
        if f.name.endswith(".status.json") or f.name.endswith(".tmp"):
            continue
        yield f


def main() -> int:
    parser = argparse.ArgumentParser(
        description="给历史笔记 markdown 里的截图 URL 补 per-task 令牌（F9）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写盘；不加此参数只做预演统计（默认）",
    )
    parser.add_argument(
        "--note-dir",
        default=os.getenv("NOTE_OUTPUT_DIR", "note_results"),
        help="note_results 目录（默认取 NOTE_OUTPUT_DIR 或 ./note_results）",
    )
    args = parser.parse_args()

    root = Path(args.note_dir).expanduser().resolve()

    # 先确认能不能签发（无密钥就别白跑）
    probe = sign_payload("__probe__")
    if probe is None:
        print(
            "⚠ 未配置 OPENNOTER_SECRET_KEY / OPENNOTER_JWT_SECRET —— 无法签发令牌。\n"
            "  这类部署（通常 AUTH_ENFORCE=0 的单人自托管）/static/ 不做鉴权，\n"
            "  本来就不需要令牌，无需迁移。已退出，未修改任何文件。",
        )
        return 0

    if not root.is_dir():
        print(f"✗ 目录不存在：{root}")
        return 2

    print(f"扫描目录：{root}")
    print(f"模式：{'写入（--apply）' if args.apply else '预演（不写盘，加 --apply 才写）'}")
    print()

    total = {"files": 0, "changed_files": 0, "signed": 0, "already": 0,
             "resigned": 0, "skip": 0, "nokey": 0, "errors": 0}

    for path in _iter_note_files(root):
        total["files"] += 1
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - 坏文件只跳过，不中断整批
            total["errors"] += 1
            print(f"  ✗ 读取失败，跳过：{path.name}（{exc}）")
            continue

        if not isinstance(data, dict):
            continue
        markdown = data.get("markdown")
        if not isinstance(markdown, str) or not markdown:
            continue

        new_md, stats = _patch_text(markdown)
        for k in ("signed", "already", "resigned", "skip", "nokey"):
            total[k] += stats.get(k, 0)

        if new_md == markdown:
            continue

        total["changed_files"] += 1
        print(f"  · {path.name}: 补 {stats.get('signed', 0)} 处"
              f"，重签 {stats.get('resigned', 0)} 处"
              f"，跳过 {stats.get('skip', 0)} 处")

        if args.apply:
            data["markdown"] = new_md
            # 原子写：先写 .tmp 再 replace，避免中途崩溃留下半截 JSON
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)

    print()
    print("=" * 60)
    print(f"扫描文件      : {total['files']}")
    print(f"需修改文件    : {total['changed_files']}")
    print(f"补签 URL      : {total['signed']}")
    print(f"重签 URL      : {total['resigned']}")
    print(f"已带有效令牌  : {total['already']}（跳过）")
    print(f"无法解析跳过  : {total['skip']}")
    print(f"读取失败      : {total['errors']}")
    print("=" * 60)

    if not args.apply:
        if total["changed_files"]:
            print()
            print("这是预演。确认数字后加 --apply 真正写入。")
        else:
            print()
            print("没有需要修改的文件 —— 无需迁移。")
    else:
        print()
        print("✓ 已写入。建议抽查一份 note_results/*.json 确认截图 URL 带上了 ?t=")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
