# OpenNoter 全面代码审查报告

- **仓库**：`raydiodroid/OpenNoter`（审查基线 `926b8b6`，标签叙事 v1.0.0）
- **日期**：2026-10-25
- **范围**：backend（FastAPI）、OpenNoter_frontend（React 19 + Vite + Spectrum 2 + Tauri 2）、OpenNoter_extension（Vue 3 MV3）、docker-compose / nginx / AIO Dockerfile、测试与运维配置
- **方法**：只读静态分析 + 对照现有 pytest；**未**对运行中的实例做渗透利用
- **证据分级**：
  - **已证实**：源码路径可定位，行为由控制流直接推出
  - **假设**：需要特定部署拓扑、外部短链开放重定向、或 XSS 前置条件才能打到
- **修复策略**：本轮**不改业务代码**。未发现「未认证远程 RCE / 默认配置下全库裸奔」这类必须立刻热修的致命漏洞；高优先级问题见文末 Top 10。

---

## 1. 总体评价

这是一次从 BiliNote 分叉后的认真重写，而不是换皮。安全与多租户方向已经做过多轮加固：`AUTH_ENFORCE` 默认开启、任务/上传/静态资源 ACL、Cookie 与 Provider `api_key` 的 Fernet+HKDF 加密、视频 URL 从「子串包含」改为 hostname 白名单、`image_proxy` 逐跳 SSRF 校验、SQLite WAL、启动时把中断任务标 FAILED。backend 约 47 个测试文件，安全回归（SSRF / IDOR / 加密 / 上传沙箱）明显强于流水线 e2e。

主要风险不在「完全没做鉴权」，而在三类断裂：

1. **契约**：前端主路径 `POST /api/notes`、扩展后台 `fetch`、Tauri Cookie 会话，与后端默认鉴权/校验不一致，导致**默认配置下若干主功能直接不可用**。
2. **状态机**：`SUCCESS` 写在结果文件之前；软删除行会被轮询伪装成 `PENDING`；`FORMATTING` 从未写入。
3. **运维**：转写临时文件清理路径是死的；Chroma 每次 `new PersistentClient`；schema 靠手工 `migrate_*.py`；AIO 镜像 supervisord 会钉死一组环境变量。

单人内网自托管、走 docker-compose + nginx、只用浏览器（非 Tauri、非扩展）生成在线视频笔记——这条路径相对可用。多用户公网、桌面端、扩展、本地上传、长期磁盘，是当前最容易踩坑的面。

**架构判断**：下载器 / 转写工厂 / GPT 工厂 / RAG / 鉴权的目录边界清晰，但 `NoteGenerator` 是编排上帝对象；持久化（状态文件 + DB upsert + 结果 JSON + Chroma）散落在 router 与 service 两侧；鉴权只在 FastAPI `Depends` 层，服务内部不感知用户。这套模型对当前单入口够用，对新脚本/内部调用不设防。

---

## 2. 架构与模块边界

```
浏览器 / Tauri / 扩展
        │  Cookie JWT  /  Bearer  /  x-api-key
        ▼
FastAPI  routers（note / chat / provider / model / auth / api_keys / config / monitor）
        │  get_optional_user / require_admin
        ▼
services.note.NoteGenerator          chat_service + chat_tools + vector_store
        ├─ downloaders/*（yt-dlp / 平台适配）
        ├─ transcriber/*（工厂 + 单例）
        ├─ gpt/*（UniversalGPT + chunker）
        └─ db（SQLite WAL）+ note_results/*.json + vector_db（Chroma）
```

| 层 | 实际职责 | 边界问题 |
|---|---|---|
| `downloaders/` | 平台 I/O、Cookie、体积/时长上限 | 接口干净；短链解析后未再走 `assert_safe_url` |
| `transcriber/` | ASR 工厂 | 跨任务共享实例；`on_finish` 全部注释掉，事件清理失效 |
| `gpt/` | 总结、分块、checkpoint | `gpt/test.py`、`utils/export.py` 为死代码 |
| `services/note.py` | 整条流水线 + 状态机 + 截图后处理 | 上帝对象；SUCCESS 与落盘分离 |
| `routers/note.py` | 鉴权、配额、PENDING、写 JSON、触发索引 | 与 service 双写状态 |
| `chat_*` | 每任务一个 Chroma collection | ACL 在 router；prompt 把不可信转写塞进 system |
| `services/auth.py` | JWT / API Key / Cookie | **没有**全局中间件，漏挂 `Depends` 就会裸奔 |

`task_serial_executor.py` 名不副实：实现是 `ThreadPoolExecutor(max_workers=6)`，`SerialTaskExecutor` 只是别名。配额默认每用户并发 2，与线程池 6 叠加。

导出 PDF/DOCX、`ppt_generator.py`、`minio_client.py`：README 已声明未接线；`CLAUDE.md` 仍把它们写成现成模块——文档漂移。

---

## 3. 流水线状态机（实现，而非 README）

**实际写入路径**（`NoteGenerator.generate`）：

```
提交 → PENDING（router）
     → PARSING
     → DOWNLOADING（`_run_with_retry` 包住下载+字幕+转写）
         有平台字幕或预取字幕时可跳过音频下载/转写
     → TRANSCRIBING（仅无字幕时）
     → SUMMARIZING
     → _post_process_markdown（截图/链接，无独立状态）
     → SAVING
     → SUCCESS          ← 此时 {task_id}.json 还不存在
     → save_note_to_file / index_task（router.run_note_task）
终端：SUCCESS / FAILED
重启/关机：fail_interrupted_tasks → 进行中全部 FAILED（不续跑）
```

`TaskStatus.FORMATTING` 在枚举、配额、中断集合、前端 `TASK_STATUS_LABEL`、扩展进度条里都在，**没有任何 `_update_status(..., FORMATTING)`**。README 已承认这是遗留。

`get_task_status` 有两条会**伪造 PENDING** 的分支：SUCCESS 但结果文件未写出；仅有软删除 DB 行。见 F4 / F5。

---

## 4. 按严重度排序的发现

每条格式：编号 · 类别 · 证据 · 位置 · 影响 · 建议。

### 4.1 致命（默认配置下主功能断裂，或桌面端命令执行面）

#### F1 · 正确性/鉴权 · 已证实

**位置**：`OpenNoter_frontend/src/store/authStore/index.ts` `login`；`OpenNoter_frontend/src/lib/api.ts`（无 `withCredentials`、无 Bearer 拦截器）；`OpenNoter_frontend/.env.tauri`；`backend/app/routers/auth.py` `_set_auth_cookie`

登录接口同时返回 JSON `token` 并 Set-Cookie `opennoter_token`（HttpOnly、SameSite=Lax、默认 Secure）。前端只把 `res.user` 写入 Zustand，**丢弃 token**。Axios 默认跨站不带 Cookie。

- Web + nginx / Vite 同源代理：Cookie 能带上，浏览器路径可用。
- Tauri：页面源是 `tauri://localhost` / `https://tauri.localhost`，API 打到 `http://127.0.0.1:8483/api`。跨站 + SameSite=Lax + 无 `withCredentials` → 后续请求无凭据。默认 `AUTH_ENFORCE=true` 时，登录成功后的生成/列表/设置全部 401，但 UI 仍显示已登录。

**建议**：桌面端把 JWT 放内存（或安全存储）并加 `Authorization: Bearer`；401 跳转走 HashRouter（见 F11）。不要依赖跨站 Cookie。

#### F2 · 契约 · 已证实

**位置**：`OpenNoter_frontend/src/pages/workbench/NoteForm.tsx` `handleSubmit`；`OpenNoter_frontend/src/services/note.ts` `generateNote` → `POST /notes`；`backend/app/routers/note.py` `CreateNoteRequest.validate_supported_url` vs `VideoRequest.validate_supported_url`

上传接口返回 `/uploads/<uuid>.ext`。表单 `platform=local` 把该路径当作 `video_url` 交给 **`POST /api/notes`**。`CreateNoteRequest` 无条件调用 `is_supported_video_url()`，该函数要求 `http/https`，相对路径直接失败。

旧接口 `POST /api/generate_note`（`VideoRequest`）对非 http(s) URL 放行，本地文件才能进 `LocalDownloader._resolve_local_path`（uploads 沙箱是对的）。

结果：**Web 主界面的「本地视频」生成在规范 API 上是死的**。契约测试只覆盖了 B 站 URL 别名，没有 `/uploads` + `platform=local`。

**建议**：`CreateNoteRequest` 在 `platform=local` 或路径以 `/uploads/` 开头时走 `resolve_under_uploads`，不要走视频域名白名单；补一条 pytest。

#### F3 · 契约 · 已证实

**位置**：`OpenNoter_extension/src/background/main.ts` `startTask`；对比 `OpenNoter_extension/src/logic/api.ts` `authHeaders`

弹窗/设置页的 `request()` 会带 `x-api-key`。右键菜单和 `opennoter-start` 消息走 background `fetch`，**只有 `Content-Type`**。默认强制认证 → 右键「总结此视频」稳定 401。弹窗可能正常，右键失败，表现为「插件时好时坏」。

设置文案还写着「后端未开启强制认证（默认）时可留空」（`General.vue` 约 114 行）——与 `auth_enforced()` 默认 `True` 相反，用户更容易不填 Key。

**建议**：background 复用 `authHeaders()`；改文案为「默认需要 API Key」。

---

### 4.2 高

#### F4 · 正确性 · 已证实 — SUCCESS 早于结果文件，轮询伪装 PENDING

**位置**：`backend/app/services/note.py` `NoteGenerator.generate`（先 `_update_status(SUCCESS)` 再 `return NoteResult`）；`backend/app/routers/note.py` `run_note_task` / `get_task_status`

```278:284:backend/app/services/note.py
            self._update_status(task_id, TaskStatus.SAVING)
            self._save_metadata(...)
            self._update_status(task_id, TaskStatus.SUCCESS)
            return NoteResult(...)
```

```237:243:backend/app/routers/note.py
    note = task_serial_executor.run(_execute_note_task)
    if not note or not note.markdown:
        return
    save_note_to_file(task_id, note)
```

```891:897:backend/app/routers/note.py
            else:
                return R.success({
                    "status": TaskStatus.PENDING.value,
                    "message": "任务完成，但结果文件未找到",
```

窗口期内前端会看到状态回退到排队；若 `save_note_to_file` 失败，DB/状态文件永久 SUCCESS，轮询却一直 PENDING。

**建议**：先写结果（或 staging rename）再标 SUCCESS；缺文件时返回 `SAVING`，禁止再铸 PENDING。

#### F5 · 正确性 · 已证实 — 软删除任务复活为 PENDING

**位置**：`backend/app/db/video_task_dao.py` `get_task_by_id`（不过滤 `deleted_at`）；`delete_task_by_task_id`（默认软删）；`get_task_status` 在无状态文件/无结果时，只要 DB 有行就返回 PENDING

删除会清结果文件，但留审计行。任何仍持有 `task_id` 的客户端再 poll，会看到「任务排队中」。

**建议**：`get_task_by_id` / 状态查询把 `deleted_at IS NOT NULL` 当 404。

#### F6 · 安全（SSRF）· 已证实（校验被绕过）；利用取决于短链是否开放重定向 · 假设

**位置**：`backend/app/validators/video_url_validator.py` `is_supported_video_url`（`SHORT_LINK_HOSTS` 提前 `return True`，**跳过** `assert_safe_url`）；`backend/app/utils/bilibili_ids.py` `resolve_short_url`（`allow_redirects=True`，最终 URL 不再校验）；`url_parser._extract_bilibili_id` / `_extract_douyin_id`

历史子串 SSRF（`?x=douyin` → 打元数据）已经修好，测试也在。短链是剩下的洞：认证用户提交 `https://b23.tv/...` 或 `https://v.douyin.com/...` 即可让 worker 跟随任意 302。官方短链通常只指向本站（**假设**利用难），但与 `image_proxy` 的「逐跳再校验」不一致，且 yt-dlp 自己也会跟跳。

`probe_endpoint`（管理员）同样 `requests.get` 默认跟随跳转，比 `image_proxy` 弱一档（中，见 F16）。

**建议**：短链每个 hop + 最终 URL 走 `assert_safe_url(..., suffixes=())`；`allow_redirects=False` 或自管跳转。

#### F7 · 安全（多租户滥用）· 已证实

**位置**：`backend/app/routers/provider.py` `test_provider`、`list_remote_models`（无 `require_admin`）；CRUD 是管理员

任意登录用户可对共享 Provider 发真实 chat completion / 拉 `/models`。内置 ollama `base_url` 为 `http://127.0.0.1:11434/v1`（`builtin_providers.json`），等于额外的回环探测与烧 Key。

**建议**：与 `probe_endpoint` 一样要求管理员；对非管理员禁止私网 `base_url`。

#### F8 · 安全（桌面）· 已证实（能力存在）；RCE 需 XSS · 假设

**位置**：`OpenNoter_frontend/src-tauri/src/lib.rs` `run_command_with_env`（任意 `program` + `args`，已注册进 `invoke_handler`）；`tauri.conf.json` `"csp": null`、`"devtools": true`

Sidecar 的 `shell:allow-execute` 白名单是紧的，但自定义 command 不经过该白名单。Markdown 当前 `react-markdown` 且无 raw HTML，扩展 `markdown-it({ html: false })`，直接 XSS 门槛不低。CSP 为 null 时一旦出现 XSS，桌面端就是任意进程执行。

**建议**：生产 CSP；`run_command_with_env` 改为只允许 `ffmpeg`/`ffprobe`；release 关闭 devtools。

#### F9 · 契约 · 已证实 — 鉴权后的截图无法给 `<img>` / Tauri 相对路径

**位置**：`backend/main.py` `serve_static`（`AUTH_ENFORCE` 时要登录且校验 `screenshots/<task_id>/`）；`OpenNoter_frontend/src/pages/workbench/MarkdownArticle.tsx`（`/static/` 保持相对）；扩展 `absolutizeMarkdownImages` 拼绝对 URL 但不带 Key

Web+nginx 同源 Cookie 可以加载图片。Tauri 会请求 `tauri://localhost/static/...`。扩展 `<img>` 不能加 `x-api-key`，强制认证下截图 401。

nginx `/static/` 还加了 `Cache-Control: public, immutable`（`nginx/default.conf`）——认证资源被标成公共缓存，前置 CDN 时可能泄露（中/高，部署相关）。

**建议**：短时签名 URL，或前端用带凭据的 blob fetch；Tauri 把 `/static/` 绝对化到 sidecar；静态响应改为 `private`。

#### F10 · 正确性 · 已证实 — `syncFromServer` 把空笔记标成 SUCCESS

**位置**：`OpenNoter_frontend/src/store/taskStore/index.ts` `syncFromServer`（只合并 `status`）；`isRunningStatus` 在 SUCCESS 时停止；`useTaskPolling.ts` 只对 running 调 `getTaskStatus`

换浏览器 / 30s 列表同步命中远端 SUCCESS 且本地 markdown 为空 → 轮询停止 → 预览空白。与 F4 叠加更明显。

**建议**：远端 SUCCESS 且 `has_result`、本地无正文时强制拉 `/tasks/{id}`。

#### F11 · 正确性 · 已证实 — HashRouter 与硬跳转 `/login`

**位置**：`OpenNoter_frontend/src/App.tsx`（Tauri 用 `HashRouter`）；`lib/api.ts` `window.location.href = '/login'`；`LoginPage.tsx` `window.location.href = '/api/auth/oidc/login'`

桌面 401 会离开 hash 路由；OIDC 在 webview 里打相对 `/api/...`，不是 sidecar 源。

**建议**：`navigate('#/login')`；OIDC 用后端绝对地址。

#### F12 · 契约 · 已证实 — 扩展平台检测仍是子串启发式

**位置**：`OpenNoter_extension/src/logic/platform.ts`（注释声称与 backend validator 一致）；后端 `url_parser.detect_platform` + `PATH_PATTERNS` + `SHORT_LINK_HOSTS`

扩展认不出 `b23.tv`、`v.douyin.com`、YouTube Shorts/live、B 站 bangumi。`url.includes('douyin')` 是服务端已经修掉的那类启发式（扩展侧目前只做 UX 判断，真正 SSRF 在服务端拦）。

**建议**：把 hostname 规则做成前后端共享夹具。

#### F13 · 运维 · 已证实 — 临时文件清理是死路径

**位置**：`backend/app/transcriber/{whisper,bcut,kuaishou,mlx_whisper_transcriber}.py` 均 `# self.on_finish(...)`；`events/handlers.py` `cleanup_temp_files` 仍注册；`groq.py` `compress_audio` 的 `mkstemp` 无删除

下载的音视频、Groq 压缩临时文件会一直堆在 data 卷。compose `mem_limit: 4g` 时磁盘满比 CPU 先炸。`scripts/cleanup_cache.py` 默认不进镜像。

**建议**：在 `generate()` 的 `finally` 按任务删；不要用 `video_id` 前缀 glob（并发不安全）。不要重新打开注释掉的 `on_finish` 除非先改删除策略。

#### F14 · 性能 · 已证实（客户端生命周期）；损坏 · 假设

**位置**：`backend/app/services/vector_store.py` `__init__` 每次 `chromadb.PersistentClient`；`note.py` / `chat.py` / `chat_service.py` 多处 `VectorStoreManager()`

并发 index + query 时 PersistentClient 文件锁/HNSW 是已知痛点。`chat.py` `_index_status` 是进程内存，重启后 UI 状态丢失。

**建议**：进程级单例 + 索引互斥；状态以 `is_indexed()` 为准。

#### F15 · 可维护性 · 已证实 — 无自动 schema 迁移

**位置**：`backend/app/db/init_db.py` 只有 `create_all`；根目录 `migrate_users.py` / `migrate_api_keys.py` / `migrate_task_status.py` 需手工跑

升级后缺列时部分功能静默异常，监控里已有「该跑 migrate」类提示。

**建议**：lifespan 里跑幂等迁移，或上 Alembic。

---

### 4.3 中

| ID | 证据 | 位置 | 影响 | 建议 |
|---|---|---|---|---|
| F16 | 已证实 | `provider.py` `probe_endpoint` 默认跟随 302 | 管理员 SSRF 到内网/元数据 | 与 `image_proxy` 同样逐跳校验 |
| F17 | 已证实 | `main.py` `serve_static`；`upload_paths.can_read_upload` | 旧截图/封面/无 sidecar 上传：任意登录用户可读 | enforce 下拒绝无主资源；封面纳入 `screenshots/<task_id>/` |
| F18 | 已证实 | `api_keys.scopes` 只存不验 | UI 暗示最小权限，实际 key ≡ 用户（含管理员） | 落地 scopes 或从 API 去掉 |
| F19 | 已证实 | `provider.py` `update_provider` `print(..., filtered_data)` | 明文 api_key 进容器日志 | 删除 print / 脱敏 |
| F20 | 已证实 | `cookie_manager._write` 无密钥时明文回退 | 未配 JWT 的部署把 SESSDATA 写成明文 | 生产 fail-closed |
| F21 | 已证实代码；高影响需直连 backend · 假设 | `ip_allow.client_ip` 无条件信 XFF 最左 | 绕过 API Key IP 白名单 | 仅信任已知反代；直连用 `request.client.host` |
| F22 | 已证实 | `cors_origins.py` + `allow_credentials=True` | 本机恶意页/任意扩展（若打开开关）可打 Cookie API | 生产禁止 `CORS_ALLOW_ANY_EXTENSION`；localhost 风险写入文档 |
| F23 | 已证实 | `task_interrupt.fail_interrupted_tasks` vs 仍在跑的线程池 | shutdown 标 FAILED 后 worker 仍可写回 SUCCESS | 取消令牌；`shutdown(cancel_futures=True)` |
| F24 | 已证实 | `whisper.py` `transcript` 异常只 `print` 并返回 `None` | `asdict(None)` → 被当成可重试失败 | 重新抛出 |
| F25 | 已证实 | `downloaders/errors.py` `is_retryable` 默认 `True` | AttributeError/坏缓存也会重试，烧配额 | 默认 False，白名单网络错误 |
| F26 | 已证实 | `run_note_task` 在 BackgroundTask 里 `future.result()` | 双占用 Starlette 线程与 note pool | 只向 pool submit，按 task_id 跟踪 |
| F27 | 已证实 | `chat_service.py` `SYSTEM_PROMPT.format(context=context)` | 转写含 `{` 时 KeyError，问答 500；另有间接注入 | `replace` / Template；不信任检索文本 |
| F28 | 已证实 | 扩展 `deleteModel` → `/api/models/delete/{id}`；后端只留 `DELETE /models/{id}` | 扩展设置页删模型 404 | 改调用新路径 |
| F29 | 已证实 | 扩展 `Popup.vue` `selectTask` 不 `clearTimeout` | 轮询叠加 | 与 Sidepanel 一样先清 timer |
| F30 | 已证实 | `General.vue`「默认不强制认证」 | 用户不填 Key | 改文案 |
| F31 | 已证实（本地 dev 读根 `.env`） | `.env.example` `VITE_API_BASE_URL=http://127.0.0.1:8483`（无 `/api`）；`vite.config.ts` 从父目录 `loadEnv` | 请求打到 `/notes` 而非 `/api/notes`。Docker 前端镜像只 COPY 前端目录，默认回退 `/api`，compose Web **不一定**中招 | 示例改为 `/api`；代理 target 与客户端 base 拆开 |
| F32 | 已证实 | 全栈 `FORMATTING` | 进度条空一档；文档谎称有该阶段 | 删或真正写入 |
| F33 | 已证实 | nginx 无 `proxy_read_timeout`；`/static/` `public, immutable` | 慢 LLM/上传撞 60s；认证图被公共缓存 | `/api/` 600s；静态 `private` |
| F34 | 已证实（所列键）；其它 OPENNOTER_* 继承 · 部分假设 | `Dockerfile.complete` `[supervisord] environment=` 写死 DATABASE_URL 等 | AIO `docker run -e` 盖不住这组键 | 不要在 supervisord 里覆盖；用 env_file |
| F35 | 已证实 | 镜像无 `USER` | 逃逸即 root | 非 root + 数据目录可写 |
| F36 | 已证实 | `requirements.txt` `chromadb>=0.5.0`、`yt-dlp>=2026.7.4` | 重建镜像不可复现 | 钉死版本 |
| F37 | 已证实 | 前端无 test script | 轮询/鉴权/表单无回归 | Vitest 覆盖 store/hook |
| F38 | 已证实 | 扩展 e2e 仍断言 `Vitesse WebExt` | CI 绿 ≠ 产品可用 | 换成真实 popup/options |
| F39 | 已证实 | OIDC 回调：用户表空则首位自动超管 | 竞态/IdP 账号接管引导 | 仅 CLI 引导管理员 |
| F40 | 已证实 | `POST /complete_browser_cookie_transfer` 免登录，凭高熵 ticket | ticket 泄露可写入下载 Cookie | 绑定 admin + 限流（已有 TTL/单次/origin） |
| F41 | 已证实 | 前端 chat timeout 45s，后端 `CHAT_TOTAL_TIMEOUT` 90s | 工具多轮时前端先断 | 对齐超时 |
| F42 | 已证实 | 上传 axios timeout 60s，默认 500MB | 慢网上传必失败 | 加大超时或分块 |

### 4.4 低

| ID | 证据 | 摘要 |
|---|---|---|
| F43 | 已证实 | `app/utils/export.py` 未接线，还拖着 weasyprint/pdfkit/python-docx |
| F44 | 已证实 | `app/gpt/test.py` 草稿；`xiaoyuzhoufm_download.py` 未进平台表 |
| F45 | 已证实 | `CLAUDE.md` 仍写 i18n、ppt_generator、minio、pnpm 前端 |
| F46 | 已证实 | 前端 i18n 已删，文案写死中文 |
| F47 | 已证实 | SQLite 文件无显式 `chmod 600`（Cookie 文件有） |
| F48 | 已证实 | `task_serial_executor` 实际并发 |
| F49 | 已证实 | 无运行期 stale-task reaper（监控只计数） |
| F50 | 模式已证实 | `assert_safe_url` 解析与真正 connect 之间 DNS rebinding TOCTOU |
| F51 | 已证实 | pytest 不在 `requirements.txt` |
| F52 | 已证实 | `AdminGuard` 在 `AUTH_ENFORCE=0` 且 `user===null` 时放行，后端仍 403 |
| F53 | 已证实 | 扩展 API Key 存 `chrome.storage.local`（预期内） |
| F54 | 已证实 | compose healthcheck 打 `/api/sys_health`（含 ffmpeg 子进程） |
| F55 | 已证实 | GPU compose 只用 `deploy.resources`，部分 Compose v2 不生效 |

---

## 5. 前后端 / 插件契约一览

| 能力 | Frontend | Extension | Backend 规范 |
|---|---|---|---|
| 创建任务 | `POST /notes` + `video_url` | `POST /generate_note`（旧） | `POST /api/notes`（`url` / `video_url` 别名） |
| 本地上传 | 上传成功后走 `/notes` → **失败（F2）** | 无本地上传 | 旧 `/generate_note` 才允许 `/uploads` |
| 轮询 | `GET /tasks/{id}` | `GET /task_status/{id}` | 新路径 + 旧别名 |
| 鉴权 | 同源 Cookie（Tauri 失效 F1） | `x-api-key`（background 缺失 F3） | Cookie / Bearer / x-api-key |
| 平台识别 | 用户自选 | 过时正则（F12） | hostname + 路径 + 短链 |
| 截图 | 相对 `/static/`（Tauri 坏，F9） | 绝对 URL，无 Key | 鉴权 `/static` |
| 删模型 | `DELETE /models/{id}` | `/models/delete/{id}` **404** | 仅 DELETE |
| `FORMATTING` | 类型/文案仍在 | 进度条仍在 | 从不写入 |
| ResponseWrapper | `code===0` 解包 | 同左；FAILED 已改为 success 包状态 | 一致 |

Web 开发代理：`vite.config.ts` 把 `/api` 转到 `VITE_API_BASE_URL`（默认 `http://127.0.0.1:8483`）。客户端 `lib/api.ts` 默认 base 是 `/api`。**不要**把示例里的无 `/api` 绝对地址烘进 bundle。

---

## 6. 测试覆盖与可维护性

**Backend（相对强）**

约 47 个文件，重点在：URL SSRF、probe SSRF、多用户 ACL、上传路径、Cookie/Provider 加密、下载限制/重试、SQLite WAL、chunker、任务中断。大量用 `importlib` 隔离模块，**很少**用 `TestClient` 走真实依赖栈（中间件 / CORS / Cookie）。

缺口：登录/JWT/OIDC；`CreateNoteRequest` + `/uploads`；`run_note_task` 全链路（假 downloader/ASR/GPT 即可）；chat 检索+tool 行为（现有偏安全）；HTTP 上传。

**Frontend**：无测试脚本（README 已写）。轮询、syncFromServer、Tauri 鉴权、本地上传全部无回归。

**Extension**：`e2e/basic.spec.ts` 仍是 Vitesse 模板；产品逻辑几乎无覆盖。

**依赖**：前端有 lockfile 且 Docker `npm ci`（好）。后端 `chromadb` / `yt-dlp` 未钉版本。导出相关重依赖仍在 Linux 镜像里。

**配置**：`.env.example` 把 `OPENNOTER_JWT_SECRET` 注释掉，但鉴权默认开——首次登录会在 `_jwt_secret()` 炸。README 有说明，bootstrap 体验仍然脆。无密钥时 Cookie 加密退化为明文（F20）。

---

## 7. 性能与运维

| 主题 | 现状 | 风险 |
|---|---|---|
| SQLite WAL | `journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`、`foreign_keys=ON`，有测试 | 适合单机 6 worker + 3s 轮询；多副本必须换 Postgres（README 已写） |
| 轮询 | 每任务 3s + 列表 30s | 用户多时读放大；WAL 能撑，但无退避 |
| Chroma | 每调用新 PersistentClient；collection=`task_id` | 锁/句柄；重启丢 in-memory 索引状态 |
| 大文件 | 下载默认 200MB / 300s；上传 500MB；nginx 512m | 前端上传 timeout 60s；4g `mem_limit` 上 500MB+Whisper 紧 |
| 清理 | 事件处理函数从未被调用 | 磁盘泄漏（F13） |
| Sidecar | 启动/杀/重启路径完整 | `csp:null` + 任意 command（F8）；非 ASCII 路径注释里已知 |
| AIO | supervisord 钉死一组 env | `-e DATABASE_URL=` 可能无效（F34） |
| 容器 | expose backend，nginx 对外 | 不要把 8483 直接打到公网（XFF、CORS localhost） |

---

## 8. 已证实的安全优点（避免误报）

这些不是「没问题」，而是已经做过、且代码与测试能对上的控制，审查时不要当漏洞重开：

1. `AUTH_ENFORCE` 默认 True；无公开注册；管理员靠 CLI / 首位 OIDC。
2. 任务 ACL `_can_access_task`；chat 复用；新截图路径 `screenshots/<task_id>/`；新上传 `.owner` sidecar。
3. `path_safety` / `note_paths` / `upload_paths` 防穿越；客户端自带 `task_id` 会再做归属校验。
4. 视频 URL 按 hostname，不再 `if "douyin" in url`；`image_proxy` 逐跳 SSRF。
5. 密码 bcrypt；API Key SHA-256 + 唯一索引；Cookie/Provider key `enc:v1:`；列表接口脱敏。
6. ffmpeg / yt-dlp 均为 `subprocess` 列表参数，**无** `shell=True` / `os.system`。
7. OpenAPI `/docs` 需鉴权；`GET /api/` 指引公开是有意的。
8. 项目 nginx **覆盖** `X-Forwarded-For` 为 `$remote_addr`（与 `ip_allow` 注释一致）。
9. compose 只 `expose` backend，不把 8483 发到宿主机。

威胁模型仍是：**信任边界在反向代理之后**。把 backend 直接暴露、或 `AUTH_ENFORCE=0` 公网，会系统性撤回上述几条。

---

## 9. 优先修复清单（Top 10）

| 优先级 | ID | 为什么先做 | 工作量直觉 |
|---|---|---|---|
| 1 | F2 | 本地上传是宣传能力，规范 API 上完全不可用；改校验 + 一条测试 | 小 |
| 2 | F1 | 默认鉴权下桌面端登录是空的 | 小 |
| 3 | F3 + F30 | 扩展右键在默认鉴权下失败，文案还在教用户别填 Key | 小 |
| 4 | F4 | SUCCESS/文件顺序会让笔记「生成成功但永远转圈」 | 小 |
| 5 | F5 | 删除后的幽灵 PENDING | 小 |
| 6 | F9 + F11 | 桌面截图与 401 恢复 | 小到中 |
| 7 | F6 + F16 | 补上短链/probe 与 image_proxy 同级的跳转校验 | 中 |
| 8 | F13 | 磁盘会在无人值守部署上先于功能挂掉 | 中 |
| 9 | F8 | 收紧桌面 command + CSP，避免下一处 XSS 升级成 RCE | 小 |
| 10 | F15 + F14 + F10 | 迁移自动化、Chroma 单例、前端 SUCCESS 注水 | 中 |

刻意没放进 Top 10、但公网多租户应尽快看的：F7（provider test 权限）、F18（scopes 名存实亡）、F19（日志泄 Key）、F17（无主静态/上传）、F21（直连 backend 时的 XFF）。

---

## 10. 审查边界

- 未启动完整 docker 栈，未用真实 B 站/YouTube 链接跑 e2e，未对 OIDC IdP 做集成测试。
- 未审计 yt-dlp 自身漏洞；只看调用方式（无 shell、有体积/时长门闩）。
- 未对 Chroma 做并发压测；F14 的损坏风险标为假设。
- 短链 SSRF 的可打性取决于 b23.tv / v.douyin.com 是否允许跳到非视频源，标为假设。
- 前端无浏览器实测（本任务为只读审查）；F1/F2/F3/F10/F11 由源码控制流推出，建议修复后用桌面端 + 本地上传 + 扩展右键三条路径做验收。

---

## 附录 A · 鉴权入口对照

| 机制 | 符号 | 作用 |
|---|---|---|
| `get_optional_user` | `services/auth.py` | 业务路由默认门闩；enforce 时无凭据 401 |
| `get_current_user` | 同文件 | API Key / Bearer JWT / Cookie |
| `require_admin` | 同文件 | 超管 |
| `ApiKeyUsageMiddleware` | `middlewares/api_key_usage.py` | **只记用量，不鉴权** |
| CORSMiddleware | `main.py` | origin regex + credentials |

**有意公开**：`GET /api/`、`/api/sys_health`、`/api/sys_check`、`POST /api/complete_browser_cookie_transfer`、`/api/auth/login|logout|config|oidc/*`。

**需登录（默认）**：notes/tasks/upload/chat/providers 读、keys、transcriber 读、`/static`、`/uploads`、`/openapi.json`。

**需管理员**：provider 写、probe、Cookie、ASR/代理写、monitor、用户管理、model 写。

**需登录但非管理员**：`POST /providers/{id}/test`、`GET /providers/{id}/remote-models`（F7）。
