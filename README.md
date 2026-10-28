<p align="center">
  <img src="./doc/icon.svg" alt="OpenNoter" width="72" height="72" />
</p>

<h1 align="center">OpenNoter</h1>

<p align="center"><i>把视频变成结构化笔记的开源工具 · 可自部署、可离线、可接任意模型</i></p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/backend-FastAPI-009688.svg" alt="FastAPI" />
  <img src="https://img.shields.io/badge/frontend-React%2019-61dafb.svg" alt="React 19" />
  <img src="https://img.shields.io/badge/design-Spectrum%202-1473e6.svg" alt="Spectrum 2" />
  <img src="https://img.shields.io/badge/python-3.11-3776ab.svg" alt="Python 3.11" />
  <img src="https://img.shields.io/badge/node-20-339933.svg" alt="Node 20" />
</p>

---

## 这是什么

OpenNoter 抓取视频链接（或上传本地视频）后，取字幕或转写音频、交给大模型总结，产出一份结构清晰的 Markdown 笔记：带层级目录、关键帧截图、原片时间跳转。生成完的笔记会建立向量索引，可以直接对内容提问，回答附原文出处与时间点。

模型和转写引擎都能在设置里换。全用本地模型时，视频与文字都不出机器。

**已支持的能力：**

| 维度 | 现在支持什么 |
|---|---|
| 视频来源 | 哔哩哔哩、YouTube、抖音、快手、本地视频 |
| 音频转写 | Faster Whisper、MLX Whisper（本地）；Groq 兼容端点、必剪、快手（在线） |
| 大模型 | 任意 OpenAI 兼容接口；内置 7 个预设（OpenAI / DeepSeek / Qwen / Gemini / Groq / Ollama / Claude¹） |
| 笔记风格 | 精简、详细、教程、学术、小红书、生活向、任务导向、商业风格、会议纪要 |
| 笔记格式 | 目录、原片跳转、原片截图、AI 总结（提交前勾选） |
| 产出 | Markdown 预览、复制全文、导出 `.md`、思维导图（可导出 XMind / SVG / PNG） |
| 问答 | 基于 ChromaDB 向量检索 + Function Calling，回答带引用来源 |
| 多用户 | 账号登录、OIDC 单点登录、API Key、配额、部署监控 |

> **注意**：笔记导出目前只有 Markdown。PDF / Word / Notion 导出**尚未实现**（后端有未接线的导出模块，前端没有入口）。

<sub>¹ 内置预设只是**空的配置行**：不含 API Key，也不带模型列表。填上 Key 后模型需要单独添加，或从供应商的 `/v1/models` 拉取。`Claude` 走的是 Anthropic 的 OpenAI 兼容端点（`https://api.anthropic.com/v1`）。</sub>

## 架构

```
                        ┌──────────────────────────────────────────┐
  浏览器 ───────────────▶│  React 19 + Vite（开发 3015 / 容器内 nginx）│
  桌面端 ───────────────▶│  Tauri 2 外壳                             │
  浏览器插件 ───────────▶└───────────────┬──────────────────────────┘
                                        │ /api  /static
                        ┌───────────────▼──────────────────────────┐
                        │  FastAPI（8483）                          │
                        │  ├─ downloaders/  平台适配器（yt-dlp 等）  │
                        │  ├─ transcriber/  转写引擎工厂            │
                        │  ├─ gpt/          OpenAI 兼容 + 分块      │
                        │  ├─ services/     流程编排 / RAG / 鉴权    │
                        │  └─ db/           SQLite（WAL）           │
                        └───────────────┬──────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
        data/open_noter.db       note_results/*.json         vector_db/（Chroma）
        配置与凭据（加密）        笔记正文                    笔记向量索引
```

**笔记生成流水线**（状态可在前端轮询看到）：

```
PENDING → PARSING → DOWNLOADING → TRANSCRIBING → SUMMARIZING → SAVING → SUCCESS
                        │              │
                        │              └── 站点有字幕时跳过（不下载音频、不转写）
                        └── 需要截图/多模态理解时才下载视频并抽帧
```

（代码里还定义了一个 `FORMATTING` 状态，但没有任何路径会写入它，属于历史遗留，可以忽略。）

技术栈：后端 FastAPI + SQLAlchemy + SQLite（WAL）+ ChromaDB；前端 React 19 + Vite + Adobe Spectrum 2；桌面端 Tauri 2（后端以 sidecar 进程随应用启动）；插件 Vue 3 + Manifest V3。

## 先看这个：首次部署会遇到的三件事

这三点在代码里是真实现状，但很容易踩坑，所以放在最前面。

### 1. 认证默认开启，而且没有注册入口

`OPENNOTER_AUTH_ENFORCE` 默认 `true`。全新部署的库里**一个用户都没有**，也没有 `/register` 接口——必须先跑 CLI 建号，否则登录页会一直拒绝你。

```bash
# 在 .env 里设置签名密钥（不设会在登录时报错）
OPENNOTER_JWT_SECRET=<长随机串>

# 生成方式
python -c "import secrets;print(secrets.token_urlsafe(48))"

# compose 部署：创建首个管理员
docker exec -it opennoter-backend python manage.py create-user you@example.com --admin
```

`OPENNOTER_JWT_SECRET` 与 `OPENNOTER_SECRET_KEY` 二者至少设一个。同一密钥经 HKDF 派生后还用于静态加密（平台 Cookie、Provider 的 api_key）。**换密钥会导致已有密文无法解密**，届时需要重新填写 Cookie 与 api_key。

单机内网自托管可以明确关闭认证：

```bash
OPENNOTER_AUTH_ENFORCE=0   # 仅限内网；公网绝对不要关
```

### 2. 后端起来的 URL 不是 `http://localhost`

两种部署方式的端口不一样，容易混淆：

| 部署方式 | 访问地址 | 说明 |
|---|---|---|
| `docker run -p 80:80`（单镜像） | `http://localhost` | 一个容器内含 nginx + 后端 |
| `docker compose up`（多容器） | **`http://localhost:3015`** | 端口来自 `.env` 的 `APP_PORT` |
| 源码开发 | `http://localhost:3015` | Vite 开发服务器 |

### 3. `docker compose` 前必须先有 `.env`

compose 里的 `${BACKEND_PORT}`、`${APP_PORT}` 没有默认值，缺 `.env` 会直接启动失败：

```bash
cp .env.example .env
```

另外 `BACKEND_PORT` 保持 `8483`——nginx 配置里硬编码了这个端口。

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
git clone https://github.com/raydiodroid/OpenNoter.git
cd OpenNoter
cp .env.example .env
docker compose up --build -d

# 创建首个管理员（否则无法登录）
docker exec -it opennoter-backend python manage.py create-user you@example.com --admin
```

访问 `http://localhost:3015`。

**GPU 加速转写**（可选，需要 NVIDIA 显卡与 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)）：

```bash
docker compose down                                       # 两套编排用相同的容器名，先停
docker compose -f docker-compose.gpu.yml up --build -d
```

GPU 栈只影响本地 Whisper 转写；在线转写引擎与 GPU 无关。确认是否真的走了 GPU：

```bash
docker logs opennoter-backend | grep -i cuda   # 跑过一次本地转写后出现 "CUDA 可用，使用 GPU"
```

这行日志由转写器初始化时打印，所以要先实际跑一个本地转写任务才会出现（不是启动时）。

**国内拉不到 docker.io** 时，所有 Dockerfile 都暴露了 `BASE_REGISTRY` / `APT_MIRROR` / `PIP_INDEX` 构建参数：

```bash
BASE_REGISTRY=docker.m.daocloud.io docker compose build
docker compose up -d
```

或写进 `.env` 长期生效。也可以用宿主机的 Docker 镜像加速器（`~/.docker/daemon.json` 的 `registry-mirrors`），那是更一劳永逸的做法。

### 方式二：源码运行

**后端**（Python 3.11）：

```bash
cd backend
pip install -r requirements.txt
python main.py            # 监听 0.0.0.0:8483
```

**前端**（Node 20）：

```bash
cd OpenNoter_frontend
npm ci                    # 仓库只跟踪 package-lock.json
npm run dev               # http://localhost:3015
```

开发服务器会把 `/api` 和 `/static` 代理到 `127.0.0.1:8483`。

首次运行同样需要建账号：

```bash
cd backend
python manage.py create-user you@example.com --admin
```

**其他常用命令：**

```bash
# 后端测试（pytest 不在 requirements.txt 里，需自行安装）
pip install pytest
cd backend && python -m pytest

# 前端构建与检查
cd OpenNoter_frontend && npm run build && npm run lint
```

### 方式三：桌面客户端

macOS 与 Windows 都有安装包，在 [Releases](https://github.com/raydiodroid/OpenNoter/releases) 页面下载。桌面版内置后端（sidecar 进程），不用另开服务。

**Windows 上请装在「不含中文与空格、且可写」的路径**，否则后端可能起不来。桌面版启动时也会自己检测这类路径并给出横幅告警。

自己构建：

```bash
cd backend && ./build.sh                  # PyInstaller 打包后端（Windows 用 build.bat）
cd ../OpenNoter_frontend
npm ci
npx tauri build                           # tauri-cli 是本仓库的 devDependency，npx 会用本地版本
```

桌面端和网页端一样用 npm（`tauri.conf.json` 里的 `beforeBuildCommand` 就是 `npm run build`）。

### 方式四：浏览器插件

```bash
cd OpenNoter_extension
pnpm install
pnpm build                # 产物在 OpenNoter_extension/extension/
```

然后在 `chrome://extensions/` 打开开发者模式，加载已解压的扩展，选中 `OpenNoter_extension/extension/` 目录。

插件支持：工具栏 popup 生成笔记、视频页悬浮按钮、右键菜单、侧边栏（Markdown / 思维导图 / AI 问答）、浏览器 Cookie 一键同步、B 站字幕直接抓取。

## 依赖

### FFmpeg（必需）

音频处理、截图都要 FFmpeg。**仓库不附带 FFmpeg 二进制**，任选一种方式装：

```bash
# 系统包管理器（推荐）
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu / Debian
```

```bash
# Windows / Linux 可以用脚本按需下载到 ./vendor/ffmpeg，然后指定路径
# （macOS 没有官方静态包，脚本只会提示用 brew）
python scripts/fetch_ffmpeg.py
# 在 .env 里设置：
FFMPEG_BIN_PATH=/path/to/vendor/ffmpeg/bin
```

Docker 镜像内已内置 FFmpeg，无需额外安装。缺失时转写与截图功能不可用，监控页的 FFmpeg 卡片会标红。

**许可证**：官方预编译的 FFmpeg 二进制多数是 GPL 构建（含 `libx264`/`libx265`），而本项目是 MIT，**不覆盖 FFmpeg**。因此仓库刻意不提交二进制。商业闭源分发请改用 LGPL 构建（`--disable-gpl --disable-nonfree`），或只调用用户环境里已安装的 FFmpeg。

### Python / Node

- Python **3.11**（所有 Dockerfile 与 CI 都用这个版本）
- Node **20**（前端构建；插件同样）
- 前端包管理器用 **npm**（`package-lock.json` 是唯一跟踪的 lockfile）；**插件用 pnpm**

### CUDA / GPU（可选）

本地 Faster Whisper 可用 NVIDIA GPU 加速，其余在线引擎与 GPU 无关。宿主机需要：

1. NVIDIA 显卡 + 驱动（CUDA ≥ 12.4），`nvidia-smi` 正常输出
2. [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)，装完验证：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

能列出显卡即可。后端会自动检测 CUDA，可用就走 GPU，否则回退 CPU，不需要手动配置 device。

## 数据与备份

### 数据存在哪

**compose 部署**只挂载数据子目录，代码在镜像里：

| 宿主机路径 | 内容 |
|---|---|
| `./backend/data/` | SQLite 数据库 `open_noter.db` + 生成的笔记 |
| `./backend/config/` | 转写器设置、平台 Cookie（加密）、代理配置 |
| `./backend/static/` | 笔记引用的视频截图 |
| `./backend/uploads/` | 上传的本地视频 |
| `./backend/note_results/` | 每个任务的笔记正文 JSON |
| `./backend/vector_db/` | ChromaDB 向量索引 |
| `./backend/models/` | Whisper 模型缓存（可选，删了会重新下载） |
| `./backend/logs/` | 运行日志 |

> ⚠️ **不要**把整个后端目录挂进去（`-v ./backend:/app`）。那样会把代码、数据库、密钥、笔记混在一起，既难备份也容易在升级时被旧代码盖住。只挂上面这些数据子目录。

### 单镜像部署（`docker run`）

仓库根目录的 `Dockerfile.complete` 会把后端、前端与 nginx 打进同一个镜像（CI 按 tag 发布会推到 `ghcr.io/<owner>/<repo>`）。

容器内工作目录是 `/app/backend`，**所以卷映射要写成 `/app/backend/...`**（和 compose 的 `/app/...` 不同，两者混淆是最常见的部署错误）：

```bash
docker build -f Dockerfile.complete -t opennoter .

docker run -d -p 80:80 \
  -v opennoter-data:/app/backend/data \
  -v opennoter-config:/app/backend/config \
  -v opennoter-static:/app/backend/static \
  -v opennoter-models:/app/backend/models \
  -v opennoter-uploads:/app/backend/uploads \
  -v opennoter-vector:/app/backend/vector_db \
  --name opennoter \
  opennoter
```

单镜像的数据库在 `/app/backend/data/open_noter.db`，备份与恢复命令里的路径要相应替换。

### SQLite 备份（注意 WAL）

后端启用了 SQLite **WAL** 模式，库里会出现伴生文件：

```
open_noter.db        ← 主库
open_noter.db-wal    ← 预写日志（未落盘的事务）
open_noter.db-shm    ← 共享内存索引
```

> ⚠️ **只 `cp open_noter.db` 会拿到不一致的快照** —— 最近提交的事务可能还在 `-wal` 里。

正确做法（在线热备，不用停服）：

```bash
docker exec opennoter-backend python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/open_noter.db'); \
   c.execute(\"VACUUM INTO '/app/data/backup-20260101.db'\"); c.close()"

mv backend/data/backup-20260101.db /backup/
```

`VACUUM INTO` 会写出一份已合并 WAL、无碎片的独立副本。单镜像部署请把路径换成 `/app/backend/data/open_noter.db`。

恢复：

```bash
docker compose stop backend
mv /backup/backup-20260101.db backend/data/open_noter.db
rm -f backend/data/open_noter.db-wal backend/data/open_noter.db-shm   # 必须删，否则会重放不匹配的日志
docker compose start backend
```

完整备份还应包含数据卷本身：

```bash
tar czf opennoter-backup-$(date +%F).tar.gz \
  backend/data backend/config backend/note_results backend/static backend/uploads backend/vector_db
```

### 为什么用 WAL

默认的 `journal_mode=DELETE` 在写事务期间连读都会被阻塞，高频轮询时容易出现 `database is locked`。本项目有多个任务 worker 频繁写状态、前端每 3 秒轮询、每 30 秒拉列表，属于典型写密集 + 高频读。WAL 让读走快照、写只锁 WAL 文件，两者互不阻塞。

相关 PRAGMA：`journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`、`foreign_keys=ON`。

## 从旧版本升级

### 数据库位置变了

默认库路径从 `open_noter.db`（代码根目录）改到 `data/open_noter.db`（data 卷）。程序内有自动迁移，但**Docker 部署必须手动搬一次**——旧库在 `./backend/open_noter.db`，不在挂载范围内，容器内看不到它：

```bash
cd backend
cp -a open_noter.db data/open_noter.db
python3 -c "import sqlite3;print(sqlite3.connect('data/open_noter.db').execute('PRAGMA integrity_check').fetchone())"
# 应输出 ('ok',)
docker compose up -d --force-recreate
```

### 认证默认值变了

`OPENNOTER_AUTH_ENFORCE` 从 `false` 改为 `true`。升级后未带凭据的请求一律 401。如果此前一直匿名使用，需要先建账号（见前面的「首次部署」），或明确设 `OPENNOTER_AUTH_ENFORCE=0` 保留旧行为。

完整变更见 [CHANGELOG.md](./CHANGELOG.md)。

## 配置

所有配置走根目录 `.env`（从 `.env.example` 复制）。**LLM 的 API Key 不要写进 `.env`**，在前端「模型配置」页面录入，会保存到数据库并加密。

几个容易搞混的点：

- **两类变量**：`VITE_*` 是构建时变量（会被打进前端 bundle），改完需要重新构建前端；其余是运行时变量，改完重启容器即可。
- **两级命名**：一部分变量读 `OPENNOTER_` 前缀（如 `OPENNOTER_AUTH_ENFORCE`），另一部分读裸名（如 `BACKEND_PORT`、`DATABASE_URL`、`WHISPER_MODEL_SIZE`）。`.env.example` 里的写法就是正确的写法，照着填即可。
- **旧的 `BILINOTE_` 前缀仍然兼容**，会带一条警告。

**常用的几组：**

| 变量 | 默认 | 作用 |
|---|---|---|
| `BACKEND_PORT` | `8483` | 后端端口。**compose 下不要改**，nginx 硬编码 |
| `APP_PORT` | `3015` | compose 对外暴露的端口 |
| `OPENNOTER_AUTH_ENFORCE` | `true` | 是否强制认证 |
| `OPENNOTER_JWT_SECRET` | 无 | JWT 签名 + 凭据加密密钥，**实际必需** |
| `OPENNOTER_COOKIE_SECURE` | `true` | Cookie 是否带 Secure。纯 HTTP 局域网部署要设 `false`，否则登录态存不下 |
| `OPENNOTER_CORS_EXTENSION_IDS` | 无 | 允许的浏览器扩展 ID（逗号分隔），插件对接生产后端需要 |
| `DATABASE_URL` | `sqlite:////app/data/open_noter.db` | 数据库连接串（多副本改 Postgres） |
| `TRANSCRIBER_TYPE` | `fast-whisper` | `fast-whisper` / `mlx-whisper` / `bcut` / `kuaishou` / `groq` |
| `WHISPER_MODEL_SIZE` | `tiny` | 本地转写模型规格，首次用建议先 tiny |
| `DOWNLOAD_MAX_SIZE_MB` | `200` | 单文件下载上限，0 = 不限 |
| `DOWNLOAD_MAX_DURATION_SECONDS` | `300` | 视频时长上限，0 = 不限 |
| `UPLOAD_MAX_SIZE_MB` | `500` | 本地上传文件上限（需同步调 nginx） |
| `OPENNOTER_USER_DAILY_TASK_LIMIT` | `50` | 每用户 24h 任务数上限 |
| `FFMPEG_BIN_PATH` | 无 | 指定 FFmpeg 路径，不走系统 PATH |
| `OPENNOTER_BYOK_MODE` | `shared` | BYOK 初始模式（`shared`/`optional`/`required`）。仅在没有 `config/byok.json` 时生效 |
| `OPENNOTER_CREDENTIAL_SECRET` | 无 | BYOK 凭据加密密钥。不设则从 JWT_SECRET 派生；**建议单独设**，否则换 JWT 会连带失效 |

`.env.example` 里有逐项注释，完整清单看那里。

### 用户自带凭据（BYOK）

默认 `shared`：所有用户共用你在「设置 → 模型配置」里填的模型 Key 与平台 Cookie，
与不启用 BYOK 时**完全一致**。适合个人自托管。

需要让用户用自己账号/额度时，在管理界面切换模式，或写 `backend/config/byok.json`：

```json
{ "mode": "optional" }
```

| 模式 | 行为 | 适用 |
|---|---|---|
| `shared` | 只用站点配置 | 个人自托管（默认） |
| `optional` | 站点是默认档位；用户配了自己的就用自己的 | 公司/社区内部 |
| `required` | 必须配自己的模型凭据，否则拒绝提交任务 | 对外服务，平台不承担 API 成本 |

两种粒度，用户可以分别配置：

- **模型凭据**（按能力位）：笔记分析 / 音频转写。端点 + API Key + 模型名。
- **平台 Cookie**（按平台）：B 站 / 抖音 / 快手 / YouTube。用于画质与字幕增强。

> Cookie 值得单独说：B 站 `SESSDATA` 是**登录身份**而不是额度。多用户共用一份
> Cookie，站点侧看到的是「同一个人异地看各种视频」——既混观看历史，也可能触发风控。
> 所以开了 BYOK 之后，用户配自己的 Cookie 比配自己的 API Key 更有意义。
>
> 注意 `required` **只约束模型凭据，不约束 Cookie**：Cookie 不花钱，强制会与它的
> 可选定位矛盾。

安全设计：凭据与 Cookie 都用 AES-GCM 加密落库，且密文**绑定到
`(用户, 能力位/平台)`**——把一行密文复制给另一个用户解不开。

用户入口：「设置 → 我的凭据」。模式切换入口（管理员）：「设置 → 模型配置」。

## HTTP API

后端提供完整 HTTP API，给个视频链接就能异步生成笔记。

**自描述入口**（免鉴权，推荐第一次就看它）：

```bash
curl https://your-host/api/                       # JSON 指引
curl -H 'Accept: text/markdown' https://your-host/api/    # Markdown 版本
```

返回鉴权方式、完整流程、端点清单和响应约定——端点清单直接从路由表生成，不会过期。

**鉴权**：所有业务接口都需要凭据。网页登录态是 JWT Cookie；脚本 / agent 用 API Key，两种请求头等价：

```bash
curl -H 'x-api-key: sk-bili-...'      https://your-host/api/notes
curl -H 'Authorization: Bearer sk-bili-...'  https://your-host/api/notes
```

API Key 在网页「设置 → 密钥管理」页创建，明文只在创建时返回一次。支持吊销、轮换（带宽限期）、克隆、IP 白名单、每分钟限流。

**最小流程**（建任务 → 轮询 → 取结果）：

```bash
# 1. 建任务：只要给 url，平台自动识别，模型缺省自动选
curl -X POST https://your-host/api/notes \
  -H 'x-api-key: sk-bili-...' -H 'Content-Type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV1LLnj6fE1z"}'
# → {"code":0,"msg":"success","data":{"task_id":"..."}}

# 2. 轮询：永远返回 200，任务状态看 data.status
curl -H 'x-api-key: sk-bili-...' https://your-host/api/tasks/<task_id>
# → PENDING → PARSING → DOWNLOADING → … → SUCCESS / FAILED
# → 成功时 data.result.markdown 是笔记正文
```

**主要端点：**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/notes` | 创建笔记任务（只需 `url`） |
| GET | `/api/tasks` | 列出自己的任务 |
| GET | `/api/tasks/{id}` | 查询状态与结果 |
| DELETE | `/api/tasks/{id}` | 删除任务及其文件、向量索引 |
| POST | `/api/upload` | 上传本地视频 |
| POST | `/api/chat/index` | 为笔记建立向量索引 |
| POST | `/api/chat/ask` | 对笔记内容提问（带引用） |
| GET | `/api/keys` | API Key 管理（另有 rotate / clone / revoke / usage） |
| GET | `/api/monitor/status` | 部署监控（仅管理员） |

**响应约定**：统一 `{code, msg, data}`。`code == 0` 表示成功。HTTP 状态码同时表达成败（2xx / 4xx / 5xx），可以直接据此判断。401 响应里会带 `auth_hint` 说明怎么带凭据。

**完整 schema**：`GET /openapi.json`（需凭据），`GET /docs` 是 Swagger UI（需凭据）。

## 部署运维

### 启动失败怎么排查

后端启动会按顺序打印 `[startup 1/5]` … `[startup 5/5] 启动完成`，卡在哪一步就是哪一步的问题：

```bash
docker logs -f opennoter-backend
```

- **`[startup 3/5]` 之后马上退出**：这一行本身只读取并打印当前转写器配置，不会因为配置写错而失败。真正会在这里抛错的是数据库不可写、或 `config/` 目录权限问题。`TRANSCRIBER_TYPE` 写错的表现是**启动正常、第一次生成任务时失败**（未知取值会静默回退到 `fast-whisper`）。

  ```bash
  docker logs opennoter-backend | grep "当前转写器"   # 确认实际生效的 type 与 model_size
  ```

  `mlx-whisper` 只能在 Apple Silicon 上用，Linux / Docker 请用 `fast-whisper` 或 `groq`。
- **首次跑视频时容器被 kill**：Whisper 模型下载触发 OOM。先把 `WHISPER_MODEL_SIZE` 改成 `tiny`，跑通后再在前端逐档升。CPU 编排默认 `mem_limit: 4g`，选中等以上模型建议调到 8g+。
- **前端空白页 / 502**：通常是 nginx 起来了但 backend 还没 healthy。`docker ps` 看 backend 的 STATUS，长期 unhealthy 就按上面查后端日志。

### 改了 `.env` 没生效

- `VITE_*`：构建时变量，需要重新构建前端镜像。
- 其他后端变量：`docker compose up -d` 重启即可。
- LLM API Key：不在 `.env` 里，在前端页面改。

### 不要用 `restart: on-failure:N`

任何 N 次连续崩溃都会让容器永远不再启动，之后改 `.env` 也没用。本项目自带编排统一用 `unless-stopped`。

### 多副本 / 水平扩展

默认是**单副本 + SQLite**。限流、OIDC state、笔记任务都在进程内，**不能**直接 `replicas: N`。要多副本至少需要：

1. `DATABASE_URL` 换成 Postgres（SQLAlchemy 已支持非 SQLite 连接池）
2. 外置任务队列与共享限流（Redis 等）——当前未内置，需自建
3. 数据卷改用共享存储（NFS / 对象存储）

未满足前请保持单副本。

### 任务中断与队列

笔记任务跑在 FastAPI `BackgroundTasks` + 进程内线程池里，**没有**独立持久化队列（没有 Celery / Redis）。折中方案：

- 进程优雅退出或下次启动时，会把仍处于中间态的任务标为 `FAILED`，避免前端永远轮询。
- **不**自动续跑，需要用户重新提交。

可靠投递请改用外部队列 + Postgres。

## 项目结构

```
OpenNoter/
├── backend/                    FastAPI 后端
│   ├── main.py                 入口：启动序列、CORS、静态文件鉴权路由
│   ├── app/
│   │   ├── routers/            API 路由（note / chat / config / auth / api_keys / monitor / provider / model）
│   │   ├── services/           业务编排：note 流水线、RAG、鉴权、配额、Cookie、代理
│   │   ├── downloaders/        平台适配器（bilibili / youtube / douyin / kuaishou / local）
│   │   ├── transcriber/        转写引擎工厂 + 各引擎实现
│   │   ├── gpt/                OpenAI 兼容客户端、分块、提示词、Function Calling
│   │   ├── db/                 SQLAlchemy 模型与 DAO
│   │   ├── utils/              截图、导出、响应包装、路径安全、URL 校验
│   │   └── middlewares/        API Key 用量统计
│   ├── tests/                  后端测试
│   ├── manage.py               用户管理 CLI（建号 / 改密 / 停用 / 提权）
│   └── migrate_users.py        建表 + 首位管理员（幂等）
├── OpenNoter_frontend/         React 前端
│   ├── src/pages/workbench/    工作台：表单、预览、原文参照、问答、思维导图
│   ├── src/pages/settings/     设置：模型、转写、下载、密钥、用户、监控、关于
│   ├── src/components/         页面骨架、状态指示、错误边界等
│   ├── src/store/              Zustand 状态（任务、模型、认证、主题）
│   └── src-tauri/              桌面端外壳
├── OpenNoter_extension/        浏览器插件（Vue 3 + MV3）
├── nginx/                      反代配置（compose 与单镜像各一份）
├── doc/                        图标与截图
├── scripts/                    fetch_ffmpeg / cleanup_cache
└── docker-compose.yml          CPU 编排（另有 .gpu.yml）
```

## 已知限制

诚实列出当前没做的事：

- **导出只有 Markdown**。PDF / Word / Notion 未实现。
- **笔记「多版本」是前端的**。重新生成时旧版本留在浏览器 IndexedDB，服务端每个任务只存一份 Markdown。
- **没有持久化任务队列**。进程重启时进行中的任务标记为 FAILED，不自动续跑。
- **小宇宙（xiaoyuzhoufm）下载器是占位实现**，未接入平台工厂。
- **没有前端测试**。后端有 pytest 测试集。
- **只支持中文界面**，未做国际化。

## 参与贡献

见 [CONTRIBUTING.md](./CONTRIBUTING.md)（分支模型、提交规范、PR 流程）。简要说：

- 后端：Python 3.11，改动至少让 `python -m py_compile` 通过，跑 `pytest`
- 前端 / 插件：改动需 `npm run build`（前端）或 `pnpm typecheck && pnpm build`（插件）通过
- 提交信息遵循 Conventional Commits（有 commitlint CI 校验）

更多开发细节见 [CLAUDE.md](./CLAUDE.md)。

## 许可证

[MIT License](./LICENSE) · Copyright (c) 2024 Jeffery Huang · Copyright (c) 2026 raydiodroid

本项目基于 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) 演进而来，感谢原作者与所有贡献者。

FFmpeg 不在本许可证覆盖范围内，详见上方「依赖 → FFmpeg」。

---

如果这个项目对你有帮助，欢迎 Star ⭐ 或提 issue。
