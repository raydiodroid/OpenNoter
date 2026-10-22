# OpenNoter 架构与功能隐患审查报告

| 项 | 内容 |
|---|---|
| 审查对象 | 本机 `…/BiliNote`（分支 `squash-release`） |
| origin | https://github.com/raydiodroid/OpenNoter.git |
| upstream | https://github.com/JefferyHcool/BiliNote.git |
| 范围 | `backend/` · `OpenNoter_frontend/` · `OpenNoter_extension/` · `nginx/` · `docker-compose*.yml` |
| 方式 | 只读静态审查；未改代码；未读取真实 `.env` 密钥；未做攻击性验证 |
| 日期 | 2026-10-23（Asia/Shanghai） |
| 用途 | 供其他 agent / 人工交叉验证与评估修改方案 |

---

## 0. 给交叉验证方的说明

1. 本报告基于代码路径与机制推断，**不是**已运行的 exploit 结果。
2. 请优先复核 **§1 P0** 三条：对照所列文件，确认逻辑仍成立后再改。
3. 修改评估建议：先闭环 P0，再开强制认证与密钥加密，再收插件/CORS；避免一次大 refactor 掩盖回归。
4. 仓库当前分支为 `squash-release`，验证时请注明对照的 commit / 分支。

---

## 1. 最高优先级摘要（P0）

### P0-1｜默认未强制鉴权 + 任务归属规则互相打架

- **现象**：`AUTH_ENFORCE` 默认 `False`，匿名可创建任务（`user_id=None`），但 `_can_access_task*` 对「无归属任务」只放行管理员 → 匿名轮询 / 列表 / 删除一律 403。
- **用户可见后果**：前端轮询吞错后任务「永远生成中」。
- **位置**：`services/auth.py`（`AUTH_ENFORCE` / `auth_enforced`）；`routers/note.py`（`_submit_note_task`、`_can_access_task*`）；前端 `useTaskPolling.ts`（失败 `catch` 空吞）。
- **测试固化**：`test_task_list_api.py`、`test_multiuser_security.py`（匿名看不到有归属任务 / 不能删）。
- **修复方向（供评估，非处方）**：公网默认强制认证；或明确「无归属任务仅凭 task_id（+短时 token）可读」，禁止「能创建却不能读」的中间态。

### P0-2｜上传接口文件名未净化 + 静态挂载 `/uploads`

- **现象**：`POST /api/upload` 直接用 `file.filename` 落盘；`main.py` 将 `/uploads` 挂为 `StaticFiles`（无鉴权）；`LocalDownloader` 对 `/uploads/...` 做 `normpath`，未强制解析后路径仍在 uploads 根内。
- **影响面**：路径穿越 / 任意写与读本地文件的组合面；再进入转写 / ffmpeg 链路风险升高。
- **位置**：`routers/note.py`（`upload`）；`downloaders/local_downloader.py`；`main.py`（StaticFiles）。
- **修复方向**：强制 `Path(filename).name` + UUID 存储名；`resolve()` 必须落在 uploads 根下；限制类型/大小；`/uploads` 勿对匿名全局可读或改为鉴权下载。

### P0-3｜结果文件无归属校验的旁路（IDOR）

- **现象**：`get_task_status` 在「无 status 文件、仅有 `{task_id}.json`」时直接返回全文，不做 owner 检查。
- **影响**：知道 UUID 即可读笔记；UUID 虽难猜，但日志、分享、插件存储、代理日志可能泄露 `task_id`。
- **位置**：`routers/note.py` `get_task_status`（报告审查时约 709–717 行，请以当前文件为准）。
- **修复方向**：无 status 时同样做归属/鉴权；或禁止无 status 的结果对外暴露。

---

## 2. 整体架构与数据流

### 2.1 组件职责

| 组件 | 职责 |
|------|------|
| **backend**（FastAPI，`backend/main.py` → `app/__init__.py`） | 鉴权（JWT / Cookie / API Key）、下载、转写、LLM 笔记、任务状态、RAG 问答、Cookie/转写/代理配置、监控 |
| **OpenNoter_frontend**（React 19 + Vite，可选 Tauri） | 工作台提交/轮询、设置、历史列表；Zustand + IndexedDB |
| **OpenNoter_extension**（Vue3 MV3） | 读活动页平台、可选抓字幕、同步站点 Cookie、调同一套后端 API |
| **nginx** | 反代 frontend + `/api` + `/static`；**不代理 `/uploads`** |
| **存储** | SQLite（`open_noter.db`）、`note_results/`、`uploads/`、`static/screenshots/`、`vector_db/`（Chroma）、加密 Cookie 文件 `config/downloader.json` |

### 2.2 主数据流（笔记生成）

```
用户/插件提交 URL（或 /uploads 本地路径）
  → POST /api/notes 或 /api/generate_note
  → 配额校验 → PENDING 落盘 → BackgroundTasks
  → ConcurrentTaskExecutor 线程池
  → Downloader（bilibili/youtube/douyin/kuaishou/local）
  → Transcriber（whisper/groq/bcut…；或 prefetched_transcript 跳过）
  → GPTFactory 生成 Markdown（可选截图/链接后处理）
  → note_results/{task_id}.json + SUCCESS
  → 可选 VectorStoreManager.index_task
前端/插件每 ~3s GET /api/tasks/{id} 或 /api/task_status/{id}
```

插件可附带 `prefetched_transcript`，写入带指纹的转写缓存，跳过下载转写。

### 2.3 鉴权模型

- 三通道：`x-api-key` / `Authorization: Bearer`（JWT 或 `sk-bili-`）/ Cookie `opennoter_token`
- 业务路由普遍挂 `get_optional_user`；写配置等用 `require_admin`
- **`auth_enforced()` 默认 False**：未登录时依赖返回 `None` 而不是 401
- API Key **等价于用户全权**（含 admin key 可改全局配置）

---

## 3. 架构隐患

| ID | 问题 | 位置 | 影响 |
|----|------|------|------|
| A1 | 单进程 + SQLite + 内存限流 / OIDC state | `engine.py`、`api_key_runtime`、`auth.py` `_OIDC_STATES` | 多副本无法水平扩展；重启丢限流与 OIDC state；写并发与线程池叠压 |
| A2 | compose 整目录挂载 `./backend:/app` | `docker-compose.yml` | 代码与数据、模型、日志、DB 混卷；备份易泄露密钥/Cookie/笔记 |
| A3 | BackgroundTasks + 线程池 `future.result()` 阻塞 | `routers/note.py`、`task_serial_executor.py` | 长时间占线程；进程退出易丢「已返回 task_id 未跑完」；无持久化队列 |
| A4 | 状态双写：文件为主、DB 为辅 | `NoteGenerator._update_status`、`list_tasks` | 列表/删除/指纹命名不一致 → 幽灵任务或残留缓存 |
| A5 | nginx `client_max_body_size 10G` vs 下载默认 200MB/300s | `nginx/*.conf`、`.env.example` | 入口与业务限额不一致，可先打满磁盘/内存 |
| A6 | 后端默认 `0.0.0.0:8483` | `main.py`、`.env.example` | 本地直跑业务口直暴露 |
| A7 | 扩展权限面极大 | `manifest.ts`：`*://*/*`、`cookies`、`<all_urls>` | 与「几家视频站 + 本机后端」不匹配，扩大攻击面 |

---

## 4. 功能隐患

### 4.1 配额超限返回值被当成 task_id（P1）

- `_submit_note_task` 标注返回 `str`，配额失败时 `return R.error(...)`（JSONResponse）。
- 调用方再 `R.success({"task_id": task_id})` → 客户端可能收到「成功包装里嵌着错误」。
- **位置**：`routers/note.py` `_submit_note_task` 及调用处。

### 4.2 删除任务清理不完整（P1）

- `_purge_task` 只删：`.status.json`、`.json`、`_transcript.json`。
- 实际还有 `{task_id}_{fingerprint}_audio/transcript/markdown` 及 legacy `_audio.json` / `_markdown.md`（`NoteCachePaths`）。
- **后果**：磁盘与敏感转写残留；配额统计偏低。

### 4.3 插件轮询未带 API Key（P1）

- `generateNote` 走带 `x-api-key` 的 `request()`，`getTaskStatus` 裸 `fetch`。
- **位置**：`OpenNoter_extension/src/logic/api.ts`
- **后果**：强制认证开启后「创建成功、进度永失败」。

### 4.4 Chat / 索引无任务归属校验（P1）

- `/api/chat/ask`、`/api/chat/index` 只认 `task_id`，不走 `_can_access_task`。
- **位置**：`routers/chat.py`、`services/chat_service.py`
- **后果**：知道 ID 即可对他人笔记做 RAG（耗 LLM、泄内容）。

### 4.5 其它一致性（P2）

- 配额检查与写 PENDING 非原子 → 并发可突破上限
- 匿名不配额（`check_submit_quota` 对 `user is None` 直接 return）→ 未强制认证时无刹车
- nginx 无 `/uploads` location → 经 3015 访问时本地上传易断（直连 8483 才通）
- 向量索引内存 `_index_status` 重启即丢

---

## 5. 安全发现（仅问题与影响，不含利用步骤）

| ID | 级别 | 问题 | 位置 / 备注 |
|----|------|------|-------------|
| S1 | P0 | 上传 + 本地路径组合面 | 见 P0-2 |
| S2 | P0 | 结果文件 IDOR 旁路 | 见 P0-3 |
| S3 | P0/P1 | 默认不强制登录即可调笔记/chat 等 | 公网未设 `OPENNOTER_AUTH_ENFORCE=true` + `OPENNOTER_JWT_SECRET` 则开放算力与存储 |
| S4 | P1 | LLM Provider `api_key` 明文落库 | Cookie 已 Fernet；供应商密钥未同等保护 |
| S5 | P1 | API Key IP 白名单可信头可伪造 | `client_ip()` 优先 `X-Forwarded-For` 最左；nginx 只设 `X-Real-IP`、未覆盖客户端 XFF |
| S6 | P1 | CORS 允许任意 `chrome-extension://[a-z]+` | 开发版扩展 ID 不固定的代价 |
| S7 | P1 | 扩展全网 host + cookies，同步 SESSDATA 等到后端 | 虽有 allowlist，仍是高敏感凭证集中 |
| S8 | P2 | `probe_endpoint` 管理员可让服务器对任意 `base_url` GET | 内网探测面；需 admin |
| S9 | — | `image_proxy` / URL 校验已有 SSRF 防护痕迹 | 设计较好；DNS 重绑定属理论残留 |
| S10 | P2 | API Key 全权等价用户 | admin key 泄露 = 管理员会话 |

**配置文档缺口**：根目录 `.env.example` 审查时未列出 `OPENNOTER_JWT_SECRET` / `AUTH_ENFORCE`；缺 JWT 时登录会炸，Cookie 加密可能退化为明文警告。

---

## 6. 优先修复建议（评估用清单）

### P0（建议先做）

1. 统一匿名 / 强制认证产品语义 — `services/auth.py`、`routers/note.py`
2. 加固上传与本地路径 — `upload`、`local_downloader.py`、StaticFiles `/uploads`
3. 堵住 `get_task_status` 结果旁路 — `routers/note.py`

### P1

4. 插件 `getTaskStatus` 复用 `authHeaders()` — `OpenNoter_extension/src/logic/api.ts`
5. Chat/Index 复用任务 ACL — `routers/chat.py`、`services/chat_service.py`
6. 修配额错误返回 — 勿再包进 `R.success`
7. 删除时按指纹/glob 清缓存 — `_purge_task` 对齐 `NoteCachePaths`
8. nginx 信任模型 — 覆盖 `X-Forwarded-For $remote_addr`（或只信 `X-Real-IP`）；补 `/uploads` 代理或统一走 API
9. Provider `api_key` 加密落库 — 复用 `secret_box`；补齐 `.env.example`
10. 收窄扩展权限 — `manifest.ts` 按平台域名

### P2

11. 持久化任务队列（或退出时标记中断为 FAILED）
12. 配额与 PENDING 原子化 / 匿名全局限流
13. compose 数据卷与代码分离；多副本文档化 Postgres 前提
14. `probe_endpoint` 复用 `assert_safe_url`
15. 生产 CORS 改为固定扩展 ID 白名单
16. 对齐 nginx body 上限与 `DOWNLOAD_MAX_*`

---

## 7. 已观察到的正向设计

- 任务缓存指纹修复「重试改链接命中旧音频」；状态原子 rename；失败不提前写 FAILED 以免打断重试
- `url_safety` + 视频 URL 校验修历史 SSRF；`image_proxy` 重定向逐跳校验
- Cookie Fernet + 0600 + 明文迁移；API Key 哈希索引、轮换宽限、用量异步
- 转写就绪门禁、下载体积/时长限制、用户配额框架、多用户列表隔离测试较完整

---

## 8. 结论

流水线（下载 → 转写 → LLM → 轮询 → RAG）边界清晰，且已有多轮安全加固痕迹。当前最大风险集中在：

1. **默认开放鉴权与任务 ACL 语义不一致**
2. **上传 / 本地路径未沙箱**
3. **结果读取旁路**
4. **插件 / 反代 / 密钥落盘** 等部署面细节

建议：先闭环 P0 三条 → 强制认证与密钥加密 → 收插件与 CORS。

---

## 9. 交叉验证检查清单（可勾选）

- [ ] 在当前分支复现：`AUTH_ENFORCE=false` 下匿名 `POST` 创建后，同客户端 `GET` 任务状态是否 403
- [ ] 核对 `upload` 是否仍直接拼接 `file.filename`
- [ ] 核对 `LocalDownloader` 是否对 resolve 后路径做 uploads 根约束
- [ ] 核对 `get_task_status` 无 status、仅有结果文件分支是否仍跳过 ACL
- [ ] 核对扩展 `getTaskStatus` 是否仍无 `x-api-key`
- [ ] 核对 chat ask/index 是否仍无任务归属
- [ ] 核对 nginx 对 `X-Forwarded-For` 的处理与 `client_ip()` 是否一致
- [ ] 核对 `providers.api_key` 列是否仍明文
- [ ] 确认 `.env.example` 是否已补鉴权相关变量（可能审查后已变）

---

*报告结束。修改前请以仓库当前代码为准复核路径与行号。*

---

## 10. 修复落地记录（`fix/p0-security-hardening`，2026-10-23）

### 已修（本分支）

| ID | 改动摘要 |
|----|----------|
| P0-1 | 无归属任务：未强制认证时允许匿名凭 `task_id` 访问，消除「能创建却 403 轮询」；前端轮询对连续 401/403 判 FAILED |
| P0-2 | `upload_paths` 沙箱：UUID 存储名、扩展名/大小限制、`resolve()` 必须落在 uploads 根；`/uploads` 改为鉴权下载（不再匿名 StaticFiles） |
| P0-3 | `get_task_status` 仅有结果文件 / 仅有 DB 行时同样走 `_can_access_task`（含 DB 归属回退） |
| P1-4 | 扩展 `getTaskStatus` 带 `authHeaders()` |
| P1-5 | chat ask/index/status 复用任务 ACL |
| P1-6 | 配额失败 `JSONResponse` 不再包进 `R.success` |
| P1-7 | `_purge_task` 清理指纹缓存与 legacy 缓存文件 |
| P1-8 | nginx：用 `$remote_addr` **覆盖** `X-Forwarded-For`，并设 `X-Real-IP`；补 `/uploads/` 鉴权反代（与 `client_ip()` 信任模型一致） |
| P1-9 | 扩展 manifest：去掉 `*://*/*` / `<all_urls>`，host/content_scripts 收窄为视频站 + 本机/已知 OpenNoter 源 |
| P1-10 | Provider `api_key` 复用 `secret_box` 加密落库；启动时明文→`enc:v1:` 幂等迁移；列表接口脱敏 |

鉴权开关实际环境变量为 **`OPENNOTER_AUTH_ENFORCE`**（兼容 `BILINOTE_AUTH_ENFORCE`），见 `app/utils/env_compat.getbool`。

### 第二步三项均已合入本分支

| 项 | 落地 |
|----|------|
| nginx XFF | `$remote_addr` 覆盖 + `/uploads/` 反代 |
| 扩展 host 收窄 | 视频站 + 本机/已知 OpenNoter |
| Provider api_key 加密 | `secret_box` + 启动迁移 |

回归：`pytest tests/test_p0_security_hardening.py tests/test_multiuser_security.py tests/test_task_list_api.py tests/test_client_ip_trust.py tests/test_provider_api_key_encryption.py`；扩展 `vitest`：`manifest-hosts.spec.ts`。
