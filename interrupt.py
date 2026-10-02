# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 The ScanBookShelf Authors

import threading

stop_event = threading.Event()


class Interrupted(Exception):
    """Обработка остановлена по Ctrl+C/SIGTERM."""


def check_interrupted():
    if stop_event.is_set():
        raise Interrupted("processing_interrupted")
