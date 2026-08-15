import logging
import sys

from app.core.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # httpx logs every outbound request at INFO; the report pipeline makes dozens.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
