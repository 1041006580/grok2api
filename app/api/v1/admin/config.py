"""Compatibility wrapper for the upstream-aligned admin config module."""

from app.api.v1.admin_api.config import _sanitize_proxy_config_payload, router

__all__ = ["router", "_sanitize_proxy_config_payload"]
