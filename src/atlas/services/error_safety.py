"""Error safety helpers shared by routes and background paths.

Unknown exceptions must never reach clients as raw text (Tier0 review
2026-10-30 H2): the original type/message/traceback goes to the server
log and callers persist/return a fixed generic message.
"""

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "internal error"
INTERNAL_ERROR_CODE = "INTERNAL_ERROR"


def safe_internal_detail(
    exc: BaseException,
    *,
    log_message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Log the original exception and return a fixed, client-safe payload."""
    logger.error(log_message, exc_info=exc, extra=extra or {})
    return {"code": INTERNAL_ERROR_CODE, "message": INTERNAL_ERROR_MESSAGE}


def internal_error_response(request: Request, exc: BaseException) -> JSONResponse:
    """Catch-all 500 response: fixed body plus the request correlation id."""
    detail = safe_internal_detail(
        exc,
        log_message="unhandled request error",
        extra={"path": request.url.path},
    )
    rid = getattr(request.state, "request_id", None)
    if rid:
        detail["requestId"] = rid
    return JSONResponse(status_code=500, content={"detail": detail})


def safe_persisted_error(exc: BaseException, *, context: str) -> str:
    """Generic message for values that get *persisted* (e.g. shadow runs).

    Diagnostics stay in logs; a stored record may surface via the API later,
    so it must never embed ``type(exc).__name__`` or ``str(exc)``.
    """
    logger.exception("%s failed with an unexpected error", context, exc_info=exc)
    return "内部执行异常，详情见服务端日志"
