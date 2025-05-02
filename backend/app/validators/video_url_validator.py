from pydantic import AnyUrl, validator, BaseModel
import re

SUPPORTED_PLATFORMS = {
    "bilibili": r"(https?://)?(www\.)?bilibili\.com/video/[a-zA-Z0-9]+",
    "youtube": r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+",
    "douyin": r"'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F])"

}



def is_supported_video_url(url: str) -> bool:
    return any(re.match(pattern, url) for pattern in SUPPORTED_PLATFORMS.values())


class VideoRequest(BaseModel):
    url: AnyUrl
    platform: str

    @validator("url")
    def validate_video_url(cls, v):
        if not is_supported_video_url(str(v)):
            raise ValueError("暂不支持该视频平台或链接格式无效")
        return v
