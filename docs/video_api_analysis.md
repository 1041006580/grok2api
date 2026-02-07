# Grok Video Generation API 分析

## 概述

Grok Imagine 的视频生成功能使用 REST API + SSE 流式响应，与图片生成的 WebSocket 方式不同。

## API 流程

### 步骤 1: 创建视频帖子

**端点**: `POST https://grok.com/rest/media/post/create`

**请求头**:
```
Content-Type: application/json
Cookie: sso={token}; sso-rw={token}
Referer: https://grok.com/imagine
```

**请求体**:
```json
{
  "mediaType": "MEDIA_POST_TYPE_VIDEO",
  "prompt": "a bird flying in the sky"
}
```

**响应**:
```json
{
  "post": {
    "id": "cc40cf04-b6dc-42ed-8e4b-36246475ae54",
    "userId": "f3c29a1d-6ec4-44fe-a967-c099b65c7c49",
    "createTime": "2026-02-07T05:57:40.484856125Z",
    "prompt": "a bird flying in the sky",
    "mediaType": "MEDIA_POST_TYPE_VIDEO",
    "mediaUrl": "",
    "mimeType": "video/mp4",
    "audioUrls": [],
    "childPosts": [],
    "originalPrompt": "a bird flying in the sky",
    "mode": "text",
    "thumbnailImageUrl": "https://imagine-public.x.ai/imagine-public/images/xxx.png",
    "availableActions": [],
    "images": [],
    "videos": [
      {
        "id": "cc40cf04-b6dc-42ed-8e4b-36246475ae54",
        "userId": "f3c29a1d-6ec4-44fe-a967-c099b65c7c49",
        "createTime": "2026-02-07T05:57:40.484856125Z",
        "prompt": "a bird flying in the sky",
        "mediaType": "MEDIA_POST_TYPE_VIDEO",
        "mediaUrl": "",
        "mimeType": "video/mp4",
        "audioUrls": [],
        "childPosts": [],
        "originalPrompt": "a bird flying in the sky",
        "mode": "text",
        "thumbnailImageUrl": "https://imagine-public.x.ai/imagine-public/images/xxx.png",
        "availableActions": [],
        "images": [],
        "videos": [],
        "inputMediaItems": []
      }
    ],
    "inputMediaItems": []
  }
}
```

### 步骤 2: 触发视频生成

**端点**: `POST https://grok.com/rest/app-chat/conversations/new`

**请求体**:
```json
{
  "temporary": true,
  "modelName": "grok-3",
  "message": "a bird flying in the sky --mode=custom",
  "toolOverrides": {
    "videoGen": true
  },
  "enableSideBySide": true,
  "responseMetadata": {
    "experiments": [],
    "modelConfigOverride": {
      "modelMap": {
        "videoGenModelConfig": {
          "parentPostId": "cc40cf04-b6dc-42ed-8e4b-36246475ae54",
          "aspectRatio": "2:3",
          "videoLength": 10,
          "resolutionName": "720p"
        }
      }
    }
  }
}
```

**响应**: SSE 流式响应，返回视频生成进度

### 步骤 3: 获取视频

视频生成完成后，可通过以下 URL 格式获取:

```
https://assets.grok.com/users/{userId}/generated/{postId}/generated_video.mp4
```

示例:
```
https://assets.grok.com/users/f3c29a1d-6ec4-44fe-a967-c099b65c7c49/generated/cc40cf04-b6dc-42ed-8e4b-36246475ae54/generated_video.mp4
```

## 视频配置选项

### videoGenModelConfig 参数

| 参数 | 类型 | 说明 | 示例值 |
|------|------|------|--------|
| parentPostId | string | 步骤1创建的帖子ID | `cc40cf04-b6dc-42ed-8e4b-36246475ae54` |
| aspectRatio | string | 宽高比 | `2:3` (竖屏), `3:2` (横屏), `1:1` (方形) |
| videoLength | number | 视频长度(秒) | `10` |
| resolutionName | string | 分辨率名称 | `720p`, `480p` |

**注意**: 实际 API 使用 `resolutionName` 参数（如 `"720p"`），而不是 `videoResolution`（如 `"SD"`/`"HD"`）。

### 视频模式 (--mode)

| 模式 | 说明 |
|------|------|
| custom | 自定义模式 |
| spicy | 成人内容模式 |
| fun | 趣味模式 |
| normal | 普通模式 |

## 图片生成 vs 视频生成对比

| 特性 | 图片生成 | 视频生成 |
|------|---------|---------|
| 协议 | WebSocket | REST API + SSE |
| 端点 | `wss://grok.com/ws/imagine/listen` | `/rest/media/post/create` + `/rest/app-chat/conversations/new` |
| 输出格式 | Base64 图片数据 | MP4 视频 URL |
| 生成时长 | 几秒 | 约30-60秒 |
| 输出时长 | N/A | ~10秒视频 |

## 其他相关 API

### 获取帖子列表

**端点**: `POST https://grok.com/rest/media/post/list`

**请求体**:
```json
{
  "limit": 40,
  "cursor": "1770311892984",
  "filter": {
    "source": "MEDIA_POST_SOURCE_LIKED"
  }
}
```

### 点赞帖子

**端点**: `POST https://grok.com/rest/media/post/like`

**请求体**:
```json
{
  "id": "cc40cf04-b6dc-42ed-8e4b-36246475ae54"
}
```

## 视频信息

从页面提取的视频元素信息:

```json
{
  "currentUrl": "https://grok.com/imagine/post/2ce3ff5f-6217-46d3-be95-4fc1fb7e08bc",
  "videoInfo": {
    "currentSrc": "https://assets.grok.com/users/f3c29a1d-6ec4-44fe-a967-c099b65c7c49/generated/2ce3ff5f-6217-46d3-be95-4fc1fb7e08bc/generated_video.mp4?cache=1",
    "duration": 10.041667,
    "src": "https://assets.grok.com/users/f3c29a1d-6ec4-44fe-a967-c099b65c7c49/generated/2ce3ff5f-6217-46d3-be95-4fc1fb7e08bc/generated_video.mp4?cache=1",
    "videoHeight": 1168,
    "videoWidth": 784
  }
}
```

## 实现建议

1. **创建 VideoClient 类**: 类似于 `ImagineWSClient`，但使用 REST API
2. **使用 aiohttp 进行 HTTP 请求**: 支持代理和 SSE 流式响应
3. **轮询或 SSE 监听**: 等待视频生成完成
4. **下载视频**: 从 assets.grok.com 下载生成的视频文件

## 注意事项

1. 视频生成需要更长时间（约30-60秒）
2. 需要处理 SSE 流式响应来获取生成进度
3. 视频 URL 可能需要认证才能访问
4. 需要考虑超时和重试机制
