"""
Grok Voice Mode Service
"""

from typing import Any, Dict

from app.core.config import get_config
from app.services.reverse.ws_livekit import LivekitTokenReverse
from app.services.reverse.utils.session import ResettableSession


class VoiceService:
    """Voice Mode Service (LiveKit)"""

    async def get_token(
        self,
        token: str,
        voice: str = "ara",
        personality: str = "assistant",
        speed: float = 1.0,
        custom_instruction: str = "",
    ) -> Dict[str, Any]:
        browser = get_config("proxy.browser")
        async with ResettableSession(impersonate=browser) as session:
            response = await LivekitTokenReverse.request(
                session,
                token=token,
                voice=voice,
                personality=personality,
                speed=speed,
                custom_instruction=custom_instruction,
            )
            return response.json()
