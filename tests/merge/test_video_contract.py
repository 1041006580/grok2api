import asyncio

import pytest


def test_video_extend_service_requires_reference_id():
    from app.core.exceptions import ValidationException
    from app.services.grok.services.video_extend import VideoExtendService

    with pytest.raises(ValidationException) as exc_info:
        asyncio.run(
            VideoExtendService.extend(
                prompt="extend this clip",
                reference_id="",
                start_time=0,
            )
        )

    assert exc_info.value.code == "invalid_request_error"


def test_function_video_start_accepts_30_second_payload():
    from app.api.v1.function.video import VideoStartRequest, function_video_start

    result = asyncio.run(
        function_video_start(
            VideoStartRequest(
                prompt="make a cinematic clip",
                aspect_ratio="3:2",
                video_length=30,
                resolution_name="480p",
                preset="normal",
            )
        )
    )

    assert "task_id" in result
    assert result["aspect_ratio"] == "3:2"
