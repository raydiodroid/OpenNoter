"""部署监控：只统计「坏了会让用户感知到」的指标。

设计原则：
  - 每张卡都必须能变红，且变红代表需要人处理
  - 探测本身有超时，**任何一项失败都只降级该项**，绝不 500
  - 只看线上真正在用的那套配置，不做「猜一个供应商」这种事后会漂的推断
  - 只对有权限的人开放，避免把内网地址暴露给普通用户

审计修复（详见 _audit/REPORT.md）：
  P0-1 整个端点用 try 包裹 + asr 过滤前做类型校验（缺 id 的 model dict 曾让整接口 500）
  P1    LLM 卡改用 resolve_model_selection 同一口径；ASR 卡按 transcriber_type 分支
  P1    任务统计区分 stale/unknown，total 只计可解析文件，mtime 之外另落 failed_at
  P2    磁盘探测 NOTE_OUTPUT_DIR 所在盘；FFmpeg 复用 ffmpeg_helper（认 FFMPEG_BIN_PATH）
  P2    NOTE_DIR 解析为绝对路径并回传，避免换 cwd 后计数静默归零
"""

import json
import os
import re
import platform
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends

from app.db.engine import SessionLocal
from app.db.models.providers import Provider
from app.db.provider_dao import reveal_api_key
from app.db.models.users import User
from app.enmus.task_status_enums import TaskStatus
from app.services.auth import require_admin
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _resolve_note_dir() -> Path:
    """把 NOTE_OUTPUT_DIR 固化成绝对路径。

    相对路径的语义是「相对进程 cwd」，但它会被三处 import 时各算一遍；一旦
    服务换了启动目录重启，旧历史就变成孤儿目录，监控会静默显示全 0。这里在
    模块加载时 resolve 一次并回传给前端，cwd 错位可以一眼看出来。
    """
    raw = os.getenv("NOTE_OUTPUT_DIR", "note_results")
    return Path(raw).expanduser().resolve()


NOTE_DIR = _resolve_note_dir()
PROBE_TIMEOUT = 4.0
# 中间态超过这个时长仍未被推进，视为疑似中断（进程崩溃/重启留下的僵尸状态）
TASK_STALE_HOURS = float(os.getenv("MONITOR_TASK_STALE_HOURS", "2"))
# 整个 /status 请求的硬上限：超时项降级为 error，而不是让请求无限挂着
REQUEST_BUDGET = float(os.getenv("MONITOR_REQUEST_BUDGET", "10"))

# 从枚举派生，不再硬编码字符串（DONE/COMPLETED/ERROR 全仓无写入点）
SUCCESS_STATES = {TaskStatus.SUCCESS.value}
FAILED_STATES = {TaskStatus.FAILED.value}
KNOWN_ACTIVE_STATES = {s.value for s in TaskStatus} - SUCCESS_STATES - FAILED_STATES

# 探测线程池：进程内复用。不能用 with ThreadPoolExecutor(...)，
# 它退出时隐式 shutdown(wait=True)，会把请求拖到慢探测自己结束（实测 30s）。
_PROBE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="monitor-probe")


# ---------------------------------------------------------------------------
# 探测层：每个函数自带超时与异常兜底，只返回结构化结果，绝不抛给上层
# ---------------------------------------------------------------------------
def _probe_models(base_url: str, api_key: str, *, verify: bool = True) -> dict:
    """探测 OpenAI 兼容端点的 /models。

    能列出模型说明「服务活着 + 鉴权通过」。空 api_key 直接短路——否则 httpx 会抛
    `Illegal header value b'Bearer '`，把「没配 key」伪装成「网关不可达」。
    """
    if not (api_key or "").strip():
        return {"ok": False, "latency_ms": None, "error": "API Key 未配置", "models": []}
    if not (base_url or "").strip():
        return {"ok": False, "latency_ms": None, "error": "Base URL 未配置", "models": []}

    url = base_url.rstrip("/") + "/models"
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT, verify=verify) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        latency = int((time.perf_counter() - start) * 1000)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "latency_ms": latency,
                "error": f"HTTP {resp.status_code}",
                "models": [],
            }
        payload = resp.json()
        raw = payload.get("data", []) if isinstance(payload, dict) else []
        # 只收非空字符串 id：曾经因为某个 dict 缺 id 得到 None，
        # 后续 .lower() 直接让整个 /monitor/status 500。
        names = [
            m.get("id")
            for m in raw
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].strip()
        ]
        return {"ok": True, "latency_ms": latency, "error": None, "models": names}
    except Exception as exc:
        return {"ok": False, "latency_ms": None, "error": str(exc)[:120], "models": []}


def _probe_ffmpeg() -> dict:
    """探测 ffmpeg，复用项目自己的 ffmpeg_helper。

    不能直接 subprocess.run(["ffmpeg", ...])：那只认 PATH，而 ffmpeg_helper 会把
    FFMPEG_BIN_PATH 前置进 PATH。两套判定曾给出相反结论（监控说缺失、helper 说可用）。
    """
    try:
        from ffmpeg_helper import check_ffmpeg_exists

        available = check_ffmpeg_exists()
    except Exception as exc:
        return {"ok": False, "version": None, "error": str(exc)[:100], "path": None}

    if not available:
        return {
            "ok": False,
            "version": None,
            "error": "ffmpeg 未找到（已尝试 PATH 与 FFMPEG_BIN_PATH）",
            "path": None,
        }

    exe = shutil.which("ffmpeg")
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5
        )
    except subprocess.TimeoutExpired:
        # 超时 ≠ 缺失，文案必须区分，否则管理员会去装一个已经装好的东西
        return {"ok": False, "version": None, "error": "ffmpeg -version 超时（5s）", "path": exe}
    except Exception as exc:
        return {"ok": False, "version": None, "error": str(exc)[:100], "path": exe}

    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()
        detail = tail[0][:100] if tail else f"exit {out.returncode}"
        return {"ok": False, "version": None, "error": detail, "path": exe}

    first = (out.stdout or "").splitlines()[0] if out.stdout else ""
    version = first.split(" version ")[1].split(" ")[0] if " version " in first else None
    if not version:
        # returncode=0 但解析不出：不能给纯绿「可用」，否则和「未检测到」自相矛盾
        return {
            "ok": True,
            "version": None,
            "error": "无法从 -version 输出解析版本号",
            "path": exe,
        }
    return {"ok": True, "version": version, "error": None, "path": exe}


def _dir_size(path: Path) -> tuple[int, int]:
    """返回 (字节数, 未能统计的条目数)。

    逐条兜底：任务结束时 _purge_task 正在删文件，rglob 与 stat 之间有竞态，
    单个文件消失不该让整张磁盘卡变红。软链不重复计入（is_symlink 先判断）。
    """
    total = 0
    skipped = 0
    try:
        entries = path.rglob("*")
    except OSError:
        return 0, 1
    for f in entries:
        try:
            if f.is_symlink() or not f.is_file():
                continue
            total += f.stat().st_size
        except (FileNotFoundError, OSError):
            skipped += 1
    return total, skipped


def _storage() -> dict:
    """磁盘指标：探测「真正在写数据的那块盘」。

    原实现探 `/app` 或 cwd，在「单镜像 + 命名卷」部署下探的是容器 overlay，
    曾出现 overlay 报 30% 充足、真实数据卷已用 97% 的误导。这里改成探
    NOTE_OUTPUT_DIR 所在分区，并把探测路径回传，让管理员能核对。
    """
    result: dict[str, Any] = {}
    target = NOTE_DIR
    probe_path = target
    try:
        if not target.exists():
            probe_path = target.parent if target.parent.exists() else Path.cwd()
        usage = shutil.disk_usage(probe_path)
        total_gb = round(usage.total / 1024**3, 1)
        used_gb = round(usage.used / 1024**3, 1)
        # free 用「已四舍五入的 total - used」推出，保证页面上三个数字自洽。
        # 独立 round 会破坏恒等式：926.3517 → 926.4，541.8399 → 541.8，
        # 384.5118 → 384.5，而 541.8 + 384.5 = 926.3 ≠ 926.4。
        free_gb = round(total_gb - used_gb, 1)
        result.update(
            {
                "probe_path": str(probe_path),
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
                # 同时给字节数，前端要精确计算时不必反推
                "total_bytes": usage.total,
                "used_bytes": usage.used,
            }
        )
    except Exception as exc:
        result["error"] = str(exc)[:100]

    # 目录体积单独 try：它失败不该连累整块盘的数字
    try:
        size, skipped = _dir_size(NOTE_DIR) if NOTE_DIR.exists() else (0, 0)
        result["note_results_mb"] = round(size / 1024**2, 1)
        if skipped:
            result["note_results_skipped"] = skipped
    except Exception as exc:
        result["note_results_error"] = str(exc)[:100]

    return result


# 任务 id 形态：标准 UUID（8-4-4-4-12）。历史上还有 task-<n> 这类测试数据，
# 一并放行；但 `xxx_markdown` 这种后缀残留必须排除。
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_task_id(name: str) -> bool:
    """判断状态文件名（去掉 .status.json）是否像合法 task_id。

    合法：标准 UUID，或 `task-*` / `t-*` 之类的简单标识（测试与旧数据）。
    非法：带 `_markdown` / `_audio` / `_transcript` 后缀的缓存残留。
    """
    if not name:
        return False
    if _UUID_RE.match(name):
        return True
    # 排除已知的缓存后缀残留
    lowered = name.lower()
    for suffix in ("_markdown", "_audio", "_transcript", "_meta"):
        if lowered.endswith(suffix):
            return False
    # 其余（task-1、t-abc 等）按简单标识放行；含路径分隔符的排除
    return "/" not in name and "\\" not in name


def _tasks() -> dict:
    """扫状态文件统计任务。

    与旧实现的差别（每条都对应一次真实误判）：
      - total 只统计**可解析**的状态文件，坏文件单列 unreadable，让
        「进行中+成功+失败」与 total 对得上（旧版 total 会把坏文件算进去）
      - 中间态超过 TASK_STALE_HOURS 单列 stale，不再冒充「进行中」
      - failed_24h 优先用状态里的 failed_at（不可变），回落 mtime
      - 整个循环逐条 try，文件被并发删除不会 500
    """
    counters = {
        "total": 0,
        "running": 0,
        "stale": 0,
        "success": 0,
        "failed": 0,
        "failed_24h": 0,
        "unknown": 0,
        "unreadable": 0,
        "zombie_scan": True,
        "note_dir": str(NOTE_DIR),
        "stale_hours": TASK_STALE_HOURS,
    }
    if not NOTE_DIR.exists():
        # 目录不存在 ≠ 一切正常：显式给出 error，让前端变红而不是绿「无异常」
        counters["error"] = f"任务目录不存在：{NOTE_DIR}"
        return counters

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stale_cutoff = time.time() - TASK_STALE_HOURS * 3600

    try:
        # 只认「纯 UUID」文件名。旧版代码曾写出过
        # `{task_id}_markdown.status.json` 这类残留文件（内容是过期的
        # SUMMARIZING），它们会被误当成独立任务，导致总数虚高、状态错乱。
        status_files = [
            p
            for p in NOTE_DIR.glob("*.status.json")
            if _looks_like_task_id(p.name[: -len(".status.json")])
        ]
    except OSError as exc:
        counters["error"] = f"扫描任务目录失败：{exc}"
        return counters

    for f in status_files:
        try:
            st = f.stat()  # 只 stat 一次，并放进 try（并发删除会抛 FileNotFoundError）
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("status file is not a JSON object")
        except Exception as exc:
            counters["unreadable"] += 1
            logger.warning(f"监控跳过无法解析的状态文件 {f.name}: {exc}")
            continue

        counters["total"] += 1
        status = str(data.get("status", "")).upper()

        if status in SUCCESS_STATES:
            counters["success"] += 1
        elif status in FAILED_STATES:
            counters["failed"] += 1
            failed_at = _parse_ts(data.get("failed_at"))
            ts = failed_at or datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if ts > cutoff:
                counters["failed_24h"] += 1
        elif status in KNOWN_ACTIVE_STATES:
            if st.st_mtime < stale_cutoff:
                counters["stale"] += 1
            else:
                counters["running"] += 1
        else:
            # 空状态或白名单外的值：单独计数，不混进 running
            counters["unknown"] += 1

    # 并入 DB 里不可被用户操作抹掉的历史口径。
    # 删任务会物理删掉 .status.json，只看文件会让「累计任务」倒退、
    # 让「24h 失败」被用户删笔记「消除」。DB 这份用来兜住这两个指标。
    db_stats = _db_task_stats(cutoff)
    if db_stats:
        # db_total 用「含软删除」的累计口径：用户删笔记不该让累计倒退。
        counters["db_total"] = db_stats.get(
            "all_including_deleted", db_stats.get("total", 0)
        )
        counters["db_deleted"] = db_stats.get("deleted", 0)
        counters["db_failed_24h"] = db_stats.get("failed_since", 0)
        # 累计口径取「文件 ∪ DB」的较大值：DB 保留了被删任务，
        # 文件覆盖迁移后新跑、还没进 DB 的在途任务，两者互补。
        counters["cumulative_total"] = max(counters["total"], counters["db_total"])
        counters["failed_24h_incl_deleted"] = max(
            counters["failed_24h"], counters["db_failed_24h"]
        )
        counters["stats_source"] = "files+db"
    else:
        # 旧库没跑迁移（无 status 列）时会走到这里，前端无碍
        counters["stats_source"] = "files"

    return counters


def _db_task_stats(cutoff: datetime) -> dict:
    """从 video_tasks 读状态统计；未迁移/查询失败时返回 {}。"""
    try:
        from app.db.video_task_dao import count_tasks_by_status_since

        return count_tasks_by_status_since(since=cutoff) or {}
    except Exception as exc:
        logger.debug(f"DB 任务统计不可用（可能未执行 migrate_task_status.py）：{exc}")
        return {}


def _parse_ts(value: Any) -> Optional[datetime]:
    """把状态文件里的时间戳解析成带时区的 datetime，失败返回 None。"""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


# ---------------------------------------------------------------------------
# 业务配置解析：与真实运行链路同一口径
# ---------------------------------------------------------------------------
def _active_llm_provider() -> tuple[Optional[dict], Optional[dict]]:
    """返回 (真正在用的供应商, 展示用的选择信息)。

    旧实现用 `id != "groq"` 猜一个，会和真实链路分叉：监控探 openai、
    线上实际用 deepseek。这里直接复用 resolve_model_selection 的同一口径。
    """
    info: dict[str, Any] = {}
    try:
        from app.services.model_selection import resolve_model_selection

        provider_id, model_name = resolve_model_selection(None, None)
        info.update({"provider_id": provider_id, "model_name": model_name, "source": "resolve_model_selection"})
        db = SessionLocal()
        try:
            row = db.query(Provider).filter(Provider.id == provider_id).first()
            if row is None:
                return None, info
            return (
                {
                    "id": row.id,
                    "name": row.name,
                    "base_url": row.base_url,
                    "api_key": reveal_api_key(row.api_key),
                },
                info,
            )
        finally:
            db.close()
    except Exception as exc:
        # 没配供应商/没模型是常态，不该让整接口失败
        info["error"] = getattr(exc, "message", None) or str(exc)[:120]
        return None, info


def _asr_config() -> dict:
    """按 transcriber_type 给出「真实的转写链路」，而不是无条件取 groq 供应商。

    5 种引擎里只有 groq 走 providers 表；本地引擎看模型落盘，bcut/kuaishou
    端点写死在代码里。旧卡在默认部署（fast-whisper）下展示的东西与真实链路无关。
    """
    try:
        from app.services.transcriber_config_manager import TranscriberConfigManager

        mgr = TranscriberConfigManager()
        cfg = mgr.get_config()
        readiness = mgr.is_model_ready()
    except Exception as exc:
        return {"kind": "unknown", "error": str(exc)[:120]}

    ttype = (cfg.get("transcriber_type") or "").strip()
    model_size = cfg.get("whisper_model_size")
    groq_model = cfg.get("groq_model")

    if ttype in ("fast-whisper", "mlx-whisper"):
        return {
            "kind": "local",
            "transcriber_type": ttype,
            "model_size": model_size,
            "ready": bool(readiness.get("ready")),
            "downloading": bool(readiness.get("downloading")),
            "reason": readiness.get("reason") or None,
        }
    if ttype in ("bcut", "kuaishou"):
        return {
            "kind": "builtin",
            "transcriber_type": ttype,
            "ready": True,
            "note": "在线引擎，端点与密钥由代码内置，不依赖供应商配置",
        }
    if ttype == "groq":
        return {
            "kind": "provider",
            "transcriber_type": ttype,
            "provider_id": "groq",
            "model": groq_model or None,
            "ready": bool(readiness.get("ready")),
            "reason": readiness.get("reason") or None,
        }
    return {
        "kind": "unknown",
        "transcriber_type": ttype or None,
        "ready": False,
        "error": f"未知的转写引擎：{ttype or '未配置'}",
    }


def _safe(name: str, fn, *args, **kwargs):
    """把任意探测包成「永不抛异常」的形式，失败只降级该项。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 监控不允许因单项失败而中断
        logger.warning(f"监控项 {name} 采集失败: {exc}", exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}


@router.get("/status")
def monitor_status(admin: User = Depends(require_admin)):
    """聚合部署监控数据。任何一项失败都只影响该项，绝不 500。"""
    llm_provider, llm_info = _active_llm_provider()
    asr_cfg = _safe("asr_config", _asr_config)
    asr_provider: Optional[dict] = None
    if asr_cfg.get("kind") == "provider":
        try:
            db = SessionLocal()
            try:
                row = db.query(Provider).filter(Provider.id == "groq").first()
                if row is not None:
                    asr_provider = {
                        "id": row.id,
                        "name": row.name,
                        "base_url": row.base_url,
                        "api_key": reveal_api_key(row.api_key),
                    }
            finally:
                db.close()
        except Exception as exc:
            logger.warning(f"读取转写供应商失败: {exc}")

    # 复用模块级线程池，而不是 `with ThreadPoolExecutor(...)`：
    # 后者退出时隐式 shutdown(wait=True)，会把请求拖到慢探测自己结束
    # （实测：budget=3s 时请求仍耗时 30s）。池子有固定 4 个 worker，
    # 天然限制并发：若已有 4 个慢探测占满，后续提交排队并很快撞上 budget 降级。
    futures = {
        "llm": _PROBE_POOL.submit(_probe_models, llm_provider["base_url"], llm_provider["api_key"])
        if llm_provider
        else None,
        "asr": _PROBE_POOL.submit(
            _probe_models, asr_provider["base_url"], asr_provider["api_key"]
        )
        if asr_provider
        else None,
        "ffmpeg": _PROBE_POOL.submit(_safe, "ffmpeg", _probe_ffmpeg),
        "storage": _PROBE_POOL.submit(_safe, "storage", _storage),
        "tasks": _PROBE_POOL.submit(_safe, "tasks", _tasks),
    }

    def _collect(fut, label: str, fallback):
        """取单项结果；超时只降级该项，绝不让请求挂住或整体 500。"""
        if fut is None:
            return fallback
        try:
            return fut.result(timeout=REQUEST_BUDGET)
        except FutureTimeout:
            logger.warning(f"监控项 {label} 超时（>{REQUEST_BUDGET}s），降级为 error")
            # 取消排队中的任务；已在跑的无法中断，但不再等待它
            fut.cancel()
            return {"error": f"探测超时（>{REQUEST_BUDGET}s）"}
        except Exception as exc:  # noqa: BLE001 — 单项异常绝不外溢
            logger.warning(f"监控项 {label} 异常: {exc}", exc_info=True)
            return {"error": f"{type(exc).__name__}: {exc}"[:160]}

    llm_result = _collect(
        futures["llm"],
        "llm",
        {"ok": False, "latency_ms": None, "error": "未配置可用的 LLM 供应商", "models": []},
    )
    asr_result = _collect(futures["asr"], "asr_probe", None)
    ffmpeg_result = _collect(futures["ffmpeg"], "ffmpeg", {"error": "采集失败"})
    storage_result = _collect(futures["storage"], "storage", {"error": "采集失败"})
    tasks_result = _collect(futures["tasks"], "tasks", {"error": "采集失败"})

    # 降级结果可能只带 error，缺固定字段。补齐成契约形状，
    # 免得前端必须为「超时」和「正常」写两套判型（曾导致 data.ffmpeg.ok 缺失）。
    if not isinstance(ffmpeg_result, dict):
        ffmpeg_result = {"error": "采集失败"}
    ffmpeg_result.setdefault("ok", False)
    ffmpeg_result.setdefault("version", None)
    ffmpeg_result.setdefault("error", None)

    if not isinstance(storage_result, dict):
        storage_result = {"error": "采集失败"}

    if not isinstance(tasks_result, dict):
        tasks_result = {"error": "采集失败"}
    for key in ("total", "running", "stale", "success", "failed", "failed_24h", "unknown", "unreadable"):
        tasks_result.setdefault(key, 0)
    tasks_result.setdefault("note_dir", str(NOTE_DIR))
    tasks_result.setdefault("stale_hours", TASK_STALE_HOURS)
    tasks_result.setdefault("zombie_scan", True)

    # 转写卡：按引擎类型组装，探测结果只在「在线 groq」时才有意义
    asr_payload: dict[str, Any] = {
        "kind": asr_cfg.get("kind", "unknown"),
        "transcriber_type": asr_cfg.get("transcriber_type"),
        "ready": bool(asr_cfg.get("ready", False)),
        "error": asr_cfg.get("error"),
        "reason": asr_cfg.get("reason"),
        "note": asr_cfg.get("note"),
        "model_size": asr_cfg.get("model_size"),
        "downloading": asr_cfg.get("downloading"),
        "configured_model": asr_cfg.get("model"),
        "name": None,
        "base_url": None,
        "ok": False,
        "latency_ms": None,
        "models": [],
        "model_count": 0,
    }
    if asr_provider:
        probe = asr_result if isinstance(asr_result, dict) else {}
        models = probe.get("models") or []
        asr_payload.update(
            {
                "name": asr_provider["name"],
                "base_url": asr_provider["base_url"],
                "ok": bool(probe.get("ok")),
                "latency_ms": probe.get("latency_ms"),
                "error": probe.get("error") or asr_payload["error"],
                "models": models[:6],
                "model_count": len(models),
            }
        )
        configured = asr_cfg.get("model")
        if configured:
            asr_payload["configured_model_present"] = configured in models
    elif asr_cfg.get("kind") == "provider":
        asr_payload["error"] = asr_payload.get("error") or "未配置 groq 供应商"

    llm_payload = {
        "name": llm_provider["name"] if llm_provider else None,
        "base_url": llm_provider["base_url"] if llm_provider else None,
        "provider_id": llm_provider["id"] if llm_provider else None,
        "model_name": llm_info.get("model_name"),
        "ok": bool(llm_result.get("ok")),
        "latency_ms": llm_result.get("latency_ms"),
        "error": llm_result.get("error") or llm_info.get("error"),
        "model_count": len(llm_result.get("models") or []),
    }

    return R.success(
        data={
            "llm": llm_payload,
            "asr": asr_payload,
            "storage": storage_result,
            "ffmpeg": ffmpeg_result,
            "tasks": tasks_result,
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note_dir": str(NOTE_DIR),
                "probe_timeout_s": PROBE_TIMEOUT,
                "request_budget_s": REQUEST_BUDGET,
                "ffmpeg_probe_timeout_s": 5,
                "platform": platform.platform(),
            },
        }
    )
