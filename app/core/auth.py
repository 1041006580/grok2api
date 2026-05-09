"""
API 认证模块
"""

import hashlib
from typing import Optional, Iterable
from fastapi import HTTPException, status, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import get_config

DEFAULT_API_KEY = ""
DEFAULT_APP_KEY = "grok2api"
DEFAULT_PUBLIC_KEY = ""
DEFAULT_PUBLIC_ENABLED = False
DEFAULT_FUNCTION_KEY = ""
DEFAULT_FUNCTION_ENABLED = False

# 定义 Bearer Scheme
security = HTTPBearer(
    auto_error=False,
    scheme_name="API Key",
    description="Enter your API Key in the format: Bearer <key>",
)


def get_admin_api_key() -> str:
    """
    获取后台 API Key。

    为空时表示不启用后台接口认证。
    """
    api_key = get_config("app.api_key", DEFAULT_API_KEY)
    return api_key or ""


def _normalize_api_keys(value: Optional[object]) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(value, Iterable):
        keys: list[str] = []
        for item in value:
            if not item:
                continue
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    keys.append(stripped)
        return keys
    return []

def get_app_key() -> str:
    """
    获取 App Key（后台管理密码）。
    """
    app_key = get_config("app.app_key", DEFAULT_APP_KEY)
    return app_key or ""

def get_public_api_key() -> str:
    """
    获取 Public API Key。

    为空时表示不启用 public 接口认证。
    """
    public_key = get_config("app.public_key", DEFAULT_PUBLIC_KEY)
    return public_key or ""


def get_function_api_key() -> str:
    """
    获取 Function API Key。

    若未配置 function_key，则兼容回退到 public_key。
    """
    function_key = get_config("app.function_key", None)
    if function_key is None:
        function_key = get_config("app.public_key", DEFAULT_PUBLIC_KEY)
    return function_key or ""


def is_public_enabled() -> bool:
    """
    是否开启 public 功能入口。
    """
    return bool(get_config("app.public_enabled", DEFAULT_PUBLIC_ENABLED))


def is_function_enabled() -> bool:
    """
    是否开启 function 功能入口。

    若未配置 function_enabled，则兼容回退到 public_enabled。
    """
    function_enabled = get_config("app.function_enabled", None)
    if function_enabled is None:
        function_enabled = get_config("app.public_enabled", DEFAULT_PUBLIC_ENABLED)
    return bool(function_enabled)


def _hash_public_key(key: str) -> str:
    """计算 public_key 的 SHA-256 哈希，与前端 hashPublicKey 保持一致。"""
    return hashlib.sha256(f"grok2api-public:{key}".encode()).hexdigest()


def _match_public_key(credentials: str, public_key: str) -> bool:
    """检查凭证是否匹配 public_key（支持原始值和 public-<sha256> 哈希格式）。"""
    if not public_key:
        return False
    normalized = public_key.strip()
    if not normalized:
        return False
    if credentials == normalized:
        return True
    if credentials.startswith("public-"):
        expected_hash = _hash_public_key(normalized)
        if credentials == f"public-{expected_hash}":
            return True
    return False


async def verify_api_key(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> Optional[str]:
    """
    验证 Bearer Token 或 X-API-Key header。

    支持 Authorization: Bearer <key> (OpenAI 风格)
    和 X-API-Key: <key> (Anthropic SDK 风格)。
    如果 config.toml 中未配置 api_key，则不启用认证。
    """
    api_key = get_admin_api_key()
    api_keys = _normalize_api_keys(api_key)
    if not api_keys:
        return None

    token = (auth.credentials if auth else None) or x_api_key
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token in api_keys:
        return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_app_key(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[str]:
    """
    验证后台登录密钥（app_key）。

    app_key 必须配置，否则拒绝登录。
    """
    app_key = get_app_key()

    if not app_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App key is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth.credentials != app_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return auth.credentials


async def verify_public_key(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[str]:
    """
    验证 Public Key（public 接口使用）。

    默认不公开，需配置 public_key 才能访问；若开启 public_enabled 且未配置 public_key，则放开访问。
    """
    public_key = get_public_api_key()
    public_enabled = is_public_enabled()

    if not public_key:
        if public_enabled:
            return None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Public access is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _match_public_key(auth.credentials, public_key):
        return auth.credentials

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_function_key(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[str]:
    """
    验证 Function Key（function 接口使用）。

    默认复用当前 public 鉴权语义；若单独配置了 function_*，则优先使用。
    """
    function_key = get_function_api_key()
    function_enabled = is_function_enabled()

    if not function_key:
        if function_enabled:
            return None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Function access is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _match_public_key(auth.credentials, function_key):
        return auth.credentials

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
