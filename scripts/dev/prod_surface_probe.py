#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/75 §4 —— demo 模拟面 prod 闸门的真进程探测（打包 P 的 〔跑〕 证据）。

为什么要有这个脚本而不是只留单测：本批修的那条 fail-open 恰恰是"**你以为在 prod，其实
档位没认出来**"。单测里 monkeypatch 一个环境变量证不到这件事——真起一个 `ATLAS_ENV=prod`
的进程、真发 HTTP、真看到 404，才是这条判断的落点。同一进程里顺带把 N1（prod 引导口令）
再验一遍，因为它和档位是同一套读档逻辑的消费者。

四段：
  1. `ATLAS_ENV=prod` ⇒ 5 条 demo 面＋老 3 条 shopify-admin mock 全 404，且响应体只说
     "Not Found"（不透露"这里有个被关掉的东西"）；公开面 `/api/health` 仍 200；
     业务端点仍 401（不是 404——排障要说实话）；`admin-a/admin123` 被拒而引导口令可登（N1）。
  2. `ATLAS_ENV=prod` ＋ `ATLAS_ENABLE_DEMO_MOCK=1` ⇒ 同一批路由回到 200/401 原语义（单点开关一次开全）。
  3. `ATLAS_ENV=PROD`（大写）⇒ 仍然 404。**这条在改前是开门的**：门函数自己比字符串，
     大写判成"非 prod"，静默 fail-open（docs/75 D-2）。
  4. `ATLAS_ENV=production`（非法值）⇒ 进程**起不来**（档位读取器抛错，而不是默认按 dev 放行）。

密钥是本机一次性生成的随机值，不是任何真实凭据；库用内存档。
用法：.venv/bin/python scripts/dev/prod_surface_probe.py [--port 8180]
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")

# 带合法 body 才谈得上"门"：POST 缺体会被 pydantic 先 422，那时门还没轮到执行。
DEMO_ROUTES = [
    ("get", "/demo/shop", None),
    ("post", "/api/demo/shop/login", {"username": "demo", "password": "demo"}),
    ("get", "/api/demo/shop/orders", None),
    ("get", "/api/demo/mock/orders", None),
    ("post", "/api/demo/mock/orders/12345/receipt", {"probe": True}),
    ("get", "/api/demo/mock/shopify-admin/webhooks.json", None),
    ("post", "/api/demo/mock/shopify-admin/webhooks.json",
     {"webhook": {"topic": "orders/paid", "address": "https://example.test/probe", "format": "json"}}),
    ("delete", "/api/demo/mock/shopify-admin/webhooks/1.json", None),
]
BOOTSTRAP_PASSWORD = "Atlas-Probe-2026-rot13-not-secret"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}", flush=True)
    if not cond:
        raise AssertionError(msg)


def _key() -> str:
    """主密钥/HMAC 密钥要求：base64url 解码后**恰 32 字节**（`secrets.token_urlsafe(48)`
    解出 48 字节，会被拒——这条在真机器上踩过一次，所以留成函数而不是随手写）。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self, env_overrides: dict[str, str], *, expect_boot: bool = True) -> None:
        env = dict(os.environ)
        env.pop("ATLAS_ENABLE_DEMO_MOCK", None)  # 先清继承，再让 overrides 说话
        env.update({
            "ATLAS_MASTER_KEY": _key(),
            "ATLAS_APPROVAL_HMAC_SECRET": _key(),
            "ATLAS_ADMIN_BOOTSTRAP_PASSWORD": BOOTSTRAP_PASSWORD,
            "ATLAS_STORAGE_BACKEND": "memory",
            "ATLAS_SCHEDULE_ENABLED": "0",
            **env_overrides,
        })
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if not self._wait_ready(60 if expect_boot else 12):
            tail = ""
            if self.proc is not None:
                try:
                    tail = (self.proc.communicate(timeout=5)[0] or "")[-2500:]
                except Exception:
                    tail = "<读不到子进程输出>"
            self.stop()
            if expect_boot:
                # 只说"没起来"等于让下一个人重新猜一遍：把子进程的尾巴打出来。
                raise RuntimeError(f"服务没起来，uvicorn 输出尾部：\n{tail}")
            # 期望起不来：把原因抓出来证明是"非法档位"而不是别的崩法
            ok("ATLAS_ENV 非法值" in tail or "production" in tail,
               f"非法档位下的拒启原因不是档位校验：{tail[-200:]}")
            return
        if not expect_boot:
            self.stop()
            raise AssertionError("非法档位竟然起来了服务——fail-open 没修掉")

    def _wait_ready(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1).status_code == 200:
                    return True
            except Exception:
                time.sleep(0.3)
        return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def call(method: str, path: str, body: dict | None = None, **kwargs) -> httpx.Response:
    base = f"http://127.0.0.1:{probe_port}"
    if body is not None:
        kwargs["json"] = body
    return httpx.request(method.upper(), base + path, timeout=15, **kwargs)


probe_port = 8180


def main() -> int:
    parser = argparse.ArgumentParser(description="prod-profile demo surface probe")
    parser.add_argument("--port", type=int, default=8180)
    args = parser.parse_args()
    global probe_port
    probe_port = args.port

    server = Server(args.port)
    try:
        print("1｜ATLAS_ENV=prod")
        server.start({"ATLAS_ENV": "prod"})
        ok(call("get", "/api/health").status_code == 200, "公开面 /api/health 仍 200")
        for method, path, body in DEMO_ROUTES:
            response = call(method, path, body)
            ok(response.status_code == 404, f"{method.upper()} {path} → 404（实得 {response.status_code}）")
            if response.status_code == 404:
                body_json = response.json()
                ok(body_json.get("detail") == "Not Found", f"{path} 的 404 不透露存在性（{body_json}）")
        bad = call("post", "/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        ok(bad.status_code == 401, f"仓库明文种子口令在 prod 被拒（实得 {bad.status_code}）——N1 复验")
        good = call("post", "/api/auth/login", json={"username": "admin-a", "password": BOOTSTRAP_PASSWORD})
        ok(good.status_code == 200, "引导口令可登（同进程内 N1 的另一半）")
        token = good.json()["token"]
        anon = call("get", "/api/graphs")
        ok(anon.status_code == 401, f"prod 下业务端点仍 401 不是 404（实得 {anon.status_code}）")
        ok(call("get", "/api/graphs", headers={"Authorization": f"Bearer {token}"}).status_code == 200,
           "带上合法会话后业务端点照常 200（门没误伤 RBAC）")
        server.stop()

        print("2｜ATLAS_ENV=prod ＋ ATLAS_ENABLE_DEMO_MOCK=1")
        server.start({"ATLAS_ENV": "prod", "ATLAS_ENABLE_DEMO_MOCK": "1"})
        ok(call("get", "/demo/shop").status_code == 200, "单点开关一开，demo 控制台回到 200")
        login = call("post", "/api/demo/shop/login", json={"username": "demo", "password": "demo"})
        ok(login.status_code == 200, "demo 商家后台原语义回来（demo/demo）")
        server.stop()

        print("3｜ATLAS_ENV=PROD（大写；改前这条路是开着的）")
        server.start({"ATLAS_ENV": "PROD"})
        for method, path, body in DEMO_ROUTES:
            response = call(method, path, body)
            ok(response.status_code == 404, f"大写档位下 {method.upper()} {path} → 404（实得 {response.status_code}）")
        server.stop()

        print("4｜ATLAS_ENV=production（非法档位）")
        server.start({"ATLAS_ENV": "production"}, expect_boot=False)
        ok(True, "非法档位下进程没有起来（拒启而不是默认按 dev 放行）")

        print("\n探测全过 ✅")
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
