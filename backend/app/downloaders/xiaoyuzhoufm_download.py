"""小宇宙播客下载器（WIP，未接入任何调用方）。

历史问题：这个文件的模块级代码会**在 import 时**就去请求小宇宙并 print 整个响应。
只要有人 import 到它（比如未来加进下载器工厂、或被测试收集），就会多一次外网请求、
多一屏日志，而且在无网络环境直接炸。

这里把它改成一个真正的占位实现：模块级不做任何 IO，需要调试时显式调 `_debug_fetch`。
"""

import logging
from typing import Optional, Union

import requests

from app.downloaders.base import Downloader
from app.enmus.note_enums import DownloadQuality
from app.models.audio_model import AudioDownloadResult

logger = logging.getLogger(__name__)

_DEBUG_URL = (
    "https://www.xiaoyuzhoufm.com/_next/data/5Pvt_oGntgdyBD_XgwBaB/"
    "podcast/62382c1103bea1ebfffa1c00.json?id=62382c1103bea1ebfffa1c00"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )
}


def _debug_fetch() -> None:
    """手动调试用：拉一次小宇宙的 next/data JSON。不要在模块级调用。"""
    response = requests.get(_DEBUG_URL, headers=_HEADERS, timeout=(5, 30))
    logger.debug("小宇宙响应: %s", response.text[:500])


class Xiaoyuzhoufm_download(Downloader):
    """小宇宙播客下载器骨架。"""

    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        raise NotImplementedError("小宇宙播客下载尚未实现")
