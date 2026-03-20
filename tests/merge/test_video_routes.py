from main import create_app


def test_video_and_admin_routes_still_exist():
    paths = {route.path for route in create_app().routes}

    assert "/v1/videos" in paths
    assert "/admin" in paths


def test_public_and_function_video_routes_coexist():
    paths = {route.path for route in create_app().routes}

    assert "/v1/public/video/start" in paths
    assert "/v1/function/video/start" in paths
