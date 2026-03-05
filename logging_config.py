"""Centralized logging configuration for SpillTheBeans."""

import logging
import sys
from pathlib import Path


def setup_logging(
    level: int = logging.INFO, log_file: str = "spillthebeans.log"
) -> None:
    """Configure logging for all modules.

    Sets up logging to both stdout and a file with timestamps.

    Args:
        level: Log level for stdout (default: INFO)
        log_file: Path to log file (default: spillthebeans.log)
    """
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    root_logger.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(stdout_handler)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.info(
        "Logging configured: stdout=%s, file=%s (DEBUG)",
        logging.getLevelName(level),
        log_file,
    )
