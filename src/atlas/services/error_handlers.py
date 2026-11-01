"""FastAPI exception handlers for graph-domain errors (extracted from api/main.py).

Each handler maps a domain exception to a structured 500/422 response so the
frontend renders by machine code while the Chinese message stays a debug
fallback. Register them all with :func:`register_exception_handlers`; the HTTP
response shapes are byte-for-byte unchanged from the previous inline handlers.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from atlas.graph.conditions import ConditionEvalError
from atlas.graph.dsl import GraphValidationError
from atlas.graph.loader import (
    AiDecisionUnavailable,
    ConditionClassifierUnavailable,
    RunNodeFailed,
    SubgraphSuspendUnsupported,
    WaitNodeFailure,
)
from atlas.services.error_safety import internal_error_response


def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    # locations 为稀疏侧车（04 §6.5/06 §6.13）：有可定位条目时才下发，index 对齐 detail。
    # codes/params 与 detail 等长、下标对齐（docs/17 §2.4）：前端按 code 映射本地文案，
    # detail 为中文 message，仅作调试日志/默认兜底，不直接面向最终用户。
    content: dict[str, Any] = {
        "detail": exc.errors,
        "codes": exc.codes,
        "params": exc.params,
    }
    if exc.locations:
        content["locations"] = exc.locations
    return JSONResponse(status_code=422, content=content)


def wait_node_failure_handler(_request: Request, exc: WaitNodeFailure) -> JSONResponse:
    # 运行期 wait 确定性失败（docs/47 §3.4）：返回结构化 500 而非让异常逃逸到 ASGI
    # 顶层。docs/54 收口修复：原先异常 re-raise 会令 uvicorn 关闭该 keep-alive 连接，
    # 失败运行后紧邻的列表/详情 GET 复用连接时被 RST（Connection reset by peer）。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


def condition_eval_failure_handler(
    _request: Request, exc: ConditionEvalError
) -> JSONResponse:
    # 运行期 condition/loop/foreach 表达式求值失败（docs/60 G1）：与 WaitNodeFailure
    # 同形返回结构化 500，携带 COND_* 机器码与 params，前端可按当前语言渲染；中文
    # message 仍是兜底真相。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "params": exc.params,
            }
        },
    )


def ai_decision_unavailable_handler(
    _request: Request, exc: AiDecisionUnavailable
) -> JSONResponse:
    # prod 档 ai_decision 拿不到 LLM 决策器（docs/73 W1-1.1）：结构化 500 携带
    # LLM_DECISION_UNAVAILABLE 与 nodeId，与 WaitNodeFailure 同形，前端按语言渲染文案。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


def condition_classifier_unavailable_handler(
    _request: Request, exc: ConditionClassifierUnavailable
) -> JSONResponse:
    # prod 档 condition(llm) 拿不到 LLM 分类器（docs/73 W5-5.4）：同形结构化 500 携带
    # LLM_CLASSIFIER_UNAVAILABLE 与 nodeId，前端按 LLM_ 前缀渲染双语文案。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


def subgraph_suspend_unsupported_handler(
    _request: Request, exc: SubgraphSuspendUnsupported
) -> JSONResponse:
    # 打包 ZK 让 SubgraphSuspendUnsupported 穿透子图 fail-safe、显式拒跑（被吞掉会变
    # 「子图 failed、父 run completed」的假完成）。SSE 通道一直有 `event: error` 帧承载它，
    # 但**同步 /run 此前没有异常处理器**：异常逃出端点 ⇒ Starlette 给一个没有 code 的裸 500，
    # 运营看到的是一句 Internal Server Error，而"把审批/wait 节点移到图顶层"这个可执行
    # 下一步只存在于日志里。补齐成与 LLM_DECISION_UNAVAILABLE 同形的结构化 500。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


def run_node_failed_handler(_request: Request, exc: RunNodeFailed) -> JSONResponse:
    # 打包 AJ（docs/121 §2 D-3）：on_error=stop 的节点失败穿透到同步 /run 时，
    # 与 WaitNodeFailure 等同形结构化 500，携带 NODE_EXECUTION_FAILED 与 nodeId。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Tier0 评审 2026-10-30 H2：未捕获异常统一 500 固定文案，str(exc)/类型名/
    # traceback/内部路径只进服务端日志；附 X-Request-Id 便于按日志定位。
    # 业务异常由上方各专用 handler 先行匹配，语义码与中文 message 不受影响。
    return internal_error_response(request, exc)


def register_exception_handlers(app: FastAPI) -> None:
    """Register every graph-domain handler on a FastAPI app."""
    app.exception_handler(GraphValidationError)(graph_validation_handler)
    app.exception_handler(WaitNodeFailure)(wait_node_failure_handler)
    app.exception_handler(ConditionEvalError)(condition_eval_failure_handler)
    app.exception_handler(AiDecisionUnavailable)(ai_decision_unavailable_handler)
    app.exception_handler(ConditionClassifierUnavailable)(condition_classifier_unavailable_handler)
    app.exception_handler(SubgraphSuspendUnsupported)(subgraph_suspend_unsupported_handler)
    app.exception_handler(RunNodeFailed)(run_node_failed_handler)
    app.exception_handler(Exception)(unhandled_exception_handler)
