"""
NSFW (Unhinged) 模式服务

使用 gRPC-Web 协议开启账号的 NSFW 功能。
包含：1. 设置年龄（>18岁）2. 开启 Unhinged 模式
"""

from dataclasses import dataclass
from typing import Optional

from curl_cffi.requests import AsyncSession

from app.core.config import get_config
from app.core.logger import logger
from app.services.grok.grpc_web import (
    encode_grpc_web_payload,
    parse_grpc_web_response,
    get_grpc_status,
)


NSFW_API = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
AGE_API = "https://grok.com/rest/auth/set-birth-date"
BROWSER = "chrome136"
TIMEOUT = 30


def _get_api_url(default_url: str) -> str:
    """获取 API URL，支持反向代理"""
    api_base = get_config("grok.api_base", "")
    if api_base:
        return default_url.replace("https://grok.com", api_base.rstrip("/"))
    return default_url


@dataclass
class NSFWResult:
    """NSFW 操作结果"""

    success: bool
    http_status: int
    grpc_status: Optional[int] = None
    grpc_message: Optional[str] = None
    error: Optional[str] = None
    age_set: Optional[bool] = None
    unhinged_enabled: Optional[bool] = None


class NSFWService:
    """NSFW 模式服务"""

    def __init__(self, proxy: str = None):
        self.proxy = proxy or get_config("grok.base_proxy_url", "")

    def _build_headers(self, token: str, content_type: str = "application/grpc-web+proto") -> dict:
        """构造请求头"""
        token = token[4:] if token.startswith("sso=") else token
        cf = get_config("grok.cf_clearance", "")
        cookie = f"sso={token}; sso-rw={token}"
        if cf:
            cookie += f"; cf_clearance={cf}"

        return {
            "accept": "*/*",
            "content-type": content_type,
            "origin": "https://grok.com",
            "referer": "https://grok.com/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "cookie": cookie,
        }

    @staticmethod
    def _build_payload() -> bytes:
        """构造请求 payload"""
        # protobuf: enable_unhinged=true (field1=1, field2=1)
        protobuf = bytes([0x08, 0x01, 0x10, 0x01])
        return encode_grpc_web_payload(protobuf)

    async def set_age(self, token: str) -> NSFWResult:
        """设置出生日期（年龄验证 > 18岁）"""
        token = token[4:] if token.startswith("sso=") else token
        headers = self._build_headers(token, content_type="application/json")
        # 移除 gRPC 特有的头
        headers.pop("x-grpc-web", None)
        headers.pop("x-user-agent", None)

        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None

        try:
            async with AsyncSession(impersonate=BROWSER) as session:
                response = await session.post(
                    _get_api_url(AGE_API),
                    json={"birthDate": "2001-01-01T16:00:00.000Z"},
                    headers=headers,
                    timeout=TIMEOUT,
                    proxies=proxies,
                )

                if response.status_code == 200:
                    logger.info(f"Age set success: HTTP {response.status_code}")
                    return NSFWResult(
                        success=True,
                        http_status=response.status_code,
                        age_set=True,
                    )
                else:
                    logger.warning(f"Age set failed: HTTP {response.status_code}")
                    return NSFWResult(
                        success=False,
                        http_status=response.status_code,
                        error=f"HTTP {response.status_code}",
                        age_set=False,
                    )

        except Exception as e:
            logger.error(f"Age set failed: {e}")
            return NSFWResult(success=False, http_status=0, error=str(e)[:100], age_set=False)

    async def _enable_unhinged(self, token: str) -> NSFWResult:
        """开启 Unhinged 模式"""
        headers = self._build_headers(token)
        payload = self._build_payload()
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None

        try:
            async with AsyncSession(impersonate=BROWSER) as session:
                response = await session.post(
                    _get_api_url(NSFW_API),
                    data=payload,
                    headers=headers,
                    timeout=TIMEOUT,
                    proxies=proxies,
                )

                if response.status_code != 200:
                    logger.warning(f"NSFW enable HTTP error: {response.status_code}")
                    return NSFWResult(
                        success=False,
                        http_status=response.status_code,
                        error=f"HTTP {response.status_code}",
                        unhinged_enabled=False,
                    )

                # 解析 gRPC-Web 响应
                content_type = response.headers.get("content-type")
                _, trailers = parse_grpc_web_response(
                    response.content, content_type=content_type
                )

                grpc_status = get_grpc_status(trailers)

                # HTTP 200 且无 grpc-status（空响应）或 grpc-status=0 都算成功
                success = grpc_status.code == -1 or grpc_status.ok

                if not success:
                    logger.warning(f"NSFW enable gRPC error: code={grpc_status.code}, msg={grpc_status.message}")

                return NSFWResult(
                    success=success,
                    http_status=response.status_code,
                    grpc_status=grpc_status.code,
                    grpc_message=grpc_status.message or None,
                    unhinged_enabled=success,
                )

        except Exception as e:
            logger.error(f"NSFW enable failed: {e}")
            return NSFWResult(success=False, http_status=0, error=str(e)[:100], unhinged_enabled=False)

    async def enable(self, token: str) -> NSFWResult:
        """
        开启 NSFW 模式（两步都必须成功）

        1. 设置年龄 > 18岁
        2. 开启 Unhinged 模式
        """
        # 1. 设置年龄
        age_result = await self.set_age(token)
        if not age_result.success:
            return NSFWResult(
                success=False,
                http_status=age_result.http_status,
                error=f"年龄设置失败: {age_result.error}",
                age_set=False,
                unhinged_enabled=False,
            )

        # 2. 开启 Unhinged
        unhinged_result = await self._enable_unhinged(token)
        if not unhinged_result.success:
            return NSFWResult(
                success=False,
                http_status=unhinged_result.http_status,
                grpc_status=unhinged_result.grpc_status,
                grpc_message=unhinged_result.grpc_message,
                error=f"Unhinged 开启失败: {unhinged_result.error or unhinged_result.grpc_message}",
                age_set=True,
                unhinged_enabled=False,
            )

        # 两者都成功
        return NSFWResult(
            success=True,
            http_status=200,
            grpc_status=unhinged_result.grpc_status,
            age_set=True,
            unhinged_enabled=True,
        )


__all__ = ["NSFWService", "NSFWResult"]
