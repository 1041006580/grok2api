# v2 数据格式规格(迁移工具依据)

> 调研自 fork v2 代码(`legacy-v2-production` @ f989f27),是 `grok2api-migrate-v2` 读取端的权威规格。

## 1. 账号(Redis)

来源:`app/control/account/backends/redis.py`、`models.py`(AccountRecord)、`enums.py`。

Key 布局(无前缀):

| Key | 类型 | 内容 |
|---|---|---|
| `accounts:rev` | STRING | 全局 revision 计数器 |
| `accounts:record:<token>` | HASH | 单账号全部字段(token 在 key 中,不在 hash 中) |
| `accounts:pool:<pool>` | SET | pool ∈ basic/super/heavy,成员为未删除账号 token |
| `accounts:revision_log` | ZSET | member=token,score=最后修改 revision |

HASH 字段(值全为字符串):`pool`, `status`, `created_at`, `updated_at`, `tags`(JSON 数组), `quota_auto`/`quota_fast`/`quota_expert`/`quota_heavy`(JSON;heavy 缺省为 `"{}"`), `usage_use_count`, `usage_fail_count`, `usage_sync_count`, `last_use_at`, `last_fail_at`, `last_fail_reason`, `last_sync_at`, `last_clear_at`, `state_reason`, `deleted_at`, `ext`(JSON), `revision`。

- 时间戳:毫秒整数字符串;**空值 = 空字符串 ""**(读取时当 None)。
- `status`:active / cooling / expired / disabled;启停靠 `status=="disabled"` 表达。
- **软删除**:`deleted_at` 非空的记录仍在 Redis,必须过滤。
- `nsfw` 是 `tags` 里的标签,不是字段。
- quota JSON 字段:`remaining`, `total`, `window_seconds`, `reset_at`, `synced_at`, `source`(0=DEFAULT/1=REAL/2=ESTIMATED)。
- **陷阱:`grok_4_3` 第五个 quota 窗口未持久化到 Redis**,读回恒空;如需补齐按 pool 默认值(basic: fast 30/86400s;super: auto 50/fast 140/expert 50/grok_4_3 50 @7200s;heavy: auto 150/fast 400/expert 150/heavy 20/grok_4_3 150 @7200s)。
- `ext` 已知键:`cooldown_until`, `cooldown_reason`, `disabled_at`, `disabled_reason`, `expired_at`, `expired_reason`, `forbidden_strikes`。

## 2. 配置(Redis)

来源:`app/platform/config/backends/redis.py`、`_serde.py`。

- `config:user`(HASH):**逐项展平**,field 名 = TOML 点号路径,field 值 = 叶子的 JSON 编码(字符串带引号)。只存用户覆盖项,缺失属正常(默认值在 `config.defaults.toml`)。
- `config:version`(STRING):计数器。

重点 field:`app.app_key`、`app.app_url`、`app.api_key`、`proxy.egress.*`、`proxy.clearance.*`(cf_cookies、flaresolverr_url 等)、`cache.local.image_max_mb`/`video_max_mb`、超时与并发类若干——完整清单见 v2 `config.defaults.toml`。

## 3. xAI API Keys

来源:`app/products/web/admin/xai_keys.py`。存于配置里:`config:user` HASH 的单个 field **`xai.keys`**,值为整个数组的 JSON 文本。每条:`id`(uuid4)、`key`(**明文**)、`name`(可 null)、`enabled`(bool)。

## 4. 媒体

- SQLite `${DATA_DIR}/cache/local_media_cache.db` 表 `local_media_files(media_type, name, size_bytes, created_at_ns, updated_at_ns)`,主键 (media_type, name);id = 文件名去扩展名。
- **陷阱:默认配置(容量上限=0)下该表不写入,可能为空**;文件真源是目录扫描:`${DATA_DIR}/files/images/<id>.jpg|.png`、`${DATA_DIR}/files/videos/<id>.mp4`。
- 迁移清单来源:优先扫描目录(v2 容器 `ls` 导出文件名清单),SQLite 仅作时间戳补充。
- 拉取接口:`GET /v1/files/image?id=`(探测 .jpg/.png)、`GET /v1/files/video?id=`(.mp4);id 校验 `^[0-9a-f\-]{16,36}$`;**这两个端点无鉴权**(v2 有意为之)。
- 允许扩展名:图片 .jpg/.jpeg/.png/.gif/.webp/.bmp;视频 .mp4/.mov/.m4v/.webm/.avi/.mkv。

## 5. Token 形态

- token = grok.com SSO cookie 裸值(纯字符串,已规范化:去 `sso=` 前缀、归一破折号/空格、剥零宽字符、丢非 ASCII)。
- 使用时拼 `Cookie: sso=<tok>; sso-rw=<tok>`;`cf_clearance` 属全局配置(`proxy.clearance.cf_cookies`),不在账号记录中。
- v3 导入 grok_web 凭据最少需:token、pool、status、tags;建议带:created_at/updated_at、usage 计数、last_* 时间、state_reason、ext(入加密档案,不写 v3 活动状态)。

## 迁移工具四陷阱

1. `grok_4_3` quota 未持久化,按 pool 补默认。
2. 空值是空串不是缺失,读取须转 None。
3. 软删除记录仍在,按 `deleted_at` 过滤。
4. 媒体索引 db 默认为空,以目录/清单扫描为准。
