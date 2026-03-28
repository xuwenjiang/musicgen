import logging
from contextvars import ContextVar, Token
from typing import Any


REQUEST_ID_CTX: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID_CTX.get()
        record.component = record.name.split(".")[-1].upper()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("musicgen")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-5s | %(component)-5s | req=%(request_id)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(RequestIdFilter())

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"musicgen.{name}")


def set_request_id(request_id: str) -> Token:
    return REQUEST_ID_CTX.set(request_id)


def reset_request_id(token: Token) -> None:
    REQUEST_ID_CTX.reset(token)


def sanitize_value(value: Any, max_length: int = 240) -> str:
    if value is None:
        return "-"

    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > max_length:
        return f"{text[:max_length - 3]}..."
    return text


def log_params(logger: logging.Logger, **params: Any) -> None:
    logger.info("[PARAMS]")
    for key, value in params.items():
        logger.info("  %s=%s", key, sanitize_value(value))