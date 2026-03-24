import copy
import os
from pathlib import Path

import pytest

from app.core.config import config


ROOT = Path(__file__).resolve().parents[2]

os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("LOG_FILE_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SERVER_WORKERS", "1")


@pytest.fixture(autouse=True)
def restore_global_config_state():
    state = {
        "_config": copy.deepcopy(config._config),
        "_defaults": copy.deepcopy(config._defaults),
        "_code_defaults": copy.deepcopy(config._code_defaults),
        "_defaults_loaded": config._defaults_loaded,
    }
    try:
        yield
    finally:
        config._config = state["_config"]
        config._defaults = state["_defaults"]
        config._code_defaults = state["_code_defaults"]
        config._defaults_loaded = state["_defaults_loaded"]
