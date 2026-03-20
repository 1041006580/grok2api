def test_new_and_old_api_modules_import():
    __import__("app.api.v1.admin")
    __import__("app.api.v1.function")
    __import__("app.api.v1.admin_api.config")
    __import__("app.api.v1.public_api.video")


def test_new_page_router_module_imports():
    module = __import__("app.api.pages.function", fromlist=["router"])
    assert hasattr(module, "router")
