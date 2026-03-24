"""Compatibility wrapper for the upstream-aligned admin logs module."""

from app.api.v1.admin_api.logs import router

__all__ = ["router"]
