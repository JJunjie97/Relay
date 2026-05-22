import logging
import sys
import os

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

_LOG_STR = os.environ.get("USE_LOG_LEVEL", "INFO").upper()
_GLOBAL_LEVEL = getattr(logging, _LOG_STR, logging.INFO)

class ColoredFormatter(logging.Formatter):
    FORMATS = {
        logging.DEBUG:    f"{CYAN}[%(name)s] DEBUG: %(message)s{RESET}",
        logging.INFO:     f"{GREEN}[%(name)s] INFO : %(message)s{RESET}",
        logging.WARNING:  f"{YELLOW}[%(name)s] WARN : %(message)s{RESET}",
        logging.ERROR:    f"{RED}[%(name)s] ERROR: %(message)s{RESET}",
        logging.CRITICAL: f"{MAGENTA}[%(name)s] FATAL: %(message)s{RESET}"
    }
    def __init__(self):
        super().__init__()
        self._formatters = {
            level: logging.Formatter(fmt) 
            for level, fmt in self.FORMATS.items()
        }
        self._fallback = self._formatters[logging.DEBUG]
    def format(self, record):
        return self._formatters.get(record.levelno, self._fallback).format(record)

_SHARED_HANDLER = logging.StreamHandler(sys.stdout)
_SHARED_HANDLER.setFormatter(ColoredFormatter())

def GetLogger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(_GLOBAL_LEVEL)
        logger.addHandler(_SHARED_HANDLER)
        logger.propagate = False
    return logger
