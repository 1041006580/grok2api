from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Mapping, Optional

from app.core.config import get_config


class XAIKeyStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    INVALID = "invalid"


@dataclass(frozen=True)
class XAIKeyInfo:
    id: str
    key: str
    name: Optional[str] = None
    enabled: bool = False
    status: Optional[str] = XAIKeyStatus.ACTIVE.value
    last_error: Optional[str] = None
    blocked_until: Optional[object] = None
    last_used_at: Optional[object] = None


class XAIKeyManager:
    def __init__(self, keys: Iterable[XAIKeyInfo]):
        self._keys: List[XAIKeyInfo] = list(keys)

    @staticmethod
    def _parse_enabled(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        return False

    @classmethod
    def from_config(cls, cfg: Mapping[str, object]) -> "XAIKeyManager":
        raw_keys = []
        xai_section = cfg.get("xai", {})
        if isinstance(xai_section, Mapping):
            raw_keys = xai_section.get("keys", [])

        parsed: List[XAIKeyInfo] = []
        for key_data in raw_keys or []:
            if not isinstance(key_data, Mapping):
                continue
            key_id = str(key_data.get("id", "") or "").strip()
            key_value = str(key_data.get("key", "") or "").strip()
            if not key_id or not key_value:
                continue
            raw_status = str(key_data.get("status", XAIKeyStatus.ACTIVE.value) or "").strip() or XAIKeyStatus.ACTIVE.value
            parsed.append(
                XAIKeyInfo(
                    id=key_id,
                    key=key_value,
                    name=str(key_data.get("name", "") or "").strip() or None,
                    enabled=cls._parse_enabled(key_data.get("enabled", False)),
                    status=raw_status,
                    last_error=str(key_data.get("last_error", "") or "").strip() or None,
                    blocked_until=key_data.get("blocked_until"),
                    last_used_at=key_data.get("last_used_at"),
                )
            )
        return cls(parsed)

    def list_keys(self) -> List[XAIKeyInfo]:
        return list(self._keys)

    def acquire_key(self) -> Optional[XAIKeyInfo]:
        for key in self._keys:
            if key.enabled and (key.status is None or key.status == XAIKeyStatus.ACTIVE.value):
                return key
        return None


def load_runtime_manager() -> XAIKeyManager:
    return XAIKeyManager.from_config({"xai": get_config("xai", {}) or {}})
