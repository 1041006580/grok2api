# v2 → v3 迁移实施计划

- 日期:2026-07-31
- 状态:待执行
- 关联设计:[2026-07-31-v3-migration-design.md](2026-07-31-v3-migration-design.md)(三部分设计均已确认,本文件补齐其第 5 节待办)

## 1. 环境事实

### 已从代码核实

**现网 v2**(`main` @ `f989f27`,镜像由 `.github/workflows/docker.yml` 构建推 ghcr.io):

- 账号存储:`ACCOUNT_STORAGE=redis`,通过 `ACCOUNT_REDIS_URL` DSN 连接(`app/control/account/backends/redis.py`)。
- 数据目录:`DATA_DIR=./data`,挂 Zeabur 持久化卷;媒体在 `data/files/images`、`data/files/videos`,索引在 `data/cache/local_media_cache.db`(SQLite)。
- 历史媒体可通过 `/v1/files/image?id=`、`/v1/files/video?id=` 按 ID 拉取(带 `api_key`)。

**上游 v3**(`upstream/main` @ `0901045`,VERSION `v3.0.11`):

- 单容器(Go 后端 + 内嵌 React 前端,端口 8000)。
- 配置**必须**以文件挂载到 `/run/grok2api/config.yaml` — `docker/entrypoint.sh` 检查 `GROK2API_CONFIG_SOURCE`,缺失直接退出;不支持纯环境变量配置。
- 数据库:`database.driver: sqlite | postgres`;服务启动自动建表并升级 schema(`backend/internal/infra/persistence/relational/schema.go` 及 `schema_*_upgrade_test.go`),无需手工跑 migration。
- 运行态:`runtimeStore.driver: memory | redis`,单实例默认 memory。
- 媒体:`media.local.path`(容器内 `/app/data/media`),数据卷挂 `/app/data`。
- 关键 secrets:`jwtSecret`(≥32 字符)、`credentialEncryptionKey`(Base64 32 字节)——**首次写入账号后必须永久保留,更换会导致已有凭据无法解密**。
- CI:自带 `.github/workflows/ghcr-image.yml` — push `main`/tag `v*.*.*` 时先跑 `go test`/`go vet`/swagger 校验/前端 lint+build,再发布双架构镜像到 ghcr.io;PR 只构建不发布。

### 已与用户确认(2026-07-31)

| 项目 | 结论 |
|---|---|
| v2 Redis 部署 | Zeabur 同项目托管 Redis 服务,内网 DSN 连接 |
| v3 runtimeStore | **memory**(单实例,不开 v3 专属 Redis;多实例时再改 redis) |
| v3 试运行环境 | Zeabur 同项目新增服务(v3 服务 + PostgreSQL 服务) |
| 入口切换方式 | Zeabur 域名改绑:域名从 v2 服务解绑 → 绑到 v3 服务 |

> runtimeStore=memory 是对设计文档"独立 Redis(运行态)"的已确认简化:v3 单实例部署下 memory 为上游默认,少维护一个组件;设计中其余决策不变。

## 2. 阶段总览

| 阶段 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| Phase 0 | 文档提交、v2 冻结(tag + legacy 分支) | — | 0.5 天 |
| Phase 1 | v3 基线分支 + fork 构建链路打通 | P0 | 0.5~1 天 |
| Phase 2 | 迁移工具 `grok2api-migrate-v2` | P1 | 3~5 天 |
| Phase 3 | `xai_official` Provider + 管理端 | P1(与 P2 并行) | 1~2 周 |
| Phase 4 | Zeabur 试运行环境搭建 | P1(可提前) | 0.5 天 |
| Phase 5 | 迁移演练(现网无感) | P2 + P4 | 0.5~1 天 |
| Phase 6 | 正式切换(停机窗口) | P2~P5 全部验收 | 窗口 ≈ 演练实测 + 缓冲 |
| Phase 7 | 收尾:v3 提升为 main、观察期、退役条件 | P6 | 2 周观察 |

## 3. 各阶段详情

### Phase 0:文档提交与 v2 冻结

1. 提交设计文档与本计划到 `main`(`docs/plans/2026-07-31-v3-migration-*.md`)。
2. 冻结现网版本:

   ```bash
   git tag v2-final-20260731 main
   git branch legacy/v2-production main
   git push origin v2-final-20260731 legacy/v2-production
   ```

3. 确认 GitHub 上 tag 与分支可见。

**验收**:tag/分支已推送;现网不受任何影响。

### Phase 1:v3 基线分支与构建链路

1. 同步上游 tag 并建 worktree:

   ```bash
   git fetch upstream --tags
   git worktree add .worktrees/v3 -b v3-main v3.0.11
   ```

2. 首个 fork 提交:修改 `.github/workflows/ghcr-image.yml`,`on.push.branches` 增加 `v3-main`,使开发分支推送即发布镜像(镜像名自动为 `ghcr.io/1041006580/grok2api`,tag 含分支名)。
3. 本地快速验证(或直接推分支看 CI 的 verify job):`cd backend && go test ./...`;`cd frontend && pnpm install && pnpm build`。
4. `git push origin v3-main`,确认 CI 全绿、GHCR 出现镜像。

**验收**:fork 的 v3-main 镜像可拉取;上游测试在 fork 环境全部通过。

### Phase 2:迁移工具 `grok2api-migrate-v2`

在 v3 backend 新增独立命令(编进同一镜像),四个子命令按设计第 2 节:`inspect` / `export` / `import` / `verify`,全部支持 `--dry-run`、幂等重跑。

数据源与通道:

- **账号 + 配置 + v2 中存的 xAI Key**:只读连接 v2 Redis(`ACCOUNT_REDIS_URL`,同项目内网可达)。
- **媒体索引**:`local_media_cache.db` — 从 v2 容器导出(文件小,Zeabur Web 终端 base64 或临时下载均可)。
- **媒体文件(首选方案 A:HTTP 拉取)**:按索引通过 `https://<v2 域名>/v1/files/image|video?id=` 逐个下载,带 v2 `api_key`;每个文件校验 SHA-256、支持断点续跑。避免跨服务卷复制。
- **备选方案 B(A 不可行时)**:v2 容器终端 `tar czf` 打包 `data/files` + 索引,经临时中转传入 v3 卷。

映射规则、归档字段(quota/冷却/失败计数只入加密档案不写 v3 状态)、`legacy-import` 标记等全部按设计第 2 节执行。

**验收**(对应设计"迁移测试"):

- 用构造的 v2 Redis 格式 fixture 通过全部迁移测试;重复导入无重复记录;中途失败可恢复。
- 无对应项配置全部出现在报告中,不静默丢弃。

### Phase 3:`xai_official` Provider(与 Phase 2 并行)

按设计第 3 节实施,提交按设计第 1 部分的独立提交组组织:

1. 抽取共享 Transport `provider/xaiapi`(Build fallback 与新 Provider 共用)。
2. Provider 领域模型 + Key 加密存储(复用 Provider Account 体系,`AuthType=api_key`)。
3. Key 管理 API + 验证流程(`/v1/models` 探活,200/401/403/5xx 分类处理)。
4. 动态模型发现(三个模型目录接口)与 `XAI/` 路由。
5. 按模型兜底 + fail-closed Scope + 禁止跨 Provider 兜底的六种情形。
6. React 管理端五个页面扩展。

**验收**(对应设计"测试范围"):单元 + 集成测试全过;`go test -race`、`go vet`、前端 lint/build 全绿;用独立低权限真实 Key 手工验证一轮(避免自动跑产生费用的图片/视频测试)。

### Phase 4:Zeabur 试运行环境(可在 P2/P3 开发期间提前搭)

1. 同项目新增 **PostgreSQL 服务**(Zeabur 模板),记录内网 DSN。
2. 生成并妥存 secrets(**credentialEncryptionKey 同时存入密码库,永久保留**):

   ```bash
   openssl rand -hex 32      # jwtSecret
   openssl rand -base64 32   # credentialEncryptionKey
   ```

3. 同项目新增 **v3 服务**:
   - 镜像:`ghcr.io/1041006580/grok2api:v3-main`。
   - 用 Zeabur 的配置文件(Config File)功能将 `config.yaml` 挂载到 `/run/grok2api/config.yaml`,关键项:`database.driver=postgres` + 内网 DSN、`runtimeStore.driver=memory`、`secureCookies=true`(用 HTTPS 域名时)、`bootstrapAdmin` 强密码。
   - 持久化卷挂载到 `/app/data`。
   - 暂用 Zeabur 生成域名,不动正式域名。
4. 首次启动确认自动建表成功;用 bootstrap 管理员登录、创建正式管理员后,从 config.yaml 删除 `bootstrapAdmin` 段并重启。

**验收**:服务健康、HTTPS 登录正常、空库加一个测试账号能跑通对话。

### Phase 5:迁移演练(现网无感,不停机)

1. 从 v2 容器导出 `local_media_cache.db` 上传至 v3 卷(或工具可读位置)。
2. 在 v3 容器内依次执行:`inspect` → `export` → `import --dry-run` → `import` → `verify`(v2 Redis 只读内网连接;媒体走方案 A 经 v2 公网域名拉取)。
3. 核对 verify 报告(账号数、Token 指纹、配置映射、媒体 SHA-256)。
4. v3 界面抽查:账号列表与 tier、发起对话、Gallery 中"历史导入"视频、旧媒体兼容 URL。
5. **记录总耗时**——作为正式切换停机窗口的依据。
6. 演练结束后重置 v3:清空 PostgreSQL(drop schema 后重启自动建表)+ 清空媒体目录,保证正式迁移从干净状态开始。
7. 用测试子域名演练一次"域名绑定/解绑"操作,熟悉 Zeabur 流程与 TLS 生效时间。

**验收**:verify 全绿;抽查全部通过;停机窗口估时确定(演练耗时 × 1.5 取整)。

### Phase 6:正式切换(停机窗口)

见第 4 节 Runbook。

### Phase 7:收尾

1. **v3 提升为 main**(设计第 1 部分:验收后才提升):
   - 前置核对:`legacy/v2-production` 分支与 `v2-final-20260731` tag 确认已在 GitHub 存在。
   - `git push origin v3-main:main --force-with-lease`;将 `ghcr-image.yml` 的触发分支改回 `main`。
   - 之后上游同步:定期 `git fetch upstream && git merge <新 tag>`,冲突集中在四个扩展提交组。
2. 观察期 ≥ 2 周:v2 服务保持暂停不删除;Redis、持久化卷、旧镜像、RDB 备份全部保留。
3. **v2 退役条件**(全部满足才可删):观察期内无回滚;新客户端 Key 已被所有调用方使用;媒体兼容 URL 访问正常;RDB 备份已验证可恢复并另存一份。
4. 归档迁移报告;更新 README 部署说明。
5. 后续单独立项:Voice、Files/Collections、视频编辑、视频延长。

## 4. 正式切换 Runbook

前提:Phase 2~5 全部验收通过;设计第 4 节"上线验收门槛"逐条核对通过;停机窗口已通知调用方。

| # | 操作 | 说明 / 命令 | 失败处理 |
|---|---|---|---|
| 1 | 通知开始,停止 v2 服务 | Zeabur 暂停 v2 服务(**Redis 服务保持运行**,迁移工具还要读) | — |
| 2 | 备份 Redis | `redis-cli -u <内网DSN> --rdb v2-final-<日期>.rdb`(从 v3 容器或本地执行);备份文件下载另存 | 备份失败则中止,恢复 v2 |
| 3 | 备份确认 | 校验 RDB 文件大小/可解析;v2 持久化卷自此不再写入,本身即媒体备份;导出 v2 环境变量与配置存档 | 同上 |
| 4 | 重置 v3 | 清演练残留:drop schema → 重启自动建表 → bootstrap 重建管理员 | — |
| 5 | 最终迁移 | `export` → `import` → `verify`(此时 v2 已停,数据静止) | verify 不过 → 中止并执行回滚 Runbook |
| 6 | v3 后置同步 | 触发账号额度/模型同步;确认 xAI Key 走验证流程 | 个别账号异常可先跳过,记录 |
| 7 | 冒烟测试 | 用 Zeabur 生成域名跑:Chat、Responses、图片生成、视频生成、显式 `XAI/` 路由、旧媒体兼容 URL、新 `g2a_...` Key 鉴权 | 关键路径失败 → 回滚 |
| 8 | 域名改绑 | Zeabur 上把正式域名从 v2 服务解绑 → 绑到 v3 服务;等 TLS 证书生效 | 绑定异常 → 改回 v2 |
| 9 | 正式域名复测 | 用正式域名重复第 7 步关键项 | 失败 → 回滚 |
| 10 | 恢复服务 | 通知调用方恢复,提供新 `g2a_...` Key 与新视频接口说明(迁移报告中) | — |
| 11 | 观察 | 首小时盯审计/错误率;v2 保持暂停不删除 | 重大异常 → 回滚 |
| 12 | 记录 | 归档时间线、verify 报告、备份位置 | — |

## 5. 回滚 Runbook(切回 v2)

任何一步失败且无法快速修复时执行;整个流程不修改 v2 数据,回滚零数据风险:

1. Zeabur 把正式域名从 v3 服务解绑,改绑回 v2 服务。
2. 恢复(启动)v2 服务;Redis 与媒体卷从未被修改,直接可用。
3. 验证 v2 正常响应后通知调用方恢复。
4. v3 服务保持运行以便排查失败原因;修复后择期重新进入切换窗口。

## 6. 风险与开放问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| Zeabur 配置文件挂载到 `/run/grok2api/config.yaml` 需实测 | v3 起不来 | Phase 4 第一步即验证;不行则 fork Dockerfile 调整 entrypoint 支持环境变量注入(独立提交,属部署配置组) |
| Zeabur Web 终端可用性(导 `local_media_cache.db`) | 媒体索引拿不到 | 备选:v2 临时加一个受 `app_key` 保护的导出接口(切换后随 v2 退役) |
| 托管 Redis 是否支持 `--rdb` 远程 dump | 备份方式变化 | 逻辑导出(`export` 命令产物)本身就是完整备份;RDB 只是额外保险,不行就用 `BGSAVE` + 卷 |
| 媒体体量未知,HTTP 拉取耗时不可控 | 演练超时 | Phase 5 演练实测;过大则改方案 B(tar 打包) |
| `credentialEncryptionKey` 丢失 | v3 全部凭据作废 | 生成时即三处备份(Zeabur 配置、密码库、离线) |
| force push main | 误覆盖历史 | 仅在 `legacy/v2-production` + tag 双重确认后执行,用 `--force-with-lease` |
| 上游 v3 持续更新 | v3-main 落后 | 切换前只跟安全修复;切换后按提交组定期 merge 上游 tag |
