# Grok Image-to-Video API Notes (Captured via MCP)

Capture date: 2026-02-08

## What was executed

- Opened `https://grok.com/imagine` via MCP browser tools.
- Confirmed prompt input enables the `Submit` button.
- Triggered image workflow, then clicked `Make video` on a generated image tile.
- Captured network requests during the image-to-video action.

Raw capture files:

- `data/mcp_image_to_video_capture.json`
- `data/mcp_debug_image_capture.json`
- `data/mcp_debug_video_capture.json`

## Observed image-to-video HTTP calls

### 1) Create image media post

- Method: `POST`
- URL: `https://grok.com/rest/media/post/create`
- Request body:

```json
{
  "mediaType": "MEDIA_POST_TYPE_IMAGE",
  "mediaUrl": "https://imagine-public.x.ai/imagine-public/images/<image-id>.jpg"
}
```

- Response body (key fields):

```json
{
  "post": {
    "id": "<post-id>",
    "mediaType": "MEDIA_POST_TYPE_IMAGE",
    "mediaUrl": "https://imagine-public.x.ai/imagine-public/images/<image-id>.jpg",
    "modelName": "imagine_x_1"
  }
}
```

### 2) Start video generation conversation

- Method: `POST`
- URL: `https://grok.com/rest/app-chat/conversations/new`
- Request body (captured):

```json
{
  "temporary": true,
  "modelName": "grok-3",
  "message": "https://imagine-public.x.ai/imagine-public/images/<image-id>.jpg  --mode=normal",
  "toolOverrides": {
    "videoGen": true
  },
  "enableSideBySide": true,
  "responseMetadata": {
    "experiments": [],
    "modelConfigOverride": {
      "modelMap": {
        "videoGenModelConfig": {
          "parentPostId": "<post-id>",
          "aspectRatio": "2:3",
          "videoLength": 10,
          "resolutionName": "720p"
        }
      }
    }
  }
}
```

Notes:

- This call returned HTTP `200` and appears to stream/continue out-of-band (no useful response body in capture).
- `parentPostId` equals the `post.id` from step 1.

### 3) Like media post (side effect)

- Method: `POST`
- URL: `https://grok.com/rest/media/post/like`
- Request body:

```json
{
  "id": "<post-id>"
}
```

- Response body: `{}`

## Header observations

Frequently present on the above calls:

- `Content-Type: application/json`
- `x-statsig-id: <dynamic>`
- `x-xai-request-id: <uuid>`
- tracing headers (`sentry-trace`, `traceparent`, `baggage`)

Authentication is cookie-based in the browser session (`sso`/`sso-rw`), even when not shown in captured per-request header subset.

## Mapping to current project

Already aligned with existing implementation:

- `app/services/grok/media.py:27` (`/rest/media/post/create`)
- `app/services/grok/media.py:28` (`/rest/app-chat/conversations/new`)
- `app/services/grok/media.py:210` (`toolOverrides.videoGen`)
- `app/services/grok/media.py:216` (`videoGenModelConfig`)
- `app/services/grok/media.py:357` (`generate_from_image` path)

Potential parity tweak with browser behavior:

- Browser quick action uses message format `"<image_url>  --mode=normal"`.
- Current code builds `message` from `prompt + mode` in `_build_payload` (`app/services/grok/media.py:186`).
- If you want browser parity for image-only quick mode, add an option to use image URL as message prefix when prompt is empty.

## Integration recommendations

- Keep the current 2-step flow for image-to-video:
  1. `POST /rest/media/post/create` with `MEDIA_POST_TYPE_IMAGE`
  2. `POST /rest/app-chat/conversations/new` with `videoGenModelConfig.parentPostId`
- Keep `videoLength`, `aspectRatio`, `resolutionName` configurable.
- Generate fresh `x-xai-request-id` and `x-statsig-id` per request.
- Preserve browser-like `Referer` (`https://grok.com/imagine`) and cookie auth context.
- Treat `/rest/media/post/like` as optional side effect, not a hard dependency.

