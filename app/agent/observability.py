"""Small structured logging helper for agent runtime events."""

import json
import logging
import os
from datetime import datetime

from .config import AGENT_LOG_FILE


def setup_logger(name="labsafe_agent", log_file=AGENT_LOG_FILE):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass
    return logger


def log_event(logger, event, **fields):
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
    }
    payload.update(fields)
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))
