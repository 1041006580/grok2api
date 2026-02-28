"""
Reverse proxy URL resolution utilities.

Replaces grok.com / assets.grok.com domains with user-configured
reverse proxy addresses when ``proxy.reverse_base_url`` or
``proxy.reverse_asset_url`` are set.
"""

from urllib.parse import urlparse

from app.core.config import get_config


def _parse_reverse_url(url: str) -> tuple[str, str, str]:
    """Parse a reverse proxy URL into (scheme, host, path).

    Supports both full URLs (https://host/path) and bare host:port inputs.
    """
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path
    scheme = parsed.scheme or "https"
    path = parsed.path.rstrip("/") if parsed.netloc else ""
    return scheme, host, path


def resolve_api_url(original_url: str) -> str:
    """Replace ``grok.com`` domain with the configured reverse proxy address.

    Handles both ``https://`` and ``wss://`` schemes.
    Supports path prefixes (e.g. https://proxy.example.com/api).
    If ``proxy.reverse_base_url`` is empty, returns *original_url* unchanged.
    """
    reverse_base: str = get_config("proxy.reverse_base_url") or ""
    reverse_base = reverse_base.strip().rstrip("/")
    if not reverse_base:
        return original_url

    scheme, host, path = _parse_reverse_url(reverse_base)

    if original_url.startswith("wss://grok.com"):
        wss_scheme = "wss" if scheme == "https" else "ws"
        return original_url.replace("wss://grok.com", f"{wss_scheme}://{host}{path}", 1)

    if original_url.startswith("ws://grok.com"):
        ws_scheme = "ws" if scheme == "http" else "wss"
        return original_url.replace("ws://grok.com", f"{ws_scheme}://{host}{path}", 1)

    return original_url.replace("https://grok.com", f"{scheme}://{host}{path}", 1)


def resolve_asset_url(original_url: str) -> str:
    """Replace ``assets.grok.com`` domain with the configured reverse asset address.

    Supports path prefixes (e.g. https://proxy.example.com/assets).
    If ``proxy.reverse_asset_url`` is empty, returns *original_url* unchanged.
    """
    reverse_asset: str = get_config("proxy.reverse_asset_url") or ""
    reverse_asset = reverse_asset.strip().rstrip("/")
    if not reverse_asset:
        return original_url

    scheme, host, path = _parse_reverse_url(reverse_asset)

    return original_url.replace(
        "https://assets.grok.com", f"{scheme}://{host}{path}", 1
    )
