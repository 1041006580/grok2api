# V2 迁移指南

本文档描述如何从旧版 grok2api（main 分支 <= ecd61f2）升级到 v2 架构（基于上游 64a71f1 + 独有功能）。

## 前置条件

- 已备份 `data/token.json` 和 `data/config.toml`
- Python 3.13+, uv 已安装
- Git worktree 已建立：`git worktree add .worktrees/v2 v2`

## 架构变更摘要

v2 采用上游重构架构，关键改变：

| 层 | 旧版 | v2 |
|---|---|---|
| 账号管理 | `app/services/token/` | `app/control/account/` (持久) + `app/dataplane/account/` (运行时) |
| API 端点 | `app/api/v1/` | `app/products/openai/` + `app/products/anthropic/` + `app/products/web/` |
| 配置 | `app/core/config.py` | `app/platform/config/` |
| 静态资源 | `app/static/` | `app/statics/` |
| 存储 backend | 内存 JSON | SQLite (local) / Redis / MySQL / PostgreSQL 可选 |

## Breaking Changes

1. **账号数据格式**：旧 `data/token.json` 不兼容，需跑迁移脚本
2. **Config schema**：`config.toml` 结构重组（见下文）
3. **API 路径**：`/v1/admin/...` 变为 `/admin/api/...`
4. **环境变量前缀**：部分配置改用 `account.` / `features.` / `proxy.` 命名空间
5. **pool 命名**：`ssoBasic` 统一为 `basic`

## 迁移步骤

### 1. 数据迁移

运行迁移脚本：

```bash
python scripts/migrate-from-legacy.py --legacy-data-dir=data --output=v2-accounts.json
```

得到 `v2-accounts.json`（AccountRecord 格式数组）。

### 2. 启动 v2 一次创建 DB

```bash
cd .worktrees/v2
uv sync
uv run python -m app.main
```

首次启动会在 `data/accounts.db` 创建 SQLite 表。Ctrl+C 停止。

### 3. 导入账号数据

**方式 A**：用 SQLite CLI 直接导入（需手写 INSERT 语句）

**方式 B**：通过 Admin API 导入

```bash
# 假设 v2 app 运行在 http://localhost:8000
curl -X POST http://localhost:8000/admin/api/tokens/import \
  -H "X-Admin-Key: your_app_key" \
  -H "Content-Type: application/json" \
  -d @v2-accounts.json
```

### 4. 配置迁移

手动将旧 `data/config.toml` 的关键字段映射到新 schema：

| 旧字段 | 新字段 | 说明 |
|---|---|---|
| `[app]` | `[app]` + `[features]` | `app_key`/`api_key` 保留，`temporary`/`stream` 等移到 `features.*` |
| `[token]` | `[account.refresh]` + `[account.selection]` | 刷新间隔重命名：`refresh_interval_hours` → `basic_interval_sec = 28800` |
| `[proxy]` | `[proxy.egress]` + `[proxy.clearance]` | 代理配置分 egress（出口）和 clearance（CF 挑战）两段 |
| `[retry]` | `[retry]` | `max_retry` → `max_retries`（复数） |
| `[xai]` | `[xai]` | v2 仅保留 `xai.keys`（通过 Admin UI 管理），其余字段上游无 |

**新增配置段**：
- `[logging]`: 日志轮转（`file_level`, `max_files`）
- `[features]`: feature flags（`auto_chat_mode_fallback`, `imagine_public_image_proxy` 等）
- `[cache.local]`: 本地缓存限额

**示例**：旧版

```toml
[app]
temporary = true
stream = true

[token]
auto_refresh = true
refresh_interval_hours = 8
```

v2 对应：

```toml
[features]
temporary = true
stream = true

[account.refresh]
enabled = true
basic_interval_sec = 28800
super_interval_sec = 7200
```

### 5. xAI Keys 迁移

旧版 `config.toml` 的 `[xai]` 段：

```toml
[xai]
keys = [{id = "k1", key = "xai-...", name = "Key 1", enabled = true}]
```

v2 读取同一格式，无需改动。或通过 Admin UI（`/admin/xai-keys` 前端页面）管理。

### 6. 验证

启动 v2 app：

```bash
cd .worktrees/v2
uv run python -m app.main
```

检查：
- `/admin/api/status` 返回 account directory size
- `/admin/api/tokens` 列出导入的账号
- `/v1/chat/completions` 能正常走 token 流程

### 7. 分支切换（可选）

确认 v2 稳定后，切换 main 分支：

```bash
git branch -m main legacy
git branch -m v2 main
git push origin main --force-with-lease
```

## 新功能

v2 相比旧版新增（来自上游或我们独有）：

| 功能 | 来源 | 说明 |
|---|---|---|
| 多存储 backend | 上游 | 支持 SQLite/Redis/MySQL/PostgreSQL |
| 5 模式 quota | 上游 | auto/fast/expert/heavy/grok_4_3 |
| Auto chat mode fallback | 上游 257b60b | auto 额度耗尽时降级到 fast/expert |
| URL citation annotations | 上游 3a8811c | OpenAI / Anthropic / Responses API 引用输出 |
| Imagine Public Image Proxy | 上游 0afce0e | `features.imagine_public_image_proxy = true` 强制下载图片 |
| xAI Official API keys | 我们独有 | 管理 x.ai API key，用于 video 等场景 |
| Admin metrics endpoint | 我们独有 | `/admin/api/metrics` 实时统计 |

## 回滚

若 v2 有问题，随时切回旧版：

```bash
git worktree remove .worktrees/v2
git checkout legacy  # 如已重命名
```

旧版数据（`data/token.json`）未被 v2 修改。

## 常见问题

**Q: v2 能否读旧版 token.json？**  
A: 不能。必须跑迁移脚本转为 AccountRecord 格式并导入 SQLite/Redis。

**Q: 上游已停止维护，v2 会继续更新吗？**  
A: 我们的 v2 fork 是该项目继任者，会持续维护。上游停止维护（e1bc5bc）发生在 64a71f1 之后，我们锁定 64a71f1 作为基线。

**Q: multi-mode quota 在 v2 是默认开启的吗？**  
A: 是。上游 64a71f1 已内置 5 模式支持，无需开关。

**Q: 旧版的 inflight tracking 在 v2 哪里？**  
A: `app/dataplane/account/selector.py:_quota_select()` 已实现，默认开启。

## 技术细节

- **Config 热重载**：`POST /admin/api/config` 更新后自动 reload，无需重启
- **Account sync**：dataplane 与 control plane 通过 `revision` 字段增量同步
- **Quota 刷新策略**：`account.refresh.enabled = true` 用评分选号，`false` 用随机选号（零探测）
- **日志轮转**：`logging.max_files = 7` 按天轮转，最多保留 7 天

## 支持

如遇问题，提 issue 附上：
- 迁移脚本输出
- v2 启动日志前 50 行
- `/admin/api/status` 返回结果
