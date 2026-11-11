"""上传目录沙箱：文件名净化、扩展名/大小限制、路径必须落在 uploads 根内。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

# 允许的媒体扩展（本地上传走转写链路，非媒体无意义且扩大攻击面）
ALLOWED_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".webm",
        ".mkv",
        ".mov",
        ".avi",
        ".m4v",
        ".mp3",
        ".wav",
        ".m4a",
        ".flac",
        ".aac",
        ".ogg",
        ".opus",
    }
)


def uploads_dir() -> Path:
    """uploads 根目录（相对路径按进程 CWD 解析）。"""
    return Path(os.getenv("UPLOAD_DIR", "uploads"))


def uploads_root() -> Path:
    return uploads_dir().resolve()


def max_upload_bytes() -> int:
    mb = int(os.getenv("UPLOAD_MAX_SIZE_MB", "500") or "500")
    if mb <= 0:
        return 0  # 0 = 不限制（仅测试/特殊部署）
    return mb * 1024 * 1024


def sanitize_store_name(original_filename: str | None) -> str:
    """丢掉路径成分，校验扩展名，返回 UUID 存储名（保留安全扩展名）。"""
    raw = Path(original_filename or "upload.bin").name
    # 再剥一层，防奇怪的 ``..`` 文件名
    raw = Path(raw).name
    ext = Path(raw).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不允许的文件类型: {ext or '(无扩展名)'}")
    return f"{uuid.uuid4().hex}{ext}"


def is_upload_ref(url_or_path: str) -> bool:
    """是不是一个「上传文件引用」（``/uploads/xxx`` 或 ``uploads/xxx``）。

    仅做前缀判断，不触碰文件系统——供 pydantic 校验器在请求入口使用。
    真正的路径合法性由 :func:`safe_upload_rel` / :func:`resolve_under_uploads` 保证。
    """
    s = str(url_or_path or "").strip()
    return s.startswith("/uploads/") or s.startswith("uploads/")


def safe_upload_rel(url_or_path: str) -> bool:
    """上传引用是否形态合法（非空相对段、无 ``..`` 穿越、不以 ``/`` 开头）。

    与 :func:`resolve_under_uploads` 的判定保持一致，但**不 resolve**，
    因此可在 pydantic 校验阶段安全调用（无 I/O、无异常路径依赖）。
    """
    s = str(url_or_path or "").strip()
    if not is_upload_ref(s):
        return False
    rel = s.split("uploads/", 1)[-1]
    return bool(rel) and ".." not in rel and not rel.startswith("/")


def resolve_under_uploads(url_or_path: str) -> Path:
    """把 ``/uploads/xxx`` 或本地路径解析成绝对路径，且必须仍在 uploads 根下。

    用于 LocalDownloader 与下载接口，堵住 ``/uploads/../etc/passwd`` 一类穿越。
    """
    if not url_or_path or not str(url_or_path).strip():
        raise ValueError("空路径")

    s = str(url_or_path).strip()
    root = uploads_root()

    if s.startswith("/uploads/") or s.startswith("uploads/"):
        # 只取 uploads/ 之后的相对段；禁止绝对化穿越
        rel = s.split("uploads/", 1)[1]
        # 拒绝空段与纯 ``.``
        if not rel or rel in (".",):
            raise ValueError("非法 uploads 路径")
        candidate = (root / rel).resolve()
    else:
        candidate = Path(s).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("路径超出 uploads 目录") from exc

    return candidate

# ---------------------------------------------------------------------------
# 上传归属（R2 修复）
# ---------------------------------------------------------------------------
# 为什么用侧车文件而不是新建表：
#   - uploads 是「任务输入」的临时区，不是业务实体，不值得进 DB
#   - 侧车文件与文件同生命周期，删文件即可连带失效
#   - 不引入迁移，老部署零成本
# 为什么需要它：
#   之前任何登录用户只要知道 UUID 就能下载别人的上传文件（横向越权）。
#   UUID 难猜，但会上传到日志/代理/分享链接里，不是可靠的访问控制。

_OWNER_SUFFIX = ".owner"


def _owner_sidecar(file_path: Path) -> Path:
    """归属侧车文件路径：`<file>.owner`（内容是一行 user_id）。"""
    return file_path.with_name(file_path.name + _OWNER_SUFFIX)


def set_upload_owner(file_path: Path, user_id: int | None) -> None:
    """写入上传者。user_id 为 None（匿名）时不写侧车。"""
    if user_id is None:
        return
    # 归属写入是上传事务的一部分；失败必须交给调用方撤销上传。
    sidecar = _owner_sidecar(file_path)
    with open(sidecar, "x", encoding="utf-8") as stream:
        os.chmod(sidecar, 0o600)
        stream.write(str(int(user_id)))
        stream.flush()
        os.fsync(stream.fileno())


def get_upload_owner(file_path: Path) -> int | None:
    """读取上传者 id。没有侧车（历史文件/匿名上传）返回 None。"""
    try:
        raw = _owner_sidecar(file_path).read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def can_read_upload(file_path: Path, user, *, auth_enforced: bool) -> bool:
    """强制认证时，只允许上传者或管理员；无归属历史文件仅管理员可读。"""
    owner = get_upload_owner(file_path)

    if owner is not None:
        if user is not None and getattr(user, "id", None) == owner:
            return True
        if user is not None and getattr(user, "is_superuser", False):
            return True
        if not auth_enforced:
            return True
        return False

    # 无归属时无法证明普通用户有访问权，必须关闭访问。
    if not auth_enforced:
        return True
    return user is not None and bool(getattr(user, "is_superuser", False))
