# API Key 管理改造方案（设计文档）

> 状态：**已实施**（2026-10-20）。本文保留完整的设计推导，实施结果见文末「实施记录」。
> 背景：2026-10-20 的 API 契约审查中顺带发现「密钥管理」功能过于简陋，本文给出改造方案。
> 文中所有「现状」结论均在 `vps-main` 线上环境与 `master`(d8a84c7) 代码上核对过，标注了出处。

---

## 0. 实施记录（最终结果）

### 最终决策（与初稿不同的地方）

| 议题 | 初稿倾向 | 最终决策 | 理由 |
|---|---|---|---|
| **API Key 的定位** | 引入 `Principal`，Key 不再等价于完整会话 | **不引入。API Key 等价于该用户的认证口令** | 用户明确要求。实现上把「Key 拥有全部用户权限」保留为默认语义；作用域字段照样存下来（默认空 = 全权），将来想收窄再启用 |
| **默认有效期** | 90 天 | **永不过期** | 用户明确要求。行业惯例是 90 天，但这是自用部署，永久更省心；到期时间在创建时可选 |
| **限流/用量存储** | 倾向 SQLite | **混合：限流计数走进程内内存，用量明细走 SQLite** | 后端是单进程单副本，进程内够用且零依赖；用量要跨重启查询，属数据不属状态。**不引入 Redis** |

### 落地清单

**数据模型**（`backend/app/db/models/api_keys.py`）
- `api_keys` 表从 9 列扩到 27 列，新增 `description` / `key_id` / `last_four` / `hash_algo` /
  `scopes` / `allowed_ips` / `rate_limit_rpm` / `status` / `revoked_at` / `revoked_reason` /
  `grace_expires_at` / `not_before` / `rotated_from_id` / `last_used_ip` /
  `last_used_endpoint` / `last_used_user_agent` / `use_count` / `last_rotated_at`
- 新增 `api_key_usage` 表（调用明细，保留 90 天）

**迁移**（`backend/migrate_api_keys.py`）
- 幂等：PRAGMA 检查 + ALTER TABLE，可重复执行
- 存量行的 `hash_algo` 按 `key_hash LIKE '$2%'` 判定（**不能**依赖新列的默认值 ——
  `ALTER TABLE ADD COLUMN ... DEFAULT 'sha256'` 会把 bcrypt 存量行也填成 sha256，
  导致全部密钥失效）

**鉴权层**（`backend/app/services/auth.py`）
- 哈希从 bcrypt 换成 SHA-256（确定性 → `key_hash` 建唯一索引 → 鉴权变成一次 O(1) 查表）
- 存量 bcrypt 密钥走 `hash_algo` 双轨，**首次鉴权成功时透明重哈希**，用户无感、密钥不用重发
- 生命周期四态判定（过期是实时算的，不依赖定时任务改库）
- IP 白名单、限流在鉴权时就地校验
- **返回类型不变**（仍是 `User`），约 20 处 `require_admin` 调用点一行未改
- 按 key 的信息通过 `request.state.api_key_id`（**标量**，不是 ORM 实例）传给中间件

**接口**（`backend/app/routers/api_keys.py`，前缀 `/api`）
`GET/POST /api/keys`、`PATCH /api/keys/{id}`、`POST /api/keys/{id}/rotate`、
`POST /api/keys/{id}/revoke`、`POST /api/keys/{id}/restore`、`POST /api/keys/{id}/clone`、
`GET /api/keys/{id}/usage`、`DELETE /api/keys/{id}[?purge=true]`
旧路径 `/api/auth/keys*` 保留为兼容别名。

**界面**（`OpenNoter_frontend/src/pages/settings/KeysPage.tsx`）
创建区（名称/用途/有效期/限流/IP 白名单）、一次性明文展示（含环境变量建议）、
列表七列（名称/前缀/限制/最后使用/到期/状态/操作）、状态四态徽章、到期高亮、
搜索 + 状态筛选、每行图标操作 + 溢出菜单、底部汇总。

**验证**
- 后端测试：169 passed（新增 34 个用例）
- 线上逐端点验证：创建/编辑/克隆/轮换/吊销/恢复/用量/限流 429/IP 白名单 403 全部通过
- 存量密钥零中断：6 把 bcrypt 密钥在用后自动升级，未使用的保持原样

---

## 1. 摘要

当前「密钥管理」页只有一个名字输入框和一张四列表格，实际能力等于「创建 / 吊销」。核查后确认，
除了界面简陋，底下还压着三个实质问题：

1. **API Key 等价于完整用户会话** —— 一把 admin 的 key 能改全局 provider 配置、能管用户。
2. **过期机制形同虚设** —— 后端支持 `expires_days`，前端 service 层也支持，但 UI 从未暴露；
   线上 6 条 key 的 `expires_at` **全部为 NULL**，即全部永久有效。
3. **鉴权热路径昂贵** —— 每个 API 请求做一次 bcrypt(rounds=12)，实测约 300ms 量级；
   且每次请求都 `db.commit()` 写 `last_used_at`。

本文给出数据模型、鉴权层改造、接口契约、界面设计与分期路线，并单独论证限流/用量的存储选型。

---

## 2. 现状诊断

### 2.1 数据模型（`backend/app/db/models/api_keys.py`）

线上 `api_keys` 表实测结构：

| 列 | 类型 | 约束 | 备注 |
|---|---|---|---|
| id | INTEGER | PK | |
| user_id | INTEGER | FK → users.id | |
| name | VARCHAR | NOT NULL | |
| prefix | VARCHAR | NOT NULL | **secret 的派生值**（`plain[:len(PREFIX)+8]`），既当展示又当查找索引 |
| key_hash | VARCHAR | NOT NULL, UNIQUE | bcrypt，**带盐** |
| last_used_at | DATETIME | | |
| expires_at | DATETIME | | 实际全为 NULL |
| revoked | BOOLEAN | NOT NULL | 裸布尔，只能表达「有效/已吊销」两态 |
| created_at | DATETIME | server_default | |

### 2.2 界面（`OpenNoter_frontend/src/pages/settings/KeysPage.tsx`）

- 创建区：只有「名称」输入框 + 创建按钮
- 表格列：名称 / 前缀 / 最后使用 / 状态 / 删除
- 状态徽章只有两态：`revoked ? 已吊销 : 有效`（`KeysPage.tsx:182-187`）
  → **一把已过期但未吊销的 key 会显示绿色「有效」**
- `expires_at`、`created_at` 后端早已返回（`routers/auth.py:229-231`），表格**不渲染**
- 没有搜索、没有过滤、没有每行菜单、没有用量信息

### 2.3 鉴权（`backend/app/services/auth.py`）

```python
# generate_api_key: prefix 是 secret 的前 8 位派生值
prefix = plain[: len(API_KEY_PREFIX) + 8]
return plain, prefix, bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

# get_current_user: bcrypt 带盐 → 只能靠 prefix 查出所有候选再逐个 checkpw
candidates = db.query(ApiKey).filter(ApiKey.prefix == prefix, ApiKey.revoked.is_(False)).all()
for key in candidates:
    if verify_api_key(credential, key.key_hash):
        ...
        key.last_used_at = now
        db.commit()          # ← 每个请求一次写事务
        user = db.query(User).filter(User.id == key.user_id).first()
        return user          # ← 直接返回完整 User 对象
```

三个问题：

| 问题 | 后果 |
|---|---|
| `return user` 返回完整 `User` | key 的权限 **完全等同** 该用户。`require_admin` 查 `user.is_superuser`，所以 admin 的 key 能调 `/api/auth/admin/users`、能改全局配置 |
| bcrypt rounds=12 | 每次鉴权约 300ms 量级；且带盐 → 必须靠 `prefix` 查候选再逐个比对，无法按哈希直接索引查找 |
| 每请求 `db.commit()` | 线上 SQLite `journal_mode=delete`（未开 WAL），写事务要 fsync + 锁库；`busy_timeout=5000` |

### 2.4 缺失能力

轮换（rotate）、作用域（scopes）、按 key 的用量可见性、描述/备注字段、克隆、限流、IP 限制、
过期提醒、孤儿 key 识别 —— 全部没有。

---

## 3. 竞品对标（结论摘要）

| 产品 | 最值得抄的三点 |
|---|---|
| **GitHub fine-grained PAT** | 权限 = 持有者权限 **∩** 授予权限，**永不放大**；组织级最大生命周期（默认 366 天）+ 到期前 7 天提醒；`last_used_before/after` 过滤，「找出僵尸 key」变成一次查询 |
| **Stripe restricted key** | 逐资源 None/Read/Write；**逐 key 请求日志**反推实际用量（"我能做什么"不够，还要看"我实际用了什么"）；Note 字段记「这把 key 存在哪」；轮换带 7 天宽限期 |
| **Cloudflare token** | 凭据格式带 checksum，可被扫描器静态识别并**自动吊销 + 通知**；CIDR 条件；`issued_on/last_used_on/expires_on` 三时间戳 |
| **Anthropic** | **person-backed vs service-account-backed** 分离 —— 共享场景必须用可独立吊销的身份，因为「共享 personal key 就是一个人，他离职那天就全崩」；**Deactivate 与 Delete 两档动作** |
| **AWS IAM** | last-used 三元组（日期+区域+服务）；**15 分钟窗口内只记第一次**；最多 2 把活动密钥用于重叠轮换 |
| **GCP Service Account** | 检测到泄露 → **自动 disable** + `disable_reason=..._EXPOSED` + `extended_status_message` 写明泄露位置；Disable ≠ Delete |

跨产品共性：
- 生命周期至少三态（active / expired / revoked / disabled）
- 列表页必展示：名称 / 权限摘要 / 最后使用 / 到期时间 / 创建时间 / 状态徽章
- 过期默认值集中在 **90 天**（GCP/AWS 90 天、OpenAI 90 天选项、GitHub 组织 366 天、Stripe CLI 90 天），
  **「永不过期」必须是显式选择而非默认**
- 轮换一律是**重叠式**（新建 → 部署 → 验证 → 吊销旧）

---

## 4. 架构决策

### 4.1 决策一：API Key 等价于该用户的认证口令（**不引入 Principal**）

**最终决策**：不做主体抽象。API Key 命中后直接返回 `User`，它能做什么，用户就能做什么。

**为什么这个决策是划算的**：
- `get_current_user` 的返回类型不变 → 约 20 处 `require_admin` 调用点**一行都不用改**
- `get_optional_user` 不变 → note/chat/model/provider 五个 router 全不动
- 按 key 维度的能力（限流、IP 白名单、用量日志）通过 `request.state.api_key_id` 传递，
  不需要新的主体类型

**代价**：只有「作用域」这一项能力做不了 —— 没法说「这把 key 只能读笔记」。
这是有意的取舍，与「API Key = 用户认证口令」的定位自洽。

**兜底**：作用域字段照样存（`scopes`，默认空 = 全权，与改造前行为一致），
将来想给某把 key 收窄时，不用改数据模型。

**风险提示**（供后续决策参考，不影响当前实现）：这把 key 能读到的与本人完全一致，
包括**B 站 Cookie**（`get_downloader_cookie` 只要 admin）。如果这类 key 会交给第三方
agent 或跑在共享 CI 上，泄露时没有粒度可收 —— 这正是 Anthropic 区分
person-backed / service-account 的原因。**如果 key 只在自己机器上，这个模型完全没问题。**

### 4.2 决策二：`key_id` / `prefix` / `last_four` 职责分离

现状 `prefix = plain[:len(PREFIX)+8]` 把 8 个 secret 字符存进库并当查找索引，职责混着。

| 字段 | 性质 | 用途 | 示例 |
|---|---|---|---|
| `key_id` | **非秘密**，独立随机生成 | 唯一索引、日志关联、轮换链引用 | `bk_9Kd2mXq7vR4p` |
| `prefix` | 非秘密 | 界面展示，让人一眼认出是哪把 | `sk-bili-cPYYq5MC` |
| `last_four` | 非秘密 | 明文后 4 位，密钥管理器里肉眼核对 | `Xw29` |
| `key_hash` | **秘密** | 鉴权用 | SHA-256 hex |

`key_id` 不再从 secret 派生 —— 派生值会随 secret 泄露一起泄露。

### 4.3 决策三：哈希用 SHA-256 而非 bcrypt

**API Key 本身有 256 bit 熵**（`secrets.token_urlsafe(32)`），离线爆破不可行。bcrypt 的慢哈希是为
**低熵人类密码**设计的：它防的是「数据库泄露后被离线爆破」，而高熵 key 本来就爆破不动。
在这里 bcrypt 只买到 CPU 成本，还要付出两个额外代价：

1. **每次鉴权约 300ms** —— 放在热路径上不可接受
2. **带盐 → 无法按哈希索引查找** —— 必须靠 `prefix` 查出所有候选再逐个 `checkpw`（现状代码）

行业通行做法（Stripe / GitHub / Anthropic / Cloudflare 一致）：**SHA-256 + 常数时间比较**。

> 熵的类比是对的，但结论要反过来：key 熵本就足够高，不需要额外拉伸；慢哈希在这里不增加安全性。

**迁移路径（透明重哈希）**：新增 `hash_algo` 列，存量记为 `bcrypt`。鉴权时按 `hash_algo` 分派，
bcrypt 校验成功后**立即用 SHA-256 重算并回写**，下次就走快路径。用户无感，密钥不用重发。

```python
if key.hash_algo == "bcrypt":
    if not verify_api_key_bcrypt(credential, key.key_hash):
        raise ...
    key.key_hash = sha256_hex(credential)   # 透明升级
    key.hash_algo = "sha256"
    db.commit()
else:
    if not hmac.compare_digest(sha256_hex(credential), key.key_hash):
        raise ...
```

### 4.4 决策四：生命周期状态机（取代裸 `revoked` 布尔）

```
                 ┌──────────────┐
    创建 ───────▶ │    active    │ ◀── 轮换（新建一把，旧的转 revoked/rotated）
                 └──────┬───────┘
        到期(now>expires_at) │        手动吊销        泄露处置
                 ┌──────────┼──────────┐      ┌──────────────┐
                 ▼          ▼          ▼      │   revoked     │
            ┌────────┐ ┌─────────┐ ┌────────┐ │ reason:       │
            │ expired│ │ disabled│ │ revoked│ │ manual/rotated│
            └────────┘ └─────────┘ └────────┘ │ /leaked/policy│
                                                └──────────────┘
```

要点（对应 Anthropic / Cloudflare 的多态）：

- **过期 ≠ 吊销 ≠ 停用** —— 界面必须能区分。过期是时间到了自动失效；吊销是人工动作；
  停用是临时的、可恢复的。
- **`revoked_reason`** 记录原因（`manual` / `rotated` / `leaked` / `expired` / `policy`），
  对应 GCP 的 `disable_reason`，出事时能看出是人为轮换还是被判定泄露。
- **吊销是软删除**（保留记录以供审计），删除记录另设「彻底删除」操作。

### 4.5 决策五：作用域模型

资源级三档（`none` / `read` / `write`），不做端点级 —— OpenNoter 的资源边界清晰，端点级会过度复杂。

对齐现有 router 的初始资源集：

| 资源 | 覆盖端点 | read | write |
|---|---|---|---|
| `notes` | `/api/notes`、`/api/tasks/*` | 查任务、读笔记 | 建任务、删任务 |
| `chat` | `/api/chat/*` | 查状态 | 提问、建索引 |
| `models` | `/api/models*`、`/api/providers*` | 读列表 | 增删改模型/供应商 |
| `config` | `/api/config*`、`/api/transcriber*` | 读配置 | 改配置、下载模型 |
| `admin` | `/api/auth/admin/*`、`/api/monitor/*` | 查用户/监控 | 管用户 |

**实现**：新增 `require_scope(scope)` 依赖工厂。router 内读写混合时逐路由挂，均匀时挂 router 级
（`APIRouter(dependencies=[...])`），改动量可控。

### 4.6 决策六：轮换（rotate）—— 当前最大的能力缺口

**重叠式轮换**（Stripe / AWS / GCP 一致）：

1. `POST /api/keys/{id}/rotate` 创建新 key，**继承** name / description / scopes / 过期设置，
   并记录 `rotated_from_id` 指向旧 key
2. 旧 key 进入 `revoked`，`revoked_reason=rotated`
3. **可选宽限期**（Stripe 7 天）：宽限期内旧 key 仍有效，但列表里标记「即将吊销」，
   并在 `data` 里带 `grace_expires_at`

宽限期的两面性：计划内迁移受益，疑似泄露时也等于给攻击者 7 天 → **疑似泄露一律立即吊销**，
不排宽限期。

### 4.7 决策七：存储选型 —— SQLite vs Redis

这是本次最需要说清楚的取舍。先列事实（均已核对）：

| 事实 | 值 | 出处 |
|---|---|---|
| 后端进程数 | **1**（`uvicorn.run(app, host=..., port=...)` 无 workers 参数） | `backend/main.py:119` |
| 部署形态 | 单容器单副本，compose 无 `deploy.replicas` | `docker-compose.yml` |
| SQLite | 3.46.1，`journal_mode=delete`（**未开 WAL**），`busy_timeout=5000` | 线上实测 |
| Redis | `redis==5.2.1` 在 requirements 里，但**代码未引用、compose 无服务** | 全仓 grep |
| 后端内存上限 | 4g；宿主 15g 但可用仅 2.7g | `docker-compose.yml` / `free -m` |
| 实际负载 | 个人自用，笔记生成是长任务，API 请求量是「个位数/分钟」量级 | 观察 |

#### SQLite 方案

限流计数表 + UPSERT 原子自增（已实测 3.46.1 支持）：

```sql
CREATE TABLE api_rate_buckets (
  key_id      INTEGER NOT NULL,
  window_start INTEGER NOT NULL,   -- unix 秒，窗口起点
  count       INTEGER NOT NULL,
  PRIMARY KEY (key_id, window_start)
);

INSERT INTO api_rate_buckets (key_id, window_start, count) VALUES (?,?,1)
ON CONFLICT(key_id, window_start) DO UPDATE SET count = count + 1
RETURNING count;
```

用量明细表（持久、可查、可导出）。

**优点**

1. **零新增组件** —— 不引入新服务、不改 compose、不加运维面。自托管场景里每多一个服务
   就是多一份「它挂了怎么办」的心智负担
2. **单点真相** —— 密钥、用量、计数都在同一个 `.db` 里，备份逻辑不用改（现在就是备份一个文件）
3. **原子性有保证** —— UPSERT + RETURNING 是 SQLite 层面的原子操作，没有「INCR 成功但 EXPIRE 失败」
   这类经典竞态（Redis 里必须用 Lua 或 MULTI/EXEC 才能避开）
4. **历史可查** —— 「这把 key 上个月用了多少次、都在什么时间」这类问题一条 SQL 就能答，
   这正是 Stripe「逐 key 请求日志」的核心价值
5. **宿主资源够** —— 4g 上限的容器跑 SQLite 毫无压力

**缺点**

1. **写放大** —— 每个请求一行写入。SQLite 单写者，但个人自用场景量级完全够
   （WAL 模式下可支撑数千 TPS；本场景远低于此）
2. **与业务写竞争同一把锁** —— 需要配 `busy_timeout`，并建议**开 WAL**（读写不互斥）
3. **需要清理任务** —— 桶表和用量表要定期 prune（Redis 用 TTL 自动过期，SQLite 要自己扫）
4. **限流状态随进程重启丢失**（若用内存表）；若用 SQLite 表则不丢，但重启后残留的窗口要靠
   `window_start` 过期判断
5. **开 WAL 需要一次迁移** —— `PRAGMA journal_mode=WAL` 是持久属性，要写进启动流程并考虑
   老备份文件的兼容

#### Redis 方案

固定窗口用 `INCR rl:{key_id}:{minute}` + `EXPIRE`；**必须用 Lua 或 MULTI/EXEC 保证原子**，
否则 EXPIRE 失败会导致计数永不复位 —— 这把 key 之后就被永久限流了。滑动窗口/令牌桶用
Sorted Set + Lua，是 Redis 的主场。

**优点**

1. **热路径不碰数据库** —— 把计数完全挪出 SQLite 的单写者，避免与业务写抢锁
2. **TTL 原生** —— 过期自动清理，不用写 prune 任务
3. **算法表达力强** —— 滑动窗口日志、令牌桶在 Redis 里是一等公民
4. **可多副本共享** —— 未来扩容后端副本时限流状态天然共享
5. **吞吐上限高** —— 本场景用不上，但不会成为未来的天花板

**缺点**

1. **多一个要运维的服务** —— 部署、监控、鉴权、升级、备份、故障预案，全是新的面
2. **新的故障模式 —— Redis 挂了怎么办？**
   - 失败开放（fail-open）：限流形同不存在，但服务可用
   - 失败关闭（fail-closed）：请求全被拒，可用性归零
   - 两种都得显式选，不能默认
3. **内存与淘汰策略** —— Redis 内存吃紧时会淘汰 key；若被淘汰的是限流桶，等于限流被悄悄关掉
4. **不该把密钥放 Redis** —— 哈希必须留在 SQLite（Redis 数据默认不落盘，丢了不可恢复）
5. **宿主只剩 2.7g 可用** —— 再加一个常驻服务不是不能做，但性价比不划算
6. **现状是 requirements 里躺着、代码没用、compose 没有** —— 启用它等于把这摊「半吊子」补完

#### 结论与建议：**混合方案，不引入 Redis**（已实施）

| 用途 | 选型 | 理由 |
|---|---|---|
| **限流计数** | **进程内内存**（固定窗口 + 惰性清理） | 单进程单副本，进程内状态足够；零延迟、零 DB 写、零新依赖。重启丢失对限流语义完全可接受（AWS 的 last-used 采样也是「够用就好」） |
| **用量明细与统计** | **SQLite 表** | 需要持久历史（跨重启、跨月查询），这是数据不是状态 |
| **last_used_at 降频** | **进程内缓冲 + 定时刷盘** | 借鉴 AWS「15 分钟内只记第一次」；退出前强制刷盘 |
| **密钥材料（hash）** | **SQLite**（不变量） | Redis 默认不落盘，丢了不可恢复 |

**什么情况下应该改用 Redis**：① 后端要跑多副本/多 worker；② 单 key 请求量到万级/秒；
③ 要做跨边缘节点的统一限流（例如 SafeLine 那层也要按 key 限流 —— 但那更适合在 SafeLine 侧做）。

**顺带建议**：把 `journal_mode=WAL` 打开（读写不互斥，降低与业务写的锁竞争），
`synchronous=NORMAL`（配合 WAL，减少 fsync 次数）。这两项对当前 SQLite 写放大问题有直接改善，
且改动极小。

---

## 5. 数据模型

```python
class ApiKey(Base):
    __tablename__ = "api_keys"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    name        = Column(String, nullable=False)          # 按使用者命名，可改
    description = Column(String, nullable=True)           # 用途备注（Stripe Note）

    # ---- 身份与展示（非秘密） ----
    key_id      = Column(String, unique=True, index=True)   # bk_ + 12 字符，独立随机
    prefix      = Column(String, nullable=False)            # sk-bili-xxxxxxxx，展示用
    last_four   = Column(String, nullable=False)            # 明文后 4 位

    # ---- 密钥材料（秘密） ----
    key_hash    = Column(String, unique=True, index=True)   # SHA-256 hex
    hash_algo   = Column(String, nullable=False, default="sha256")  # sha256 | bcrypt(存量)

    # ---- 作用域与约束 ----
    scopes         = Column(JSON, nullable=True)            # {"notes": "write", ...}
    allowed_ips    = Column(JSON, nullable=True)            # ["1.2.3.0/24", ...]
    rate_limit_rpm = Column(Integer, nullable=True)

    # ---- 生命周期 ----
    status          = Column(String, nullable=False, default="active")
                     # active | expired | disabled | revoked
    revoked         = Column(Boolean, nullable=False, default=False)  # 与 status 保持一致
    revoked_at      = Column(DateTime, nullable=True)
    revoked_reason  = Column(String, nullable=True)   # manual|rotated|leaked|expired|policy
    grace_expires_at = Column(DateTime, nullable=True)  # 轮换宽限期截止
    expires_at      = Column(DateTime, nullable=True)
    not_before      = Column(DateTime, nullable=True)  # 预约生效
    rotated_from_id = Column(Integer, nullable=True)    # 轮换链

    # ---- 活动 ----
    last_used_at         = Column(DateTime, nullable=True)
    last_used_ip         = Column(String, nullable=True)
    last_used_endpoint   = Column(String, nullable=True)
    last_used_user_agent = Column(String, nullable=True)
    use_count            = Column(Integer, nullable=False, default=0)
    last_rotated_at      = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
```

配套新表：

```python
class ApiKeyUsage(Base):
    """按 key 的调用明细。限流计数不入此表（那是状态，不该无限增长）。"""
    __tablename__ = "api_key_usage"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    key_id     = Column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), index=True)
    method     = Column(String)
    path       = Column(String)
    status_code = Column(Integer)
    ip         = Column(String)
    duration_ms = Column(Integer)
    created_at = Column(DateTime, server_default=func.now(), index=True)
```

**保留期**：明细默认留 90 天，聚合数据（每日计数）长期留。

---

## 6. 迁移方案

沿用 `backend/migrate_users.py` 的**幂等 + PRAGMA 检查 + ALTER TABLE** 模式（该脚本已有先例，
线上执行过一次）。新增 `backend/migrate_api_keys.py`：

1. `init_db()` 建新表（`api_key_usage`）
2. 逐列检查 `api_keys` 是否存在新列，缺则 `ALTER TABLE ADD COLUMN`
3. 存量行回填：
   - `key_id`：按 id 生成稳定值 `bk_legacy{id:04d}`（保证唯一且可追溯）
   - `last_four`：留空（bcrypt 不可逆，存量拿不到明文后 4 位）
   - `hash_algo`：`'bcrypt'`
   - `status`：`'active'`
   - `scopes`：按「继承用户全部权限」初始化（与现状行为一致，不改变存量 key 的能力）
4. `revoked=1` 的存量行：`status='revoked'`, `revoked_at=created_at`, `revoked_reason='manual'`
5. **不强制存量 key 立即过期** —— 加了过期会让在跑的脚本突然挂掉；改为在 UI 上标「永久有效（无过期时间）」
   并提示用户逐步收敛

**灰度**：`hash_algo` 双轨运行，存量 key 首次成功鉴权时透明重哈希（§4.3）。

---

## 7. 接口契约

统一沿用现有 `{code, msg, data}` 信封与真实 HTTP 状态码（2026-10-20 已统一）。

### 7.1 列出密钥

```
GET /api/keys
```

`data` 每项：

```json
{
  "id": 6,
  "key_id": "bk_9Kd2mXq7vR4p",
  "name": "obsidian-plugin",
  "description": "Obsidian 插件同步用",
  "prefix": "sk-bili-cPYYq5MC",
  "last_four": "Xw29",
  "scopes": {"notes": "write", "chat": "read"},
  "status": "active",
  "revoked": false,
  "revoked_reason": null,
  "grace_expires_at": null,
  "expires_at": "2027-01-18T08:00:00Z",
  "not_before": null,
  "rotated_from_id": null,
  "last_used_at": "2026-10-20T08:54:29Z",
  "last_used_ip": "1.2.3.4",
  "last_used_endpoint": "POST /api/notes",
  "use_count": 37,
  "rate_limit_rpm": 60,
  "allowed_ips": [],
  "created_at": "2026-10-20T08:02:30Z"
}
```

### 7.2 创建

```
POST /api/keys
{
  "name": "obsidian-plugin",
  "description": "Obsidian 插件同步用",
  "expires_days": 90,          // 可选，默认 90；传 null 显式表示永久
  "scopes": {"notes": "write"},
  "allowed_ips": [],
  "rate_limit_rpm": 60
}
```

响应一次性返回明文（沿用现状）：

```json
{"id": 7, "name": "obsidian-plugin", "key": "sk-bili-...", "key_id": "bk_...",
 "usage_hint": "export OPENNOTER_API_KEY=sk-bili-..."}
```

### 7.3 轮换

```
POST /api/keys/{id}/rotate
{ "grace_days": 7 }        // 0 = 立即吊销旧的
```

返回新 key 明文（一次性）+ 旧 key 的宽限期截止时间。

### 7.4 吊销 / 恢复 / 彻底删除

```
POST   /api/keys/{id}/revoke     { "reason": "manual" }
POST   /api/keys/{id}/restore    // 仅 revoked(manual) 可恢复
DELETE /api/keys/{id}            // 彻底删除（保留审计则用软删）
```

### 7.5 编辑

```
PATCH /api/keys/{id}
{ "name": "...", "description": "...", "scopes": {...},
  "expires_at": "...", "rate_limit_rpm": 60, "allowed_ips": [...] }
```

### 7.6 用量

```
GET /api/keys/{id}/usage?days=30
```

返回：每日调用次数、最近 N 条明细、Top 端点分布。
（Stripe「逐 key 请求日志」的轻量版 —— 先做聚合计数 + 最近 50 条，不做全量存储）

### 7.7 克隆

```
POST /api/keys/{id}/clone
{ "name": "obsidian-plugin-staging" }
```

继承 description / scopes / 过期设置，不继承明文。

---

## 8. 界面设计

### 8.1 创建区（折叠式，不占首屏）

| 字段 | 控件 | 默认 |
|---|---|---|
| 名称 | 输入框，占位符 `ci-github-actions` / `obsidian-plugin` | 必填 |
| 用途说明 | 输入框 | 选填 |
| 有效期 | 下拉 7/30/90/180/365 天 + 永不过期 | **永不过期** |
| 权限 | 资源 × none/read/write 分段控件，预设「只读」「完全访问」 | 完全访问 |
| IP 白名单 | CIDR 列表 | 空（不限） |
| 每分钟限流 | 数字输入 | 空（不限） |

创建后的**一次性展示区**（强化引导，对齐 Stripe/GitHub）：

- 醒目警示「仅此一次显示完整内容」
- 完整 key + 复制按钮
- **建议的环境变量名**：`export OPENNOTER_API_KEY=sk-bili-...`
- 建议存入密码管理器的提示

### 8.2 列表区

```
名称 | 前缀 | 权限 | 最后使用 | 到期 | 状态 | ⋯
```

- **状态徽章四态**：`有效`(positive) / `即将到期`(warning) / `已过期`(neutral) / `已吊销`(negative)
  —— 修掉「过期 key 显示绿色有效」
- **到期列**：< 24h 红色高亮，< 7 天黄色高亮，显示「X 天后到期」
- **权限列**：chips，如 `notes:rw · chat:r · admin:–`
- **最后使用**：相对时间 + 绝对时间 tooltip；从未使用显示「从未使用」
- **每行 overflow 菜单**（而非裸露的删除图标）：轮换 / 编辑 / 用量详情 / 克隆 / 吊销
- **筛选行**：搜索框（名称/前缀模糊）+ 状态 tabs（全部/有效/即将到期/已过期/已吊销）
- **底部汇总**：「3 把有效 · 1 把 7 天内到期 · 2 把从未使用」
- **孤儿 key 提示**：`last_used_at` 为空或早于 90 天 → 单独分组，提示「疑似可删除」
  （GitHub "never used" + AWS Access Analyzer 同思路）

### 8.3 命名规范

沿用 Anthropic 的指导：**按使用者命名**（`prod-support-bot`、`ci-evals`、`local-dev`），
带环境前缀（`prod-` / `staging-`）。避免 `test`、`my key`、`default`。
名称**创建后可改**。

---

## 9. 分期路线

以下阶段**已全部实施**（阶段五的泄露检测未做，见备注）：

| 阶段 | 内容 | 理由 |
|---|---|---|
| **一** | 状态四态 + 到期/创建时间列 + 过期高亮 + 区分「从未使用」+ 创建时默认 90 天 + 暴露 `expires_days` | 纯 UI + 小改，无破坏性；**直接修掉「过期 key 显示为有效」这个错误** |
| **二** | `Principal` 抽象 + scopes 生效 + `is_admin` 不再随用户继承 + SHA-256 哈希（含透明重哈希）+ `key_id` 分离 + `last_used_at` 降频 | 修掉最实质的安全问题与两个性能问题；改动集中在鉴权层 |
| **三** | 轮换（带宽限期）+ 克隆 + 编辑名称/权限 + 描述字段 | 补齐「密钥管理」相对「创建/删除」的缺失能力 |
| **四** | IP 白名单 + 进程内限流 + 用量统计与详情视图 + 孤儿 key 识别 | 纵深防御与运维可见性 |
| **五** | 泄露检测：自查（扫应用日志）→ 申请 GitHub Secret Scanning 合作方计划（正则 + 验签 webhook + 自动吊销 + 通知） | 需公网端点与外部对接，放最后。**本次未做**，正则与验签要点见附录 A |

**建议先做阶段一** —— 收益明确、风险低、当天可上线，且能立刻让页面从「简陋」变得「像个管理界面」。

---

## 10. 风险与开放问题

| # | 问题 | 处理 |
|---|---|---|
| 1 | **存量 key 若强制加过期会在跑的脚本突然挂掉** | 已处理：迁移不强制过期（存量行 `expires_at` 保持 NULL），默认也是「永不过期」；UI 的「到期」列显示「永久」 |
| 2 | 切 SHA-256 后 bcrypt 存量 key 如何验证 | `hash_algo` 双轨 + 透明重哈希（§4.3） |
| 3 | 作用域粒度可能过粗 | 资源级起步；若某资源内读写需区分，再细化到端点级 |
| 4 | 用量明细表的写放大 | WAL + 定期 prune + 只在 API Key 请求时记（网页登录态不记） |
| 5 | 宽限期对泄露场景是负资产 | 疑似泄露一律立即吊销，不排宽限期；UI 明确提示 |
| 6 | `require_admin` 语义变更可能影响现有前端 | **未发生**：最终决策是不引入 Principal，返回类型保持 `User`，调用点无感 |
| 7 | 限流进程内状态随重启丢失 | 限流语义可接受；若不接受则改用 SQLite 桶表（见 §4.7） |
| 8 | 作用域收窄会改变现有 key 的能力 | **未发生**：最终决策是 API Key 等价于用户口令，作用域字段存但不生效（默认空 = 全权），与改造前行为完全一致 |

---

## 附录 A：泄露检测对接要点（阶段五）

若接入 GitHub Secret Scanning 合作方计划，需要提供三样东西：

1. **精确正则**（当前格式是 `secrets.token_urlsafe(32)`，base64url 含 `-` `_`）：
   `sk-bili-[A-Za-z0-9_-]{43}`
   —— GitHub 明确要求「尽量精确，以减少误报」
2. **公网 webhook 端点**接收泄露上报
3. **验签**：`Github-Public-Key-Identifier` + `Github-Public-Key-Signature`，
   ECDSA NIST P-256 / SHA-256。**必须用原始 body 验签**，不能先 parse 再 stringify
   （空格/顺序变化会导致验签失败）

收到上报后的三步：**验签 → 吊销密钥（`revoked_reason='leaked'`）→ 通知密钥所有者**。

另一种更省事的思路（GCP 的做法）：**自己扫**。周期性扫自己的应用日志与错误追踪服务
（Sentry/Datadog 的请求头、堆栈里常带 key）里是否出现 `sk-bili-` 前缀。
行业观察是「一半的真实泄露是自己通过日志造成的，不是第三方提交造成的」。

## 附录 B：字段与竞品对照

| 字段 | GitHub | Stripe | Cloudflare | AWS | 本方案 |
|---|---|---|---|---|---|
| name | ✅ | ✅ | ✅ | ✅ | ✅ |
| description/note | ✅ | ✅ | — | — | ✅ |
| prefix/redacted | ✅ | ✅ | ✅ | ✅ | ✅ |
| last_four | ✅ | ✅ | ✅ | ✅ | ✅ |
| key_id（独立公开 ID） | ✅ token_id | — | ✅ id | ✅ AccessKeyID | ✅ |
| scopes | ✅ 细粒度 | ✅ 逐资源 | ✅ 权限组 | ✅ policy | ✅ 资源级 |
| status 多态 | ✅ | ✅ expire/rotate | ✅ active/disabled/expired | ✅ active | ✅ 四态 |
| expires_at | ✅ | ✅ | ✅ | —（刻意不用） | ✅ 默认 90 天 |
| revoked_reason | — | — | — | — | ✅ |
| last_used_at | ✅ | ✅ | ✅ | ✅ | ✅ |
| last_used_ip | — | ✅(日志) | ✅ | ✅ 区域+服务 | ✅ |
| use_count | — | ✅ | — | — | ✅ |
| rotation | ✅ regenerate | ✅ 7 天宽限 | ✅ 滚动 | ✅ 2 把重叠 | ✅ 可选宽限 |
| clone | — | ✅ duplicate | — | — | ✅ |
| 孤儿 key 识别 | ✅ never used | ✅ 180 天降权 | — | ✅ unused analyzer | ✅ |
| 自动吊销（泄露） | ✅ | — | ✅ | ✅ Trusted Advisor | 阶段五 |
