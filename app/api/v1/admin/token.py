"""Compatibility wrapper for the upstream-aligned admin token module."""

from app.api.v1.admin_api.token import router

__all__ = ["router"]
