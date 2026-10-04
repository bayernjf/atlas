#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/08 打包 ZO —— 文件型密钥 `<NAME>_FILE` 的真进程探测（〔跑〕 证据）。

为什么不能只留单测：本批的价值是「prod 部署可以把凭据交给 vault／docker secret 的
**文件**形态，不用把明文写进 `.env`」。单测 monkeypatch 的是 `os.getenv`，证不到
「真起一个 `ATLAS_ENV=prod` 的进程、env 里**只有** `_FILE` 指针、服务照样起来」——
而那正是 1.1 完成判据要的那件事。

五段：
  1. prod 档，三把必需值**只经文件**注入（env 里删掉本体）⇒ 服务起来、引导口令可登、
     Bearer 会话可用 ⇒ 证明文件通道贯通到真进程，且没误伤 RBAC；
  2. `ATLAS_MASTER_KEY_FILE` 指向**不存在**的路径 ⇒ 进程起不来，且拒启原因点名
     `ATLAS_MASTER_KEY_FILE`（不回退旁边那份 env 明文）；
  3. `_FILE` 指向**空文件** ⇒ 同样拒启（"挂载了但没内容"不能被当成"没配"而放行）；
  4. `OPENAI_API_KEY_FILE` ⇒ 真子进程里 `hydrate_file_secrets()` 把值补进 env；
     env 已有值 ⇒ 不被文件覆盖（不制造第二个真值来源）；
  5. 差分对照：回到老路径（只给 env、不给 `_FILE`）⇒ 服务照常起来（零回归）。

密钥与口令都是本机一次性生成的随机值，不是任何真实凭据；库用内存档。
用法：.venv/bin/python scripts/dev/secret_file_probe.py [--port 8181]
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")
BOOTSTRAP_PASSWORD = "Atlas-Probe-2026-file-secret"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}", flush=True)
    if not cond:
        raise AssertionError(msg)


def _key() -> str:
    """与 prod_surface_probe 同口径：按最严的那个生成。

    `assert_prod_secrets()` 只查字符串 ≥32 字节，而 `ATLAS_MASTER_KEY` 还要经 base64url
    解码后**恰 32 字节**才能建 AES-GCM provider，故统一按后者生成。
    """
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def _write(parent: Path, name: str, content: str) -> str:
    path = parent / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self, env_overrides: dict[str, str], *, expect_boot: bool = True) -> None:
        env = dict(os.environ)
        # 先清继承，再让 overrides 说话：机器上残留的密钥会让"只经文件"这段变成假绿。
        for name in (
            "ATLAS_MASTER_KEY",
            "ATLAS_APPROVAL_HMAC_SECRET",
            "ATLAS_ADMIN_BOOTSTRAP_PASSWORD",
        ):
            env.pop(name, None)
            env.pop(f"{name}_FILE", None)
        env.update({
            "ATLAS_STORAGE_BACKEND": "memory",
            # 本探针只验"`*_FILE` 指针能补水、prod 能起服"，不需要持久库。A-11 之后 prod 的
            # memory 档必须显式署名才让起（docs/89 §16），这里正是那条门的合法逃生门用途。
            "ATLAS_ALLOW_VOLATILE_STORAGE": "1",
            "ATLAS_SCHEDULE_ENABLED": "0",
            **env_overrides,
        })
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if not self._wait_ready(60 if expect_boot else 15):
            if expect_boot:
                tail = self._tail()
                self.stop()
                raise RuntimeError(f"服务没起来，uvicorn 输出尾部：\n{tail}")
            # 期望起不来的那段：留着子进程让调用方把拒启原因读走（stop 会把它清成 None）
            return
        if not expect_boot:
            self.stop()
            raise AssertionError("本段期望进程起不来，它却起来了——fail-closed 没生效")

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

    def _tail(self) -> str:
        if self.proc is None:
            return "<无子进程>"
        try:
            return (self.proc.communicate(timeout=5)[0] or "")[-2500:]
        except Exception:
            return "<读不到子进程输出>"

    def refuse_reason(self, env_overrides: dict[str, str]) -> str:
        """起一次注定起不来的进程，把拒启原因抓出来（证明是密钥门而不是别的崩法）。"""
        self.start(env_overrides, expect_boot=False)
        tail = self._tail()
        self.stop()
        return tail

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
    base = f"http://127.0.0.1:{PORT}"
    if body is not None:
        kwargs["json"] = body
    return httpx.request(method.upper(), base + path, timeout=15, **kwargs)


PORT = 8181


def main() -> int:
    parser = argparse.ArgumentParser(description="file-based secret injection probe")
    parser.add_argument("--port", type=int, default=8181)
    args = parser.parse_args()
    global PORT
    PORT = args.port

    server = Server(args.port)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            print("1｜prod 档，三把必需值只经文件注入（env 里没有本体）")
            server.start({
                "ATLAS_ENV": "prod",
                "ATLAS_MASTER_KEY_FILE": _write(tmpdir, "master", _key()),
                "ATLAS_APPROVAL_HMAC_SECRET_FILE": _write(tmpdir, "hmac", _key()),
                "ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE": _write(tmpdir, "boot", BOOTSTRAP_PASSWORD),
            })
            ok(call("get", "/api/health").status_code == 200, "服务起来了（/api/health 200）")
            good = call("post", "/api/auth/login",
                        json={"username": "admin-a", "password": BOOTSTRAP_PASSWORD})
            ok(good.status_code == 200, f"文件注入的引导口令可登（实得 {good.status_code}）")
            token = good.json()["token"]
            authed = call("get", "/api/graphs", headers={"Authorization": f"Bearer {token}"})
            ok(authed.status_code == 200, "Bearer 会话照常可用（文件通道没误伤 RBAC）")
            server.stop()

            print("2｜`_FILE` 指向不存在的路径 ⇒ 拒启且点名该变量")
            tail = server.refuse_reason({
                "ATLAS_ENV": "prod",
                "ATLAS_MASTER_KEY_FILE": str(tmpdir / "gone"),
                "ATLAS_APPROVAL_HMAC_SECRET_FILE": _write(tmpdir, "hmac", _key()),
                "ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE": _write(tmpdir, "boot", BOOTSTRAP_PASSWORD),
            })
            ok("ATLAS_MASTER_KEY_FILE" in tail, f"拒启原因点名 ATLAS_MASTER_KEY_FILE：{tail[-300:]}")

            print("3｜`_FILE` 指向空文件 ⇒ 同样拒启（挂载了没内容 ≠ 没配）")
            tail = server.refuse_reason({
                "ATLAS_ENV": "prod",
                "ATLAS_MASTER_KEY_FILE": _write(tmpdir, "empty", ""),
                "ATLAS_APPROVAL_HMAC_SECRET_FILE": _write(tmpdir, "hmac", _key()),
                "ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE": _write(tmpdir, "boot", BOOTSTRAP_PASSWORD),
            })
            ok("ATLAS_MASTER_KEY_FILE" in tail, f"空文件同样拒启：{tail[-300:]}")

            print("4｜OPENAI_API_KEY_FILE ⇒ 真子进程补水进 env；env 已有值不被覆盖")
            snippet = (
                "from atlas.security.bootstrap import hydrate_file_secrets;"
                "hydrate_file_secrets();"
                "import os;print(os.environ.get('OPENAI_API_KEY',''))"
            )
            probe_env = dict(os.environ)
            probe_env.pop("OPENAI_API_KEY", None)
            probe_env["OPENAI_API_KEY_FILE"] = _write(tmpdir, "llm", "sk-from-file-probe")
            out = subprocess.run([PYTHON, "-c", snippet], cwd=str(ROOT), env=probe_env,
                                 capture_output=True, text=True, timeout=120)
            ok(out.stdout.strip().endswith("sk-from-file-probe"),
               f"文件内容补进了 env（实得 {out.stdout.strip()!r}）")

            probe_env["OPENAI_API_KEY"] = "sk-already-there"
            out = subprocess.run([PYTHON, "-c", snippet], cwd=str(ROOT), env=probe_env,
                                 capture_output=True, text=True, timeout=120)
            ok(out.stdout.strip().endswith("sk-already-there"),
               f"env 已有值不被文件覆盖（实得 {out.stdout.strip()!r}）")

        print("5｜差分对照：老路径（只给 env、无 _FILE）照常起来")
        server.start({
            "ATLAS_ENV": "prod",
            "ATLAS_MASTER_KEY": _key(),
            "ATLAS_APPROVAL_HMAC_SECRET": _key(),
            "ATLAS_ADMIN_BOOTSTRAP_PASSWORD": BOOTSTRAP_PASSWORD,
        })
        ok(call("get", "/api/health").status_code == 200, "env 路径零回归")
        server.stop()

        print("\n探测全过 ✅")
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
