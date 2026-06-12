"""
结构化 JSON 日志 - 极简格式
"""

import sys
import os
import json
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

# Provide logging.Logger compatibility for legacy calls
if not hasattr(logger, "isEnabledFor"):
    logger.isEnabledFor = lambda _level: True

# 日志目录
DEFAULT_LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR = Path(os.getenv("LOG_DIR", str(DEFAULT_LOG_DIR)))
_LOG_DIR_READY = False
_LOG_FILE_PATTERN = re.compile(r"^app_(\d{4}-\d{2}-\d{2})\.log$")
_LAST_PRUNE_DAY: str = ""


def _prepare_log_dir() -> bool:
    """确保日志目录可用"""
    global LOG_DIR, _LOG_DIR_READY
    if _LOG_DIR_READY:
        return True
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _LOG_DIR_READY = True
        return True
    except Exception:
        _LOG_DIR_READY = False
        return False


def _prune_old_logs(max_files: int) -> None:
    """保留最近 max_files 个按天滚动的日志文件。"""
    if max_files <= 0:
        return
    try:
        entries = []
        for p in LOG_DIR.iterdir():
            m = _LOG_FILE_PATTERN.match(p.name)
            if m:
                entries.append((m.group(1), p))
        if len(entries) <= max_files:
            return
        entries.sort(key=lambda x: x[0])
        for _, path in entries[: len(entries) - max_files]:
            try:
                path.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _format_json(record) -> str:
    """格式化日志"""
    # ISO8601 时间
    time_str = record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    tz = record["time"].strftime("%z")
    if tz:
        time_str += tz[:3] + ":" + tz[3:]

    log_entry = {
        "time": time_str,
        "level": record["level"].name.lower(),
        "msg": record["message"],
        "caller": f"{record['file'].name}:{record['line']}",
    }

    # trace 上下文
    extra = record["extra"]
    if extra.get("traceID"):
        log_entry["traceID"] = extra["traceID"]
    if extra.get("spanID"):
        log_entry["spanID"] = extra["spanID"]

    # 其他 extra 字段
    for key, value in extra.items():
        if key not in ("traceID", "spanID") and not key.startswith("_"):
            log_entry[key] = value

    # 错误及以上级别添加堆栈跟踪
    if record["level"].no >= 40 and record["exception"]:
        log_entry["stacktrace"] = "".join(
            traceback.format_exception(
                record["exception"].type,
                record["exception"].value,
                record["exception"].traceback,
            )
        )

    return json.dumps(log_entry, ensure_ascii=False)

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _make_json_sink(output):
    """创建 JSON sink"""

    def sink(message):
        json_str = _format_json(message.record)
        print(json_str, file=output, flush=True)

    return sink


def _make_file_json_sink(max_files: int):
    """创建按天滚动 + 自动清理的文件 sink"""

    def sink(message):
        global _LAST_PRUNE_DAY
        try:
            record = message.record
            json_str = _format_json(record)
            day = record["time"].strftime("%Y-%m-%d")
            log_file = LOG_DIR / f"app_{day}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json_str + "\n")
            # Day boundary → prune (lazy, one prune per day per process)
            if max_files > 0 and day != _LAST_PRUNE_DAY:
                _LAST_PRUNE_DAY = day
                _prune_old_logs(max_files)
        except Exception:
            pass

    return sink


def setup_logging(
    level: str = "DEBUG",
    json_console: bool = True,
    file_logging: bool = True,
    file_level: str = None,
    max_files: int = 14,
):
    """设置日志配置

    Args:
        level: 控制台日志级别（默认 DEBUG）
        json_console: 控制台是否输出 JSON 格式
        file_logging: 是否开启文件日志（环境变量 LOG_FILE_ENABLED 可覆盖）
        file_level: 文件日志独立级别（None 则跟随 level）
        max_files: 保留的滚动日志文件数（按天，<=0 表示不清理）
    """
    logger.remove()
    file_logging = _env_flag("LOG_FILE_ENABLED", file_logging)

    # 环境变量覆盖
    env_file_level = os.getenv("LOG_FILE_LEVEL")
    if env_file_level:
        file_level = env_file_level
    env_max_files = os.getenv("LOG_MAX_FILES")
    if env_max_files:
        try:
            max_files = int(env_max_files)
        except ValueError:
            pass
    effective_file_level = file_level or level

    # 控制台输出
    if json_console:
        logger.add(
            _make_json_sink(sys.stdout),
            level=level,
            format="{message}",
            colorize=False,
        )
    else:
        logger.add(
            sys.stdout,
            level=level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{file.name}:{line}</cyan> - <level>{message}</level>",
            colorize=True,
        )

    # 文件输出
    if file_logging:
        if _prepare_log_dir():
            file_sink = _make_file_json_sink(max_files)
            # Process startup: prune immediately
            if max_files > 0:
                _prune_old_logs(max_files)
            try:
                logger.add(
                    file_sink,
                    level=effective_file_level,
                    format="{message}",
                    enqueue=True,
                )
            except Exception:
                logger.add(
                    file_sink,
                    level=effective_file_level,
                    format="{message}",
                    enqueue=False,
                )
                logger.warning("File logging queue disabled: falling back to direct writes.")
        else:
            logger.warning("File logging disabled: no writable log directory.")

    return logger


def get_logger(trace_id: str = "", span_id: str = ""):
    """获取绑定了 trace 上下文的 logger"""
    bound = {}
    if trace_id:
        bound["traceID"] = trace_id
    if span_id:
        bound["spanID"] = span_id
    return logger.bind(**bound) if bound else logger


__all__ = ["logger", "setup_logging", "get_logger", "LOG_DIR"]
