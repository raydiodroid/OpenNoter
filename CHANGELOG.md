# Changelog

本项目所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed

- **FFmpeg 改为按需下载，不再随仓库分发**。`scripts/fetch_ffmpeg.py` 新增：按平台给出下载源，支持 `--print-url` 只打印地址（便于审计）、`--dest` 指定目录，下载后提示设置 `FFMPEG_BIN_PATH`。`.gitignore` 补上 `/vendor/ffmpeg/`。README「依赖说明 → FFmpeg」重写为「包管理器优先 / 按需下载 / Docker 内置」三选一。
  - **许可证**：官方预编译 FFmpeg 二进制多为 GPL 构建（含 `libx264`/`libx265`），而本项目是 MIT。此前仓库曾提交过一个 GPLv3 的 ffmpeg Windows 构建（289MB，commit `05fdc36` 引入、`49a1b3a` 删除），已造成许可证污染。现在明确约定：仓库不含 FFmpeg 二进制，用户自行获取，再分发受 FFmpeg 自身许可约束；商业闭源分发需改用 LGPL 构建或依赖系统已安装的 FFmpeg。
  - ffmpeg 缺失时的报错文案同步更新：给出两种安装方式 + 许可证提醒，并指向 README。

### Security

- **修复 SSRF：任意 URL 可通过平台校验并被服务端实际请求**。`is_supported_video_url()` 原先用「URL 里包含 `douyin` / `kuaishou` 子串」判断平台，于是 `http://169.254.169.254/latest/meta-data/?x=douyin` 能通过校验，随后 `DouyinDownloader.extract_video_id()` 会真的对该地址发起 `requests.head`（可探测/访问云元数据端点）。现在改为按 `hostname` 精确匹配平台域名 + 路径形态校验，再过一遍内网地址黑名单（复用 `assert_safe_url`，传 `suffixes=()` 只做 IP 层校验，避免套用 image_proxy 的图片 CDN 白名单）。与 `detect_platform()` 的实现保持一致。
- **不再把 Cookie 打进日志**。下载器里 20+ 处 `print()` 会打印完整 headers / 请求 URL / 响应体，其中 B 站与抖音的 headers 直接带 `Cookie`。全部改为 `logger.debug/info`，且不再输出凭据。

### Fixed

- **部署监控页 5 张卡的数据源，经逐个审计后重写**：
  - **LLM 卡探的不是线上在用的供应商**：原实现取 `providers` 里「第一个 id != groq」的启用项，而真实生成笔记走 `resolve_model_selection()`（第一个「已启用且有模型」的供应商）。实测默认安装后会探 openai（空 key，请求根本没发出）而真实用 deepseek。改为复用同一口径。
  - **转写端点卡与真实链路无关**：原实现无条件探 `providers['groq']`，但默认部署是本地 fast-whisper（`transcriber_type` 有 5 种取值，只有 groq 走供应商表）。改为按 `transcriber_type` 分 local / builtin / provider / unknown 四支渲染。
  - **任务统计口径**：「进行中」把崩溃遗留的中间态永久计入（实测 6 个僵尸任务永不下降）；「累计任务」是状态文件数，用户删笔记会让它倒退；坏 JSON 计入总数但不计入任何分类（`total += 1` 在 `json.loads` 之前）；`failed_24h` 用文件 mtime，重试会刷新它、把旧失败洗成进行中。现分别修为：超 2h 中间态单列 `stale`、状态落库 + **软删除**保证累计不倒退、坏文件单列 `unreadable`、24h 窗口改用不可变的 `failed_at`。
  - **磁盘卡可能探错盘**：原实现探 `/app` 或 cwd，在「单镜像 + 命名卷」部署下探的是容器 overlay（实测 overlay 报 30% 充足，真实数据卷已用 97%）。改为探 `NOTE_OUTPUT_DIR` 所在分区并回传 `probe_path`。
  - **FFmpeg 卡是假告警**：`_probe_ffmpeg` 只认 PATH，不认 `FFMPEG_BIN_PATH`，而同进程的 `ffmpeg_helper` 认——同一时刻两套判定给出相反结论。改为复用 helper。
  - **「探测超时 4 秒」失实**：4s 只是 httpx 单次读超时。实测分块慢速传输绕过它、请求跑 16s；ffmpeg 是 5s；`.result()` 与目录扫描无超时。改为模块级线程池 + 总预算，慢探测从 30s 降到 3s 内。
- **一个畸形供应商响应能让整个监控接口 500**：`/models` 返回的 model dict 缺 `id` 时 `names` 里出现 `None`，随后 `None.lower()` 抛 `AttributeError`，5 张卡全部消失。现在只收非空字符串 id，且整个接口分项降级、单项失败只影响该项。
- **监控页响应形状偏离契约就整站白屏**：`data?.storage.percent ?? 0` 只短路了 `data`，返回 `data: []` 时 `[].storage` 是 `undefined` → 未捕获 TypeError，且全前端没有 ErrorBoundary，连侧栏一起卸载。现在加了运行时契约校验 + 路由级 ErrorBoundary + 所有子对象 `?.` 收敛。
- **监控页把「没数据」显示成健康**：`percent` 缺失 → `?? 0` → `0 < 90` → 绿色「充足」；而同一张卡下面又渲染红色错误。现在缺失/null 一律「未知」，`storage.error` 显示红「读取失败」，badge 与 Meter 共用同一阈值（80/90）。
- **监控页失败后继续显示过期绿灯**：`updatedAt` 只在成功时更新，失败只写 errorMessage、旧 data 原样保留，全页没有过期提示。现在超 2 倍刷新周期即降级所有健康标并显示「已过期 N 分钟」。
- **转写就绪门禁早已失效**：`transcriber_config_manager` 仍 import 被 `dl_state` 取代的 `_downloading`，`ImportError` 走到「保守放行」分支 → 对未下载模型恒返回 `ready=True`。改为从 `app.transcriber.model_download_state` 取。
- **状态文件写盘失败会写坏 `.status.json`**：兜底分支把 `"Error writing status: ..."` 直接写进去，产出「合法但非 JSON」的坏文件——监控计入 unreadable、任务本身从此读不出状态、前端永远停在 PENDING。改为保留旧状态 + 错误落到旁路 `.err`。
- **截图失败会让整篇笔记变空白但任务仍标 SUCCESS**：`_insert_screenshots` 任一张失败就 `return None`，调用方把 `None` 赋回 markdown。改为只跳过该标记，并让后处理对非字符串结果做防御。
- **`ffmpeg_helper` 的 PATH 改写会无限膨胀**：每次调用都无条件前置、从不去重，而它被 `/sys_health` 反复触发（docker healthcheck 30s、Tauri 5s），实测 8579 次后 PATH 达 1MB，execve 报 `Argument list too long`。改为幂等。
- **普通用户能看到「用户管理 / 部署监控」入口，点进去吃 403**：`SETTINGS_NAV` 是静态数组，未按 `is_superuser` 过滤。现在按权限过滤 + 路由外包 AdminGuard。
- **扩展端保留着已被删掉的装饰性指标**：「后端 running」是后端硬编码字符串 + UI 写死绿色，证伪能力为 0（commit `1458860` 明说要从主前端删掉这类指标，主前端删了、扩展留着）。改为显示真实请求耗时，并把 `/sys_health` 的结构化子状态如实渲染。
- **扩展在开启强制认证后整个不可用**：`OPENNOTER_AUTH_ENFORCE=1` 时插件不带任何凭据（localStorage 的 cookie 对 localhost 不生效）→ 所有接口 401。新增 API Key 设置项 + `x-api-key` 头。

 → 现在免登录可用**。原实现依赖 `iesdouyin.com/web/api/v2/aweme/iteminfo/` 与自制 `a_bogus` / `msToken`，实测该接口现在返回 `{"status_code":11110,"status_msg":"encrypt_data_miss"}`，且 `aweme/v1/web/aweme/detail/` 缺风控头时返回 `403 Blocked by ArgusSecurityPlugin Uifid Not Found`。改为当前可用的最小方案：`GET /aweme/v1/web/aweme/detail/?aweme_id=<id>` + `x-tt-argus: 1` 头 + `Referer` + 自引导 `ttwid`（来自 `live.douyin.com`），**不需要任何密码学签名**。ttwid 进程内缓存复用；「HTTP 200 + 0 字节」判定为软拦截并抛可重试异常。
- **抖音取错音频**：原实现用 `aweme_detail.music.play_url.uri` 当音频，那是 BGM/原声（`is_use_music=True` 的作品会拿到别人的配乐），转写结果完全不匹配。改为从无水印视频流用 ffmpeg 抽取音轨。
- **抖音时长单位错误**：`video.duration` 是毫秒（如 395440），原实现当秒用，11 秒的视频会记成 185 分 23 秒，污染笔记元数据与 RAG 索引。改为毫秒换算；注意 `music.duration` 才是秒。
- **抖音标题恒为空**：原实现读 `item_title`（实测为空字符串），真实标题在 `desc`。改为 `desc` 优先、`item_title` 兜底。
- **抖音封面 KeyError**：图文作品没有 `cover_original_scale`，原实现的 else 分支还会访问不存在的顶层 `video` 键，直接抛 `KeyError`。改为 `cover_original_scale` → `origin_cover` → `cover` 逐级兜底。
- **抖音下到带水印视频**：原实现用 `download_addr`（重编码带水印版）。改为 `play_addr`（无水印）优先，候选顺序 `play_addr` → `play_addr_h264` → `play_addr_265` → `download_addr`，并逐个候选回退。同时修正选流逻辑：`bit_rate[]` 里 `format == "dash"` 的档位是纯视频轨无音轨，且码率排序 ≠ 分辨率排序（540P 档码率可能高于 720P 档），改为按分辨率排序。
- **抖音图文作品无法解析**：详情接口对图文返回 `aweme_detail: null` + `filter_detail.filter_reason: "images_base"`，新增回退到移动端分享页解析 `window._ROUTER_DATA`（分享页地址里的 `/playwm/` 替换为 `/play/` 得到无水印版）。
- **抖音链接形态覆盖不足**：原实现只认 `/video/(\d+)`，`/note/`、`/slides/`、`modal_id=` 等形态全部返回空 ID。补齐所有形态（与 `astrbot_plugin_link_resolver` 的覆盖面对齐）。
- **B 站音频码率被写死 64kbps**。`preferredquality` 硬编码 `'64'`，实测产出 64kbps，而 B 站源音频有 44k / 103k / 204k 三档，等于主动丢弃约 2/3 的信息量，转写与下游 LLM 都在吃这个损失。改为跟随 `quality` 参数：`fast→64` / `medium→128` / `slow→192`。
- **B 站 `av` 号视频拼出 `None.mp4`**：`extract_video_id()` 只认 BV 正则，`av170001` 返回 `None`，随后被拼成缓存文件名 `None.mp4`（存在性检查永远 miss，极端情况下会串片）。新增 `app/utils/bilibili_ids.py`（av↔BV 互转，算法常量对齐 B 站前端公开转换表），`extract_video_id` 现在统一返回 BV 号。
- **B 站多 P 视频「已缓存」判断永久失效**：`extract_video_id("...?p=2")` 返回 `BV1Ranw6DEYW`，而 yt-dlp 实际落盘 `BV1Ranw6DEYW_p2.mp4`，导致存在性检查对多 P 永远 False、每次重新下载。现在 download / download_video / 缓存探测共用同一套输出命名规则。
- **B 站下载视频默认选集 AV1**：`bv*[ext=mp4]/...` 实测选中 `av01`，而产出视频用于浏览器预览与截图，Safari 等播放器不支持。格式选择器改为优先 `avc1`。
- **B 站临时 Cookie 文件泄漏**：`_write_netscape_cookie_file()` 用 `NamedTemporaryFile(delete=False)` 落一个含 `SESSDATA` / `bili_jct` 明文的文件，且没有任何清理逻辑，长期运行会在临时目录堆积成百上千个。改为下载方法内的 `contextmanager` + `TemporaryDirectory`，`finally` 清理；同时把「用到时才写」取代「`__init__` 就写」。
- **`skip_download` 参数导致 TypeError，省流量优化完全失效**：`NoteGenerator` 在「已有字幕、无需截图」时会传 `skip_download=True` 走元信息捷径，但 B 站 / 抖音下载器不接受这个参数，`TypeError` 被 `except Exception` 吞掉后静默回退**完整下载**。现在四个下载器统一签名并真正支持跳过媒体下载。
- **前端「快速/标准/高质量」三个选项是纯装饰**：`QUALITY_MAP` 被 import 后从未参与任何 yt-dlp / ffmpeg 参数，各平台各自写死码率。现已真正串联到音频后处理。
- **YouTube Cookie 被永久禁用**：`COOKIE_ALLOWLISTS["youtube"]` 是空元组，`filter_downloader_cookies()` 永远返回空串，接口还会提示「YouTube 下载器当前不使用 Cookie」，而 YouTube 恰恰是最需要 Cookie 的平台（年龄限制 / 会员 / 403 风控）。Cookie 策略层现在区分两种格式：B站/抖音/快手用 `header` 形式，YouTube 用 `netscape`（cookies.txt），各自校验。
- **YouTube 无 403 回退**：新增 `player_client=web_embedded` 的回退重试。
- **快手请求无超时、Cookie 为 None 时崩溃**：`requests.get/post` 全部补上 `timeout`；`cookies.strip()` 在无 Cookie 时会 `AttributeError`（原来只打日志就继续往下走），改为提前抛明确异常。
- **`config/downloader.json` 改完不生效，需重启后端**：`SUPPORT_PLATFORM_MAP` 在 import 时就把所有下载器实例化，下载器 `__init__` 里读一次 Cookie 后不再重读。改为 `get_downloader(platform)` 工厂，每个任务新建实例、Cookie 现读。
- **抖音 Cookie 白名单缺 `UIFID_TEMP` / `UIFID`**：浏览器一键同步过来的风控相关项会被过滤掉，导致「同步成功但没生效」。已补入白名单（`msToken` 仍会丢弃，那是每次请求动态生成的）。
- **`xiaoyuzhoufm_download.py` 在 import 时发外网请求**：模块级直接 `requests.get()` + `print(response.json())`，任何 import 都会触发一次网络请求（无网环境直接抛异常）。改为占位实现，调试入口收进函数。
- **`test_task_serial_executor.py` 长期失败**：`SerialTaskExecutor` 早已被 `ConcurrentTaskExecutor`（线程池）替换，但测试仍断言串行语义。改为断言当前真实承诺：结果返回、异常传播、并发度受 `max_workers` 约束。

### Added

- `app/downloaders/limits.py`：下载大小 / 时长 / 重试预算，支持 `DOWNLOAD_MAX_SIZE_MB`、`DOWNLOAD_MAX_DURATION_SECONDS`、`DOWNLOAD_RETRY_COUNT` 环境变量覆盖（默认 200MB / 300s / 2 次，与 `astrbot_plugin_link_resolver` 默认值对齐）。0 表示不限制。
- `app/downloaders/errors.py`：失败分类（`RetryableDownloadError` / `SizeLimitExceeded` / `DurationLimitExceeded`）+ `is_retryable()`，供任务级重试决策使用。
- `app/utils/bilibili_ids.py`：`av2bv` / `bv2av` / `normalize_bvid`。
- 任务级重试：网络类异常按指数退避重试，确定性失败（超限）立即失败并给出可读消息。
- 下载缓存 key 加入 `video_url + quality` 指纹，避免「前端重试复用 task_id + 改了链接」时命中旧视频的音频缓存。
- 抖音 / B 站 / 快手 / YouTube 下载全部改为流式写盘（`iter_content`），不再把整个媒体读进内存。
- `.env.example` 补充下载限制与各平台 Cookie 格式说明。

## [2.4.4] - 2026-06-23

### Security

- **升级 Starlette 0.46.1 → 0.47.2 修复 CVE-2025-54121**（[GHSA-2c2j-9gv5-cj73](https://github.com/advisories/GHSA-2c2j-9gv5-cj73)，#411）：旧版在解析 multipart 表单中的大文件时，`SpooledTemporaryFile` 从内存溢写到磁盘的 rollover 在事件循环线程内**同步执行**，攻击者可借大文件上传阻塞事件循环造成拒绝服务（DoS）。新版把该写入移到线程池。FastAPI 同步升级 0.115.12 → 0.116.2（其 starlette 约束由 `<0.47.0` 放宽，以容纳修复版本），与现有 pydantic / anyio / python-multipart 均兼容。

## [2.4.3] - 2026-06-23

### Fixed

- **Whisper `large-v3-turbo` 模型无法下载**（#402）：内置映射指向的 `Systran/faster-whisper-large-v3-turbo` 仓库已从 HuggingFace 下架（返回 401/404），点击下载会静默失败、状态一直显示「未下载」。改用社区维护的 CT2 转换版 `deepdml/faster-whisper-large-v3-turbo-ct2`（直链可达、含 `model.bin`，与 faster-whisper 的 `large-v3-turbo` 等价）。
- **模型下载失败时前端无任何提示**（#402 衍生）：`/transcriber_models_status` 此前只回传 `downloading`/`downloaded`，后台下载失败状态被丢弃。现新增 `model_download_state` 统一维护下载状态与失败原因，状态接口新增 `failed` 字段并透传 `error`；前端模型列表展示「下载失败」徽标 + 错误详情，按钮变为「重试」，并对新出现的失败弹出提示。

## [2.4.2] - 2026-06-17

### Fixed

- **Docker 部署打开显示 nginx 欢迎页**：`nginx/default.conf` 被 docker-compose（多容器）与 `Dockerfile.complete`（单镜像）共用，但两种模式对 `location /` 的需求相反（多容器需反代独立的 frontend 容器，单镜像需直接服务本地静态文件），导致其中一种部署方式总会回退到 nginx 默认欢迎页。现拆分为两份配置：`nginx/default.conf`（compose，反代 frontend 容器）与新增的 `nginx/standalone.conf`（单镜像，静态前端 + 本地 backend 代理）；`Dockerfile.complete` 改用后者并删除 Debian 默认站点，两种部署方式均恢复正常。

## [2.4.1] - 2026-06-17

### Added

- **YouTube Shorts 链接支持**：后端 URL 校验与 video id 提取支持 `youtube.com/shorts/<id>` 形态，Shorts 链接可正常提交生成笔记（#381）。

### Fixed

- **B 站 412（wbi/playurl 风控）**：B 站 `x/player/wbi/playurl` 网关新增 `dm_img_list`/`dm_img_str`/`dm_cover_img_str`/`dm_img_inter` + `web_location` 风控校验，缺失即返回 HTTP 412。多数视频网页内嵌 playinfo、yt-dlp 不调此 API；而网页不内嵌 playinfo、必须走 API 的视频（如 BV1X9L16oEgB）会撞上风控，刷新 cookie 无效、yt-dlp（含最新版）上游尚未适配。现于 wbi 签名前注入哑值 dm_img 风控参数（形态对齐 yt-dlp 自身 arc/search 用法）恢复 200（#410）。
- **B 站分 P 视频字幕取错集**：分 P 视频提交 `?p=N` 时，字幕优先链路未透传 p 参数，始终取第 1 集 cid，导致笔记内容与实际下载的 p=N 音频不一致。现从 `data.pages[N-1]` 取对应分 P 的 cid（#409）。

## [2.4.0] - 2026-06-07

### Added

- **可配置 Whisper 模型**：转写设置支持自定义 Whisper 模型与名称映射，可指定自定义 HuggingFace repo 或本地路径（新增 `backend/app/transcriber/whisper_models.py` + 测试），转写设置页可选择 / 配置模型。
- **关注公众号获取交流群**：关于页群二维码改为公众号二维码，关注公众号后回复「交流群」即可获取最新群二维码，避免群码过期失效。

### Fixed

- **浏览器扩展**：修复标题显示异常，优化脑图（Markmap）导出。

### Docs

- **GPU/CUDA 部署**：README 补全 GPU/CUDA 部署说明。

## [2.3.4] - 2026-05-27

### Added

- **一对一搭建服务二维码**：新增「OpenNoter AI 笔记系统一对一搭建服务」二维码（README + 关于页），扫码加微信、备注「搭建服务」即可咨询。

## [2.3.3] - 2026-05-22

### Fixed

- **预构建 Docker 镜像数据持久化**：文档的 `docker run` 只挂了 `data/`（媒体缓存），而 SQLite 数据库（LLM 供应商配置 + 笔记历史）和笔记文件不在该卷下，导致删除 / 升级容器时丢失配置与历史。现将数据库重定向到 `/app/backend/data/open_noter.db`、笔记到 `data/note_results`（随 data 卷持久化）；README 更新为挂载 `data` / `config` / `static` / `models` 四个数据卷，并提示**勿**挂整个 `/app/backend`（命名卷会固化镜像内代码，导致 `docker pull` 升级不生效）。`docker-compose` 路径本就正确（`./backend:/app` 整目录绑挂），未受影响。

## [2.3.2] - 2026-05-22

### Fixed

- **后端启动崩溃（Docker）**：`python:3.11-slim` 基础镜像升级到 Debian 13 / glibc 2.41 后，`ctranslate2` 4.5.0 预编译库带「可执行栈」标记被 glibc 拒绝加载（`cannot enable executable stack ... Invalid argument`）。由于 `from faster_whisper import WhisperModel` 在顶层 import，import 失败直接拖垮整个后端启动 → 容器反复重启。升级 `ctranslate2` 4.5.0→4.6.0（wheel 加入 `noexecstack` 链接标志，从二进制层根治）
- **whisper 模型误报「离线模式找不到模型」**：下载（modelscope 自定义目录）与加载（faster-whisper HF cache）布局不一致导致命不中缓存。统一为下载 / 加载 / 完整性检测 / 损坏自愈都走 HF cache 布局，并向后兼容老 modelscope 目录
- **桌面端构建产物版本恒为 2.0.0**：Release 工作流在 `pnpm tauri build` 前从 git tag 注入版本号到 `tauri.conf.json`，使产物版本与 Release 版本对齐

## [2.3.1] - 2026-05-22

### Changed

- **更新微信交流群二维码**：旧二维码即将失效，替换 README 中 5 个交流群（群 1-5）的入群二维码。

## [2.3.0] - 2026-05-14

主线：一波部署与运行时韧性专项——Docker / 桌面端 / 在线引擎三端的"装不上、起不来、跑一半挂"问题集中清理，并新增全局代理与转写模型就绪门禁。

### Added

- **全局代理**：新增 `ProxyConfigManager`（`config/proxy.json` 持久化 + `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 环境变量兜底）。一处配置同时作用于 LLM API、转写 API（Groq 等）、yt-dlp 视频下载、youtube-transcript-api 字幕拉取。前端「设置 → 下载配置」页新增代理卡片，会显示当前实际生效值（含 env 兜底来源提示）
- **转写模型就绪门禁**：`/generate_note` 在排队前检查本地转写引擎（fast-whisper / mlx-whisper）的模型是否已下载完整，未就绪直接拦截并返回 `reason=transcriber_model_not_ready`，不再让任务静默卡在首次大文件下载；前端引导用户去「音频转写配置」页下载
- **桌面端后端健康监控韧性**：Tauri 侧 spawn sidecar 后以 HTTP 探针轮询 `/api/sys_check` 判就绪并 emit `backend-ready`；`RunEvent::Exit` 钩子在 app 退出前 kill sidecar，杜绝孤儿进程占用 8483 端口；启动失败对话框展示原因 + 最近 stderr + 一键重启 / 复制日志
- `/sys_health` 重构为结构化健康响应 `{backend, ffmpeg, db, whisper_model}`；部署监控页显示 Whisper 模型本地下载状态
- 所有 Dockerfile 新增 `BASE_REGISTRY` build-arg，国内拉不到 docker.io 时可换 daocloud 等镜像源

### Fixed

- **whisper 模型损坏自愈**：`model.bin` 截断 / 损坏导致 `Unable to open file 'model.bin'` 死循环——加载失败时删除损坏目录、重新下载、重试一次；mlx-whisper 同样按 `config.json` 判定完整性
- **空 API Key 天书报错**：空 key 会让 httpx 拼出非法 header `Bearer ` 并抛 `LocalProtocolError: Illegal header value b'Bearer '`。新增 `build_openai_client` 在入口校验，给出「xxx 的 API Key 未配置」的清晰提示
- **新模型 temperature 不兼容**：OpenAI o1 / o3 / gpt-5 系列拒绝自定义 `temperature`，命中后就地去掉该参数重试，不消耗重试预算
- **桌面端「后端加载中」死循环**：`useCheckBackend` 重写——60s 总超时取代 `while(true)` 无限轮询，订阅 Tauri `backend-ready` / `backend-terminated` / `backend-startup-timeout` 事件；裸 `fetch` 探测避免启动期 toast 叠堆
- **CORS 漏配桌面端 origin**：补全 `tauri://localhost` / `https://tauri.localhost`，修桌面端 fetch 拿到 200 却被浏览器 CORS 拒绝读响应（表现为"连不上后端"但后端日志全 200）
- `/api/api/sys_health` 双 `/api` 前缀导致健康检查 404
- `docker-compose` 的 `restart: on-failure:3` 改为 `unless-stopped`，避免短暂崩溃后容器被永久打死；GPU compose 补齐 `healthcheck` / `restart` / `mem_limit`
- `Dockerfile.complete` 的 supervisord 用 `%(ENV_*)s` 透传环境变量给 backend 子进程（此前只白名单 2 个，`docker run -e` 配的变量后端看不到）
- `.env.example`：修正 `VITE_API_BASE_URL` 端口（8000→8483）、`WHISPER_MODEL_SIZE`（medium→tiny，首次启动不被 ~1.5GB 下载卡住）
- Onboarding：第 1 步后端连通检测改为自动重试 + Tauri 事件触发 + 手动重检按钮；第 2 步撞预置供应商名时改为更新已存在供应商而非报错
- 模型供应商列表卡片整行可点击切换（此前仅 icon 区域响应）
- `connect_test` 改用真实 chat completion 探测而非 `/v1/models`（后者在 key 无 inference 权限 / 供应商不实现该端点时会误判）

### Internal

- `backend/main.py` lifespan 拆为 `[startup 1/5]…[startup 5/5]` 分段日志，启动期异常可一眼定位死在哪一步
- `request.ts` 新增 `suppressToast` 配置位，预期内的失败（如 onboarding 撞名重试）不弹全局红 toast
- `CLAUDE.md` 勘误：移除不存在的 `app/messaging/` / `app/i18n/` / `worker_registry.py` 描述，修正 `events/` 路径，补 `pytest` / 前端 `typecheck` 命令

## [2.2.3] - 2026-05-09

### Fixed

- 前端 vite build 在 Docker / Tauri CI 中失败：`Rollup failed to resolve import '@tauri-apps/api/event'`。v2.2.0 加的 P1/P2 桌面端组件用了 `await import('@tauri-apps/api/event')` 与 `'@tauri-apps/api/core'`，但 `@tauri-apps/api` 只是 `@tauri-apps/plugin-shell` 的间接依赖，没在 `OpenNoter_frontend/package.json` 直接声明，Rollup 在 production build 时静态分析报"无法解析"
  - `OpenNoter_frontend/package.json`：把 `@tauri-apps/api` 加为直接依赖（`^2.10.1`，与 lockfile 中已有的 transitive 版本一致）
  - 本地 `DOCKER_BUILD=1 pnpm run build` 复现 + 验证修复

## [2.2.2] - 2026-05-09

补 v2.2.1 漏掉的 Tauri 桌面端 build 修复。

### Fixed

- 桌面端 Tauri 构建失败：v2.2.1 的 hotfix 只修了 Docker 镜像构建里的 pnpm 版本，`main.yml` 的 `pnpm/action-setup@v4 with: version: 'latest'` 没改，于是桌面端 build 仍然在 `Install frontend dependencies` 步报 `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module: node:sqlite`（pnpm 11 要求 Node 22+，但 main.yml 用的 node 20）。pin 到 `9.15.0`，与 Docker 侧一致。

## [2.2.1] - 2026-05-09

补 v2.2.0 ghcr.io 镜像构建失败。

### Fixed

- Docker 镜像构建失败：`v2.2.0` tag 触发的 ghcr.io 推送在 frontend-builder 第 5/7 步 `pnpm install --frozen-lockfile` 报 `ERR_UNKNOWN_BUILTIN_MODULE`。根因：`corepack prepare pnpm@latest` 拉到了 pnpm 11.0.9，而 pnpm 11 要求 Node 22+，跟我们的 `node:20-alpine` 不兼容。
  - `Dockerfile.complete` 与 `OpenNoter_frontend/Dockerfile` 的 pnpm 版本 pin 到 `9.15.0`（lockfile 由 pnpm 9 生成，匹配 Node 20）

## [2.2.0] - 2026-05-09

主线：浏览器插件功能与 web 端 NoteForm 完整对齐；桌面客户端 UX 与错误恢复一波重炼。

### Added — 浏览器插件

- 笔记选项与 web 端 NoteForm 完整对齐：
  - `style` 由自由文本改成 9 个预设下拉（minimal / detailed / academic / tutorial / xiaohongshu / life_journal / task_oriented / business / meeting_minutes），与 backend `prompt_builder.note_styles` 严格匹配（之前自由文本不命中 enum 等于没传——隐性 bug）
  - `format` 完整 4 个 checkbox（toc / link / screenshot / summary，原来只有 screenshot/link）
  - `extras` 文本框：拼接到 prompt 末尾的 ad-hoc 提示
- 多模态视频理解：`video_understanding` 开关 + `video_interval`（1-30 秒）+ `grid_size`（[r,c]，1-10），抽帧拼图喂视觉模型，提示需选视觉模型才生效

### Added — 桌面客户端

- **首启 4 步引导**（`/onboarding`）：后端连通性自检 → LLM 供应商 + 模型 → 转写引擎选择（默认推荐 Groq）→ Cookie 同步说明。完成后 `localStorage('opennoter-onboarded')` 标记，纯 web 端不打扰
- **Sidecar 健康度面板**：右下角浮动状态点（绿/黄/红，5s 轮询 `/sys_health`），点开抽屉看最近 200 行后端日志、一键重启后端（新增 Tauri command `restart_backend_sidecar`）、复制日志
- **启动期路径诊断**：Tauri `setup` 中检测安装路径含非 ASCII / 含空格 / 父目录不可写时，emit `backend-warning` 让前端顶端横幅显式告警，主动暴露 README 长期文字警告但无防御的"中文路径"等坑

### Changed

- Whisper 默认模型 size 从 `medium`（~1.5GB）改为 `tiny`（~75MB）：新装用户没主动设置时不再卡在首次大模型下载；高精度可在「音频转写配置」页主动切
- 切到 `fast-whisper` / `mlx-whisper` 且当前 size 未下载时，「音频转写配置」页保存前 confirm 体积提示，并推荐改用在线引擎
- Tauri sidecar 启动逻辑抽出 `spawn_backend_sidecar()`；child handle 存进 `SidecarHandle` state 以支持后续 restart
- sidecar stdout/stderr emit 时不再用 `format!("'{}'", ...)` 包引号，原文直传（前端 hook 兼容旧格式兜底剥引号）

### Fixed

- WhisperTranscriber 在半成品模型目录上死循环报 `Unable to open file 'model.bin'`：判定从「目录存在」改为「`model.bin` 落盘」，半成品目录会被识别并重新下载（PR `fix/backend-deploy-resilience`）
- `/api/deploy_status` 在没装 torch 的部署上 `ModuleNotFoundError: No module named 'torch'` 500：torch 改 try/except，未装时返回 `{available: false, torch_installed: false}`；transcriber 配置 + ffmpeg 也都裹 try，单项失败不再打死整个监控页（同上 PR）
- `routers/config._check_whisper_model_exists` 同步改用 `model.bin` 判定，避免「已下载」状态在监控页误报

## [2.1.4] - 2026-05-07

CI 工程化修复，无运行时行为变化。

### Internal

- 桌面端 Tauri 构建矩阵去掉 Linux（`ubuntu-22.04 / x86_64-unknown-linux-gnu`）。Linux 桌面端构建持续 17m+，且无对应分发渠道；Linux 用户继续可以走 Docker 镜像 (`ghcr.io/jefferyhcool/opennoter`)
- commitlint workflow 去掉无效的 `firstParent` input（wagoid/commitlint-github-action@v6 不支持，被忽略并打 warn）
- 规范 release merge commit 标题：`chore(release): vX.Y.Z`（合 master）/ `chore(release): merge release/X.Y.Z back into develop`（回灌 develop），让 commitlint 能正确识别。`RELEASING.md` §3 与 `CONTRIBUTING.md` §6.3 同步更新

## [2.1.3] - 2026-05-07

### Fixed

- DeepSeek 等非多模态供应商被 400 拒绝（issue #282）：`UniversalGPT.create_messages` 与 `_build_merge_messages` 此前**无条件**把 content 拼成 OpenAI 多模态数组 `[{"type":"text",...}]`，DeepSeek `deepseek-chat` 等模型不识别 `image_url` 变体直接报 `invalid_request_error`。`GPTFactory.from_config` 一律实例化 `UniversalGPT`，所以问题覆盖**所有**通过模型设置页接入的非多模态供应商，不止 DeepSeek。
  - 现按 `video_img_urls` 是否非空切换 content 形态：有图保留多模态数组（视觉模型不退化），无图退回 string。合并阶段历来不带图，统一改 string。
  - 与同包内 `deepseek_gpt.py` / `openai_gpt.py` / `qwen_gpt.py` 的 message builder 行为对齐。
  - 新增 `backend/tests/test_universal_gpt_content_format.py` 6 个 case 回归覆盖（含 `image_url` 字面 not-in JSON 断言）。

感谢 @voidborne-d 的修复（#345）。

## [2.1.2] - 2026-05-07

补 v2.1.1 上 ghcr.io 镜像构建失败的坑。

### Fixed

- Docker 镜像构建失败：v2.1.1 tag 触发的 ghcr.io 推送在 frontend-builder 第 7/7 步 `pnpm run build` 挂掉（vite `loadConfigFromBundledFile` 加载 `@tailwindcss/vite` plugin 时 1.5s 内异常退出）。
  - `Dockerfile.complete` 与 `OpenNoter_frontend/Dockerfile` 升 `node:18-alpine` → `node:20-alpine`：Tailwind v4 已不再支持 Node 18，Vite 6 也推荐 Node 20+
  - `Dockerfile.complete` 的 frontend 阶段同时复制 `pnpm-lock.yaml` 并改用 `--frozen-lockfile`，杜绝每次构建重解析 semver 拉到比本地新的 native dep
  - `OpenNoter_frontend/pnpm-lock.yaml` 强制入库（之前一直未提交，导致 CI / 本地依赖图持续漂移）
- README 联系社区段补上微信群二维码（之前只写"年会恢复更新以后放出最新社区地址"）

## [2.1.1] - 2026-05-07

工程化与文档收尾，无运行时行为变化。

### Added

- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — 贡献指南，落地简化 Git Flow（master + develop + 短生命周期分支）+ 提交规范 + 合并规范
- [`RELEASING.md`](./RELEASING.md) — 发版手册（Release Manager 视角），含 release/* 流程 + 各商店人工上传步骤 + 自动发布所需 secrets
- `.github/ISSUE_TEMPLATE/{config,bug_report,feature_request}.yml` — 表单形式的 issue 模板，按工作区分类
- `.github/pull_request_template.md` — PR 模板，把 CONTRIBUTING §5.2 落成 checklist
- `.commitlintrc.json` + `.github/workflows/commitlint.yml` — commitlint CI（PR + push develop/master 时校验，自定义 type 白名单，兼容中文 subject）
- `.github/workflows/release-extension.yml` — `v*` tag push 时自动构建插件 .zip / .xpi / .crx 并挂到对应 GitHub Release（商店自动发布以注释形式预留）

### Changed

- 关于页二维码改为 `import @/assets/wechat.png`，不再依赖腾讯云 COS CDN，更新只需替换文件 + 跑构建
- 群聊 QR 替换为最新版本（`doc/wechat.png` + `OpenNoter_frontend/src/assets/wechat.png`）

### Removed

- 关于页 QQ 群联系方式（号 785367111，已不再活跃维护）
- 旧版 `.md` 格式 issue 模板（被新 yml 表单模板取代）

## [2.1.0] - 2026-05-07

本次发布的主线是**浏览器插件**和 **B 站字幕优先链路**。配合一些后端 / 前端体验修复。

### Added — 浏览器插件 (`OpenNoter_extension/`)

全新 Chrome / Edge / Firefox MV3 扩展。Vue 3 + Vite + UnoCSS，骨架基于 vitesse-webext。

- **入口四件套**
  - 工具栏 popup：识别当前 tab → 一键提交，紧凑展示标题 + 封面 + 进度
  - 视频页悬浮按钮：仅在支持平台注入，点击即触发任务
  - 右键菜单"用 OpenNoter 总结此视频"：限定 4 个支持域名
  - 侧边栏（side panel）：详情视图 + 三模式切换
- **侧边栏三视图**
  - Markdown：渲染笔记，复制 / 下载 .md
  - 思维导图：基于 markmap-lib + markmap-view 的可缩放 mind map
  - AI 问答：复用后端 RAG `/chat/index`、`/chat/status`、`/chat/ask` 三件套，自动索引 + 多轮历史
- **设置页五大块**（搬入 web 端全部配置能力，今后插件即配置中心）
  - 通用：后端地址、连通性测试、默认供应商 / 模型 / 画质 / 截图 / 跳转 / 风格
  - 模型供应商：完整 CRUD、启用切换、连接测试、模型增删
  - 音频转写配置：fast-whisper / mlx-whisper / Groq / 必剪 / 快手 切换、Whisper 模型大小、本地下载状态、触发下载
  - 下载配置：每平台 cookie 显示、浏览器一键同步、手动粘贴
  - 部署监控：后端 / FFmpeg / CUDA / Whisper 状态总览
- **浏览器 cookie 直通**：`chrome.cookies.getAll` 一键把 `.bilibili.com` 等域 cookie 同步到后端 `/api/update_downloader_cookie`
- **B 站字幕浏览器抓取**：插件直接调 player API 拿字幕，借 host_permissions 自动带本地登录态 cookie，绕过 CORS；随提交以 `prefetched_transcript` 字段附给后端，后端跳过 `download_subtitles` + 音频转写，直接进 GPT 总结

### Added — 后端

- `BilibiliSubtitleFetcher`（`app/downloaders/bilibili_subtitle.py`）：直接调 B 站 player API 拿字幕，作为非插件场景下 yt-dlp 路径的更可靠替代
- `VideoRequest.prefetched_transcript` 字段：客户端预取的字幕直接落到 `<task_id>_transcript.json`，NoteGenerator cache-hit 自动复用

### Added — 前端 Web

- Zustand persist 迁移到 IndexedDB（#318）

### Changed

- 后端 CORS：从静态 origin 列表改为 regex，兼容 `chrome-extension://`、`moz-extension://`、`localhost`、`tauri.localhost`
- mlx-whisper 仓库 ID 改用 `MLX_MODEL_MAP`：`whisper-{size}-mlx` 命名（`large-v3-turbo` 例外），不再 hardcode 出 404
- BilibiliDownloader 从 `CookieConfigManager` 读取 cookie 并注入 yt-dlp cookiefile（#333）
- CLAUDE.md 补充 v2.0.0 引入的子系统说明（RAG / Chat、可选 Nacos+RabbitMQ、i18n、cookie/transcriber 管理器）以及浏览器插件 workspace

### Fixed

- AILogo：自定义供应商（`logo='custom'`）走兜底渲染时不再误报 `console.error`，未匹配的名称降级为 warn
- SettingPage `Model.tsx` 双栏布局加 `min-h-0 overflow-y-auto`：供应商列表过长时无法滚动
- 供应商开关切换不能实时生效（#336）
- `/get_all_providers` 中 301 行历史伪内置脏数据清理 + `add_provider` 加防御（强制 `type='custom'`、同名查重、错误向上抛）
- `/api/task_status` 拆 ResponseWrapper：插件侧进度条因未拆 `data` 全灰；同时把 `R.error` 翻译为 `status:'FAILED'`，避免 UI 卡在轮询循环
- ESLint / ESM `__dirname` 在 production build 中未定义（多个 docker / vite 配置修复）
- GitHub Actions 构建错误 + apt-get 安装失败 + 删除仓库内 ffmpeg 二进制
- 渲染时剥掉 backend 注入的 `> 来源链接：URL` 行（与 web 端 MarkdownViewer 一致），导出文件保留原行便于溯源
- 侧边栏布局收紧：完成后不再渲染 8 段进度条；标题压成单行；视图切换 + 复制 / 下载并入一行；历史任务从底部 details 改为顶栏下拉

### Internal

- 新增分支策略：`develop` / `release/x.y.z` / `master` git-flow
- 备份 backend SQLite DB 前 / 清理脏数据后均落盘存档
