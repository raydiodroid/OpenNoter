# 媒体 worker 与会话撤销

API 保留鉴权、任务状态、BYOK 决策、LLM 调用和笔记持久化。下载器、yt-dlp、字幕解析、音频转写、FFmpeg 截图和多模态图集在一次性媒体容器内执行。

## 信任边界

- API 通过仅 root 可连接的 Unix socket 请求 `media-broker`。API 和媒体容器均不挂载 Docker socket。
- broker 不加载应用代码、不开放 TCP 端口，只接受固定操作和随机工作目录 ID；镜像、入口、挂载及资源参数不可由请求覆盖。broker 拥有 Docker socket，因此属于可信控制面，必须与 API 同级保护。
- 每次媒体操作启动独立容器，非 root UID 10001、只读根文件系统、全部 capability 关闭、禁止提权；最多 128 个进程、2 CPU、2 GiB 内存、256 MiB 临时目录，默认同时执行两项，单项最多 1800 秒。
- 容器只挂载当前任务的命名卷子目录和只读模型缓存。数据交换卷为总计 4 GiB 的 tmpfs，不挂载数据库、站点配置、用户上传目录全集、笔记、JWT 密钥或凭据加密主密钥。
- API 只复制当前任务授权的上传文件。只有需要联网的操作接收当前平台 Cookie、选定 ASR 凭据和短期出口代理令牌；通过 stdin 传递，不写入 Docker 参数、容器环境配置或容器日志。LLM 凭据留在 API。
- 截图、图集和隔离探针禁用网络。联网操作使用单独的 internal bridge，禁用容器互通及 IPv6；宿主防火墙只放行到出口代理的 3128 端口，拒绝访问宿主机、其他容器、私网和云元数据地址。
- `media-egress` 没有 Docker socket、数据库或 JWT 密钥。它只接受 API 经 Unix socket 创建的短期令牌，每次 CONNECT/HTTP 请求都先解析、校验并固定公网 IP，再直连或通过站点 HTTP(S) 代理连接。上游代理的地址和密码不传给 worker，私网目标即使通过代理也不能访问。令牌在操作结束后撤销，存量隧道也检查撤销/到期，TCP 连接数和传输量设有上限。配置了 SOCKS 上游时需改用其 HTTP(S) 监听端口。
- API 将 worker 输出视为不可信：只接收有体积上限的 JSON、严格限制产物路径、拒绝符号链接及非普通文件、丢弃下载器原始元信息，复制图片时重新生成文件名。broker 超时强制销毁容器，完成后删除临时目录，启动时清理遗留任务。
- worker 故障不会退回 API 进程执行。仅未设置 `MEDIA_WORKER_SOCKET` 的本机开发/桌面模式保留原进程内适配器；它不具备 Docker 隔离。独立 GPU compose 尚需按本文件配置 broker，不能视为已隔离部署。

## 部署（Linux / Docker Engine 26+）

默认 `docker-compose.yml` 启用 worker。`volume-subpath` 要求 Docker Engine 26 或更新版本。

1. 在项目目录执行 `docker compose --profile build build backend frontend media-image media-broker`。
2. 以 root 执行 `sh scripts/media-network.sh`。它只操作 `172.16.249.0/24` 的规则，阻断 worker 到私网及宿主机的流量，不改变其他服务的网络策略。若该网段被占用，应统一修改脚本、compose 以及 `media_worker.py` / `media_egress.py` 中的出口地址。
3. 安装 `scripts/opennoter-media-network.service` 到 systemd，按实际项目路径调整 `ExecStart`，启用该服务，以便重启 Docker/主机时恢复规则。升级、防火墙重载后重新执行规则脚本并检查；不要单独删除规则。
4. 执行 `docker compose up -d --wait backend frontend nginx media-broker media-egress`。`MEDIA_MODELS_PATH` 默认取执行 compose 时的 `${PWD}/backend/models`，必须从项目目录运行。
5. 验证 worker 非 root、无数据库/主密钥/Docker socket，确认私网和宿主访问被拒绝，再验证下载、转写、截图及任务生成。

broker 的两项额外文件 capability 只用于清理恶意改过权限的临时文件；不授予媒体容器。Docker socket 自身具有宿主级权限，不应向其他服务共享。内存/时限配置不足时操作应明确失败，不能为恢复功能关闭隔离。

## 可撤销 JWT 会话

登录及 OIDC 回调在同一事务内创建 `auth_sessions` 记录，数据库只存随机 session ID 的 SHA-256。每次 Cookie/Bearer JWT 鉴权都检查签名、必需 claims、会话存在、到期时间、撤销时间和用户认证版本。

- 退出登录撤销当前会话，重放原 JWT 返回 401，其他设备保持登录。
- 修改/重置密码、管理员停用用户或修改权限会撤销该用户所有会话，并原子递增认证版本。停用后重新启用不会恢复旧会话；并发使用旧密码创建的会话也无法通过版本校验。
- API Key 继续使用独立的撤销和权限机制。
- 首次升级前签发的 JWT 没有服务端会话记录，需要重新登录。用户、任务、供应商、默认模型和原有 API Key 不受此迁移影响。

## 依赖与已知限制

Web 与扩展统一使用安全修订版 Vite 7.3.8、Vitest 5.1.0，并刷新扩展过时的构建/打包依赖。完整 npm/pnpm 审计的高危、严重项均为 0；仍有中低危间接依赖，不能宣称全量漏洞清零。

ChromaDB 1.5.9 是核验时最新 Python 版本，公告中的服务端代码执行/多租户鉴权问题没有已发布修复。项目使用本地 `PersistentClient`，不运行 Chroma HTTP 服务、不接受外部 embedding-function 配置，也不启用 `trust_remote_code`。保留依赖告警，不使用审计忽略项掩盖它；若未来独立部署 Chroma 服务，必须先重新评估这些公告。
