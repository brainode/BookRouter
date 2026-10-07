# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import builtins
import logging
import os
import threading
from datetime import datetime

from config import LOG_FILE, LOG_LEVEL, LOG_TO_CONSOLE

_PRINT_LOCK = threading.Lock()
_ORIGINAL_PRINT = builtins.print


def _parse_level(raw: str) -> int:
    if not raw:
        return logging.DEBUG
    level_name = str(raw).upper().strip()
    return getattr(logging, level_name, logging.DEBUG)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("bookrouter")
    if logger.handlers:
        return logger

    logger.setLevel(_parse_level(LOG_LEVEL))
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir = os.path.dirname(os.path.abspath(LOG_FILE))
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(_parse_level(LOG_LEVEL))
    logger.addHandler(file_handler)

    if LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(_parse_level(LOG_LEVEL))
        logger.addHandler(console_handler)

    logger.propagate = False
    logger.info("=" * 80)
    logger.info("Run started at %s", datetime.now().isoformat(timespec="seconds"))
    logger.info("Logging to file: %s", os.path.abspath(LOG_FILE))
    return logger


def install_print_logging(logger: logging.Logger):
    if getattr(builtins, "_bookrouter_print_patched", False):
        return

    def logged_print(*args, **kwargs):
        message = " ".join(str(arg) for arg in args)
        end = kwargs.get("end", "\n")
        if end and end != "\n":
            message = f"{message}{end}"
        if not message.strip():
            return
        with _PRINT_LOCK:
            logger.debug(message.rstrip("\n"))

    builtins.print = logged_print
    builtins._bookrouter_print_patched = True


def restore_print():
    builtins.print = _ORIGINAL_PRINT
    if hasattr(builtins, "_bookrouter_print_patched"):
        delattr(builtins, "_bookrouter_print_patched")
