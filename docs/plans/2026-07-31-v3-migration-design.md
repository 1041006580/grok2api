# v2 → v3 全量迁移设计

- 日期:2026-07-31
- 状态:设计已确认(三部分均获认可),实施计划待制定
- 决策来源:2026-07-31 Codex 会话(id `019fb5cc-2564-7651-8f73-3dcbdc72ae6d`),设计三部分在会话中逐条获得用户认可
- 基线:`upstream/v3.0.11`(commit `0901045`);现网 v2 为 `f989f27`(2026-06-16,v2.0.4.rc4)

## 1. 背景与路线决策

上游 chenyme/grok2api 已从 v2(Python/FastAPI + 静态页面)完全重写为 v3(Go 后端 + React/TypeScript 前端),最新版本 v3.0.11。本地 fork 领先 7 个提交、落后 461 个提交,直接对比涉及 696 个文件(约 14.1 万行新增、4.1 万行删除)。语言与架构整体替换,Redis 数据模型、API 鉴权和媒体模型均不兼容,无法通过 Git 合并追平。

### 候选路线对比

- **方案 A:把上游 v3 merge 到当前 v2** — 不采用。696 个文件的语言级替换,即使解决 Git 冲突也无法解决数据模型不兼容,最终留下难以维护的混合历史。
- **方案 B:上游 v3 作为新基线,重新实现必要扩展** — ✅ **已采用**。最大限度继承上游更新,最终架构干净;代价是需要认真完成一次数据迁移和 Provider 扩展。
- **方案 C:v3 不改,另建 xAI Sidecar** — 不采用。会造成两套 Key 管理、模型路由、审计、限流和错误处理,xAI 无法自然显示在 v3 管理端,难以实现可靠的按模型兜底。

### 已确认的关键决策

| 议题 | 决策 |
|---|---|
| 新基线 | `upstream/v3.0.11`,不合并旧 `main` 历史 |
| 现网 v2 | 冻结为 `legacy/v2-production` 分支 + 不可移动 tag,只作迁移来源和回滚版本 |
| v3 持久化 | 独立 PostgreSQL(账号、配置、Key、媒体元数据)+ 独立 Redis(仅运行态) |
| 迁移方式 | 一次性完整停机离线迁移(约 40~50 个账号),不做双写/CDC |
| 功能取舍 | 全面采用 v3(React Creative Console、图库、标准 Docker);不迁移旧 WebUI/Masonry、ChatKit/Voice、Vercel/Render 部署 |
| 唯一新增 | 官方 xAI API Key 池,实现为 v3 第四个 Provider `xai_official`,按模型配置参与兜底 |
| 媒体数据 | 文件 + 元数据都迁入 v3 Gallery;旧视频标记 `legacy-import`,不伪造已丢失的任务信息 |
| 首期不做 | Voice、Files/Collections、视频编辑、视频延长、Batch API(后期分别单独设计) |
| 对外 API | 以 v3 标准接口为准,不保留旧 Admin/WebUI API;仅为历史媒体保留兼容 URL |
| 部署 | GitHub Actions 构建 Docker 镜像 → Zeabur 部署;媒体目录挂载在 Zeabur 持久化卷 |

## 2. 设计第 1 部分:主线、部署、数据迁移与回滚

核心原则是"新建 v3,不改造现网 v2;离线迁移,不做双写"。

```text
v2 Redis + v2 媒体目录
          │
          ▼
只读检查/导出工具
          │
          ├── 加密凭据数据
          └── 非敏感校验报告
          │
          ▼
v3 迁移工具
          │
          ├── PostgreSQL:账号、配置、Key、媒体元数据
          ├── 独立 Redis:运行态
          └── v3 媒体目录:图片、视频文件
```

### Git 主线

- 当前 `f989f27` 固定为 `legacy/v2-production`。
- 创建不可移动的 `v2-final-<日期>` 标签。
- v3 开发分支直接从 `upstream/v3.0.11` 创建。
- 当前 `main` 在正式切换前仍代表 v2 生产版;v3 验收后才提升为新的 `main`。
- 不把旧 `main` merge 进 v3。
- v3 自定义改动保持为独立提交组,便于后续持续 rebase/merge 上游:
  1. `xai_official` Provider
  2. v2 Redis 迁移工具
  3. 旧媒体导入和兼容访问
  4. 部署配置

### 双环境部署

迁移期间同时保留两套完全隔离的环境:

- v2:原 Redis、原媒体目录、原服务端口。
- v3:独立 PostgreSQL、独立 Redis、独立媒体目录、临时端口。
- v3 不直接访问或修改 v2 Redis;只有迁移命令以只读方式连接 v2 Redis。
- 在切换入口前,可以不限时间测试 v3。
- 因为允许停机,不引入 CDC、双写、Redis Keyspace 监听或请求镜像。

### 迁移工具

在 v3 Go 工程中增加独立命令:

```text
grok2api-migrate-v2 inspect
grok2api-migrate-v2 export
grok2api-migrate-v2 import
grok2api-migrate-v2 verify
```

要求:

- 支持 `--dry-run`;可重复执行,按稳定来源标识保持幂等。
- 不在日志中打印 SSO Token、官方 xAI Key、Cookie 或完整 Redis URL。
- 敏感导出文件必须加密;非敏感报告只保存数量、哈希和映射结果。
- 通过 v3 服务层/Repository 写入,确保凭据经过 v3 的加密机制,不直接拼 SQL。

### 账号迁移

映射规则:

- v2 Token → v3 `grok_web` SSO 凭据。
- `basic/super/heavy` → v3 Web tier。
- 启用/停用状态尽量保持;`nsfw` 等有明确含义的标签转换为 v3 对应字段。
- 创建时间、最后使用时间等可映射字段保留。
- Token 仅用不可逆指纹做迁移前后核对。

以下数据完整保存到加密迁移档案,但**不**作为 v3 当前状态直接写入(与 v3 的 Provider、额度窗口和健康模型语义不同,强行写入会让 v3 启动时得到错误状态;导入后由 v3 从上游重新同步额度和健康):

- v2 quota 快照、冷却截止时间、连续失败次数、revision 及 revision log、旧版内部状态原因。

### 配置迁移

配置不按 Redis Hash 原样复制,而是生成逐项映射报告:

- 对外 URL → v3 Public API Base URL。
- 旧 API Key → 创建对应的 v3 客户端 Key;建议同时生成新的 `g2a_...` Key。
- 代理和资源代理 → v3 对应作用域的 Egress 节点。
- Cloudflare/FlareSolverr → v3 Clearance 设置。
- 超时、重试、媒体容量 → 对应 v3 运行设置。
- xAI 官方 Key → 新 `xai_official` Provider 凭据。
- 无对应项的旧配置保留在报告中,标记为"已废弃",不静默丢弃。

### 图片和视频迁移

v2 媒体索引只保存文件名、类型、大小和时间,没有永久保存视频提示词、账号、模型等完整任务信息,因此:

- 扫描 `data/files/images`、`data/files/videos` 和 `local_media_cache.db`。
- 计算每个文件的 MIME、大小和 SHA-256;尽量保留原文件 ID。
- 文件通过临时文件加原子重命名写入 v3 媒体目录。
- 图片导入为正常 `media_assets`;视频导入为 `kind=video` 的媒体资产。
- v3 Video Gallery 增加"历史导入"类型展示这些视频。
- 不制造虚假的提示词、账号、模型和用量记录。
- 保留旧 `/v1/files/image?id=...`、`/v1/files/video?id=...` 兼容入口,重定向到 v3 媒体资源。

### 切换流程

1. 完成多次 dry-run。
2. 停止 v2 API 和后台。
3. 备份 Redis RDB/AOF、媒体目录和环境配置。
4. 执行最终导出。
5. 初始化空的 v3 PostgreSQL。
6. 执行账号、配置、xAI Key、媒体导入。
7. 执行完整校验。
8. 让 v3 同步账号额度和模型。
9. 执行 Chat、Responses、图片、视频和 xAI 回退测试。
10. 切换反向代理或服务端口。
11. 保留 v2 服务、Redis、镜像和备份,不立即删除。

### 回滚边界

切换失败时:停止 v3 → 入口切回 v2 → 启动原 v2 服务。原 Redis 和媒体目录没有被修改,不需要反向迁移。

v3 切换后的新数据不会自动回写 v2。正式确认前不删除 v2;如需长时间观察,暂缓在 v3 后台进行不可逆的账号删除和配置重构。

## 3. 设计第 2 部分:`xai_official` Provider

官方文档已确认 API Key 支持:`GET /v1/models` 及语言/图片/视频模型目录、Responses、Chat Completions、compact、stored responses、图片生成和 JSON 图片编辑、异步视频生成与状态轮询。因此实现为完整 Provider,而不是临时 HTTP 兜底。

```text
客户端请求
   │
   ├── 显式模型 XAI/grok-4.5
   │          └── 只允许 xai_official
   │
   └── 普通模型 grok-4.5
              ├── Build 路由
              ├── Web 路由
              ├── Console 路由
              └── XAI 路由(管理员启用后作为最后候选)
                           │
                           ▼
                 xAI Key 选择与并发门禁
                           │
                           ▼
                     https://api.x.ai/v1
```

### 领域模型

新增:Provider `xai_official`、模型命名空间 `XAI/`、认证类型 `api_key`、独立出口作用域 `xai_official`、无远端额度窗口策略 `quota_unavailable`。

API Key **不**建立另一套孤立的 `xai_keys` 表,而是复用 v3 的 Provider Account 体系:

- Key 加密保存到 `EncryptedAccessToken`;`AuthType` 标记为 `api_key`。
- 使用 Key 的 SHA-256 指纹作为稳定 `SourceKey`。
- 复用现有账号优先级、启停、最大并发、失败冷却和选号逻辑。
- PostgreSQL 只保存密文、指纹和脱敏显示值;管理 API 列表不返回完整 Key。

这样 xAI Key 自然进入账号管理、模型发现、并发控制、审计和路由系统。

### 首期能力范围

| v3 对外接口 | xAI Official 实现 |
|---|---|
| `/v1/responses` | 原生转发 |
| `/v1/responses/compact` | 原生转发 |
| stored response GET/DELETE | 原生转发并保持 Key/Provider 归属 |
| `/v1/chat/completions` | 转换或原生 Chat 接口 |
| `/v1/messages` | 转换为官方 Responses |
| `/v1/images/generations` | 官方 JSON 接口 |
| `/v1/images/edits` | 将 v3 输入转换为官方 JSON,不向官方发送 multipart |
| `/v1/videos/generations` | 官方异步创建 |
| `/v1/videos/{id}` | 官方轮询后归档到本地媒体存储 |

首期不实现:Voice、Files/Collections、视频编辑、视频延长、Batch API(当前 v3 没有对应的统一公开接口,首期加入会扩大维护范围)。

### 复用上游代码

上游 Build Adapter 已实现 `api.x.ai/v1` 的 Responses 调用、compact、视频创建/轮询、错误诊断、reasoning replay、usage 解析和流式响应处理,但绑定 Build OAuth Token。不复制这些文件建第二套协议实现,而是先抽取共享的 Official API Transport:

```text
provider/xaiapi
├── client.go
├── responses.go
├── models.go
├── images.go
├── video.go
├── errors.go
└── usage.go
```

Build Provider 的 XAI fallback 与新 `xai_official` Provider 共用该 Transport;Build 继续使用 OAuth Token,XAI Official 使用管理员导入的 API Key。上游协议变化只维护一处。

### Key 管理

管理端支持:单个创建、JSON/JSONL 批量导入、修改名称/优先级/并发/启用状态、替换 Key、删除、手工验证、批量验证、查看最近成功/失败/冷却和错误摘要。

创建或替换 Key 时的验证流程:

1. 使用 Key 请求官方 `/v1/models`。
2. `200`:Key 有效并同步可用模型。
3. `401`:拒绝保存或标记 `reauthRequired`。
4. `403`:Key 有效但权限不足,保存为受限状态并展示原因。
5. 网络或 `5xx`:允许保存,但标记为"待验证",不能静默判定 Key 无效。

从 v2 迁移的 xAI Key 走同一验证流程。

### 动态模型发现

每个启用 Key 分别调用 `/v1/language-models`、`/v1/image-generation-models`、`/v1/video-generation-models`,同步后形成:

- `XAI/<model>` 内部路由;每个模型支持哪些 Key。
- 文本、图片、图片编辑或视频能力;官方别名;当前官方价格信息快照。
- 不同 Key 的模型权限不同时,只把请求分配给实际支持该模型的 Key。

### 按模型参与兜底

兜底不设全局总开关,依赖模型路由:

- 管理员启用 `XAI/grok-4.5` 后,无前缀的 `grok-4.5` 才能把 XAI 作为候选。
- 禁用该 XAI 路由后,该模型不再使用官方 Key。
- 显式请求 `XAI/grok-4.5` 时,只走官方 API。
- 客户端 Key 的 Provider Scope 也必须允许 `xai_official`,否则 fail-closed。
- XAI 默认排列在 Build、Web、Console 之后。

以下情况禁止跨 Provider 兜底:

- 请求模型或能力不兼容。
- 已绑定另一个 Provider 的 stored response。
- compact/encrypted reasoning 内容属于另一个 Provider。
- 上游已经接受图片或视频生成任务。
- 响应已经向客户端开始输出。
- 内容审核、参数错误等确定性客户端错误。

### 错误与轮换规则

- `401`:Key 失效,标记 `reauthRequired`,尝试其他 Key。
- `403`:优先视为模型/团队权限不足,不直接永久禁用整个 Key。
- `429`:读取 `Retry-After`,冷却该 Key,安全时尝试下一个 Key。
- 网络错误、连接超时:按指数退避并切换 Key。
- `5xx`:仅在确认请求尚未提交时重试。
- 参数错误、内容审核拒绝:不换 Key、不跨 Provider 重试。
- 图片/视频已创建但本地下载失败:只重试下载,绝不重新生成。

### 媒体处理

官方图片 URL 是临时地址,成功响应后:立即下载 → 校验 MIME/大小/安全上限 → 计算 SHA-256 → 写入 v3 媒体目录 → 创建 `media_assets` → 按客户端要求返回本地 URL 或 base64。视频完成后采用相同归档方式。官方 API Key 流程直接下载官方结果 URL,不要求公网暴露 v3 的一次性 `upload_url`。

### 用量和费用

- 文本请求使用官方返回的 token usage;图片按官方返回数量和模型价格计算。
- 视频优先使用响应中的 `cost_in_usd_ticks`;无官方费用字段时标记为 `estimated`,不伪装成权威费用。
- 所有记录进入 v3 Audit,关联客户端 Key、模型路由和具体 xAI Key。
- 日志和审计不保存完整官方 Key。

官方依据:[Quickstart](https://docs.x.ai/developers/quickstart)、[Models API](https://docs.x.ai/developers/rest-api-reference/inference/models)、[Chat and Responses](https://docs.x.ai/developers/rest-api-reference/inference/chat)、[Context Compaction](https://docs.x.ai/developers/advanced-api-usage/context-compaction)、[Imagine API](https://docs.x.ai/developers/model-capabilities/imagine)、[Video API](https://docs.x.ai/developers/rest-api-reference/inference/videos)。

## 4. 设计第 3 部分:API 兼容、管理端、安全与验收

### 对外 API 策略

首期以 v3 标准接口为准,不长期背负完整 v2 兼容层。

保留:`/v1/models`、`/v1/responses`、`/v1/responses/compact`、`/v1/chat/completions`、`/v1/messages`、`/v1/images/generations`、`/v1/images/edits`、`/v1/videos/generations`、`/v1/videos/{id}`。

不保留:旧 Admin API、旧 WebUI API、`POST /v1/videos`、ChatKit/Voice 路由、旧配置接口。调用方需要改用 v3 的视频接口和客户端 Key。

### 旧媒体 URL 兼容

保留两个只读兼容入口,保证历史图片、视频仍可访问:

```text
GET /v1/files/image?id=<legacy-id>
GET /v1/files/video?id=<legacy-id>
```

不实现第二套文件读取逻辑:校验旧 ID → 查询迁移后的 `media_assets` → 调用 v3 现有媒体服务返回内容,使用与 v3 标准媒体接口相同的 MIME、大小限制和安全响应头。新请求只返回 v3 标准媒体 URL(`/v1/media/images/{asset_id}`、`/v1/media/videos/{asset_id}`)。

### API Key 和管理员迁移

v2 的单个 `app.api_key` 不继续作为长期鉴权机制:

- 迁移工具记录其不可逆指纹;在 v3 创建名为 `legacy-v2-client` 的新客户端 Key,生成符合 v3 格式的 `g2a_...` 新密钥。
- 迁移报告中提示需要替换的调用端;原 Key 不打印、不写日志、不作为兼容后门。

v2 的 `app.app_key` 不直接转成管理员密码:

- v3 使用新的 bootstrap 管理员和强密码;首次登录后删除或禁用 bootstrap 配置。
- 管理员鉴权完全使用 v3 JWT/刷新令牌体系。

### React 管理端

在 v3 管理端扩展现有页面,不增加独立的旧式管理页。

- **账号页面**:新增 `xAI Official` Provider 标签;显示名称、Key 指纹、启用状态、验证状态、最近成功、最近错误和冷却时间;提供创建、批量导入、替换、启停、验证和删除;Key 输入框只用于创建或替换,保存后不再回显完整值。
- **模型页面**:展示 `XAI/<model>` 路由及文本/图片/图片编辑/视频能力、由哪些 xAI Key 支持;支持启停某个 XAI 模型路由;无前缀模型能否回退到 XAI 由该路由是否启用决定。
- **客户端 Key 页面**:Provider Scope 增加 `xai_official`,默认 fail-closed(未明确允许就不能使用 XAI);继续支持模型白名单、RPM、并发、费用和到期限制。
- **设置页面**:Egress Scope 增加 `xai_official`;可配置超时、健康检查间隔和模型同步间隔;官方 Base URL 固定为 `https://api.x.ai/v1`,首期不允许通过后台修改,降低凭据被发送到恶意地址的风险。
- **Gallery**:正常展示 v3 新生成的媒体;旧图片按正常资产显示;旧视频增加"历史导入"标识,只显示真实存在的信息(文件、大小、时间、MIME、哈希),不展示伪造的提示词、账号、模型或费用。

### 安全边界

官方 xAI Key:

- 使用 v3 `credentialEncryptionKey` 加密;数据库不得出现明文 Key。
- 日志、审计、错误响应只显示短指纹;管理 API 不返回明文。
- 导出凭据必须使用现有受保护的显式导出流程。

外部媒体下载:

- 仅允许 HTTPS;禁止 URL 用户信息;禁止访问 loopback、私网、链路本地和云元数据地址。
- 每次重定向都重新校验;限制重定向次数、响应大小和下载时间。
- 校验 MIME 与真实文件类型;优先限制到 xAI 官方媒体域名。
- 下载使用独立 Egress Scope,不携带 API Key。

请求处理:

- 不把客户端提供的 Authorization 转发到 xAI;只注入当前选中的官方 Key。
- 错误正文在记录前脱敏和截断;不把完整请求体长期写入审计。
- compact 和 stored response 必须绑定原 Provider、客户端 Key 和账号,防止跨 Provider 重放加密内容。

### 可观测性

每次尝试记录:请求 ID、客户端 Key ID、公开模型与真实 XAI 模型、xAI Key 内部 ID 及脱敏名称、是否显式调用或后备调用、前一个 Provider 的失败分类、排队/首字节/总耗时、token/image/video usage、权威或估算费用、最终状态。

新增指标:XAI 请求成功率、各 Key 的 401/403/429/5xx、Key 冷却次数、模型发现成功率、后备路由触发和成功次数、图片/视频下载失败次数、因幂等性保护而阻止重试的次数。

任何指标标签都不能包含完整 Key、Token、提示词或用户内容。

### 测试范围

**后端单元测试**:API Key 加密/解密/遮罩/指纹、Provider Definition、模型发现和能力映射、多 Key 选择/并发/冷却、401/403/429/5xx 分类、按模型启停后备路由、stored response/compact Provider 绑定、非幂等媒体请求不得重复提交、媒体下载/哈希/类型/SSRF 防护。

**迁移测试**:构造真实 v2 Redis Key 格式的测试库;验证账号、tier、启停、配置和 xAI Key 映射;重复导入不产生重复记录;中途失败可以恢复;图片和视频文件逐个校验 SHA-256;无对应项的配置必须出现在报告中,不能静默忽略。

**集成测试**:PostgreSQL、Redis、模拟 xAI HTTP 服务;Responses 流式/非流式;Chat、Messages、compact、stored response;图片生成/编辑;视频创建/轮询/下载;主 Provider 失败后进入 XAI;XAI 失败后返回正确原始错误。

**真实 xAI 测试**:默认不在公共 CI 中运行;通过显式环境变量和独立低权限 Key 启用;避免自动执行产生费用的图片和视频测试;费用型测试必须由人工触发。

### 上线验收门槛

只有全部满足才允许切换:

- 账号数量、Token 指纹和启停状态核对一致。
- 配置项全部处于"已映射、已废弃或人工确认"之一。
- 图片、视频数量和 SHA-256 全部一致。
- PostgreSQL 中不存在明文 SSO Token 或 xAI Key。
- 新客户端 Key 可调用文本、图片和视频。
- 显式 `XAI/...` 路由正常。
- 每个启用的后备模型至少完成一次可控失败切换测试。
- 401、429 和媒体下载失败不会造成重复生成。
- 旧媒体 URL 可访问。
- `go test ./...`、`go test -race ./...`、`go vet ./...`、Go 构建、前端 lint 和前端构建全部通过。
- 已实际演练一次切回 v2。

### 后续阶段

首期稳定后,再分别设计:Voice Provider 能力、Files/Collections、视频编辑、视频延长。它们复用 `xai_official` 的凭据、模型发现、审计和媒体基础设施,但每项单独设计、测试和发布,不与首期迁移混在一起。

## 5. 待办:实施计划(未完成)

原设计会话在确认部署环境后中断,针对实际部署环境的实施阶段计划尚未制定。已确认的环境事实:

- 生产通过 GitHub Actions 构建 Docker 镜像,部署在 Zeabur。
- `data/files/images`、`data/files/videos`、`data/cache/local_media_cache.db` 挂载在 Zeabur 持久化卷上。

实施计划需要补充的内容:

1. 基于 Zeabur 环境的具体备份命令和操作步骤(Redis RDB/AOF 与持久化卷媒体的备份方式)。
2. 双环境在 Zeabur 上的落地方式(v3 独立服务 + 独立 PostgreSQL/Redis 实例的开通与网络)。
3. 分阶段实施顺序与每阶段验收点(建 v3 基线分支 → 迁移工具 → `xai_official` Provider → 管理端 → 迁移演练 → 切换)。
4. 正式切换窗口的逐条 runbook 与切回 v2 的演练脚本。
