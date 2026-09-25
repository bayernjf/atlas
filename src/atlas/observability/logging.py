"""docs/65 K-D：日志最小面——统一 formatter + request-id 注入。

- `configure_logging()`：全仓统一日志格式（UTC 时间戳、级别、logger 名、request_id
  字段、消息）；级别 env `ATLAS_LOG_LEVEL`（默认 INFO）；幂等（重复调用不叠 handler）。
- `request_id_var`：ContextVar；`install_request_id_middleware(app)` 生成 `X-Request-Id`
  响应头并让后续日志 record 带上 `request_id=` 字段。
  【落码偏差（收口注记）】响应头只在成功路径可加（异常 500 由 Starlette 外层生成，
  中间件拿不到响应）；异常路径改为：捕获时以带 request_id 的 error 日志记录后 re-raise，
  日志仍可关联。契约 §5"成功与异常路径都带"按此收窄为"成功带响应头、异常带日志"。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("atlas_request_id", default="")


class RequestIdFilter(logging.Filter):
    """把当前请求 id 注入日志 record 的 request_id 字段（无请求时为空白）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _UtcFormatter(logging.Formatter):
    """asctime 恒为 UTC（ISO 风格），不受本机时区影响。"""

    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        if datefmt:
            return super().formatTime(record, datefmt)
        # 与存储侧 ISO 口径一致：YYYY-MM-DDTHH:MM:SS.mmm+00:00
        return time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created)) + (
            f".{int(record.msecs):03d}+00:00"
        )


_configured = False


def configure_logging() -> None:
    """全仓统一日志初始化；幂等（第二次调用直接返回，不叠 handler）。"""
    global _configured
    if _configured:
        return
    level_name = os.environ.get("ATLAS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        _UtcFormatter("%(asctime)s %(levelname)s [%(name)s] %(request_id)s%(message)s")
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    _configured = True


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def install_request_id_middleware(app) -> None:
    """注册 http 中间件：生成短 uuid 请求 id → ContextVar → 响应头 X-Request-Id。

    成功路径带头；异常路径以带 request_id 的日志记录后 re-raise（见模块 docstring
    落码偏差）。contextvar 在 finally 复位（防请求间串扰）。
    """
    request_logger = logging.getLogger("atlas.request")

    @app.middleware("http")
    async def _request_id_middleware(request, call_next):
        rid = uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        except Exception:
            # 异常路径：日志仍带 request_id（此时 contextvar 有效）；响应头由
            # Starlette 外层生成、中间件拿不到，按模块 docstring 偏差处理。
            request_logger.error(
                "request %s %s failed", request.method, request.url.path, exc_info=True
            )
            raise
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

    return _request_id_middleware
