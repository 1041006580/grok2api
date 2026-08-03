# v2 → v3 收尾操作清单(Phase 4-7)

> 开发阶段(Phase 0-3)已全部完成:v3-main 基于上游 v3.0.11,含迁移工具
> `grok2api-migrate-v2`、第四个 Provider `xai_official` 与完整管理端支持。
> 镜像:`ghcr.io/1041006580/grok2api:v3-main`(CI 每次推送自动重建)。
> 本清单是剩余人工步骤的唯一入口;细节见
> [实施计划](../plans/2026-07-31-v3-migration-plan.md)(在 main 分支 docs/plans/ 下)。

## Phase 4:Zeabur 试运行环境(约半小时)

1. Zeabur 现有项目中新增 **PostgreSQL** 服务,记录内网 DSN。
2. 生成并三处备份 secrets(Zeabur 配置、密码库、离线):

   ```bash
   openssl rand -hex 32      # jwtSecret
   openssl rand -base64 32   # credentialEncryptionKey —— 首次写入账号后永久不可更换
   ```

3. 新增 **v3 服务**:
   - 镜像 `ghcr.io/1041006580/grok2api:v3-main`。
   - 用 Zeabur 配置文件功能把 `config.yaml` 挂到 **`/run/grok2api/config.yaml`**(entrypoint 强制;这是首要验证点,若 Zeabur 挂载路径受限,回报后我调整 entrypoint)。
   - config 关键项:`database.driver: postgres` + 内网 DSN;`runtimeStore.driver: memory`;`secureCookies: true`;`bootstrapAdmin` 强密码;其余照 `config.example.yaml` 默认。
   - 持久化卷挂到 `/app/data`;暂用 Zeabur 生成域名。
4. 首次启动确认自动建表;bootstrap 登录 → 创建正式管理员 → 从 config 删除 `bootstrapAdmin` 段重启。

**验收**:HTTPS 登录正常;加一个测试账号能跑通对话;(可选)在账号页 xAI Official 标签导入一个官方 Key,启用 `XAI/` 模型路由后显式调用成功。

## Phase 5:迁移演练(现网无感)

在 v2 容器(Zeabur Web 终端)导出三样东西:

```bash
ls /app/data/files/images  > /tmp/images.txt
ls /app/data/files/videos  > /tmp/videos.txt
# 连同 /app/data/cache/local_media_cache.db(可选,常为空)一起取出
```

把清单放到 v3 容器可读位置(小文件,base64 粘贴即可),然后在 v3 容器终端:

```bash
/app/grok2api-migrate-v2 inspect --redis-url $V2_REDIS_URL
/app/grok2api-migrate-v2 export  --config /app/config.yaml --redis-url $V2_REDIS_URL \
    --archive /app/data/migrate.enc --image-list images.txt --video-list videos.txt --report /app/data/export-report.json
/app/grok2api-migrate-v2 import  --config /app/config.yaml --archive /app/data/migrate.enc \
    --v2-base-url https://<v2 域名> --dry-run --report /app/data/import-dry.json
/app/grok2api-migrate-v2 import  --config /app/config.yaml --archive /app/data/migrate.enc \
    --v2-base-url https://<v2 域名> --report /app/data/import-report.json
/app/grok2api-migrate-v2 verify  --config /app/config.yaml --archive /app/data/migrate.enc --report /app/data/verify-report.json
```

- `$V2_REDIS_URL` = v2 服务的 `ACCOUNT_REDIS_URL` 值(同项目内网可达;工具只读)。
- 全部子命令幂等,可断点重跑;报告不含任何凭据,档案已加密。
- 核对 verify 报告与配置映射表(`disposition: manual` 的项需在 v3 管理端手工配置;`sensitive` 项在加密档案中)。
- **记录 export+import 总耗时** → 停机窗口 = 耗时 × 1.5。
- v3 界面抽查:账号 tier/启停、xAI Key、发对话、Gallery"历史导入"视频、旧媒体 URL(`/v1/files/image?id=...`)。
- 演练完重置 v3:PostgreSQL drop schema → 重启自动建表 → 重建管理员(正式迁移从干净状态开始)。

## Phase 6:正式切换(停机窗口)

按实施计划第 4 节 Runbook 12 步执行,要点:

1. 通知调用方 → Zeabur 暂停 v2 服务(**Redis 保持运行**)。
2. 备份:`redis-cli -u $V2_REDIS_URL --rdb v2-final.rdb` 下载另存;v2 卷自此只读即媒体备份。
3. 重置 v3(清演练残留)→ export → import → verify(此时数据静止,verify 必须零问题)。
4. 触发账号额度/模型同步;用 Zeabur 生成域名冒烟:Chat、Responses、图片、视频、显式 `XAI/` 路由、旧媒体 URL、新 `g2a_...` 客户端 Key。
5. Zeabur 把正式域名从 v2 解绑 → 绑到 v3 → 等 TLS 生效 → 正式域名复测。
6. 通知调用方恢复(新 Key + `/v1/videos/generations` 接口)。
7. 任一步失败:回滚 Runbook(域名绑回 v2 → 启动 v2;数据零风险,v2 从未被修改)。

## Phase 7:收尾

1. 观察期 ≥ 2 周:v2 保持暂停不删;Redis、卷、RDB 备份全留。
2. v3 提升为 main(确认 `legacy-v2-production` 分支 + `v2-final-20260731` tag 在 GitHub 后):

   ```bash
   git push origin v3-main:main --force-with-lease
   ```

   然后把 `.github/workflows/ghcr-image.yml` 触发分支改回 `main` 提交。
3. v2 退役条件(全部满足才删):观察期无回滚、所有调用方已换新 Key、媒体兼容 URL 正常、RDB 备份验证可恢复。
4. 归档迁移报告;后续单独立项:Voice、Files/Collections、视频编辑、视频延长。

## 已知边界(开发阶段确认)

- 首期 `xai_official` 覆盖 Responses/Chat/Messages/Stored 透传 + 模型发现 + Key 池管理;图片/视频生成端点未纳入首期声明(能力边界诚实,后续增量)。
- 客户端 Key 的 XAI 权限 fail-closed:存量与新建 Key 默认只含三个既有渠道,需在 Key 编辑里显式勾选 xAI Official。
- "同步全部额度"按钮对 xai_official 隐藏(无全量端点);账号级同步可用。
- Windows 本地开发有 7 个平台性测试失败(视频 fsync/SOCKS5/文件名清洗),Linux CI 全绿为准。
- `go test -race` 在 Windows 需 CGO/gcc,本地未跑;上线验收门槛要求的 race 检查请在 Linux 环境执行一次:`CGO_ENABLED=1 go test -race ./...`(或临时在 CI verify job 加一步)。
