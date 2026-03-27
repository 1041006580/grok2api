"""
Grok 模型真实额度测试脚本

用法:
    python scripts/quota_test.py --model grok-3 --token "你的sso_token"

每次请求发送最短 prompt "hi"，计数直到收到 429 或其他非 200 状态码。
每个模型使用一个全新的 basic token 以保证数据准确。
"""

__test__ = False

import argparse
import asyncio
import base64
import random
import string
import time
import uuid
import sys

import orjson
from curl_cffi.requests import AsyncSession

# ─── 模型注册表 ───────────────────────────────────────────────
MODELS = {
    # chat models (basic tier)
    "grok-3":           ("grok-3",                        "MODEL_MODE_GROK_3"),
    "grok-3-mini":      ("grok-3",                        "MODEL_MODE_GROK_3_MINI_THINKING"),
    "grok-3-thinking":  ("grok-3",                        "MODEL_MODE_GROK_3_THINKING"),
    "grok-4":           ("grok-4",                        "MODEL_MODE_GROK_4"),
    "grok-4-mini":      ("grok-4-mini",                   "MODEL_MODE_GROK_4_MINI_THINKING"),
    "grok-4-thinking":  ("grok-4",                        "MODEL_MODE_GROK_4_THINKING"),
    "grok-4-heavy":     ("grok-4",                        "MODEL_MODE_HEAVY"),
    "grok-4.1-mini":    ("grok-4-1-thinking-1129",        "MODEL_MODE_GROK_4_1_MINI_THINKING"),
    "grok-4.1-fast":    ("grok-4-1-thinking-1129",        "MODEL_MODE_FAST"),
    "grok-4.1-expert":  ("grok-4-1-thinking-1129",        "MODEL_MODE_EXPERT"),
    "grok-4.1-thinking":("grok-4-1-thinking-1129",        "MODEL_MODE_GROK_4_1_THINKING"),
    "grok-4.1-companion": ("grok-4-1-non-thinking-companion", "MODEL_MODE_UNKNOWN"),
    "grok-4.20-beta":   ("grok-420",                      "MODEL_MODE_GROK_420"),
}

CHAT_URL = "https://grok.com/rest/app-chat/conversations/new"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
BROWSER = "chrome136"


# ─── 工具函数 ─────────────────────────────────────────────────
def gen_statsig_id() -> str:
    if random.choice([True, False]):
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        msg = f"e:TypeError: Cannot read properties of null (reading 'children['{rand}']')"
    else:
        rand = "".join(random.choices(string.ascii_lowercase, k=10))
        msg = f"e:TypeError: Cannot read properties of undefined (reading '{rand}')"
    return base64.b64encode(msg.encode()).decode()


def build_headers(sso_token: str) -> dict:
    token = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Baggage": "sentry-environment=production,sentry-release=d6add6fb0460641fd482d767a335ef72b9b6abb8,sentry-public_key=b311e0f2690c81f25e2c4cf6d4f7ce1c",
        "Content-Type": "application/json",
        "Cookie": f"sso={token}; sso-rw={token}",
        "Origin": "https://grok.com",
        "Priority": "u=1, i",
        "Referer": "https://grok.com/",
        "Sec-Ch-Ua": '"Google Chrome";v="136", "Chromium";v="136", "Not(A:Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": USER_AGENT,
        "x-statsig-id": gen_statsig_id(),
        "x-xai-request-id": str(uuid.uuid4()),
    }


def build_payload(message: str, model_name: str, model_mode: str) -> dict:
    return {
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenWidth": 2056,
            "screenHeight": 1329,
            "viewportWidth": 2056,
            "viewportHeight": 1083,
        },
        "disableMemory": True,
        "disableSearch": False,
        "disableSelfHarmShortCircuit": False,
        "disableTextFollowUps": False,
        "enableImageGeneration": True,
        "enableImageStreaming": True,
        "enableSideBySide": True,
        "fileAttachments": [],
        "forceConcise": False,
        "forceSideBySide": False,
        "imageAttachments": [],
        "imageGenerationCount": 2,
        "isAsyncChat": False,
        "isReasoning": False,
        "message": message,
        "modelMode": model_mode,
        "modelName": model_name,
        "responseMetadata": {
            "requestModelDetails": {"modelId": model_name},
        },
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "sendFinalMetadata": True,
        "temporary": True,
        "toolOverrides": {},
    }


# ─── 核心测试逻辑 ─────────────────────────────────────────────
async def test_model_quota(model_id: str, sso_token: str, proxy: str = None, interval: float = 20.0):
    if model_id not in MODELS:
        print(f"[ERROR] 未知模型: {model_id}")
        print(f"可用模型: {', '.join(sorted(MODELS.keys()))}")
        return

    model_name, model_mode = MODELS[model_id]
    payload_bytes = orjson.dumps(build_payload("hi", model_name, model_mode))

    print(f"{'='*60}")
    print(f"模型: {model_id}")
    print(f"  -> modelName: {model_name}")
    print(f"  -> modelMode: {model_mode}")
    print(f"{'='*60}")

    success_count = 0
    error_status = None
    error_body = ""
    start_time = time.time()

    async with AsyncSession() as session:
        while True:
            attempt = success_count + 1
            headers = build_headers(sso_token)
            # 每次请求刷新 statsig 和 request-id
            headers["x-statsig-id"] = gen_statsig_id()
            headers["x-xai-request-id"] = str(uuid.uuid4())

            req_start = time.time()
            try:
                proxies = None
                proxy_arg = None
                if proxy:
                    from urllib.parse import urlparse
                    scheme = urlparse(proxy).scheme.lower()
                    if scheme.startswith("socks"):
                        proxy_arg = proxy
                    else:
                        proxies = {"http": proxy, "https": proxy}

                response = await session.post(
                    CHAT_URL,
                    headers=headers,
                    data=payload_bytes,
                    timeout=120,
                    stream=True,
                    proxy=proxy_arg,
                    proxies=proxies,
                    impersonate=BROWSER,
                )

                status = response.status_code
                req_elapsed = time.time() - req_start

                if status == 200:
                    # 消费完响应流
                    async for _ in response.aiter_lines():
                        pass
                    success_count += 1
                    elapsed_total = time.time() - start_time
                    print(f"  [#{attempt:3d}] 200 OK  ({req_elapsed:.1f}s)  累计: {success_count} 次  总耗时: {elapsed_total:.0f}s")
                    # 等待间隔
                    if interval > 0:
                        print(f"           等待 {interval:.0f}s ...")
                        await asyncio.sleep(interval)
                else:
                    # 非200，记录并退出
                    try:
                        error_body = response.text[:500]
                    except Exception:
                        try:
                            error_body = (await response.atext())[:500]
                        except Exception:
                            error_body = "<无法读取>"
                    error_status = status
                    print(f"  [#{attempt:3d}] {status} STOPPED  ({req_elapsed:.1f}s)")
                    break

            except KeyError as e:
                key = str(e).strip("'\"")
                if key in (":status", "code"):
                    error_status = 429
                    error_body = f"curl_cffi KeyError: {e} (treated as 429)"
                    print(f"  [#{attempt:3d}] 429 (KeyError)  STOPPED")
                    break
                raise
            except Exception as e:
                error_status = -1
                error_body = f"{type(e).__name__}: {e}"
                print(f"  [#{attempt:3d}] EXCEPTION: {error_body}")
                break

    total_time = time.time() - start_time

    print()
    print(f"{'='*60}")
    print(f"测试结果: {model_id}")
    print(f"  成功请求数: {success_count}")
    print(f"  终止状态码: {error_status}")
    print(f"  终止响应体: {error_body[:200]}")
    print(f"  总耗时: {total_time:.1f}s")
    if success_count > 0:
        print(f"  平均每次: {total_time/success_count:.1f}s")
    print(f"{'='*60}")


# ─── 入口 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Grok 模型真实额度测试")
    parser.add_argument("--model", "-m", required=True, help="模型ID")
    parser.add_argument("--token", "-t", required=True, help="SSO token")
    parser.add_argument("--proxy", "-p", default=None, help="代理地址 (可选)")
    parser.add_argument("--interval", "-i", type=float, default=20.0, help="请求间隔秒数 (默认20s)")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有可测模型")
    args = parser.parse_args()

    if args.list:
        print("可测模型:")
        for mid, (mn, mm) in sorted(MODELS.items()):
            print(f"  {mid:35s} -> {mn} / {mm}")
        return

    asyncio.run(test_model_quota(args.model, args.token, args.proxy, args.interval))


if __name__ == "__main__":
    main()
