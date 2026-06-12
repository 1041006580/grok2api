# Changelog

## [2.0.0] - 2026-06-12

完全重构：基于上游 chenyme/grok2api 64a71f1 架构，迁移自 1.x 旧版。

### Breaking Changes

- **架构重组**：`app/services/token/` → `app/control/account/` + `app/dataplane/account/`；`app/api/v1/` → `app/products/`
- **API 路径**：`/v1/admin/...` → `/admin/api/...`
- **数据格式**：`data/token.json` 不再使用，改用 SQLite (`data/accounts.db`) / Redis / MySQL / PostgreSQL
- **Pool 命名**：`ssoBasic` 统一为 `basic`
- **Config schema** 重组：`[token]` → `[account.refresh]` + `[account.selection]`，`[proxy]` → `[proxy.egress]` + `[proxy.clearance]`，`[app]` 部分字段移到 `[features]`

升级流程见 [MIGRATION.md](MIGRATION.md)。

### Added

- 5 模式 quota 系统（auto/fast/expert/heavy/grok_4_3，上游已内置）
- 多存储 backend（SQLite/Redis/MySQL/PostgreSQL）
- 账号增量同步（control plane revision → dataplane）
- Auto chat mode fallback（auto 额度耗尽降级到 fast/expert）
- URL citation annotations（OpenAI / Anthropic / Responses API）
- Imagine Public Image Proxy（`features.imagine_public_image_proxy`）
- Voice custom instructions（自定义指令传递）
- Grok 4.3 Beta 模型支持
- Random selection 策略（`account.refresh.enabled = false`，零探测）
- Inflight 计数 + 评分选号（默认）
- 日志轮转（`logging.max_files`）
- xAI Official API keys 管理（`/admin/api/xai-keys`，本项目独有）
- Admin metrics endpoint（`/admin/api/metrics`，本项目独有）

### Changed

- account selection 从单 token 列表升级为 (pool, mode) 二维表
- quota 数据从 `int` 升级为 `QuotaWindow{remaining, total, window_seconds, reset_at}`
- 重试逻辑从 transport 层抽离到 product 层 `_account_selection.py`

### Removed

- `app/services/token/` 整套（替换为 `app/control/account/`）
- `app/api/pages/admin.py`（合并到 `app/products/web/`）
- 旧版 `[xai]` config 段中除 `keys` 外的所有字段（`base_url`/`timeout`/`video_poll_*` 等已废弃）

### Migration

提供 `scripts/migrate-from-legacy.py` 转换 `data/token.json` 到 v2 格式。

---

## [1.x]

旧版变更见 git log。最后一个 1.x 提交：`ecd61f2 feat(admin): /v1/admin/metrics endpoint`。
