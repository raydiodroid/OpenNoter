"""Regression coverage for the retry contract at the yt-dlp boundary."""
import importlib.util
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "downloaders" / "youtube_downloader.py"


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _load_real(monkeypatch, name, path, **overrides):
    """把真实模块按路径加载进 sys.modules（不触发 app 包初始化）。

    纯 Python 模块（base / errors）能独立加载时就不该造假：假模块会让被测代码
    看到与生产不同的行为。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} spec not found")
    module = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules 再 exec：limits.py 用了 @dataclass，而 dataclasses
    # 在解析字段类型时会 `sys.modules[cls.__module__].__dict__`，未注册就 AttributeError。
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    for key, value in overrides.items():
        setattr(module, key, value)
    return module


class _Downloader:
    """最小可用的 Downloader 替身。

    只替换「真实基类里会碰磁盘/data 目录」的部分；`_resolve_output_dir` 必须提供，
    否则被测代码走的是 stub 才有的降级分支，测不到生产行为。
    """

    def __init__(self):
        self.cache_data = "/tmp"

    def _resolve_output_dir(self, output_dir):
        return output_dir or self.cache_data


class _AudioDownloadResult:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_downloader_module(monkeypatch):
    _stub(monkeypatch, "app")
    _stub(monkeypatch, "app.downloaders")
    _stub(monkeypatch, "app.models")
    _stub(monkeypatch, "app.services")
    _stub(monkeypatch, "app.utils")
    # base.py 只依赖一个枚举，stub 掉枚举即可跑真实的 base.py。
    _stub(monkeypatch, "app.enmus")
    _stub(monkeypatch, "app.enmus.note_enums", DownloadQuality=str)
    _load_real(
        monkeypatch,
        "app.downloaders.base",
        ROOT / "app" / "downloaders" / "base.py",
        Downloader=_Downloader,
    )
    _load_real(monkeypatch, "app.downloaders.errors", ROOT / "app" / "downloaders" / "errors.py")
    _load_real(monkeypatch, "app.downloaders.limits", ROOT / "app" / "downloaders" / "limits.py")
    _load_real(monkeypatch, "app.services.cookie_policy", ROOT / "app" / "services" / "cookie_policy.py")
    _load_real(monkeypatch, "app.services.cookie_manager", ROOT / "app" / "services" / "cookie_manager.py")
    _stub(monkeypatch, "app.downloaders.youtube_subtitle", YouTubeSubtitleFetcher=object)
    _stub(monkeypatch, "app.models.notes_model", AudioDownloadResult=_AudioDownloadResult)
    _stub(monkeypatch, "app.models.transcriber_model", TranscriptResult=object)
    _stub(
        monkeypatch,
        "app.services.proxy_config_manager",
        ProxyConfigManager=type("ProxyConfigManager", (), {"get_proxy_url": lambda self: None}),
    )
    _stub(monkeypatch, "app.utils.path_helper", get_data_dir=lambda: "/tmp")
    _stub(monkeypatch, "app.utils.url_parser", extract_video_id=lambda url, platform: "video-id")

    spec = importlib.util.spec_from_file_location("youtube_downloader_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _CapturingYoutubeDL:
    options = None

    def __init__(self, options):
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, _url, download=True):
        return {
            "id": "video-id",
            "title": "Example",
            "duration": 1,
            "thumbnail": None,
            "ext": "m4a",
            "tags": [],
        }


def test_audio_download_configures_a_nonzero_retry_budget(tmp_path, monkeypatch):
    """Removing retry options must make transient download errors unprotected."""
    module = _load_downloader_module(monkeypatch)
    original_youtube_dl = module.yt_dlp.YoutubeDL
    module.yt_dlp.YoutubeDL = _CapturingYoutubeDL
    try:
        module.YoutubeDownloader().download("https://youtu.be/example", str(tmp_path))
    finally:
        module.yt_dlp.YoutubeDL = original_youtube_dl

    options = _CapturingYoutubeDL.options
    assert options["retries"] > 0
    assert options["fragment_retries"] > 0
    assert options["socket_timeout"] > 0
