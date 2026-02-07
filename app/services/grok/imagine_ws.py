"""Grok Imagine WebSocket 客户端 - 支持流式预览和 HTTP 代理"""

import asyncio
import json
import uuid
import time
import ssl
import re
from typing import Optional, List, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field

import aiohttp
from aiohttp_socks import ProxyConnector

from app.core.config import config
from app.core.logger import logger
from app.services.token import TokenService, EffortType


# WebSocket 常量
WS_URL = "wss://grok.com/ws/imagine/listen"


@dataclass
class ImageProgress:
    """单张图片的生成进度"""
    image_id: str  # 从 URL 提取的 UUID
    stage: str = "preview"  # preview -> medium -> final
    blob: str = ""
    blob_size: int = 0
    url: str = ""
    is_final: bool = False


@dataclass
class GenerationProgress:
    """整体生成进度"""
    total: int = 4  # 预期生成数量
    images: Dict[str, ImageProgress] = field(default_factory=dict)
    completed: int = 0  # 已完成的最终图片数量

    def get_completed_images(self) -> List[ImageProgress]:
        """获取所有已完成的图片"""
        return [img for img in self.images.values() if img.is_final]


# 流式回调类型
StreamCallback = Callable[[ImageProgress, GenerationProgress], Awaitable[None]]


class ImagineWSClient:
    """Grok Imagine WebSocket 客户端"""

    def __init__(self):
        self._ssl_context = ssl.create_default_context()
        # 用于从 URL 提取图片 ID
        self._url_pattern = re.compile(r'/images/([a-f0-9-]+)\.(png|jpg)')

    def _get_connector(self) -> Optional[aiohttp.BaseConnector]:
        """获取连接器（支持代理）"""
        proxy_url = config.get("grok.base_proxy_url")

        if proxy_url:
            logger.info(f"[ImagineWS] 使用代理: {proxy_url}")
            # 支持 http/https/socks4/socks5 代理
            return ProxyConnector.from_url(proxy_url, ssl=self._ssl_context)

        return aiohttp.TCPConnector(ssl=self._ssl_context)

    def _get_ws_headers(self, token: str) -> Dict[str, str]:
        """构建 WebSocket 请求头"""
        return {
            "Cookie": f"sso={token}; sso-rw={token}",
            "Origin": "https://grok.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _extract_image_id(self, url: str) -> Optional[str]:
        """从 URL 提取图片 ID"""
        match = self._url_pattern.search(url)
        if match:
            return match.group(1)
        return None

    def _is_final_image(self, msg: Dict[str, Any]) -> bool:
        """判断是否是最终高清图片"""
        return msg.get("percentage_complete") == 100

    def _build_request_message(
        self,
        request_id: str,
        text: str,
        aspect_ratio: str,
        enable_nsfw: bool,
        msg_type: str = "input_text"
    ) -> Dict[str, Any]:
        """构建 WebSocket 请求消息"""
        return {
            "type": "conversation.item.create",
            "timestamp": int(time.time() * 1000),
            "item": {
                "type": "message",
                "content": [{
                    "requestId": request_id,
                    "text": text,
                    "type": msg_type,
                    "properties": {
                        "section_count": 0,
                        "is_kids_mode": False,
                        "enable_nsfw": enable_nsfw,
                        "skip_upsampler": False,
                        "is_initial": False,
                        "aspect_ratio": aspect_ratio
                    }
                }]
            }
        }

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = None,
        n: int = None,
        enable_nsfw: bool = True,
        token: Optional[str] = None,
        max_retries: int = 5,
        stream_callback: Optional[StreamCallback] = None
    ) -> Dict[str, Any]:
        """
        生成图片

        Args:
            prompt: 提示词
            aspect_ratio: 宽高比 (1:1, 2:3, 3:2)
            n: 生成数量，如果不指定则使用配置的默认值
            enable_nsfw: 是否启用 NSFW
            token: 指定 Token，否则从池中获取
            max_retries: 最大重试次数 (用于轮询不同 Token)
            stream_callback: 流式回调，每次收到图片更新时调用

        Returns:
            生成结果，包含图片 base64 列表
        """
        # 使用配置的默认值
        if n is None:
            n = config.get("imagine.default_image_count", 4)
        if aspect_ratio is None:
            aspect_ratio = config.get("imagine.default_aspect_ratio", "2:3")

        logger.info(f"[ImagineWS] 请求生成 {n} 张图片")

        last_error = None
        blocked_retries = 0
        max_blocked_retries = 3

        for attempt in range(max_retries):
            current_token = token if token else await TokenService.get_token("ssoBasic")

            if not current_token:
                return {"success": False, "error": "没有可用的 Token"}

            try:
                result = await self._do_generate(
                    token=current_token,
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    n=n,
                    enable_nsfw=enable_nsfw,
                    stream_callback=stream_callback
                )

                if result.get("success"):
                    await TokenService.consume(current_token, EffortType.HIGH)
                    return result

                error_code = result.get("error_code", "")

                # 检查是否被 blocked
                if error_code == "blocked":
                    blocked_retries += 1
                    logger.warning(
                        f"[ImagineWS] 检测到 blocked，重试 {blocked_retries}/{max_blocked_retries}"
                    )
                    await TokenService.record_fail(current_token, 403, "blocked - 无法生成最终图片")

                    if blocked_retries >= max_blocked_retries:
                        return {
                            "success": False,
                            "error_code": "blocked",
                            "error": f"连续 {max_blocked_retries} 次被 blocked，请稍后重试"
                        }
                    # 如果指定了 Token 则不重试
                    if token:
                        return result
                    continue

                if error_code in ["rate_limit_exceeded", "unauthorized"]:
                    await TokenService.record_fail(current_token, 429 if error_code == "rate_limit_exceeded" else 401, result.get("error", ""))
                    last_error = result
                    if token:
                        return result
                    logger.info(f"[ImagineWS] 尝试 {attempt + 1}/{max_retries} 失败，切换 Token...")
                    continue
                else:
                    return result

            except Exception as e:
                logger.error(f"[ImagineWS] 生成失败: {e}")
                await TokenService.record_fail(current_token, 500, str(e))
                last_error = {"success": False, "error": str(e)}
                if token:
                    return last_error
                continue

        return last_error or {"success": False, "error": "所有重试都失败了"}

    async def _do_generate(
        self,
        token: str,
        prompt: str,
        aspect_ratio: str,
        n: int,
        enable_nsfw: bool,
        stream_callback: Optional[StreamCallback] = None
    ) -> Dict[str, Any]:
        """执行生成"""
        request_id = str(uuid.uuid4())
        headers = self._get_ws_headers(token)

        ws_timeout = config.get("imagine.ws_timeout", 120)

        connector = self._get_connector()

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.ws_connect(
                    WS_URL,
                    headers=headers,
                    heartbeat=20,
                    receive_timeout=ws_timeout
                ) as ws:
                    # 发送初始生成请求
                    message = self._build_request_message(
                        request_id=request_id,
                        text=prompt,
                        aspect_ratio=aspect_ratio,
                        enable_nsfw=enable_nsfw,
                        msg_type="input_text"
                    )

                    await ws.send_json(message)
                    logger.info(f"[ImagineWS] 已发送请求: {prompt[:50]}...")

                    # 进度跟踪
                    progress = GenerationProgress(total=n)
                    error_info = None
                    start_time = time.time()
                    last_activity = time.time()
                    filtered_count = 0  # 被 NSFW 过滤的图片数量
                    translated_prompt = None  # 服务端翻译后的 prompt（用于 scroll）
                    scroll_count = 0
                    max_scroll = max((n - 1) // 6, 0)  # 每批约 6 张

                    while time.time() - start_time < ws_timeout:
                        try:
                            ws_msg = await asyncio.wait_for(ws.receive(), timeout=5.0)

                            if ws_msg.type == aiohttp.WSMsgType.TEXT:
                                last_activity = time.time()
                                msg = json.loads(ws_msg.data)
                                msg_type = msg.get("type")

                                if msg_type == "json":
                                    # 提取翻译后的 prompt（用于 scroll 请求）
                                    server_prompt = msg.get("prompt")
                                    if server_prompt:
                                        translated_prompt = server_prompt

                                    # 检查是否被 NSFW 过滤
                                    pct = msg.get("percentage_complete")
                                    r_rated = msg.get("r_rated", False)
                                    if pct == 100 and r_rated:
                                        filtered_count += 1
                                        logger.warning(
                                            f"[ImagineWS] 图片被 NSFW 过滤 "
                                            f"(已过滤 {filtered_count} 张)"
                                        )

                                elif msg_type == "image":
                                    blob = msg.get("blob", "")
                                    url = msg.get("url", "")

                                    if blob and url:
                                        image_id = self._extract_image_id(url)
                                        if not image_id:
                                            continue

                                        blob_size = len(blob)
                                        is_final = self._is_final_image(msg)

                                        # 确定阶段
                                        if is_final:
                                            stage = "final"
                                        else:
                                            stage = "preview"

                                        # 更新或创建图片进度
                                        img_progress = ImageProgress(
                                            image_id=image_id,
                                            stage=stage,
                                            blob=blob,
                                            blob_size=blob_size,
                                            url=url,
                                            is_final=is_final
                                        )

                                        # 只更新到更高阶段
                                        existing = progress.images.get(image_id)
                                        if not existing or (not existing.is_final):
                                            progress.images[image_id] = img_progress

                                            # 更新完成计数
                                            progress.completed = len([
                                                img for img in progress.images.values()
                                                if img.is_final
                                            ])

                                            logger.debug(
                                                f"[ImagineWS] 图片 {image_id[:8]}... "
                                                f"阶段={stage} 大小={blob_size} "
                                                f"进度={progress.completed}/{n}"
                                            )

                                            # 调用流式回调
                                            if stream_callback:
                                                try:
                                                    await stream_callback(img_progress, progress)
                                                except Exception as e:
                                                    logger.warning(f"[ImagineWS] 流式回调错误: {e}")

                                elif msg_type == "error":
                                    error_code = msg.get("err_code", "")
                                    error_msg = msg.get("err_msg", "")
                                    logger.warning(f"[ImagineWS] 错误: {error_code} - {error_msg}")
                                    error_info = {"error_code": error_code, "error": error_msg}

                                    if error_code == "rate_limit_exceeded":
                                        return {
                                            "success": False,
                                            "error_code": error_code,
                                            "error": error_msg
                                        }

                                # 检查是否收集够了最终图片
                                if progress.completed >= n:
                                    logger.info(f"[ImagineWS] 已收集 {progress.completed} 张最终图片")
                                    break

                            elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning(f"[ImagineWS] WebSocket 关闭或错误: {ws_msg.type}")
                                break

                        except asyncio.TimeoutError:
                            if progress.completed > 0 and time.time() - last_activity > 10:
                                # 还没收够且可以 scroll
                                if progress.completed < n and translated_prompt and scroll_count < max_scroll:
                                    scroll_count += 1
                                    scroll_msg = self._build_request_message(
                                        request_id=str(uuid.uuid4()),
                                        text=translated_prompt,
                                        aspect_ratio=aspect_ratio,
                                        enable_nsfw=enable_nsfw,
                                        msg_type="input_scroll"
                                    )
                                    await ws.send_json(scroll_msg)
                                    logger.info(
                                        f"[ImagineWS] 发送 scroll 请求 ({scroll_count}/{max_scroll})，"
                                        f"继续生成更多图片..."
                                    )
                                    last_activity = time.time()
                                    continue
                                else:
                                    logger.info(f"[ImagineWS] 超时，已收集 {progress.completed} 张最终图片")
                                    break
                            continue

                    # 收集最终图片的 base64
                    result_b64 = self._collect_final_images(progress, n)

                    if result_b64:
                        result = {
                            "success": True,
                            "b64_list": result_b64,
                            "count": len(result_b64)
                        }
                        if filtered_count > 0:
                            result["filtered"] = filtered_count
                        return result
                    elif error_info:
                        return {"success": False, **error_info}
                    elif filtered_count > 0:
                        return {
                            "success": False,
                            "error_code": "nsfw_filtered",
                            "error": f"所有 {filtered_count} 张图片都被 NSFW 过滤"
                        }
                    else:
                        return {"success": False, "error": "未收到图片数据"}

        except aiohttp.ClientError as e:
            logger.error(f"[ImagineWS] 连接错误: {e}")
            return {"success": False, "error": f"连接失败: {e}"}

    def _collect_final_images(
        self,
        progress: GenerationProgress,
        n: int
    ) -> List[str]:
        """收集最终图片的 base64 列表（只收集 final 阶段的图片）"""
        result_b64 = []
        saved_ids = set()

        # 只选择 final 图片
        final_images = [img for img in progress.images.values() if img.is_final]
        for img in sorted(final_images, key=lambda x: x.blob_size, reverse=True):
            if img.image_id in saved_ids:
                continue
            if len(saved_ids) >= n:
                break

            result_b64.append(img.blob)
            saved_ids.add(img.image_id)

            logger.debug(
                f"[ImagineWS] 收集图片: {img.image_id[:8]}... "
                f"({img.blob_size / 1024:.1f}KB)"
            )

        return result_b64

    async def generate_stream(
        self,
        prompt: str,
        aspect_ratio: str = None,
        n: int = None,
        enable_nsfw: bool = True,
        token: Optional[str] = None
    ):
        """
        流式生成图片 - 使用异步生成器

        Yields:
            Dict 包含当前图片进度信息
        """
        # 使用配置的默认值
        if n is None:
            n = config.get("imagine.default_image_count", 4)
        if aspect_ratio is None:
            aspect_ratio = config.get("imagine.default_aspect_ratio", "2:3")

        queue: asyncio.Queue = asyncio.Queue()
        done = asyncio.Event()

        async def callback(img: ImageProgress, prog: GenerationProgress):
            await queue.put({
                "type": "progress",
                "image_id": img.image_id,
                "stage": img.stage,
                "blob": img.blob if img.is_final else "",  # 只返回最终图片的 blob
                "blob_size": img.blob_size,
                "is_final": img.is_final,
                "completed": prog.completed,
                "total": prog.total
            })

        async def generate_task():
            result = await self.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                n=n,
                enable_nsfw=enable_nsfw,
                token=token,
                stream_callback=callback
            )
            await queue.put({"type": "result", **result})
            done.set()

        task = asyncio.create_task(generate_task())

        try:
            while not done.is_set() or not queue.empty():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield item
                    if item.get("type") == "result":
                        break
                except asyncio.TimeoutError:
                    continue
        finally:
            if not task.done():
                task.cancel()


__all__ = ["ImagineWSClient", "ImageProgress", "GenerationProgress"]
