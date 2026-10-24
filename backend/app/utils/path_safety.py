"""静态文件路径沙箱。

`/static/{file_path}` 这类「把 URL 路径映射到本地文件」的接口必须防穿越：
用户传 `../../etc/passwd` 时，`Path(root) / "../../etc/passwd"` 会逃出根目录。

做法是解析后校验前缀（`resolve()` 会把 `..` 折叠成绝对路径），
再用 `relative_to()` 判断是否仍在根内——这是唯一可靠的方式，
字符串前缀比较（`str(p).startswith(str(root))`）会被
`/app/static_evil` 这类同前缀目录绕过。

用 `resolve()` 而非 `abspath()` 的原因：`resolve()` 还会展开符号链接。
如果根目录里被放了指向 `/etc` 的软链，`abspath` 判断不出来，`resolve` 能。
"""

from __future__ import annotations

from pathlib import Path


def resolve_under_dir(root: Path | str, relative: str) -> Path:
    """把 `relative` 解析到 `root` 之下，越界抛 ValueError。

    :param root: 允许访问的根目录
    :param relative: URL 里的相对路径（可能含 `..`、绝对路径等恶意内容）
    :raises ValueError: 路径为空、越界、或指向根目录本身
    """
    if not relative or not str(relative).strip():
        raise ValueError("空路径")

    root_path = Path(root).resolve()

    # 关键：先剥掉前导 /，否则 Path(root) / "/etc/passwd" 会得到 /etc/passwd
    # （pathlib 的语义：右侧绝对路径会覆盖左侧）
    cleaned = str(relative).lstrip("/")
    if not cleaned:
        raise ValueError("非法路径")

    candidate = (root_path / cleaned).resolve()

    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("路径超出允许目录") from exc

    if candidate == root_path:
        raise ValueError("路径是目录本身")

    return candidate
