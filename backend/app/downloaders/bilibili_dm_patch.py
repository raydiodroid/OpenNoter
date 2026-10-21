"""给 yt-dlp 的 B 站 playurl 请求补上 dm_img_* / web_location 风控参数。

背景
----
2026-06 前后开始，``x/player/wbi/playurl`` 网关会拒绝**完全没有 dm 指纹字段**的
请求并返回 HTTP 412。实测（2026-10-21，见 ``_ref/findings/probe_log.md``）：

===============  ==================================================
请求                                                结果
===============  ==================================================
仅 bvid/cid/fnval + 正确 w_rid                       412
+ ``Origin: https://www.bilibili.com`` 头            200（兜底有效）
+ 任意一个 dm 参数（哪怕值是 ``not-json``）           200
+ ``web_location`` 或无关的 ``foo=bar``               412
===============  ==================================================

也就是说网关校验的是「dm 这组字段存在不存在」，而不是值是否真实。
调用方（``bilibili_downloader``）已经在所有请求头里带了 ``Origin``，这里再补一层
参数注入。

与上游 yt-dlp 的关系（重要）
---------------------------
``BilibiliBaseIE._dm_params`` 抽成 property 并在 ``_download_playinfo`` 里注入，是
yt-dlp PR #13730 / commit e8de28e，随 **2026.07.04** 发布。当前仓库锁的是
``yt-dlp>=2026.7.4``，所以新环境里 dm_img_* 上游已经自己带上了，只剩
``web_location`` 没带（实测不带也可以 code=0）。

因此这里不再无条件 monkey-patch，而是**先探测上游已经提供了哪些字段，只补缺的**：

- 上游有 ``_dm_params`` 且 ``_download_playinfo`` 已经在用 → dm_img_* 不用管；
- ``web_location`` 上游没带 → 由本 patch 补；
- 两者都不缺（未来 yt-dlp 补全）→ 直接跳过，不做任何 monkey-patch，
  避免上游改签名时我们的 wrapper 变成白炸的 TypeError 源。

这样既保留了 history 里「修 412」的能力，又不会在 yt-dlp 升级后拖后腿。
"""

import base64
import logging
import random
import re
import string

logger = logging.getLogger(__name__)

# dm 参数抽成 property 并用于 playurl 的 yt-dlp 版本（PR #13730）。低于这个版本
# 的 yt-dlp 在 playurl 上缺少 dm_*，会稳定 412，只能靠本 patch 兜底。
MIN_YTDLP_VERSION = (2026, 7, 4)

# 网关要求的指纹字段。web_location 严格来说不是「必需」，但上游不带、
# 本站在旧版本里靠它兜底，所以一起维护。
DM_IMG_KEYS = ("dm_img_list", "dm_img_str", "dm_cover_img_str", "dm_img_inter")
RISK_CONTROL_KEYS = (*DM_IMG_KEYS, "web_location")


def build_dm_img_params() -> dict:
    """构造网关能接受的 dm_img_* / web_location 参数（值不要求真实）。"""
    return {
        'web_location': 1550101,
        'dm_img_list': '[]',
        'dm_img_str': base64.b64encode(
            ''.join(random.choices(string.printable, k=random.randint(16, 64))).encode()
        )[:-2].decode(),
        'dm_cover_img_str': base64.b64encode(
            ''.join(random.choices(string.printable, k=random.randint(32, 128))).encode()
        )[:-2].decode(),
        'dm_img_inter': '{"ds":[],"wh":[6093,6631,31],"of":[430,760,380]}',
    }


def _version_tuple(version: str) -> tuple:
    """把 ``2026.08.19`` / ``2026.7.4`` 这类版本号裁成可比较的整数元组。

    非数字段一律截断（例如本地 dev 版本会带 ``-dev`` 后缀）。
    """
    parts = []
    for chunk in re.split(r"[.\-+_]", str(version or "")):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts)


def _installed_ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__ as version

        return version
    except Exception:  # pragma: no cover - 环境里没有 yt-dlp
        return ""


def warn_if_ytdlp_too_old(version: str = None) -> bool:
    """低于 2026.07.04 时打一条告警，返回是否过旧。"""
    version = _installed_ytdlp_version() if version is None else version
    if not version:
        return False
    if _version_tuple(version) >= MIN_YTDLP_VERSION:
        return False
    logger.warning(
        "yt-dlp 版本 %s 低于 %s：playurl 缺少 dm_* 参数可能返回 412，建议升级",
        version,
        ".".join(str(part) for part in MIN_YTDLP_VERSION),
    )
    return True


def _code_mentions(func, name: str) -> bool:
    """判断函数体里是否出现了某个名字/字符串常量。"""
    code = getattr(func, "__code__", None)
    if code is None:
        return False
    if name in code.co_names:
        return True
    return any(isinstance(const, str) and name in const for const in code.co_consts)


def missing_risk_control_keys(base_cls=None) -> list:
    """探测上游还缺哪些风控字段；返回空列表表示本 patch 完全冗余。

    :param base_cls: 默认为 ``yt_dlp.extractor.bilibili.BilibiliBaseIE``
    """
    if base_cls is None:
        try:
            from yt_dlp.extractor.bilibili import BilibiliBaseIE as base_cls
        except Exception as exc:  # yt-dlp 缺失或模块布局变了
            logger.warning("Bilibili dm_img patch 无法探测上游：%s", exc)
            return list(RISK_CONTROL_KEYS)

    playinfo = getattr(base_cls, "_download_playinfo", None)
    if playinfo is None:
        return list(RISK_CONTROL_KEYS)

    has_dm_property = hasattr(base_cls, "_dm_params")
    missing = []
    if not has_dm_property or not _code_mentions(playinfo, "_dm_params"):
        missing.extend(DM_IMG_KEYS)
    if not _code_mentions(playinfo, "web_location"):
        missing.append("web_location")
    return missing


def apply_bilibili_dm_img_patch() -> bool:
    """按需给 ``BilibiliBaseIE._download_playinfo`` 补上缺失的风控参数。

    幂等、防御性：返回 ``True`` 表示「环境已经 OK」（本次打上了 patch，或本来就
    不需要 / 之前已经打过），返回 ``False`` 表示探测失败但**不抛异常**，
    调用方照常工作。
    """
    warn_if_ytdlp_too_old()

    try:
        from yt_dlp.extractor.bilibili import BilibiliBaseIE
    except Exception as e:  # yt-dlp 缺失或模块布局变了
        logger.warning("Bilibili dm_img patch 跳过，无法导入 extractor：%s", e)
        return False

    original = BilibiliBaseIE._download_playinfo
    if getattr(original, "_bili_dm_patched", False):
        return True

    missing = missing_risk_control_keys(BilibiliBaseIE)
    if not missing:
        logger.info("yt-dlp 已自带 dm_* / web_location 风控参数，跳过 B站 412 patch")
        return True

    def _patched_download_playinfo(self, bvid, cid, headers=None, query=None, **kwargs):
        # dm_* 合并进交给 _sign_wbi 的 query；调用方显式传入的参数优先。
        # **kwargs 保持开放：yt-dlp 会往 _download_playinfo 加新参数
        # （2026.x 加了 fatal），wrapper 把签名钉死的话，上游一升级就 TypeError。
        merged_query = {**build_dm_img_params(), **(query or {})}
        return original(self, bvid, cid, headers=headers, query=merged_query, **kwargs)

    _patched_download_playinfo._bili_dm_patched = True
    BilibiliBaseIE._download_playinfo = _patched_download_playinfo
    logger.info(
        "已为 yt-dlp BilibiliBaseIE 补上 playurl 风控参数（缺少：%s）",
        ", ".join(missing),
    )
    return True
