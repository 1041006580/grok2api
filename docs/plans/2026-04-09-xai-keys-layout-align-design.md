# xAI Keys 管理页布局对齐设计

## 背景

当前 `xAI Keys` 管理页已经具备基本增删改查能力，但页面骨架、标题字号、表格容器、空态、弹窗和按钮风格仍然明显轻于 `Token 管理` 页。

用户要求 `xAI Keys` 管理页与 `Token 管理` 页保持一致，包括：

- 页面整体布局
- 字体大小
- 按钮尺寸与风格
- 表格壳子与空态呈现
- 新增弹窗的视觉样式

本次目标是做一次**最小风险的视觉一致化**，不改动 xAI Key 管理的后端接口和核心行为。

## 目标

- 让 `app/static/admin/pages/xai-keys.html` 与 `_public/static/admin/pages/xai-keys.html` 在视觉壳子上对齐 `token.html`
- 复用 `token.css` 里的表格与弹窗样式
- 保持 xAI 页面自己的字段、列结构和业务操作不变
- 用最小前端契约测试约束这种一致性

## 非目标

- 不重构 Token 页面
- 不新增新的通用 admin CSS 文件
- 不修改 xAI Key 管理 API
- 不引入新的管理功能
- 不改变 xAI Key 的数据模型或状态逻辑

## 方案比较

### 方案 A：直接复用 Token 页面壳子与样式（推荐）

做法：

- `xai-keys.html` 改成与 `token.html` 同层级的页面骨架
- 直接引入 `/static/admin/css/token.css`
- 把新增弹窗切换到 `token` 页同款 `modal-overlay` / `modal-content` 结构
- 对 `xai-keys.js` 做最小适配，使加载态、空态和弹窗继续正常工作

优点：

- 改动最小
- 风险最低
- 与用户要求最贴合
- `app` / `_public` 两套镜像容易同步

缺点：

- `xAI Keys` 页面会直接依赖 `token.css`

### 方案 B：抽公共 admin layout CSS

做法：

- 把 token 页当前使用的表格/弹窗样式抽到单独公共 CSS
- 再让 token/xAI 页面共同引用

优点：

- 长期结构更清晰

缺点：

- 超出本次需求
- 改动面更大
- 容易引入 token 页回归

## 最终方案

采用**方案 A**。

### 页面结构

`xai-keys.html` 将对齐 `token.html` 的以下结构：

- 主容器宽度、内边距、顶部间距
- 标题区左右布局
- 顶部主操作按钮位置与大小
- 表格容器外壳
- `loading` / `empty-state` 的呈现方式

保留 xAI 页面自己的列定义：

- 名称
- 掩码值
- 启用
- 操作

### 样式复用

两套 xAI 页面都引入：

- `common.css`
- `toast.css`
- `token.css`

不新增专用 `xai-keys.css`，避免样式分叉。

### 弹窗

新增 Key 弹窗改成 token 页同款：

- `modal-overlay`
- `modal-content modal-md`
- `modal-header`
- `modal-title`
- `modal-close`
- `modal-label`
- 底部 `geist-button-outline text-xs px-3` / `geist-button text-xs px-3`

### JS 适配

`xai-keys.js` 做最小改动：

- `openCreateModal()` / `closeCreateModal()` 支持 token 页同款 `is-open` 打开关闭过渡
- `renderXAIKeys()` 改为兼容新的 `loading` / `empty-state`
- 行内操作按钮统一使用 token 页相近尺寸

不改接口，不改请求流程，不引入新的状态机。

## 影响文件

- `app/static/admin/pages/xai-keys.html`
- `_public/static/admin/pages/xai-keys.html`
- `app/static/admin/js/xai-keys.js`
- `_public/static/admin/js/xai-keys.js`
- `tests/merge/test_xai_key_pool_contract.py`

## 测试策略

新增最小前端契约测试，覆盖：

- xAI 页面已引入 `token.css`
- xAI 页面标题区与表格壳子对齐 token 页结构
- xAI 页面新增弹窗已使用 token 页 modal 样式结构

回归验证：

- `tests/merge/test_xai_key_pool_contract.py`
- `tests/merge/test_token_contract.py`

## 风险与控制

### 风险 1：xAI 页面绑定的 DOM id 被结构调整破坏

控制：

- 保留现有 `xai-keys-table-body`
- 只调整外围结构
- 通过契约测试约束关键节点

### 风险 2：引入 `token.css` 后出现局部覆盖差异

控制：

- 不增加额外自定义 CSS
- 仅保留必要的表格最小宽度与业务字段 class
- 通过 app/_public 双份同步修改避免镜像不一致

### 风险 3：弹窗开关行为与原先不一致

控制：

- 只引入 token 页已有的 `is-open` 打开/关闭模式
- 保持 `openCreateModal` / `closeCreateModal` 对外接口不变
