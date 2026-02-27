"""
Reverse proxy URL resolution utilities.

Replaces grok.com / assets.grok.com domains with user-configured
reverse proxy addresses when ``proxy.reverse_base_url`` or
``proxy.reverse_asset_url`` are set.
"""

from urllib.parse import urlparse

from app.core.config import get_config


def resolve_api_url(original_url: str) -> str:
    """Replace ``grok.com`` domain with the configured reverse proxy address.

    Handles both ``https://`` and ``wss://`` schemes.
    If ``proxy.reverse_base_url`` is empty, returns *original_url* unchanged.
    """
    reverse_base: str = get_config("proxy.reverse_base_url") or ""
    reverse_base = reverse_base.strip().rstrip("/")
    if not reverse_base:
        return original_url

    parsed = urlparse(reverse_base)
    proxy_host = parsed.netloc or parsed.path  # handle bare "host:port" input
    proxy_scheme = parsed.scheme or "https"

    if original_url.startswith("wss://grok.com"):
        wss_scheme = "wss" if proxy_scheme == "https" else "ws"
        return original_url.replace("wss://grok.com", f"{wss_scheme}://{proxy_host}", 1)

    if original_url.startswith("ws://grok.com"):
        ws_scheme = "ws" if proxy_scheme == "http" else "wss"
        return original_url.replace("ws://grok.com", f"{ws_scheme}://{proxy_host}", 1)

    return original_url.replace("https://grok.com", f"{proxy_scheme}://{proxy_host}", 1)


def resolve_asset_url(original_url: str) -> str:
    """Replace ``assets.grok.com`` domain with the configured reverse asset address.

    If ``proxy.reverse_asset_url`` is empty, returns *original_url* unchanged.
    """
    reverse_asset: str = get_config("proxy.reverse_asset_url") or ""
    reverse_asset = reverse_asset.strip().rstrip("/")
    if not reverse_asset:
        return original_url

    parsed = urlparse(reverse_asset)
    proxy_host = parsed.netloc or parsed.path
    proxy_scheme = parsed.scheme or "https"

    return original_url.replace(
        "https://assets.grok.com", f"{proxy_scheme}://{proxy_host}", 1
    )
