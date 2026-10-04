"""浏览器出向闸门的真机验证（docs/13 I21–I23；威胁面 docs/89 §14 N-1）。

与 `test_web_browser_integration.py` 分文件的原因不是分类，是 Playwright 的 sync API
在一个线程里只允许一个活动实例——那边的模块级 fixture 会把浏览器一直开到模块结束。

三条各断一件事，缺一条这批断言就不可信：
  I21 闸门生效时被禁的内网子资源一条都打不出去，而公开页照常渲染（不是"谁都拦"）；
  I22 判别对照：放开同一地址后确实打得通（否则 I21 的"拦住了"只是"本来就通不了"）；
  I23 现状边界：3xx 跳转后的请求不进路由回调 ⇒ 闸门挡不住跳转（docs/14 D52 存在的理由）。

两台服务器都监听本机的真实环回地址（127.0.0.1 与 ::1，同一台机器、同样可达），闸门用
`permit_cidrs` 区分它们——那是 `EgressGuard` 里仅进程内测试可用、无 env 通道的注入缝，
本批不给生产配置面扩任何口子。
"""

from __future__ import annotations

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from atlas.harness.base import Permission
from atlas.security.egress import EgressGuard
from atlas.web.adapter import WebHarnessAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1",
        reason="set ATLAS_RUN_INTEGRATION=1 to run browser integration tests",
    ),
]

_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)
_LANDED = b"<html><head><title>landed</title></head><body>internal</body></html>"


def _serve(host: str, handler_cls) -> HTTPServer:
    class _Server(HTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    server = _Server((host, 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def loopback_pair():
    """公开页走 127.0.0.1；被当作内网目标的那台走 ::1。返回 (打到内网的路径, 公开页地址)。"""
    hits: list[str] = []

    class Internal(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            body = _GIF if self.path.endswith(".png") else _LANDED
            self.send_response(200)
            self.send_header("Content-Type", "image/gif" if body is _GIF else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    class Public(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            internal_port = internal.server_address[1]
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://[::1]:{internal_port}/landed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = (
                "<!doctype html><html><head><title>guard</title></head><body>"
                f'<img id="px" src="http://[::1]:{internal_port}/secret.png">'
                "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    internal = _serve("::1", Internal)
    public = _serve("127.0.0.1", Public)
    yield hits, f"http://127.0.0.1:{public.server_address[1]}"
    internal.shutdown()
    internal.server_close()
    public.shutdown()
    public.server_close()


@pytest.fixture()
def web_factory():
    """按策略起一个真浏览器；每个用例独立开关（sync Playwright 同线程只能有一个实例）。"""
    running: list[WebHarnessAdapter] = []

    def build(permits: tuple[str, ...]) -> WebHarnessAdapter:
        guard = EgressGuard(allowlist=(), resolver=lambda host: [host], permit_cidrs=permits)
        try:
            web = WebHarnessAdapter(
                egress=guard,
                granted_permissions={Permission.READ, Permission.WRITE},
                headless=True,
            )
            web.start()
        except Exception as exc:  # 浏览器未安装/无法启动
            pytest.skip(f"chromium unavailable: {exc}")
        running.append(web)
        return web

    yield build
    for web in running:
        web.close()


# 只放行公开页那台；::1 一律拒绝。I21/I23 共用这套策略。
_DENY_V6 = ("127.0.0.1/32",)


def test_i21_route_guard_stops_an_internal_subresource(loopback_pair, web_factory):
    hits, public_base = loopback_pair
    web = web_factory(_DENY_V6)
    web.page.goto(f"{public_base}/", wait_until="load")
    web.page.wait_for_load_state("networkidle")
    assert hits == [], f"内网子资源被打到了——route 级闸门没生效：{hits}"
    assert any("::1" in d for d in web.egress_denials), web.egress_denials
    # 闸门只拦被禁的目标：公开页本身必须照常渲染。
    assert web.page.title() == "guard"


def test_i22_positive_control_permitted_internal_target_is_reached(loopback_pair, web_factory):
    hits, public_base = loopback_pair
    web = web_factory(("127.0.0.1/32", "::1/128"))
    web.page.goto(f"{public_base}/", wait_until="load")
    web.page.wait_for_load_state("networkidle")
    assert "/secret.png" in hits, f"放开了也打不到 ⇒ I21 的『拦住了』可能只是地址本来不可达：{hits}"
    assert web.egress_denials == []


def test_i23_redirect_target_bypasses_the_route_gate(loopback_pair, web_factory):
    """断的是**已知的弱**，不是回归：Playwright 不为 3xx 跳转后的请求回调路由。

    这条红了就说明平台行为变了（跳转请求开始进回调）⇒ 去更新 docs/89 §14、docs/14 D52，
    并重新评估 prod 是否可以放开 `web-playwright` 注册。
    """
    hits, public_base = loopback_pair
    web = web_factory(_DENY_V6)
    web.page.goto(f"{public_base}/redirect", wait_until="load")
    assert "/landed" in hits, f"跳转目标竟被拦下了：{hits}"
    assert not any("/landed" in d for d in web.egress_denials), (
        f"路由回调看见了跳转目标，闸门已覆盖 302：{web.egress_denials}"
    )
    assert web.page.title() == "landed"
