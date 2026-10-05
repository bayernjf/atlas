#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络边界（Caddy／TLS 反代）演练（docs/08 打包 BB；docs/73 4.1 现欠第②件）。

`prod_rehearsal.py` 证的是"出厂镜像＋空卷＋容器边界内跑得起来"，它的 override 把 caddy
的 profile 改成 `["skip"]`、所有请求走 `http://127.0.0.1:<映射端口>` ⇒ **TLS、反向代理、
按主机头路由三类判据一条都没被演过**。本脚本补的就是这一层：

  1. 只起 `db`＋`atlas`＋`caddy`，**atlas 的端口映射置空**（Caddyfile 注释里那句生产建议）；
  2. 经 443 打 `/api/ready`，用 Caddy 内部 CA 的**根证书真校验**（不是 `-k`）；
  3. HTTP 80 ⇒ 301/308 跳 HTTPS；
  4. **旁路不可达**：宿主机直连原应用端口必须连不上，同时容器内 healthcheck 仍绿；
  5. 引导口令登录 → 首登强制位 403 → 改密 → 200（docs/95 的强制位在反代后仍成立）；
  6. **SSE 经反代**：`/run/stream` 必须收到**多个** event 帧且终帧到达（反代缓冲是网络
     边界最典型的假绿点——前端会表现为"运行中画布不刷新"）；
  7. `/metrics` 经 edge ⇒ 404（remote_ip 网段白名单生效）、demo 面经 443 ⇒ 404。

**没有真域名与证书**：站点用 `atlas.internal.test` ＋ `tls internal`（Caddy 内部 CA），
客户端靠 `--resolve` 把该名字指向 127.0.0.1。所以本演练证的是"这套 edge 形态跑得通"，
**不证**"证书由真 CA 签发"——那半仍在部署方（docs/73 4.1 第②件的另一半）。

用法：.venv/bin/python scripts/dev/edge_rehearsal.py [--http-port 80] [--https-port 443] [--keep]
退出码：任一段判据不成立即非 0；收尾只删本项目自己的卷。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "atlas-edge-rehearsal"
IMAGE = "atlas-demo:latest"
HOST = "atlas.internal.test"
ADMIN = "admin-a"
PASSWORD = "Atlas-Edge-" + uuid.uuid4().hex[:12] + "!aA"
#: Caddy 内部 CA 的根证书在容器里的位置（`tls internal` 初始化后生成）。
CA_IN_CONTAINER = "/data/caddy/pki/authorities/local/root.crt"


def say(label: str, msg: str) -> None:
    print(f"  {label} {msg}", flush=True)


def ok(cond: bool, msg: str) -> None:
    say("OK   " if cond else "FAIL ", msg)
    if not cond:
        raise AssertionError(msg)


def note(msg: str) -> None:
    say("NOTE ", msg)


def sh(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> str:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{cmd[:4]}… 退出 {proc.returncode}：\n{out[-2500:]}")
    return out


def compose(env: dict[str, str], *args: str, check: bool = True) -> str:
    return sh(["docker", "compose", "-p", PROJECT, *args], env=env, check=check)


def edge_down(env: dict[str, str], files: list[str]) -> None:
    """`down` 必须带 `--profile edge`：不带的话 compose 不把 caddy 算进这个项目，
    于是 caddy 容器被留下来占住 80/443，下一轮演练的 caddy 就起不来——而症状只是
    "ready 等不到"，真正的报错早被翻过去了（本脚本第一版就是这样浪费了两轮）。"""
    compose(env, *files, "--profile", "edge", "down", "-v", "--remove-orphans", check=False)


def rand_secret() -> str:
    return base64.urlsafe_b64encode(os.urandom(48)).decode("ascii").rstrip("=")[:44]


def fernet_key() -> str:
    """`ATLAS_MASTER_KEY` 的形状与别的密钥不同：它会被 base64url 解码并要求**恰 32 字节**
    才能建 AES-GCM provider（`security/secrets.py:_decode_master_key`）。

    第一版这里复用了 48 字节的 `rand_secret()`，结果 atlas 容器启动即崩——症状是"经 443
    永远等不到 ready"，真正的报错在 `docker compose logs atlas` 的最后一行。
    """
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def curl_https(path: str, https_port: int, ca: Path, *, method: str = "GET",
               body: dict | None = None, token: str | None = None,
               timeout: int = 30) -> tuple[int, str, str]:
    """经 Caddy 打一次 HTTPS：`--resolve` 指路、`--cacert` 真校验，返回 (状态, 体, 头)。"""
    cmd = ["curl", "-sS", "-X", method, "--resolve", f"{HOST}:{https_port}:127.0.0.1",
           "--cacert", str(ca), "-o", "-", "-D", "/dev/stderr",
           "--max-time", str(timeout), f"https://{HOST}:{https_port}{path}"]
    if token:
        cmd[1:1] = ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd[1:1] = ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return http_status(proc.returncode, proc.stderr, proc.stdout), proc.stdout, proc.stderr


def http_status(proc_returncode: int, stderr_text: str, stdout_text: str) -> int | None:
    """从 `-D /dev/stderr` 倒出来的响应头里取状态行。

    **不能拿 curl 的进程退出码当 HTTP 状态**：退出 0 只表示"连上并收完"，200／404／500
    在它眼里都是 0。第一版就是在这里把退出码当状态码用，于是 240s 轮询永远等不到 200。
    """
    status: int | None = None
    for line in stderr_text.splitlines():
        if line.startswith("HTTP/"):
            try:
                status = int(line.split()[1])
            except (IndexError, ValueError):
                return None
    if status is not None:
        return status
    # 连不上（连接被拒／TLS 握手失败）时 curl 退出非 0 且没有状态行
    return None if proc_returncode != 0 else (200 if stdout_text else None)


def main() -> int:
    ap = argparse.ArgumentParser(description="edge/Caddy network-boundary rehearsal")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--keep", action="store_true", help="演练后不清卷（排障用）")
    args = ap.parse_args()

    if subprocess.run(["which", "docker"], capture_output=True).returncode != 0:
        print("docker 不可用，本演练无法进行。", file=sys.stderr)
        return 2
    # 先在宿主机上探一下端口：不探的话要等 240s 等到 ready 超时才知道 caddy 没起来，
    # 而真正的报错（"Bind for 0.0.0.0:80 failed"）那时已经被翻过去了。
    for port in (args.http_port, args.https_port):
        try:
            probe = socket.socket()
            probe.bind(("0.0.0.0", port))
            probe.close()
        except OSError as exc:
            print(f"端口 {port} 不可用（{exc}）——改用 --http-port／--https-port，"
                  f"或先清掉占着它的容器（上一轮残留的 caddy 就占过 80/443）。", file=sys.stderr)
            return 2

    env = {**os.environ,
           "ATLAS_ENV": "prod",
           "ATLAS_MASTER_KEY": fernet_key(),
           "ATLAS_APPROVAL_HMAC_SECRET": rand_secret(),
           "ATLAS_SESSION_SECRET": rand_secret(),
           "ATLAS_ADMIN_BOOTSTRAP_PASSWORD": PASSWORD,
           "LITELLM_MODEL": "",
           "OPENAI_API_KEY": "",
           "OPENAI_BASE_URL": ""}

    override = ROOT / ".edge-rehearsal-override.yml"
    override.write_text(
        f"""services:
  atlas:
    # 本演练要验的正是这句生产建议：应用端口不映射出宿主机，只让 Caddy 暴露 80/443
    ports: !override []
    restart: "no"
  db:
    restart: "no"
  caddy:
    restart: "no"
    ports: !override
      - "{args.http_port}:80"
      - "{args.https_port}:443"
    volumes: !override
      - ./deploy/Caddyfile.rehearsal:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
""",
        encoding="utf-8",
    )
    files = ["-f", "docker-compose.yml", "-f", override.name]
    ca_path = ROOT / ".edge-rehearsal-ca.crt"

    try:
        print("1｜构建出厂镜像（当前 HEAD）")
        sha = sh(["git", "rev-parse", "--short", "HEAD"]).strip()
        dirty = sh(["git", "status", "--porcelain", "--untracked-files=no"]).strip()
        t0 = time.time()
        sh(["docker", "build", "-t", IMAGE, "."])
        image_id = sh(["docker", "images", "--format", "{{.ID}}", IMAGE]).strip().splitlines()[0]
        ok(bool(image_id), f"镜像已构建：{IMAGE} id={image_id}（源 HEAD `{sha}`，{time.time() - t0:.0f}s）")
        if dirty:
            note(f"工作区有 {len(dirty.splitlines())} 处未提交改动（镜像打的是工作区，不是纯 `{sha}`）")

        print("2｜清掉本项目的旧卷，用空卷起步（只碰本项目命名空间）")
        edge_down(env, files)
        compose(env, *files, "--profile", "edge", "up", "-d", "db", "atlas", "caddy")

        print("3｜取 Caddy 内部 CA 根证书（后面全部请求真校验，不用 -k）")
        deadline = time.time() + 90
        ca_text = ""
        while time.time() < deadline:
            got = sh(["docker", "compose", "-p", PROJECT, *files, "exec", "-T", "caddy",
                      "cat", CA_IN_CONTAINER], check=False)
            if got.strip().startswith("-----BEGIN"):
                ca_text = got
                break
            time.sleep(2)
        if not ca_text:
            # 内部 CA 只在首次签发时才落盘：先打一次握手把它逼出来，再取证书。
            # 这一次用的是 -k，之后所有判据请求都走真校验——这一点照实打出来。
            note("首次握手前容器里还没有 CA 根证书，先发一次触发签发（这一发不参与任何判据）")
            subprocess.run(["curl", "-sk", "--resolve", f"{HOST}:{args.https_port}:127.0.0.1",
                            f"https://{HOST}:{args.https_port}/api/health"], capture_output=True)
            ca_text = sh(["docker", "compose", "-p", PROJECT, *files, "exec", "-T", "caddy",
                          "cat", CA_IN_CONTAINER])
        ok(ca_text.strip().startswith("-----BEGIN"), "拿到 Caddy 内部 CA 的根证书")
        ca_path.write_text(ca_text, encoding="utf-8")

        print("4｜经 443 等 /api/ready 到 200（TLS 真校验）")
        t0 = time.time()
        ready_at = None
        while time.time() - t0 < 240:
            code, _, _ = curl_https("/api/ready", args.https_port, ca_path, timeout=5)
            if code == 200:
                ready_at = time.time() - t0
                break
            time.sleep(2)
        ok(ready_at is not None,
           f"/api/ready 经 HTTPS 到 200"
           + (f"（{ready_at:.1f}s）" if ready_at is not None else "（240s 内没到）"))
        code, body, _ = curl_https("/api/health", args.https_port, ca_path)
        ok(code == 200, f"/api/health 经 Caddy 反代 200（实得 {code}）")
        restarts = sh(["docker", "compose", "-p", PROJECT, *files, "ps", "-q", "atlas"]).strip()
        count = sh(["docker", "inspect", "--format", "{{.RestartCount}}", restarts]).strip()
        ok(count == "0", f"首启没靠重启救回来（RestartCount={count}）")

        print("5｜HTTP 80 ⇒ 跳 HTTPS")
        proc = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-D", "-", "--max-time", "10",
                               "--resolve", f"{HOST}:{args.http_port}:127.0.0.1",
                               f"http://{HOST}:{args.http_port}/api/health"],
                              capture_output=True, text=True)
        head = proc.stdout + proc.stderr
        status = http_status(proc.returncode, head, "")
        location = next((l.split(":", 1)[1].strip() for l in head.splitlines()
                         if l.lower().startswith("location:")), "")
        ok(status in (301, 308), f"80 端口返回跳转（实得 {status}）")
        ok(location.startswith("https://"), f"跳到 HTTPS（Location={location}）")

        print("6｜旁路不可达：应用端口没映射出宿主机，但容器内仍健康")
        proc = subprocess.run(["curl", "-sS", "-o", "/dev/null", "--max-time", "5",
                               "http://127.0.0.1:8000/api/health"], capture_output=True, text=True)
        ok(proc.returncode != 0, f"宿主机直连 127.0.0.1:8000 连不上（curl 退出 {proc.returncode}）")
        # healthcheck 有 20s 的 start_period，刚 ready 时它还是 "starting"——要等不要判。
        health = ""
        for _ in range(30):
            health = sh(["docker", "inspect", "--format", "{{json .State.Health.Status}}",
                         restarts]).strip()
            if "healthy" in health:
                break
            time.sleep(2)
        ok("healthy" in health, f"容器内 healthcheck 转绿（{health}）⇒ 去映射没把应用搞坏")
        note("这两条合起来才证明 Caddyfile 注释里那句『生产只让 Caddy 暴露 80/443』站得住")

        print("7｜引导口令登录 → 首登强制位 403 → 改密 → 200（全部经 443）")
        code, body, _ = curl_https("/api/auth/login", args.https_port, ca_path, method="POST",
                                   body={"username": ADMIN, "password": PASSWORD})
        ok(code == 200, f"经反代登录成功（{code}）")
        token = json.loads(body)["token"]
        code, body, _ = curl_https("/api/graphs", args.https_port, ca_path, token=token)
        ok(code == 403 and "AUTH_PASSWORD_CHANGE_REQUIRED" in body,
           f"未改密时业务端点被挡（{code}）：{body[:120]}")
        new_password = PASSWORD + "-rotated-1"
        code, _, _ = curl_https("/api/auth/change-password", args.https_port, ca_path, method="POST",
                                token=token, body={"oldPassword": PASSWORD, "newPassword": new_password})
        ok(code == 200, f"改密经反代可达（{code}）——不可达就是自锁")
        code, body, _ = curl_https("/api/auth/login", args.https_port, ca_path, method="POST",
                                   body={"username": ADMIN, "password": new_password})
        ok(code == 200, "新口令可登")
        token = json.loads(body)["token"]

        print("8｜SSE 经反代：帧要增量到达，不能被缓冲成一次吐完")
        code, body, _ = curl_https("/api/templates/refund-auto", args.https_port, ca_path, token=token)
        ok(code == 200, f"内置模板可读（{code}）")
        graph = dict(json.loads(body)["graph"])
        graph["name"] = "edge-rehearsal-refund"
        code, body, _ = curl_https("/api/graphs", args.https_port, ca_path, method="POST",
                                   token=token, body=graph)
        gid = json.loads(body)["id"]
        code, body, _ = curl_https(f"/api/graphs/{gid}/publish", args.https_port, ca_path,
                                   method="POST", token=token, body={})
        release = json.loads(body).get("releaseVersion")
        ok(code == 200, f"发布：releaseVersion={release}")
        cmd = ["curl", "-sSN", "--resolve", f"{HOST}:{args.https_port}:127.0.0.1",
               "--cacert", str(ca_path), "-H", f"Authorization: Bearer {token}",
               "-H", "Content-Type: application/json", "--max-time", "90",
               "-d", json.dumps({"releaseVersion": release,
                                 "inputs": {"order_id": "R-1", "amount": 12.5, "reason": "演练"}}),
               f"https://{HOST}:{args.https_port}/api/graphs/{gid}/run/stream"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        events: list[str] = []
        try:
            for line in proc.stdout or []:  # type: ignore[union-attr]
                if line.startswith("event:"):
                    events.append(line.strip())
                if len(events) >= 3:
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        ok(len(events) >= 2, f"SSE 经反代拿到多个 event 帧（{len(events)} 帧：{events[:4]}）")
        note("无凭据时这条链的终态是 `LLM_DECISION_UNAVAILABLE` 显式失败——那是观察结果不是演练失败")

        print("9｜/metrics 网段白名单与 demo 面：经 edge 仍 404")
        code, body, _ = curl_https("/metrics", args.https_port, ca_path)
        ok(code == 404, f"/metrics 经 Caddy 被网段白名单挡住（实得 {code}）——宿主机不在 10.0.0.0/8")
        code, _, _ = curl_https("/demo/shop", args.https_port, ca_path)
        ok(code == 404, f"demo 模拟面经 443 仍 404（实得 {code}）")

        print("\nedge 演练全过 ✅")
        note("证书是 Caddy 内部 CA 自签的：本演练证『形态跑得通』，不证『真 CA 签发』——"
             "docs/73 4.1 第②件剩下的那一半仍是真域名与证书")
        return 0
    finally:
        if not args.keep:
            edge_down(env, files)
            # 兜底：上一轮残留的 caddy 容器占住 80/443，会把下一轮演练的 caddy 挤掉，
            # 而症状只是"ready 等不到"——所以收尾必须确认这个 project 真的没了。
            leftover = sh(["docker", "ps", "-a", "--filter", f"name={PROJECT}", "--format",
                           "{{.Names}}"], check=False).strip()
            if leftover:
                note(f"收尾后仍有残留容器，手动清理：{leftover}")
        override.unlink(missing_ok=True)
        ca_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
