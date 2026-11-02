"""Error safety helpers shared by routes and background paths.

Unknown exceptions must never reach clients as raw text (Tier0 review
2026-10-30 H2): the original type/message/traceback goes to the server
log and callers persist/return a fixed generic message.
"""

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from atlas.graph.loader import runtime_error_meta

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


def safe_run_error(exc: Exception) -> str:
    """docs/101 D58：运行失败 message 收口（SSE error 帧与持久化 error 共用）。

    未知异常（RUNTIME_UNEXPECTED）→ 安全文案，内部串（类型名/repr）只进服务端日志；
    已知业务异常 → 保留中文业务文案（去掉类型名前缀），code/params 已另行结构化。
    """
    if runtime_error_meta(exc)["errorCode"] == "RUNTIME_UNEXPECTED":
        return "运行时发生未预期错误"
    return str(exc)


def unexpected_stream_error() -> dict[str, Any]:
    """SSE worker 暴毙（未沉淀终帧）时生成器合成的安全 error 终帧（docs/125 §3.1）。

    固定 code/文案，不带任何内部异常串；细节由 worker 侧 logger.exception 落日志。
    """
    return {"message": "运行时发生未预期错误", "code": "RUNTIME_UNEXPECTED", "params": {}}


def token_ref(token: Any) -> str:
    """日志里指代一枚令牌的可读引用（docs/89 §15 A-8b）。

    `resume_token`／审批 `token` 都是能直接推进状态的凭证，全文进日志＝任何读得到日志的人
    都能代替持有人续跑或决策；而运维排障又确实需要一个能对上表行的标识，所以留 8 位前缀：
    够定位，不够使用。
    """
    text = str(token or "")
    return text[:8] + "…" if len(text) > 8 else (text or "<空令牌>")
