# V2 迁移映射文档

基线：upstream/main @ `64a71f1`（Merge PR #521 from chenyme/optimise）
我们 main：`ecd61f2`（Step 0 完成后）

## 关键事实修正

校准结论 — 调研上游 v2 后发现，**绝大多数我们以为独有的功能上游已经合并**：

| 我们独有功能（计划中） | 实际上游状态 | 上游 commit |
|---|---|---|
| auto chat mode fallback | ✅ 已合并 | `257b60b` |
| url_citation annotations | ✅ 已合并 | `3a8811c` |
| inline citation links `[[N]](url)` | ✅ 已合并 | `3a8811c`（StreamAdapter 内置） |
| voice custom instructions | ✅ 已合并 | `30dce37` |
| Imagine Public Image Proxy | ✅ 已合并 | `0afce0e` |
| batch token disable/restore | ✅ 已合并 | `f9576cc` |
| random selection strategy + cooldown | ✅ 已合并 | `100654a` |
| Grok 4.3 Beta 支持 | ✅ 已合并 | `287f434`/`7b640e7` |
| QuotaWindow + 多模式 quota 数据模型 | ✅ 已合并（5模式：auto/fast/expert/heavy/grok_4_3） | `64a71f1` 基线已有 |
| inflight 计数 + score-based selection | ✅ 已合并 | `64a71f1` 基线已有（`_quota_select`） |
| acquire/release（Lease 模式） | ✅ 已合并（`AccountDirectory.reserve` + `lease`） | `64a71f1` 基线已有 |
| per-mode rate-limit | ✅ 已合并 | `64a71f1` 基线已有（`mode_available`） |
| KaTeX math rendering | 待核实，上游已重写 webui chatkit | — |
| 日志轮转 | 待核实 `app/platform/logging/logger.py` | — |

**真正我们独有的：**

| 功能 | 文件 | 是否需要移植 |
|---|---|---|
| `xai_keys` 全套（管理 API key） | `app/api/v1/admin_api/xai_keys.py` + 前端 `static/admin/js/xai-keys.js` + HTML + `services/grok/utils/xai_video.py`（接入点） | ✅ 必须 — 上游完全没有 |
| `/v1/admin/metrics` endpoint | `app/api/v1/admin_api/metrics.py` | ✅ 必须 — 上游完全没有 |
| `feature_enabled` 配置门控 helper | `app/core/config.py:feature_enabled()` | 待核实是否被上游 `app/platform/config/snapshot.get_config` 取代 |
| `tests/merge/test_multi_mode_quota.py` | 我们的测试 | 重写为上游格式 |
| 启动 flag 摘要日志 | `main.py` 的 startup 输出 | 上游 `platform/startup/` 已有类似机制 |

## 上游已停止维护

`e1bc5bc` (`docs: Update README files to announce project discontinuation`) — 上游已宣布停止维护。

**含义：**
- v2 基线锁在 `64a71f1` 之后，上游不会再有重大功能更新（仅 `7015258` 一个安全 fix）。
- 我们的 fork 实质上将成为该项目的**继任者**。
- 不必担心后续 rebase 上游冲突。

## 架构映射（我们 → 上游 v2）

### 文件路径映射

| 我们的位置 | 上游 v2 位置 | 说明 |
|---|---|---|
| `app/services/token/service.py` | `app/control/account/runtime.py` + `app/dataplane/account/sync.py` | 上游分离了控制面/数据面 |
| `app/services/token/manager.py` | `app/control/account/repository.py` + `app/control/account/commands.py` | CRUD vs 路径分离 |
| `app/services/token/models.py` | `app/control/account/models.py` | 已有 QuotaWindow/AccountQuotaSet/AccountRecord |
| `app/services/token/pool.py` | `app/dataplane/account/table.py` + `app/dataplane/account/selector.py` | 上游用 array.array 优化热路径 |
| `app/services/token/scheduler.py` | `app/control/account/scheduler.py` + `app/control/account/refresh.py` | |
| `app/services/grok/services/chat.py` | `app/products/openai/chat.py` + `app/products/anthropic/messages.py` | 上游分 OpenAI / Anthropic 两端 |
| `app/services/grok/services/image.py` | `app/products/openai/images.py` | |
| `app/services/grok/services/image_edit.py` | `app/products/openai/images.py`（合并） | |
| `app/services/grok/services/video.py` | `app/products/openai/video.py` | |
| `app/services/grok/services/video_extend.py` | `app/products/openai/video.py`（合并） | |
| `app/services/grok/services/voice.py` | `app/products/web/webui/voice.py`（前端代理） + `app/dataplane/reverse/transport/livekit.py` | |
| `app/services/grok/services/model.py` | `app/control/model/registry.py` + `app/control/model/spec.py` | |
| `app/services/grok/utils/retry.py` | `app/products/_account_selection.py` | 上游已实现 mode_candidates + reserve_account |
| `app/services/grok/utils/stream.py` | `app/products/openai/_format.py` + `app/dataplane/reverse/protocol/xai_chat.py` | StreamAdapter 在 protocol 层 |
| `app/services/grok/utils/download.py` | `app/dataplane/reverse/transport/assets.py` + `app/platform/storage/media_cache.py` | |
| `app/services/reverse/ws_livekit.py` | `app/dataplane/reverse/transport/livekit.py` + `app/dataplane/reverse/protocol/xai_livekit.py` | |
| `app/api/v1/admin_api/__init__.py` | `app/products/web/admin/__init__.py` + `app/products/web/router.py` | |
| `app/api/v1/admin_api/metrics.py` | （新增）`app/products/web/admin/metrics.py` | 我们独有 |
| （xai_keys 全套） | （新增）`app/products/web/admin/xai_keys.py` | 我们独有 |
| `app/core/config.py` | `app/platform/config/loader.py` + `app/platform/config/snapshot.py` | |
| `app/core/logger.py` | `app/platform/logging/logger.py` | |
| `app/static/` | `app/statics/` | 路径重命名 |
| `app/static/admin/` | `app/statics/admin/` | |
| `app/static/public/` | `app/statics/webui/` + `app/statics/js/webui/` | 上游统一为 chatkit |
| `main.py` | `app/main.py` | |
| `tests/` | （上游无） | 需自行建立 |

### 数据模型对照

我们的 `Token` 对象（`app/services/token/models.py`） vs 上游 `AccountRecord`：

| 我们的字段 | 上游字段 | 说明 |
|---|---|---|
| `token` | `token` | 同 |
| `pool` (`basic`/`super`/`heavy`) | `pool` 同 | |
| `status` | `status: AccountStatus` | 上游用 enum |
| `quota` (单值) | 不存在（已替换为 `quota_set()`） | |
| `quotas: Dict[str, QuotaWindow]` | `quota: dict` 序列化为 `AccountQuotaSet` | 上游 5 模式：auto/fast/expert/heavy/grok_4_3 |
| `tags: List[str]` | `tags: list[str]` | 同 |
| `created_at`/`updated_at` | 同 | 上游用 ms 而非 s |
| `last_use_at` | `last_use_at` | 同 |
| `inflight` | 不在 record 中（在 `AccountRuntimeTable.inflight_by_idx`） | 上游运行时数据与持久数据分离 |

**结论：** 数据模型上游已完全覆盖我们的需求（且更细：5 模式 vs 我们 3 模式），只需在迁移脚本里把旧 `data/token.json` 转写为上游格式。

## 数据迁移脚本

详见 `scripts/migrate-from-legacy.py`（Step 2 产出）。脚本职责：
1. 读取旧 `data/token.json`（pool 列表，每项含 `token, quota, quotas, ...`）。
2. 写出上游 `AccountRecord` 列表 JSON，目标位置取决于 backend（local/sql/redis）。
3. 把旧 `config.toml` 的关键字段映射到新 schema：
   - `tokens.pool` → 不再需要（已迁移到 account backend）
   - `features.*` → `features.*`（上游同名）
   - `tokens.multi_mode_quota_enabled` → 上游默认开启（`mode_available` 总是 per-mode）
   - `tokens.inflight_enabled` → 上游默认开启
   - `retry.auto_chat_mode_fallback` → `features.auto_chat_mode_fallback`（上游 commit `257b60b`）

## 重新评估的迁移工作量

考虑到上游已合并大部分功能，**实际工作量大幅缩减**：

| 原 Step | 原计划工作 | 校准后工作 |
|---|---|---|
| Step 3 (P0) | xai_keys + multi_mode quota + per-mode rate-limit | xai_keys 一项即可，其余上游已有 |
| Step 4 (P1) | inflight + acquire/release + score selection + feature_enabled + metrics | metrics endpoint + feature_enabled helper（如上游 `get_config` 不够），其余上游已有 |
| Step 5 (P2) | 6 项功能（url_citation/inline citation/KaTeX/voice instructions/Imagine proxy/log rotation） | 仅 KaTeX 与日志轮转待核实，其余上游已有 |

## 风险 & 决策

1. **上游停止维护**：我们的 fork 即继任。版本号建议跳到 `v2.0.0`。
2. **Schema 兼容性**：上游 `quota` 字段是 dict，旧字段 `quotas` 直接重命名 + 5 模式扩展即可。
3. **前端**：上游用 `chatkit.html`/`chatkit.js`，与我们的 `chat.html` 完全不同，KaTeX 接入点要重新做。
4. **xai_keys 归属决策**：上游 `app/products/web/admin/` 是 admin API 路径，新建 `xai_keys.py` 即可。
5. **配置 schema**：上游用 `app/platform/config/loader.py` + 多 backend，迁移脚本要把旧 toml 拆分映射到新 backend 选择。
