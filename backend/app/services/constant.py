"""下载器工厂。

历史问题：这里原本是一个模块级字典，import 时就构造好所有 downloader：

    SUPPORT_PLATFORM_MAP = {'bilibili': BilibiliDownloader(), ...}

由此带来三个后果：

1. **Cookie 改完不生效**。BilibiliDownloader.__init__ 里 `self._cookie =
   CookieConfigManager().get('bilibili')` 只读一次；用户在 UI 里配好 Cookie、
   甚至扫码登录成功之后，跑的还是 import 那一刻的旧值，必须重启后端。
2. **import 阶段就有磁盘副作用**：会建 `config/downloader.json`、打 warning、
   初始化快手 helper。
3. **测试难写**：想构造「无 Cookie」场景，得先处理掉 import 时就建好的单例。

改成按需工厂：任务开始时创建、用完丢弃。构造开销是纯 CPU + 一次小文件读取，
与一次网络下载相比可以忽略。

兼容性：保留 `SUPPORT_PLATFORM_MAP` 这个名字，但它现在是 `platform -> 类` 的
映射（不是实例），并且**不再在 import 时实例化**。
"""

from typing import Dict, Optional, Type

from app.downloaders.base import Downloader
from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.downloaders.douyin_downloader import DouyinDownloader
from app.downloaders.kuaishou_downloader import KuaiShouDownloader
from app.downloaders.local_downloader import LocalDownloader
from app.downloaders.youtube_downloader import YoutubeDownloader

# platform -> Downloader 子类。注意是类，不是实例。
SUPPORT_PLATFORM_MAP: Dict[str, Type[Downloader]] = {
    'youtube': YoutubeDownloader,
    'bilibili': BilibiliDownloader,
    'tiktok': DouyinDownloader,   # 历史别名，DouyinDownloader 同时处理 douyin / tiktok
    'kuaishou': KuaiShouDownloader,
    'douyin': DouyinDownloader,
    'local': LocalDownloader,
}

PLATFORM_ALIASES = {
    'tiktok': 'douyin',
    'douyin': 'douyin',
    'bilibili': 'bilibili',
    'youtube': 'youtube',
    'kuaishou': 'kuaishou',
    'local': 'local',
}


def normalize_platform(platform: Optional[str]) -> Optional[str]:
    """把平台别名归一成规范名（tiktok -> douyin）。"""
    if not platform:
        return None
    return PLATFORM_ALIASES.get(platform.strip().lower(), platform.strip().lower())


def is_supported_platform(platform: Optional[str]) -> bool:
    normalized = normalize_platform(platform)
    return bool(normalized and normalized in SUPPORT_PLATFORM_MAP)


def get_downloader(platform: str, user_id: Optional[int] = None) -> Downloader:
    """为一次任务创建一个新的下载器实例。

    每次调用都重新读 Cookie，所以后台改配置后**下一个任务**立即生效。

    :param user_id: BYOK —— 任务归属用户。下载器用它解析「用户自己的平台
        Cookie」；None 表示匿名，一律用站点 Cookie。
    :raises ValueError: 平台不受支持
    """
    normalized = normalize_platform(platform)
    downloader_cls = SUPPORT_PLATFORM_MAP.get(normalized or '')
    if downloader_cls is None:
        raise ValueError(f"不支持的平台：{platform}")

    # 本地文件下载器不接受 user_id（没有 Cookie 概念），按需回退
    try:
        return downloader_cls(user_id=user_id)
    except TypeError:
        return downloader_cls()
