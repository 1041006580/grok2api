"""
Reverse proxy URL resolution utilities.

Replaces grok.com / assets.grok.com / accounts.x.ai / livekit.grok.com
domains with user-configured reverse proxy addresses.

All domains derive from ``proxy.reverse_base_url`` with path prefixes:
  - grok.com          → {base}/
  - assets.grok.com   → {base}/assets/  (or ``proxy.reverse_asset_url``)
  - accounts.x.ai     → {base}/accounts/
  - livekit.grok.com  → {base}/livekit/
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


def _get_base() -> tuple[str, str, str] | None:
    """Load and parse ``proxy.reverse_base_url``. Returns None if empty."""
    reverse_base: str = get_config("proxy.reverse_base_url") or ""
    reverse_base = reverse_base.strip().rstrip("/")
    if not reverse_base:
        return None
    return _parse_reverse_url(reverse_base)


def resolve_api_url(original_url: str) -> str:
    """Replace ``grok.com`` domain with the configured reverse proxy address.

    Handles both ``https://`` and ``wss://`` schemes.
    Supports path prefixes (e.g. https://proxy.example.com/api).
    If ``proxy.reverse_base_url`` is empty, returns *original_url* unchanged.
    """
    base = _get_base()
    if not base:
        return original_url

    scheme, host, path = base

    if original_url.startswith("wss://grok.com"):
        wss_scheme = "wss" if scheme == "https" else "ws"
        return original_url.replace("wss://grok.com", f"{wss_scheme}://{host}{path}", 1)

    if original_url.startswith("ws://grok.com"):
        ws_scheme = "ws" if scheme == "http" else "wss"
        return original_url.replace("ws://grok.com", f"{ws_scheme}://{host}{path}", 1)

    return original_url.replace("https://grok.com", f"{scheme}://{host}{path}", 1)


def resolve_asset_url(original_url: str) -> str:
    """Replace ``assets.grok.com`` domain with the configured reverse asset address.

    Uses ``proxy.reverse_asset_url`` if set, otherwise falls back to
    ``proxy.reverse_base_url`` + ``/assets`` prefix.
    """
    # Try dedicated asset URL first
    reverse_asset: str = get_config("proxy.reverse_asset_url") or ""
    reverse_asset = reverse_asset.strip().rstrip("/")
    if reverse_asset:
        scheme, host, path = _parse_reverse_url(reverse_asset)
        return original_url.replace(
            "https://assets.grok.com", f"{scheme}://{host}{path}", 1
        )

    # Fall back to base URL + /assets prefix
    base = _get_base()
    if not base:
        return original_url

    scheme, host, path = base
    return original_url.replace(
        "https://assets.grok.com", f"{scheme}://{host}{path}/assets", 1
    )


def resolve_accounts_url(original_url: str) -> str:
    """Replace ``accounts.x.ai`` domain with reverse proxy + ``/accounts`` prefix.

    If ``proxy.reverse_base_url`` is empty, returns *original_url* unchanged.
    """
    base = _get_base()
    if not base:
        return original_url

    scheme, host, path = base
    return original_url.replace(
        "https://accounts.x.ai", f"{scheme}://{host}{path}/accounts", 1
    )


def resolve_livekit_url(original_url: str) -> str:
    """Replace ``livekit.grok.com`` domain with reverse proxy + ``/livekit`` prefix.

    Handles ``wss://`` scheme.
    If ``proxy.reverse_base_url`` is empty, returns *original_url* unchanged.
    """
    base = _get_base()
    if not base:
        return original_url

    scheme, host, path = base

    if original_url.startswith("wss://livekit.grok.com"):
        wss_scheme = "wss" if scheme == "https" else "ws"
        return original_url.replace(
            "wss://livekit.grok.com", f"{wss_scheme}://{host}{path}/livekit", 1
        )

    return original_url.replace(
        "https://livekit.grok.com", f"{scheme}://{host}{path}/livekit", 1
    )
