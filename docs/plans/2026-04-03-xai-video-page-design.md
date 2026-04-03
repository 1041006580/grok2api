# xAI Video 页面接入设计

## 背景

当前仓库已经新增了基于 `xai.api_key` 的官方视频生成能力：

- 新模型：`grok-imagine-video`
- 后端入口：`/v1/videos`
- 实现服务：`app/services/grok/services/xai_video.py`

但现有页面侧视频入口仍然只接了旧的 SSO/reverse 视频链路：

- `public` 视频页通过 `app/static/public/js/video.js` 调用 `/v1/public/video/start`
- `function` 视频页通过 `_public/static/function/js/video.js` 调用 `/v1/function/video/start`

这导致新增能力目前主要只能通过接口调用，页面用户无法显式选择并使用 `grok-imagine-video`。

## 目标

将 `grok-imagine-video (xAI API)` 同时接入 `public` 与 `function` 两套视频页面，并保持现有旧视频链路完全可用。

## 非目标

- 不重构整套视频前端为单一共享实现
- 不替换现有 `grok-imagine-1.0-video` / `grok-imagine-1.0-video-super` 的调用路径
- 不修改 `/v1/public/video/*` 和 `/v1/function/video/*` 的 SSE 任务协议

## 方案选择

### 方案 A：页面新增“模型/引擎”下拉，按模型切换调用路径

推荐方案。

做法：

- 在 `public` 和 `function` 视频页都新增模型下拉
- 默认仍选择现有旧模型
- 当用户选择 `grok-imagine-video (xAI API)` 时，前端改为调用 `/v1/videos`
- 当用户选择旧模型时，继续走现有 `/v1/public/video/start` / `/v1/function/video/start`

优点：

- 对现有链路侵入最小
- 新旧能力边界清晰
- 用户可以显式理解“这是不同引擎”

缺点：

- 两套页面 JS/HTML 都要改
- 前端存在少量重复逻辑

### 方案 B：先抽象公共视频页面逻辑，再统一接入新模型

不推荐作为本次任务。

优点：

- 长期结构更干净

缺点：

- 会把一次功能接入扩大成前端重构
- 风险和回归面明显变大

### 方案 C：新增单独 xAI 视频页面

不推荐。

优点：

- 接入快

缺点：

- 用户入口割裂
- 与“两个页面都加一下”的目标不一致

## 最终设计

采用方案 A。

### 页面层

两套页面都新增一个模型下拉：

- 旧链路选项：保留当前视频模型语义
- 新链路选项：`grok-imagine-video (xAI API)`

建议默认项保持旧链路，避免影响已有用户习惯和已有自动化脚本。

### 交互层

当选择 `grok-imagine-video (xAI API)` 时，前端动态切换规则：

- 时长限制改为 `1-15s`
- 保留单张参考图输入
- 隐藏或禁用旧链路专属/不适用控件
- 给出提示文案，明确这是 xAI API 模式，与 SSO 视频模式不同

当切回旧模型时：

- 恢复现有控件和默认值
- 不改变现有 SSE 任务交互

### 调用路径

#### 旧模型

继续保持当前行为：

- `public` 页：`/v1/public/video/start` + `/v1/public/video/sse`
- `function` 页：`/v1/function/video/start` + `/v1/function/video/sse`

#### 新模型 `grok-imagine-video`

直接调用 `/v1/videos`，由后端现有 `app/api/v1/video.py` 处理。

这条链路是同步完成态返回，不走现有 SSE task 模型，因此前端需要新增一个直接创建并拿回结果 URL 的分支。

### 请求映射

页面字段映射到 `/v1/videos`：

- `prompt` -> `prompt`
- 模型下拉 -> `model`
- 比例选择 -> `size` 映射
- 时长 -> `seconds`
- 分辨率选择 -> `quality` 映射
- 参考图 -> `image_reference`

现有页面使用的是：

- `aspect_ratio`
- `video_length`
- `resolution_name`

而 `/v1/videos` 需要的是：

- `size`
- `seconds`
- `quality`

因此前端必须新增一个单独的参数转换层，不能把旧 payload 直接复用给新接口。

### 错误处理

- 若未配置 `xai.api_key`，直接展示后端返回的明确错误，不回退旧链路
- 若用户输入超过 `15s`，前端先阻止提交，后端继续保底
- 若参考图数量/格式不符合 xAI 模式要求，前端直接提示
- 若 `/v1/videos` 成功但没返回 URL，按生成失败处理

### 测试

需要覆盖：

- 页面出现新模型选项
- 选中 xAI 模式时，页面规则切换正确
- 选中旧模型时，原逻辑不变
- xAI 模式提交时，调用 `/v1/videos` 且 payload 正确
- 旧模型提交时，仍调用旧的 `/v1/public/video/start` / `/v1/function/video/start`

## 风险

### 风险 1：两套页面逻辑漂移

`public` 与 `function` 页面结构相似但不是完全共用，若只改其中一侧会造成体验不一致。

应对：

- 本次明确双改
- 新增测试分别覆盖两侧

### 风险 2：旧链路被误伤

若把新模型直接塞进旧 SSE 流，会破坏当前稳定视频能力。

应对：

- 新模型走 `/v1/videos` 独立分支
- 旧模型调用路径不改

### 风险 3：参数语义错配

页面现在用比例/分辨率/时长字段，`/v1/videos` 新链路使用不同字段语义。

应对：

- 前端显式做参数转换
- 通过测试锁定映射关系

## 验收标准

- `/video` 页面在 `function` 模式下可以选择 `grok-imagine-video (xAI API)`
- `public` 对应视频页也可以选择相同模型
- 选择新模型后，前端限制为 `1-15s`
- 选择新模型并提交后，页面能返回最终视频 URL
- 旧模型视频生成行为不回归
