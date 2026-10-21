"""部署监控（/api/monitor/status）回归测试。

每条用例对应 `_audit/REPORT.md` 里的一个真实缺陷：
  P0-1 畸形供应商响应曾让整个接口 500
  P0-4 转写就绪门禁曾因 `_downloading` ImportError 恒放行
  P1   任务统计：僵尸计入 running / total 会倒退 / 分类和≠total / mtime 语义
  P2   磁盘探测路径、FFmpeg 认 FFMPEG_BIN_PATH、请求总预算

这些测试**不启动 HTTP 服务**，直接调用 monitor 的纯函数与聚合入口
（`monitor_status` 返回 JSONResponse，取 .body 解析即可）。
"""

import json
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# backend/ 作为 import 根：pytest 从 backend 目录运行时可直接 import app
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _ensure_app_namespace():
    """确保 `app` 包可导入，且**不触发** app/__init__.py 的聚合导入。

    app/__init__.py 会 import 全部 router，从而拉 chromadb / torch / faster_whisper
    等重依赖。本文件只测 monitor.py 的纯逻辑，不需要那条链路。

    两种运行顺序都要兼容：
      - 本文件先跑：`app` 还没进 sys.modules → 注册 namespace package（跳过 __init__）
      - 别的测试先跑并已 import 真实 `app` → 直接复用；但此时若删掉 monitor 子模块，
        重新 import 会走真实 `app.routers` 包（它本身是空的 __init__），不会重跑
        app/__init__.py，因此是安全的。
    """
    if "app" in sys.modules:
        return
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg



def _load_monitor():
    """按文件路径加载 `app.routers.monitor`，绕过 app/__init__.py 的聚合导入。

    为什么不用 `from app.routers import monitor`：
      - app/__init__.py 会 import 全部 router → 拉 chromadb / torch；
      - 若别的测试先 import 过真实 `app`，该包已被实例化为**普通包**，
        其子包解析行为会随 import 顺序变化（本项目 `app/utils` 甚至没有
        __init__.py，靠 namespace package 才成立），很容易 ModuleNotFoundError。

    这里让 app / app.routers / app.utils 等全部按 namespace package 重新注册，
    再用 importlib 从源码文件加载 monitor —— 加载的是仓库真实源码，
    且与其它测试的 import 顺序完全解耦。
    """
    import importlib.util

    target = BACKEND_DIR / "app" / "routers" / "monitor.py"

    # 把 `app` 及其下没有被显式 __init__.py 覆盖的子包统一登记为 namespace，
    # 保证 relatie import（from app.xxx import yyy）都能解析。
    def _register_ns(name: str, path: Path):
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            mod.__package__ = name
            sys.modules[name] = mod
        paths = list(getattr(mod, "__path__", []) or [])
        if str(path) not in paths:
            paths.append(str(path))
        try:
            mod.__path__ = paths
        except Exception:
            pass

    for name, path in (
        ("app", BACKEND_DIR / "app"),
        ("app.routers", BACKEND_DIR / "app" / "routers"),
        ("app.utils", BACKEND_DIR / "app" / "utils"),
        ("app.services", BACKEND_DIR / "app" / "services"),
        ("app.db", BACKEND_DIR / "app" / "db"),
        ("app.enmus", BACKEND_DIR / "app" / "enmus"),
        ("app.exceptions", BACKEND_DIR / "app" / "exceptions"),
    ):
        if path.is_dir():
            _register_ns(name, path)

    spec = importlib.util.spec_from_file_location("app.routers.monitor", target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app.routers.monitor"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def monitor_env(tmp_path, monkeypatch):
    """隔离的 NOTE_OUTPUT_DIR + SQLite + 重新加载的 monitor 模块。"""
    notes = tmp_path / "note_results"
    notes.mkdir()
    db_path = tmp_path / "open_noter.db"
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # monitor 的 NOTE_DIR 在 import 时求值，必须重新加载才能吃到新环境变量
    for name in list(sys.modules):
        if name == "app.routers.monitor" or name.startswith("app.routers.monitor."):
            del sys.modules[name]

    monitor = _load_monitor()

    monkeypatch.setattr(monitor, "NOTE_DIR", notes, raising=False)

    # 建表（只建需要的）
    from app.db.engine import Base, get_engine
    from app.db.models.api_keys import ApiKey  # noqa: F401
    from app.db.models.models import Model  # noqa: F401
    from app.db.models.providers import Provider  # noqa: F401
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
    yield monitor, notes


def _write_status(notes: Path, name: str, payload, *, mtime_ago_s: float = 0):
    p = notes / f"{name}.status.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    if mtime_ago_s:
        t = time.time() - mtime_ago_s
        os.utime(p, (t, t))
    return p


class _FakeAdmin:
    is_superuser = True


# ---------------------------------------------------------------------------
# P0-1：畸形供应商 payload 不得让接口 500
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"id": "whisper-1"}, {"object": "model"}]},  # 缺 id
        {"data": [{"id": None}, {"id": 123}, {"id": "whisper-large-v3"}]},  # id 为 None/数字
        {"data": "not-a-list"},  # data 不是 list
        {},  # 没有 data
        {"data": [None, "str", 42]},  # data 里是脏类型
    ],
)
def test_probe_models_never_leaks_non_string_ids(monitor_env, monkeypatch, payload):
    monitor, _ = monitor_env
    import httpx

    class FakeResponse:
        status_code = 200

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = monitor._probe_models("http://example.invalid/v1", "key")
    assert result["ok"] is True
    # 关键：只保留非空字符串，后续 .lower() 才不会炸
    assert all(isinstance(m, str) and m for m in result["models"])


def test_probe_models_short_circuits_empty_api_key(monitor_env):
    monitor, _ = monitor_env
    result = monitor._probe_models("http://example.invalid/v1", "")
    assert result["ok"] is False
    assert "API Key" in result["error"]
    assert result["latency_ms"] is None


def test_monitor_status_survives_hostile_status_files(monitor_env):
    monitor, notes = monitor_env
    _write_status(notes, "good", {"status": "SUCCESS"})
    _write_status(notes, "nondict", ["SUCCESS"])  # 合法 JSON 但非 dict
    _write_status(notes, "broken", "{not json")
    # 不应抛异常
    resp = monitor.monitor_status(admin=_FakeAdmin())
    data = json.loads(bytes(resp.body))["data"]
    assert data["tasks"]["success"] == 1
    assert data["tasks"]["unreadable"] == 2


# ---------------------------------------------------------------------------
# P1：任务统计口径
# ---------------------------------------------------------------------------
def test_task_total_equals_sum_of_categories(monitor_env):
    monitor, notes = monitor_env
    _write_status(notes, "a", {"status": "SUCCESS"})
    _write_status(notes, "b", {"status": "FAILED", "failed_at": time.time()})
    _write_status(notes, "c", {"status": "TRANSCRIBING"})
    _write_status(notes, "d", "{broken")
    t = monitor._tasks()
    assert t["total"] == 3  # 只计可解析的
    assert t["unreadable"] == 1
    assert (
        t["running"] + t["stale"] + t["success"] + t["failed"] + t["unknown"] == t["total"]
    )


def test_stale_tasks_not_counted_as_running(monitor_env):
    """僵尸中间态（进程崩溃遗留）必须进 stale，不能冒充进行中。"""
    monitor, notes = monitor_env
    _write_status(notes, "zombie", {"status": "DOWNLOADING"}, mtime_ago_s=30 * 86400)
    _write_status(notes, "alive", {"status": "DOWNLOADING"}, mtime_ago_s=1)
    t = monitor._tasks()
    assert t["stale"] == 1
    assert t["running"] == 1


def test_failed_24h_prefers_failed_at_over_mtime(monitor_env):
    """重试会刷新 mtime，所以 24h 窗口必须看不可变的 failed_at。"""
    monitor, notes = monitor_env
    old_failed_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # mtime 很新（模拟「刚被重试写过」），但 failed_at 是 30 天前
    _write_status(notes, "old-fail", {"status": "FAILED", "failed_at": old_failed_at}, mtime_ago_s=0)
    _write_status(notes, "new-fail", {"status": "FAILED", "failed_at": time.time()})
    t = monitor._tasks()
    assert t["failed"] == 2
    assert t["failed_24h"] == 1


def test_unknown_status_not_folded_into_running(monitor_env):
    monitor, notes = monitor_env
    _write_status(notes, "weird", {"status": "NOT_A_REAL_STATE"})
    _write_status(notes, "blank", {"status": ""})
    t = monitor._tasks()
    assert t["unknown"] == 2
    assert t["running"] == 0


def test_missing_note_dir_reports_error_not_all_zero(monitor_env, monkeypatch, tmp_path):
    """目录不存在时必须给 error，让前端变红；旧实现静默返回全 0 + 绿「无异常」。"""
    monitor, _ = monitor_env
    monkeypatch.setattr(monitor, "NOTE_DIR", tmp_path / "does-not-exist", raising=False)
    t = monitor._tasks()
    assert "error" in t
    assert t["total"] == 0


# ---------------------------------------------------------------------------
# P2：磁盘 / FFmpeg / 预算
# ---------------------------------------------------------------------------
def test_storage_probes_note_output_dir_and_is_internally_consistent(monitor_env):
    monitor, notes = monitor_env
    s = monitor._storage()
    assert "error" not in s
    assert Path(s["probe_path"]).resolve() == notes.resolve() or s["probe_path"] == str(
        notes.resolve()
    )
    # total == used + free（free 由 total-used 推出，保证恒等）
    assert round(s["used_gb"] + s["free_gb"], 1) == s["total_gb"]


def test_storage_degrades_to_error_without_percent(monitor_env, monkeypatch):
    """disk_usage 失败时不能有 percent（前端据此显示「未知」而不是绿「充足」）。"""
    monitor, _ = monitor_env
    import shutil

    def boom(_):
        raise OSError("disk_usage failed")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    s = monitor._storage()
    assert "error" in s
    assert "percent" not in s


def test_probe_ffmpeg_honours_ffmpeg_bin_path(monitor_env, monkeypatch, tmp_path):
    """只配 FFMPEG_BIN_PATH、ffmpeg 不在 PATH 时，监控必须与主流程结论一致。"""
    monitor, _ = monitor_env
    import shutil as shutil_mod

    real = shutil_mod.which("ffmpeg")
    if not real:
        pytest.skip("本机没有 ffmpeg，跳过")

    bin_dir = tmp_path / "custom-ffmpeg"
    bin_dir.mkdir()
    os.symlink(real, bin_dir / "ffmpeg")

    monkeypatch.setenv("FFMPEG_BIN_PATH", str(bin_dir))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    # 清掉可能残留的 helper 模块状态
    sys.modules.pop("ffmpeg_helper", None)

    result = monitor._probe_ffmpeg()
    assert result["ok"] is True, result
    assert result["version"]


def test_ffmpeg_helper_path_prepend_is_idempotent():
    """PATH 改写若不去重会撑爆 ARG_MAX（实测第 8579 次调用后 E2BIG）。"""
    sys.modules.pop("ffmpeg_helper", None)
    from ffmpeg_helper import _prepend_path

    original = os.environ.get("PATH", "")
    try:
        for _ in range(200):
            _prepend_path("/tmp/__ffmpeg_audit_dir__")
        path = os.environ["PATH"]
        assert path.count("/tmp/__ffmpeg_audit_dir__") == 1
        assert path.split(os.pathsep)[0] == "/tmp/__ffmpeg_audit_dir__"
        assert len(path) < len(original) + 200
    finally:
        os.environ["PATH"] = original


def test_request_budget_degrades_slow_probe(monitor_env, monkeypatch, tmp_path):
    """慢探测必须被总预算截断并降级，不能让请求挂住（旧实现实测 30s）。"""
    monitor, notes = monitor_env
    monkeypatch.setattr(monitor, "REQUEST_BUDGET", 1.0)

    # 放一个可用供应商，好让 LLM 探测真的发起
    from app.db.engine import SessionLocal
    from app.db.models.models import Model
    from app.db.models.providers import Provider

    db = SessionLocal()
    try:
        # 用 upsert 语义，避免与其它用例/既有数据撞 id
        existing = db.query(Provider).filter(Provider.id == "slow").first()
        if existing is None:
            db.add(
                Provider(
                    id="slow",
                    name="Slow",
                    logo="s",
                    type="custom",
                    api_key="sk",
                    base_url="http://127.0.0.1:9/v1",
                    enabled=1,
                )
            )
            db.commit()
        else:
            existing.enabled = 1
            existing.base_url = "http://127.0.0.1:9/v1"
            db.commit()
        if db.query(Model).filter_by(provider_id="slow").first() is None:
            db.add(Model(provider_id="slow", model_name="m"))
            db.commit()
    finally:
        db.close()

    def hang(*a, **k):
        time.sleep(5)
        return {"ok": True, "models": [], "error": None, "latency_ms": 5000}

    monkeypatch.setattr(monitor, "_probe_models", hang)

    started = time.perf_counter()
    resp = monitor.monitor_status(admin=_FakeAdmin())
    elapsed = time.perf_counter() - started
    data = json.loads(bytes(resp.body))["data"]

    assert elapsed < 4, f"请求没有被预算截断，耗时 {elapsed:.1f}s"
    assert data["llm"]["ok"] is False
    assert data["llm"]["error"]
    # 其它卡不受影响
    assert "storage" in data and "tasks" in data and "ffmpeg" in data


# ---------------------------------------------------------------------------
# P0-4：转写就绪门禁
# ---------------------------------------------------------------------------
def test_readiness_gate_reports_not_ready_for_missing_local_model(monkeypatch):
    """`_downloading` 曾因 ImportError 让门禁恒放行；现在必须真的判未就绪。"""
    from app.services.transcriber_config_manager import TranscriberConfigManager

    monkeypatch.setattr(
        TranscriberConfigManager,
        "get_config",
        lambda self: {
            "transcriber_type": "fast-whisper",
            "whisper_model_size": "definitely-not-downloaded-xyz",
            "groq_model": "",
        },
    )
    readiness = TranscriberConfigManager().is_model_ready()
    assert readiness["ready"] is False
    assert "尚未下载" in readiness["reason"]
