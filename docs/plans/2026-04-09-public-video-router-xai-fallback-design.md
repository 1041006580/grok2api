# Public Video 路由与 xAI Fallback 修复设计

## 背景

当前视频链路存在两个彼此关联的问题：

1. `main.py` 把 `/v1/public` 错误挂到了 `function_router`
2. `XAIVideoService` 在 xAI 上游返回 `429` 等可重试错误时不会切换到下一个可用 key

这导致实际现象是：

- 前端 public 页访问的是 `/v1/public/video/start` 与 `/v1/public/video/sse`
- 但后端实际执行的是 `app/api/v1/function/video.py`
- 所以日志会出现“请求路径是 public，但 warning 是 `Function video SSE error`”
- 同时 xAI key 池虽然支持多个 key 持久化管理，但创建阶段遇到 429 会直接失败，无法发挥池化价值

## 根因

### 根因 1：路由挂载错误

`main.py` 当前代码：

- `app.include_router(function_router, prefix="/v1/public")`
- `app.include_router(function_router, prefix="/v1/function")`

这意味着：

- `/v1/public/*` 和 `/v1/function/*` 都走 `function_router`
- `public_api` 模块虽然存在，但没有真正挂载到 FastAPI app

### 根因 2：xAI 视频服务只取一个 key

`app/services/grok/services/xai_video.py` 当前逻辑：

- `_headers()` 第一次调用时通过 `self._key_manager.acquire_key()` 取一个 key
- `_request_json()` 遇到任何 `response.status >= 400` 直接抛 `UpstreamException`
- 不区分“可切换错误”和“不可切换错误”
- 不做 key 轮换或阶段内重试

而 `XAIKeyManager.acquire_key()` 当前也只是：

- 返回第一个 `enabled=true` 且 `status=active` 的 key
- 没有轮询、跳过、或排除已尝试 key 的能力

## 目标

- 修正 `/v1/public` 挂载，确保 public 请求走 `public_api` 路由模块
- 保留 `/v1/function` 兼容层继续走 `function_router`
- 在 xAI 视频**创建阶段**支持多个 key 的顺序 fallback
- 在 xAI 视频**轮询阶段**固定原 key，不跨 key 轮询
- 轮询阶段对原 key 增加短暂重试（2~3 次小 backoff）
- 补齐最小测试，防止路由和 fallback 回归

## 非目标

- 不做 xAI key 状态持久化写回（如自动 blocked/invalid）
- 不改现有 admin xAI key 页面字段
- 不修改 public/function 前端请求协议
- 不为轮询阶段引入跨 key 查询
- 不改造为复杂的 key 调度器

## 方案比较

### 方案 A：只修路由挂载

做法：

- `main.py` 改成 `/v1/public -> public_api.router`
- 保持 xAI 视频服务不变

优点：

- 改动小
- 立刻修正日志与 handler 错位

缺点：

- 429 仍然直接失败
- key 池能力没有真正改善

### 方案 B：修路由挂载 + 创建阶段 key fallback + 轮询阶段原 key 重试（推荐）

做法：

- `main.py` 正确挂载 `public_api.router`
- 在 `XAIVideoService.start_generation()` 所属链路中，对 429 / 部分 5xx 等可切换错误切换下一个 key
- 一旦拿到 `request_id`，后续 `get_generation()` 固定原 key，仅做短暂重试，不跨 key

优点：

- 同时解决“路由错误”和“key 池无实际容错”两个问题
- 保持 request_id 与原始 key 的绑定语义
- 风险可控，符合现有设计边界

缺点：

- 需要增加一点服务层逻辑
- 需要补更多测试

### 方案 C：创建和轮询阶段都支持跨 key 轮换

优点：

- 理论上容错最激进

缺点：

- request_id 很可能绑定到创建它的账号上下文
- 轮询时换 key 可能查错任务或拿到无意义错误
- 风险最高

## 最终方案

采用**方案 B**。

## 详细设计

### 一、路由修正

在 `main.py` 中：

- 新增 `from app.api.v1.public_api import router as public_router`
- 修改挂载为：
  - `/v1/public -> public_router`
  - `/v1/function -> function_router`

这样之后：

- public 前端页面的 `/v1/public/video/*` 将执行 `app/api/v1/public_api/video.py`
- function 前端页面的 `/v1/function/video/*` 将执行 `app/api/v1/function/video.py`
- warning 日志与鉴权语义恢复一致

### 二、xAI key fallback 边界

#### 创建阶段

创建阶段还没有 `request_id`，因此允许切 key。

策略：

- 从所有 `enabled=true` 且 `status=active` 的 key 中按顺序尝试
- 如果当前 key 命中“可切换上游错误”，尝试下一个 key
- 直到成功或所有 key 都失败

可切换错误先收敛到：

- `429`
- `500`
- `502`
- `503`
- `504`

不对所有错误都 fallback，避免把参数错误/鉴权错误误当成池内切换问题。

#### 轮询阶段

轮询阶段已经拿到 `request_id`，必须固定原 key。

策略：

- 只使用创建成功时绑定的 key
- 不跨 key
- 对原 key 做 2~3 次短暂重试

推荐默认：

- 尝试总次数：3 次
- backoff：`0.5s -> 1s -> 2s`

#### 为什么轮询不切 key

因为：

- `request_id` 很可能与创建它的上游账号/凭据上下文相关
- 换 key 轮询虽然“看起来更能容错”，但可能是错误对象
- 在这个阶段，**正确性优先于激进容错**

### 三、最小代码结构调整

#### `XAIKeyManager`

保留现有 `acquire_key()` 以兼容其他调用方，新增一个轻量方法：

- `iter_active_keys()` 或同等语义方法

返回：

- 当前可用于创建阶段尝试的 key 列表

不在本次引入复杂轮询索引或持久化状态更新。

#### `XAIVideoService`

新增最小辅助逻辑：

- 判断 `UpstreamException.details["status"]` 是否属于“可切换错误”
- 创建阶段顺序尝试多个 key
- 轮询阶段在固定 key 上做短暂重试

实现原则：

- 不改已有外部调用方式
- 尽量把 fallback 逻辑封装在 `XAIVideoService` 内部

### 四、测试策略

#### 路由测试

新增或扩展 merge 测试，验证：

- `/v1/public/video/start` 对应 endpoint module 为 `app.api.v1.public_api.video`
- `/v1/public/video/sse` 对应 endpoint module 为 `app.api.v1.public_api.video`
- `/v1/function/video/start` / `/v1/function/video/sse` 仍来自 `app.api.v1.function.video`

#### xAI 服务测试

新增测试覆盖：

1. 创建阶段：
   - 第一个 key 返回 429
   - 第二个 key 成功
   - 最终返回成功结果

2. 创建阶段失败耗尽：
   - 所有 key 都返回可切换错误
   - 最终抛出最后一个上游错误

3. 轮询阶段：
   - 固定原 key
   - 前两次 429，第三次成功
   - 不调用其他 key

4. 轮询阶段失败：
   - 原 key 重试耗尽后抛错
   - 不跨 key

## 风险与控制

### 风险 1：改路由后破坏 public/function 兼容行为

控制：

- 仅修 `main.py` 挂载
- 不改 `public_api.video.py` / `function.video.py` 的对外路径定义
- 用 endpoint module 断言测试锁死

### 风险 2：fallback 误吞业务错误

控制：

- 只对明确的上游状态码触发 fallback
- 参数错误、校验错误、配置错误不切 key

### 风险 3：轮询逻辑重试导致等待变长

控制：

- 仅做短暂、有限次重试
- 默认最多 3 次
- 仍然快速失败，不引入长时间阻塞
