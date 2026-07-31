# v3 backend 架构地图(开发依据)

> 调研自 upstream v3.0.11。路径相对 `backend/`。两个开发任务的定位地图。

## Provider 体系
- 枚举:`internal/domain/account/account.go:14-16`(grok_build/grok_web/grok_console)+ `providers` 数组(:29)、`IsValid()`(:37)、`ModelNamespace()`(:47)。
- 实现包:`internal/infra/provider/cli/`(=Build)、`web/`、`console/`;注册:`internal/app/application.go:216` `provider.NewRegistry(...)` + `Validate()`(失败即启动中止)。
- 接口:身份接口 `Adapter{Provider()}` + 能力接口(ResponseAdapter/ModelCatalogAdapter[必需]/Billing/Quota/CredentialRefresh/DeviceOAuth/CredentialCodec/AccountIdentity/ImageGeneration/ImageEdit/Video/RoutingMetadata/ModelAlias/PricingMetadata/CredentialMetadata 等,`provider.go:299-442`)。
- Definition 契约 `definition.go:83`;**Validate :146 只允许 AuthType oauth|sso,:143 要求 Quota 三选一** —— 需放宽 api_key 与"无额度"。
- 模型路由:`internal/domain/model/model.go` NormalizePublicID(:53)/PublicIDCandidates(:99)/ExternalPublicID(:89);分发 `internal/application/gateway/service.go`。
- Build 双平面 fallback 在 `cli/fallback.go`(Provider 私有,不复用);跨账号重试在 `gateway/attempt.go`+`failure.go`。

## 账号体系
- `account.Credential`(account.go:127,~60 字段:Provider/AuthType/SourceKey/EncryptedAccessToken/Priority/MaxConcurrent/CooldownUntil/EgressIdentity…)。AuthType 仅 oauth/sso(:63)。
- 落库:`provider_accounts`(models.go:27)+ `account_credentials`(:69)。identity_key = sha256(provider|user|UserID|TeamID),降级 email→SourceKey(mapping.go:187);provider 是第一段,新 Provider 不撞 key。
- 加密:`internal/infra/security/cipher.go` AES-256-GCM;密钥 `secrets.credentialEncryptionKey`(config.go:249);持久化层不碰加解密。
- **坑:mapping.go:158-165 AuthType 为空时 Web/Console 推 sso、其余推 oauth** —— 新 Provider 必须显式写 AuthType。
- 选择器:`gateway/selector.go` AcquireForKey(:304);候选 `account_repository.go:199 ListRoutingCandidates`。

## Build 的 api.x.ai 代码(Transport 抽取对象)
- 入口 `cli/adapter.go:170 ForwardResponse`;发请求 :452 doResponseRequest;鉴权头 :757 applyHeaders。
- Base URL:config.go:157 `DefaultBuildFallbackBaseURL="https://api.x.ai/v1"`。
- compaction:`responses_compaction*.go`;视频 `video.go`(GenerateVideo:155,已有 xaiVideoRequestProfile 抽象);ListModels:555(标准 OpenAI 形状);错误归一 `provider.ReadDiagnosticBody`/`rate_limit.go`/`account_block.go`;OAuth 全在 `oauth.go`(Build 专属)。
- **usage 解析在 transport 层**(inference/handler.go extractMetadata:1443)——新 Provider 零工作量。
- 出口:`cli/egress.go egressTransport` → `infraegress.Manager.AcquireIfConfigured(ctx, Scope, affinity)`;Egress Scope 封闭枚举(`internal/domain/egress/`)。
- 可整体搬的纯函数:`normalize.go` 全部、`responses_input/codex_tools/tool_*/arguments/custom/x_search_filter.go`、`responses_response.go` SSE 解析、视频 payload、`conversation/` 整包。
- 切法:①`Authorizer{Apply(*http.Request) error}` 接口,Build 头 vs API Key Bearer;②不复用 fallback.go;③ForwardResponse 拆 normalizeRequest(纯)/execute(传输)/normalizeResponse(纯)。
- Build 专属勿复用:oauth.go、responses_cache_route.go、CredentialMetadata、billing.go。

## 数据库
- GORM AutoMigrate:`schema.go:103 InitializeSchema`,模型清单 schemaModels(:21-52,29 个);加表/加列免迁移代码;改 CHECK/回填才要 `ensure*`/`migrate*` 函数(:142-194 固定顺序)。
- `ensureNamedConstraints(:479)`:约束文本含 marker 即跳过,否则 drop+重建;**加 Provider 照抄 ensureConsoleConstraints(:285)换 marker**;测试模板 `schema_console_upgrade_test.go`。
- 会撞的 CHECK:provider_accounts(:30)、model_routes(:185)、request_audits(:297)、response_ownership(:362)、media_jobs(:400);
- **client_keys scope mask BETWEEN 1 AND 7(:259)→ 15,且存量 7=全部 需值迁移到 15**(唯一的数据迁移)。
- `ensureCanonicalModelPublicIDs`(model_public_id.go:22)必须能处理新 Provider,否则启动失败。
- media_assets(models.go:434):ID/Kind(image|video)/StorageKey(unique)/MIMEType/SizeBytes(≤256MiB)/SHA256/CreatedAt;文件在 `infra/media/local_store.go`;media_repository.go ListMediaAssets 硬过滤 image。
- 开库 `database.go OpenSQLite(:43)/OpenPostgres(:56)`。

## cmd 与迁移工具挂载
- `cmd/grok2api/main.go` → `internal/cli/run.go`(手写 parseOptions,无 cobra)。
- **迁移工具独立二进制 `cmd/grok2api-migrate-v2/main.go` + `internal/migratev2/`**;Dockerfile 加第二个 build;不碰 run.go。
- 写入口:`AccountRepository.UpsertManyByIdentity(ctx, []account.Credential)`(account_repository.go:932,幂等);加密自理(security.NewCipher→Encrypt)。

## 管理端 HTTP
- `internal/transport/http/`;admin 组 `/api/admin/v1`(server.go:138,AdminAuth);公开 `/v1`(:159,ClientAuth)。
- 账号 CRUD `account/handler.go Register(:134)`;导入端点按 Provider 分(/accounts/import、/web/import、/console/import → importFile(:991))。
- Provider 校验散点:account/handler.go L393/645/676/991/1090/1251/1503、model/handler.go L88/132/144、clientkey/handler.go L293-328(硬编码文案)、settings/handler.go L25-27。
- ClientKey Scope 位掩码:`domain/clientkey/client_key.go`(:21 Build=1/Web=2/Console=4、Parse:45、Values:92、Normalize:124(0=All)、Allows:146);强制点 selector.go:309/:607、service.go:513/525/570。
- 审计:`domain/audit/audit.go:69 Record`(Provider 是裸 string);pricing.go normalizePricingModel(:132)硬编码前缀剥离表要加新 Provider。

## 模型管理
- `application/model/service.go`:Create(:181,validateProviderCapability:269)、Update(:209,Provider 不可变)、BatchSetEnabled(:322)、Sync(:332→ListModels→UpsertDiscovered)。

## 清单 A:xai_official 最小改动
领域:account.go(枚举+AuthTypeAPIKey)、definition.go 放宽、client_key.go(1<<3,All=15)、egress.go(ScopeXAIOfficial)、pricing.go 前缀。
实现:新建 `provider/xaitransport/`(共享纯函数+Client/Authorizer)、`provider/xaiofficial/`(adapter/definition/import/egress);cli 改用共享包。
装配:application.go:200-216、config.go(Provider.XAIOfficial,BaseURL 固定)、settings/service.go:298/404/486/596。
数据库:models.go 5 个 CHECK+scope 上界+egress_operations_config fallback 列;schema.go ensure 函数+scope 7→15 值迁移;schema_xai_official_upgrade_test.go。
HTTP/前端:account/clientkey/settings handler;前端 13 文件(accounts-api/page、model types、client-keys、audits、dashboard、settings、i18n)。

## 清单 B:迁移子命令最小改动
cmd/grok2api-migrate-v2/main.go;internal/migratev2/;Dockerfile 第二 build;Makefile。
