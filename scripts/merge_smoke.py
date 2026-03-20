from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import create_app


def _route_paths() -> set[str]:
    app = create_app()
    return {route.path for route in app.routes}


def _assert_paths(required: set[str], label: str) -> None:
    paths = _route_paths()
    missing = sorted(required - paths)
    if missing:
        raise SystemExit(f"{label} smoke failed, missing routes: {missing}")


def _assert_files(required: list[Path], label: str) -> None:
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"{label} smoke failed, missing files: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal smoke checks for the upstream merge plan.")
    parser.add_argument("--routes-only", action="store_true")
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--files", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--tokens", action="store_true")
    parser.add_argument("--admin", action="store_true")
    parser.add_argument("--static", action="store_true")
    args = parser.parse_args()

    if args.routes_only:
        _assert_paths(
            {
                "/v1/chat/completions",
                "/v1/images/generations",
                "/v1/responses",
                "/v1/models",
                "/v1/videos",
            },
            "routes-only",
        )

    if args.chat:
        _assert_paths({"/v1/chat/completions", "/v1/responses"}, "chat")

    if args.files:
        _assert_paths(
            {
                "/v1/files/image/{filename:path}",
                "/v1/files/video/{filename:path}",
            },
            "files",
        )

    if args.video:
        _assert_paths(
            {
                "/v1/videos",
                "/v1/public/video/start",
                "/v1/public/video/sse",
            },
            "video",
        )

    if args.tokens:
        _assert_paths(
            {
                "/v1/admin/token",
                "/v1/admin/config",
            },
            "tokens",
        )

    if args.admin:
        _assert_paths({"/admin", "/admin/login", "/admin/token"}, "admin")

    if args.static:
        _assert_files(
            [
                ROOT / "app" / "static" / "admin" / "pages" / "login.html",
                ROOT / "app" / "static" / "admin" / "pages" / "token.html",
                ROOT / "app" / "static" / "public" / "pages" / "video.html",
            ],
            "static",
        )


if __name__ == "__main__":
    main()
