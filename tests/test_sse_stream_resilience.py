"""SSE 健壮性回归（docs/125 §7 U1374–U1378，审查发现 #1/#2/#4）。

- U1374：run worker except 分支内 record_run 二次抛错时，仍只发一个折叠为
  RUNTIME_UNEXPECTED 的安全 error 终帧，cancellation broker 必注销；
- U1375：worker 存活期间心跳注释帧按间隔下发；worker 裸死（BaseException，无终帧）
  时生成器合成 error 终帧并正常关闭，不永久挂死；
- U1376：超过 anyio 默认工作线程数（40）的长生存 SSE 流并发期间，同步
  GET /api/health 仍持续快速 200（async 生成器等待不占服务器工作线程）；
- U1378：客户端真断连只关桥接不取消 run_graph，后台运行照常跑完落 completed；
- U1377（有界桥接丢帧/计数/终帧必达恰好一次）由 tests/test_sse_bridge.py 承担。

通道说明：进程内 TestClient/httpx ASGITransport 都会把响应全量缓冲到结束才返回、
且每次请求各自独立事件循环，既无法中途断连，也证明不了「共享 anyio 40 线程令牌
不饿死」。因此 U1376/U1378 在回环口真起 uvicorn、用 httpx 真 TCP 流式访问
（同 tests/test_api_demo.py 的 live server 先例）；U1374/U1375 只断言终帧/心跳
的全量产出内容，进程内 TestClient 足够。

模块自建 TestClient，鉴权一律显式带 DEFAULT_AUTH_HEADER（conftest autouse 夹具
只向指定既有模块注入 client 会话）。
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.monitoring.records import MonitoringStore

from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)
TIMEOUT = 10
STREAM_URL = "/api/graphs/{gid}/run/stream"


@pytest.fixture(autouse=True)
def _reset_demo():
    client.post("/api/demo/reset", headers=DEFAULT_AUTH_HEADER)
    yield


# --------------------------------------------------------------------------- #
# 图夹具 / 进程内助手
# --------------------------------------------------------------------------- #
def _wait_fail_graph(timeout_seconds: int = 1) -> dict:
    """事件等待超时即失败：最快诱导 run worker 走 except Exception 分支。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "event", "eventKey": "sse_resilience_never_fires",
                        "timeoutSeconds": timeout_seconds, "onTimeout": "fail"}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def _long_wait_graph(duration_seconds: int = 120) -> dict:
    """长 duration 等待：打开 SSE 后 worker 长时间阻塞在 wait 节点。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "duration", "durationSeconds": duration_seconds}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def _create_graph(payload: dict) -> str:
    resp = client.post("/api/graphs", json=payload, headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _find_run_id(graph_id: str) -> str:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        items = client.get("/api/runs", headers=DEFAULT_AUTH_HEADER).json().get("items", [])
        for item in items:
            if item.get("graphId") == graph_id:
                return item["runId"]
        time.sleep(0.05)
    raise AssertionError("run id not found")


def _parse_event_frames(raw: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        if "event: " not in block or "data: " not in block:
            continue  # 心跳注释帧（: keepalive）天然不入事件帧列表
        lines = block.splitlines()
        event = next(ln[len("event: "):] for ln in lines if ln.startswith("event: "))
        data_line = next(ln[len("data: "):] for ln in lines if ln.startswith("data: "))
        frames.append((event, json.loads(data_line)))
    return frames


def _drain_stream(graph_id: str, body: dict | None = None):
    """后台线程完整消费一条 SSE；返回 (thread, holder)。"""
    holder: dict = {"chunks": [], "status": None, "errors": []}

    def work() -> None:
        try:
            with client.stream(
                "POST", STREAM_URL.format(gid=graph_id),
                json=body if body is not None else {"inputs": {}},
                headers=DEFAULT_AUTH_HEADER,
            ) as resp:
                holder["status"] = resp.status_code
                for chunk in resp.iter_text():
                    holder["chunks"].append(chunk)
        except Exception as exc:  # noqa: BLE001 - 断连路径预期可能抛流异常，收集即可
            holder["errors"].append(repr(exc))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, holder


# --------------------------------------------------------------------------- #
# 回环口真 uvicorn（进程内通道全缓冲、每请求独立事件循环，不能用于 U1376/U1378）
# --------------------------------------------------------------------------- #
_live_server_base: str | None = None


def _live_server_base_url() -> str:
    global _live_server_base
    if _live_server_base is not None:
        return _live_server_base

    import socket
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        try:
            if httpx.get(
                f"http://127.0.0.1:{port}/api/health", timeout=1
            ).status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.05)
    else:
        raise RuntimeError("live uvicorn server did not become ready")

    _live_server_base = f"http://127.0.0.1:{port}"
    return _live_server_base


# --------------------------------------------------------------------------- #
# U1374：收尾二次异常仍恰一个安全 error 终帧；unregister 必执行
# --------------------------------------------------------------------------- #
def test_run_stream_finalization_secondary_error_emits_single_safe_frame(monkeypatch):
    def _record_boom(self, **kwargs):
        raise RuntimeError("record_run boom secret marker")

    monkeypatch.setattr(MonitoringStore, "record_run", _record_boom)

    gid = _create_graph(_wait_fail_graph())
    thread, holder = _drain_stream(gid)
    thread.join(TIMEOUT)
    assert not thread.is_alive(), "stream thread hung"
    assert holder["status"] == 200
    assert holder["errors"] == []

    raw = "".join(holder["chunks"])
    frames = _parse_event_frames(raw)
    error_frames = [(name, data) for name, data in frames if name == "error"]
    assert len(error_frames) == 1
    assert [name for name, _ in frames].count("result") == 0
    detail = error_frames[0][1]["detail"]
    # 收尾已不可信：折叠为固定安全 error，不携带业务错误码与内部异常串。
    assert detail["code"] == "RUNTIME_UNEXPECTED"
    assert detail["message"] == "运行时发生未预期错误"
    assert "record_run boom secret marker" not in raw

    run_id = _find_run_id(gid)
    # finish(failed) 在 record_run 之前已成功：run 落 failed。
    run = client.get(f"/api/runs/{run_id}", headers=DEFAULT_AUTH_HEADER).json()
    assert run["status"] == "failed"
    # unregister 已执行：broker 无句柄 → cancel 不能再 200；run 仍在 → 409 而非 404。
    resp = client.post(f"/api/runs/{run_id}/cancel", headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 409, resp.text


# --------------------------------------------------------------------------- #
# U1375：心跳保活 + worker 裸死合成 error 终帧不挂死
# --------------------------------------------------------------------------- #
def test_run_stream_emits_heartbeats_while_worker_alive(monkeypatch):
    def _slow_boom(graph, **kwargs):
        time.sleep(0.5)
        raise RuntimeError("slow boom secret marker")

    monkeypatch.setattr("atlas.api.main.run_graph", _slow_boom)
    monkeypatch.setattr("atlas.api.main.SSE_HEARTBEAT_SECONDS", 0.08)

    gid = _create_graph(_long_wait_graph())
    thread, holder = _drain_stream(gid)
    thread.join(TIMEOUT)
    assert not thread.is_alive(), "stream thread hung"

    raw = "".join(holder["chunks"])
    assert raw.count(": keepalive\n\n") >= 3
    frames = _parse_event_frames(raw)
    assert [name for name, _ in frames] == ["error"]
    detail = frames[0][1]["detail"]
    assert detail["code"] == "RUNTIME_UNEXPECTED"
    assert "slow boom secret marker" not in raw


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_stream_worker_violent_death_synthesizes_error_without_hang(monkeypatch):
    def _die(graph, **kwargs):
        # BaseException 不被 except Exception 捕获：worker 裸死、无终帧 offer；
        # finally 仍执行 unregister。SystemExit 被 threading.excepthook 静默忽略。
        raise SystemExit(7)

    monkeypatch.setattr("atlas.api.main.run_graph", _die)
    monkeypatch.setattr("atlas.api.main.SSE_HEARTBEAT_SECONDS", 0.05)

    gid = _create_graph(_long_wait_graph())
    thread, holder = _drain_stream(gid)
    thread.join(TIMEOUT)  # 不挂死断言：线程必须在超时内正常退出
    assert not thread.is_alive(), "stream thread hung after worker violent death"
    assert holder["status"] == 200

    frames = _parse_event_frames("".join(holder["chunks"]))
    assert [name for name, _ in frames] == ["error"]
    detail = frames[0][1]["detail"]
    assert detail["code"] == "RUNTIME_UNEXPECTED"
    assert detail["message"] == "运行时发生未预期错误"


# --------------------------------------------------------------------------- #
# U1376：40+ 长生存 SSE 流并发不饿死同步端点的 anyio 工作线程（真 uvicorn）
# --------------------------------------------------------------------------- #
def test_health_stays_fast_under_many_concurrent_long_sse_streams():
    stream_count = 45  # > anyio 默认 40 工作线程，旧同步生成器模型下 health 会排队
    duration_seconds = 8  # duration wait 走不可中断 time.sleep：短到自然收尾即可，不依赖 cancel

    base = _live_server_base_url()
    with httpx.Client(base_url=base, timeout=30) as http:
        resp = http.post(
            "/api/graphs",
            json=_long_wait_graph(duration_seconds=duration_seconds),
            headers=DEFAULT_AUTH_HEADER,
        )
        assert resp.status_code == 200, resp.text
        gid = resp.json()["id"]

        statuses: list[int] = []

        def hold_stream() -> None:
            with http.stream(
                "POST", STREAM_URL.format(gid=gid),
                json={"inputs": {}}, headers=DEFAULT_AUTH_HEADER,
            ) as resp:
                statuses.append(resp.status_code)
                for _ in resp.iter_raw():
                    pass  # 真 TCP 流式读到终帧关流（duration 睡完自然到达）

        threads = [
            threading.Thread(target=hold_stream, daemon=True)
            for _ in range(stream_count)
        ]
        for t in threads:
            t.start()

        # duration wait 首次进入即经 frame_sink 置 run 为 suspended：45 条 suspended
        # 就是 45 个 worker 真挂在 wait、45 条 SSE 连接真开着的证据。
        deadline = time.monotonic() + TIMEOUT
        suspended = 0
        while time.monotonic() < deadline:
            items = http.get(
                "/api/runs?status=suspended&limit=200", headers=DEFAULT_AUTH_HEADER
            ).json().get("items", [])
            suspended = sum(1 for item in items if item.get("graphId") == gid)
            if suspended >= stream_count:
                break
            time.sleep(0.1)
        assert suspended >= stream_count, f"only {suspended} runs suspended"
        assert len(statuses) == stream_count and all(
            code == 200 for code in statuses
        )

        # 真 uvicorn 单事件循环：sync health 必须借 anyio 工作线程（容量 40）处理。
        # 旧同步生成器模型下 45 条长流占满令牌，health 会排队到流结束；
        # 新模型 async 生成器等待不占工作线程，health 持续快速 200。
        for _ in range(15):
            started = time.monotonic()
            resp = http.get("/api/health")
            elapsed = time.monotonic() - started
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert elapsed < 2.0, f"health blocked for {elapsed:.2f}s (thread starvation)"

        # duration sleep 不可中断，cancel 不能加速清理：等其自然睡完落 completed 关流。
        for t in threads:
            t.join(TIMEOUT)
        assert not any(t.is_alive() for t in threads), "stream threads hung on cleanup"


# --------------------------------------------------------------------------- #
# U1378：真断连只关桥接不取消，run_graph 继续跑完（真 uvicorn + 真 TCP 断开）
# --------------------------------------------------------------------------- #
def test_disconnect_closes_bridge_but_run_reaches_completed():
    base = _live_server_base_url()
    with httpx.Client(base_url=base, timeout=15) as http:
        resp = http.post(
            "/api/graphs",
            json=_long_wait_graph(duration_seconds=3),
            headers=DEFAULT_AUTH_HEADER,
        )
        assert resp.status_code == 200, resp.text
        gid = resp.json()["id"]

        holder: dict = {"status": None, "errors": []}
        chunks: list[bytes] = []
        reached_wait = threading.Event()

        def reader() -> None:
            try:
                with http.stream(
                    "POST", STREAM_URL.format(gid=gid),
                    json={"inputs": {}}, headers=DEFAULT_AUTH_HEADER,
                ) as resp:
                    holder["status"] = resp.status_code
                    for chunk in resp.iter_raw():
                        chunks.append(chunk)
                        if b'"node_id": "wait-1"' in b"".join(chunks):
                            reached_wait.set()
                            break  # 退出 with：关闭响应、真断 TCP
            except Exception as exc:  # noqa: BLE001 - 断连可能带流异常，不影响后台运行
                holder["errors"].append(repr(exc))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        assert reached_wait.wait(TIMEOUT), "worker never reached wait-1"
        thread.join(TIMEOUT)
        assert not thread.is_alive(), "disconnect thread hung"
        assert holder["status"] == 200, holder["errors"]

        # 找到 run（断连后由独立请求观测后台状态）。
        deadline = time.monotonic() + TIMEOUT
        run_id = None
        while time.monotonic() < deadline:
            items = http.get("/api/runs", headers=DEFAULT_AUTH_HEADER).json().get("items", [])
            for item in items:
                if item.get("graphId") == gid:
                    run_id = item["runId"]
                    break
            if run_id:
                break
            time.sleep(0.05)
        assert run_id, "run id not found after disconnect"

        # 断连不是取消：后台 run_graph 继续，3s wait 后 op-after 跑完落 completed。
        deadline = time.monotonic() + TIMEOUT
        status = None
        while time.monotonic() < deadline:
            status = http.get(
                f"/api/runs/{run_id}", headers=DEFAULT_AUTH_HEADER
            ).json().get("status")
            if status == "completed":
                break
            time.sleep(0.1)
        assert status == "completed", f"run did not survive disconnect: {status}"
