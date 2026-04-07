from main import create_app


def test_route_surface_contains_current_core_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/v1/chat/completions" in paths
    assert "/v1/images/generations" in paths
    assert "/v1/images/edits" in paths
    assert "/v1/responses" in paths
    assert "/v1/models" in paths
    assert "/v1/models/{model_id}" in paths
    assert "/v1/language-models" in paths
    assert "/v1/language-models/{model_id}" in paths
    assert "/v1/image-generation-models" in paths
    assert "/v1/image-generation-models/{model_id}" in paths
    assert "/v1/videos" in paths
    assert "/v1/videos/generations" in paths
    assert "/v1/videos/{request_id}" in paths
    assert "/v1/files/image/{filename:path}" in paths
    assert "/v1/files/video/{filename:path}" in paths


def test_route_surface_contains_current_ui_and_public_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/admin" in paths
    assert "/admin/login" in paths
    assert "/v1/admin/config" in paths
    assert "/v1/public/video/start" in paths
    assert "/v1/public/video/sse" in paths


def test_route_surface_contains_upstream_alias_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/v1/function/video/start" in paths
    assert "/v1/function/video/sse" in paths
    assert "/health" in paths
    assert "/favicon.ico" in paths


def test_route_surface_contains_xai_keys_admin_endpoints():
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/admin/xai-keys" in paths
    assert "/v1/admin/xai-keys" in paths
    assert "/v1/admin/xai-keys/{key_id}" in paths
