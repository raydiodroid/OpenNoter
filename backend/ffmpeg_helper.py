import os
import subprocess
import sys
from dotenv import load_dotenv

from app.utils.logger import get_logger
logger = get_logger(__name__)


def _load_dotenv_from_multiple_paths():
    """尝试多个位置加载 .env，适配源码运行和 PyInstaller 打包场景。

    PyInstaller 打包后当前工作目录是 EXE 所在目录，而源码运行时 .env
    通常在项目根目录或 backend/ 同级。遍历常见候选路径确保能命中。
    """
    candidates = []
    # 1. 当前工作目录（EXE 所在目录）
    candidates.append(os.path.join(os.getcwd(), '.env'))
    # 2. 本脚本所在目录（backend/）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, '.env'))
    # 3. 项目根目录（backend/../.env）
    candidates.append(os.path.join(script_dir, '..', '.env'))
    # 4. PyInstaller 打包后的 _internal/ 子目录
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, '_internal', '.env'))

    for path in candidates:
        normalized = os.path.normpath(path)
        if os.path.isfile(normalized):
            load_dotenv(normalized)
            return
    # 都没找到，fallback 到默认行为（从 CWD 找）
    load_dotenv()


_load_dotenv_from_multiple_paths()


def _prepend_path(dir_path: str) -> None:
    """把目录前置进 PATH，**幂等**。

    原实现每次都无条件 `dir + os.pathsep + PATH`，从不去重。而本函数会被
    /api/sys_health（docker healthcheck 每 30s、Tauri 桌面端每 5s）反复触发，
    PATH 会线性膨胀：实测每次 +122 字节，撑到 ~1MB 后 execve 抛
    `[Errno 7] Argument list too long`，连 ffmpeg 本身都开始调不起来。
    """
    if not dir_path:
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    # 已存在就提到最前（保持「优先使用」语义），否则插入
    if dir_path in parts:
        if parts[0] == dir_path:
            return
        parts.remove(dir_path)
    parts.insert(0, dir_path)
    os.environ["PATH"] = os.pathsep.join(parts)


def check_ffmpeg_exists() -> bool:
    """
    检查 ffmpeg 是否可用。优先使用 FFMPEG_BIN_PATH 环境变量指定的路径。
    """
    ffmpeg_bin_path = os.getenv("FFMPEG_BIN_PATH")
    logger.info(f"FFMPEG_BIN_PATH: {ffmpeg_bin_path}")
    if ffmpeg_bin_path and os.path.isdir(ffmpeg_bin_path):
        _prepend_path(ffmpeg_bin_path)
        logger.info(f"使用FFMPEG_BIN_PATH: {ffmpeg_bin_path}")
    else:
        # 遍历系统PATH寻找ffmpeg.exe
        system_path = os.environ.get("PATH", "")
        path_dirs = system_path.split(os.pathsep)
        for path_dir in path_dirs:
            ffmpeg_exe_path = os.path.join(path_dir, "ffmpeg.exe")
            if os.path.isfile(ffmpeg_exe_path):
                _prepend_path(path_dir)
                logger.info(f"在系统PATH中找到ffmpeg: {path_dir}")
                break
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        logger.info("ffmpeg 已安装")
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        logger.info("ffmpeg 未安装")
        return False


def ensure_ffmpeg_or_raise():
    """
    校验 ffmpeg 是否可用，否则抛出异常并提示安装方式。
    """
    if not check_ffmpeg_exists():
        logger.error("未检测到 ffmpeg，请先安装后再使用本功能。")
        raise EnvironmentError(
            " 未检测到 ffmpeg，请先安装后再使用本功能。\n"
            "\n"
            "方式一：用系统包管理器（推荐）\n"
            "  macOS          : brew install ffmpeg\n"
            "  Ubuntu / Debian: sudo apt install ffmpeg\n"
            "\n"
            "方式二：按需下载独立二进制（不进入版本库）\n"
            "  python scripts/fetch_ffmpeg.py\n"
            "  然后按输出提示设置 FFMPEG_BIN_PATH。\n"
            "\n"
            "手动安装的话，把 ffmpeg 所在目录写进 `.env`：\n"
            "  FFMPEG_BIN_PATH=/your/custom/ffmpeg/bin\n"
            "\n"
            "⚠️ 许可证提醒：官方预编译二进制多为 GPL 构建，与 OpenNoter 的 MIT 许可无关；\n"
            "   随产品再分发需遵守 FFmpeg 自身许可证，商业闭源分发请用 LGPL 构建。\n"
            "   详见 README「依赖说明 → FFmpeg」。"
        )
