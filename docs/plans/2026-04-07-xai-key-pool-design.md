# xAI Key 池与独立管理页设计

## 背景

当前仓库的 xAI 视频链路已经具备官方接口能力：

- 官方视频服务实现位于 `app/services/grok/services/xai_video.py`
- OpenAI 兼容视频入口位于 `app/api/v1/video.py`
- `public` / `function` 视频页也已经支持选择 `grok-imagine-video`

但现有实现仍然只支持单个 `xai.api_key`：

- 配置层只有一个 `xai.api_key`
- `XAIVideoService` 在构造时直接读取该单值配置
- 管理后台没有独立的 xAI Key 管理入口

这带来几个实际问题：

- 无法在多个 xAI Key 之间轮换
- 无法单独启用/禁用某个 xAI Key
- 运行时状态无法持久化到后台可管理的数据源
- 无法在后台对 xAI Key 做增删改查

此外，现有 Token 管理页还有一个独立问题：

- 点击单行 Token 刷新按钮后会触发一次全量重载
- 重载后表格滚动位置回到顶部，用户视角丢失

本次设计将一并解决这两个问题。

## 目标

实现一个独立的 xAI Key 池系统，提供：

- 多个 xAI API Key 的统一管理
- 独立的后台管理页 `/admin/xai-keys`
- 独立的后台 API `/v1/admin/xai-keys`
- Key 状态的持久化
- 手动启用/禁用与新增/删除能力
- 运行时按池选 key，并在请求失败时切换 key
- xAI 视频创建与轮询阶段对同一个 key 的绑定

同时修复 Token 管理页单行刷新后滚动位置回顶的问题。

## 非目标

- 不保留旧 `xai.api_key` 的兼容运行路径
- 不新建专用数据库表保存 xAI Key
- 不实现复杂的额度探测或账号配额同步
- 不在本次引入后台告警、指标大盘或多租户路由
- 不重构现有 Token 管理体系

## 方案比较

### 方案 A：继续把 xAI Key 放在配置页里管理

做法：

- 扩展 `xai.*` 配置项
- 在 `/admin/config` 中编辑 `xai.keys`
- 运行时直接从配置中取结构化 key 列表

优点：

- 改动少
- 复用现有配置接口

缺点：

- “配置”与“运行态资源管理”混在一起
- 增删 key、启停 key、显示状态都会让配置页变得臃肿
- 与现有 Token 管理页的交互模式不一致

### 方案 B：独立管理页 + 独立 Manager，持久化仍复用配置存储

做法：

- 新增 `/admin/xai-keys`
- 新增 `/v1/admin/xai-keys`
- 新增 `XAIKeyManager`
- 持久化结构化 `xai.keys`

优点：

- 页面职责清晰
- 运行时逻辑与后台管理逻辑解耦
- 不需要扩展新的存储表
- 与现有 Token 管理模式足够接近，便于维护

缺点：

- 仍然通过配置存储回写整段结构化数据
- 需要新增一套独立页面和 API

### 方案 C：独立管理页 + 独立 Manager + 专用存储表

做法：

- 单独为 xAI Key 建表或独立存储命名空间
- 后台 API 和运行时都绕过配置层

优点：

- 数据边界最清晰
- 长期扩展空间最大

缺点：

- 改动面最大
- 需要同时改 Local/Redis/SQL 三种存储实现
- 对本次需求明显过度设计

## 结论

采用方案 B。

理由：

- 已经能满足“持久化 key 状态、后台增删 key、手动启用/禁用”的目标
- 不需要引入新的存储表结构
- 可以复用现有后台页面、路由和配置持久化模式
- 为后续真要独立存储时保留迁移空间

## 最终设计

### 配置模型

删除旧 `xai.api_key` 作为运行时来源，唯一权威来源改为：

- `xai.keys`

`xai.keys` 为对象数组。每一项包含：

- `id`：稳定内部标识
- `key`：xAI API Key 明文
- `name`：后台展示名称或备注
- `enabled`：人工启停开关
- `status`：运行态摘要
- `last_error`：最近错误摘要
- `blocked_until`：临时阻塞恢复时间
- `last_used_at`：最近使用时间

其中：

- `enabled` 表示人工管理意图
- `status` 表示运行态摘要，不在本次固定穷举所有状态语义
- 前端 API 响应只返回 masked key，不返回明文

### 运行时组件

新增 `XAIKeyManager`，负责：

- 从 `xai.keys` 加载 key 池
- 对 key 做增删改查
- 保存手动启停与运行态状态摘要
- 在请求创建阶段选择一个可用 key
- 在需要时切换到下一个可用 key
- 将已创建的 xAI request 与选中的 key 绑定

`XAIVideoService` 不再直接读取 `xai.api_key`，而是通过 `XAIKeyManager` 获取一个 key 上下文。

### 选 key 与请求绑定

#### 创建阶段

- 从 `enabled=true` 且当前可用的 key 中选择一个
- 选择策略先做成简单轮询
- 若创建阶段遇到可切换错误，则切到下一个 key 重试

#### 轮询阶段

- 一旦创建成功，后续 `GET /videos/{request_id}` 固定使用同一个 key
- 不允许轮询阶段跨 key
- 原因是 request_id 与具体账号/凭据上下文耦合，跨 key 轮询存在失配风险

### 后台 API

新增独立 API：

- `GET /v1/admin/xai-keys`
- `POST /v1/admin/xai-keys`
- `PATCH /v1/admin/xai-keys/{key_id}`
- `DELETE /v1/admin/xai-keys/{key_id}`

功能边界：

- `GET`：返回列表、统计和 masked key
- `POST`：新增 key
- `PATCH`：修改名称、人工启用/禁用
- `DELETE`：删除 key

不在本次设计中加入批量接口；先把单项管理做扎实。

### 后台页面

新增独立页面：

- `/admin/xai-keys`

页面结构参考现有 Token 管理页：

- 顶部标题和说明
- 新增 key 按钮
- 表格列表
- 每行启用/禁用、删除
- 展示运行态摘要与最近错误

页面资源采用与现有 admin 页一致的双份静态结构：

- `app/static/admin/pages/...`
- `_public/static/admin/pages/...`

并在以下位置增加导航入口：

- `app/static/common/html/header.html`
- `_public/static/common/html/header.html`

### 页面与 API 路由

新增页面路由：

- `/admin/xai-keys`

新增管理路由挂载：

- `app/api/v1/admin_api/__init__.py`
- `app/api/v1/admin/__init__.py`

为保持现有模块别名结构，增加与其他 admin 模块一致的 alias 文件。

### xAI 视频接口集成

以下路径都改为通过 `XAIKeyManager` 获取 key：

- `app/services/grok/services/xai_video.py`
- `app/api/v1/video.py`
- `app/api/v1/function/video.py`
- `app/api/v1/public_api/video.py`

具体原则：

- 不再检查 `xai.api_key`
- 改为检查“是否存在可用 key”
- 没有可用 key 时返回明确错误
- 创建成功后使用同一个 key 轮询直到完成或失败

### Token 页面滚动位置修复

现有问题根因是单行刷新成功后直接执行 `loadData()`，导致表格 DOM 完整重绘且未保存滚动位置。

修复方式：

- 在刷新前记录当前滚动位置
- 在数据重新加载并渲染完成后恢复滚动位置
- 同步修复 `app` 与 `_public` 两套 Token 管理脚本

不改变现有刷新 API，也不改变整页数据重载策略，只修复用户视角保持问题。

## 错误处理

### 后台管理 API

- 新增重复 key：返回 400
- 空 key 或格式非法：返回 400
- 修改不存在的 key：返回 404
- 删除不存在的 key：返回 404

### 运行时

- 没有启用且可用的 key：返回明确的 key-pool-unavailable 错误
- 创建阶段全部 key 都失败：返回聚合后的失败结论
- 轮询阶段若绑定 key 失效：返回该 request 对应的明确错误，不跨 key 回退

## 风险

### 风险 1：配置与运行态状态混用导致写回过多

因为本方案复用配置持久化，运行态状态变化也会触发配置保存。

应对：

- 只保存必要状态字段
- 不引入高频统计写回

### 风险 2：前后台静态资源再次漂移

当前仓库同时维护 `app/static` 与 `_public/static` 两套资源。

应对：

- 新页面、导航和 bug 修复都双改
- 测试同时覆盖两边

### 风险 3：轮询阶段换 key 造成 request_id 失配

应对：

- 创建后固定绑定同一个 key
- 只在创建阶段进行跨 key 切换

## 测试策略

需要覆盖：

- `xai.keys` 配置驱动的 manager 行为
- 后台 API 的增删改查
- 页面路由和导航入口存在
- 新页面静态资源存在且具备核心交互标记
- xAI 视频服务改为通过 key 池选 key
- Token 页单行刷新后恢复滚动位置

## 验收标准

- 后台可在 `/admin/xai-keys` 新增、删除、启用、禁用 xAI Key
- xAI 视频相关链路不再依赖 `xai.api_key`
- `xai.keys` 为空时，xAI 视频请求返回明确错误
- 多个 key 时，创建阶段可切换 key
- 创建成功后轮询固定使用同一 key
- Token 管理页单行刷新后不再把滚动条重置到顶部
