"""笔记目录沙箱：task_id 格式校验 + 所有落盘/读取路径必须落在 NOTE_OUTPUT_DIR 内。

## 为什么需要这一层

`task_id` 会参与 5 处文件路径拼接，而 `POST /api/notes` 允许客户端自带 task_id
（前端「重试」复用同一个任务）。不校验时，`../config/downloader` 这类值会造成：

1. 请求内就把 `{task_id}.status.json` 写到 NOTE_OUTPUT_DIR 之外（`_update_status`
   在 POST 请求处理函数里同步执行）；
2. `_task_owner` 读的正是这个文件 → 攻击者植入自己的 user_id 后 ACL 判定通过；
3. `get_task_status` 读 `{task_id}.json` → 返回目录外任意 JSON 文件内容；
4. `_purge_task` 的 `exact_names` / glob 用同一个 task_id → 删除目录外同名文件。

## 两层防御（都要有）

- `validate_task_id()`：在 API 边界拒绝可疑值，给调用方明确的 400，且不让坏值
  进入任何业务逻辑。这是**入口防线**。
- `resolve_under()`：任何落盘/读取前断言解析后的路径仍在根目录内。这是**纵深防线**，
  兜住内部调用或将来新增的路径拼接。

只有入口防线时，一个漏掉校验的新端点就会重新打开漏洞；只有路径防线时，错误会
以「路径越界」的形式在业务深处炸开，难以定位。两层一起上。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

# task_id 长度上限。UUID4 是 36 字符；128 是给「自定义 id」留的余量。
TASK_ID_MAX_LEN = 128

# 只允许 ASCII 字母数字 + `.` `_` `-`，且首尾必须是字母数字。
# 这一条同时排除了：路径分隔符（`/` `\`）、`..`、以 `.` 开头/结尾、
# 空白与控制字符、以及非 ASCII（历史上 task_id 一律是服务端生成的 uuid4）。
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$")
_TASK_ID_SINGLE_RE = re.compile(r"^[A-Za-z0-9]$")


class UnsafeTaskId(ValueError):
    """task_id 含路径穿越或非法字符。"""


def is_safe_task_id(task_id: object) -> bool:
    """task_id 是否是安全的单段文件名（不含路径成分）。"""
    if not isinstance(task_id, str):
        return False
    if not task_id or len(task_id) > TASK_ID_MAX_LEN:
        return False
    if ".." in task_id:
        return False
    if _TASK_ID_SINGLE_RE.match(task_id):
        return True
    return bool(_TASK_ID_RE.match(task_id))


def validate_task_id(task_id: object) -> str:
    """校验并原样返回 task_id；不合法时抛 `UnsafeTaskId`。

    调用方应把它包成 400/403 返回，不要让它冒泡成 500。
    """
    if not is_safe_task_id(task_id):
        raise UnsafeTaskId(f"非法 task_id: {task_id!r}")
    return str(task_id)


def resolve_under(root: PathLike, *parts: str) -> Path:
    """把 `parts` 拼到 `root` 下并解析，断言结果仍在 `root` 内。

    越界时抛 `UnsafeTaskId`。注意 `resolve()` 会把 `..` 规范化，所以判定的
    是「最终落点」而不是「字面路径」——`root/a/../b` 这类写法会被正确识别。
    """
    base = Path(root).resolve()
    target = base.joinpath(*parts).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise UnsafeTaskId(f"路径越出笔记目录: {target} (root={base})") from exc
    return target


def note_file(root: PathLike, task_id: str, suffix: str = ".json") -> Path:
    """`{root}/{task_id}{suffix}` 的安全版本。

    先校验 task_id 格式（拦掉 `../`），再用 `resolve_under` 断言落点——
    即使校验被绕过，也不会写到目录外。
    """
    validate_task_id(task_id)
    return resolve_under(root, f"{task_id}{suffix}")


def note_named_file(root: PathLike, filename: str) -> Path:
    """`{root}/{filename}` 的安全版本，用于「文件名已经自己带好后缀」的场景。

    与 `note_file()` 的区别：这里的 `filename` 是完整文件名（如
    `task-abc_13abbdd5322f_audio.json`），不再追加后缀。仍做同样的越界断言，
    所以 `filename` 里若含路径成分（`../` 或 `/`）会被拒绝。

    为什么需要它：缓存文件名由 `{task_id}_{指纹}_audio.json` 拼成，指纹是
    16 位十六进制，形状上是合法的 task_id 片段 —— 不能拿去当 task_id 校验，
    否则会被 `note_file()` 当作「追加后缀」处理，拼出 `.json.json`。
    """
    name = str(filename)
    if not name or "\x00" in name:
        raise UnsafeTaskId(f"非法文件名: {filename!r}")
    if "/" in name or "\\" in name:
        raise UnsafeTaskId(f"文件名含路径分隔符: {filename!r}")
    if name in (".", ".."):
        raise UnsafeTaskId(f"非法文件名: {filename!r}")
    return resolve_under(root, name)
