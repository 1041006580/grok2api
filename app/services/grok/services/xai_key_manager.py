from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Mapping, Optional


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
    status: XAIKeyStatus = XAIKeyStatus.ACTIVE


class XAIKeyManager:
    def __init__(self, keys: Iterable[XAIKeyInfo]):
        self._keys: List[XAIKeyInfo] = list(keys)

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
            raw_status = str(key_data.get("status", XAIKeyStatus.ACTIVE.value) or "").strip()
            try:
                status = XAIKeyStatus(raw_status or XAIKeyStatus.ACTIVE.value)
            except ValueError:
                status = XAIKeyStatus.ACTIVE
            parsed.append(
                XAIKeyInfo(
                    id=key_id,
                    key=key_value,
                    name=str(key_data.get("name", "") or "").strip() or None,
                    enabled=bool(key_data.get("enabled", False)),
                    status=status,
                )
            )
        return cls(parsed)

    def list_keys(self) -> List[XAIKeyInfo]:
        return list(self._keys)

    def acquire_key(self) -> Optional[XAIKeyInfo]:
        for key in self._keys:
            if key.enabled:
                return key
        return None
