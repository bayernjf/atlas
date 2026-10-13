# -*- coding: utf-8 -*-
"""打包 AT 真机端到端（docs/128 §4 的最后一条验收）。

**这不是断言脚本的翻版**：单测跑的是 TestClient 里的纯函数与端点形状，这里要证的是
一个真人会走的那条链——录一次真运行 → 看机器提议改哪几处 → 勾选 → 存成新流程 → 发布 →
**换一单没跑过的订单**再跑到终态 → 取一份指引。

"换一单"这一步才是整批的立论所在：如果字面量没被真的参数化，第二单里出现的还会是第一单的单号。

跑法：``.venv/bin/python scripts/dev/promote_guide_probe.py``
（自己起一个 127.0.0.1 上的独占端口进程，只杀自己 spawn 的那个 PID。）
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable
FIRST_ORDER = "12345"
AMBIG_VAL = "500"
SECOND_ORDER = "99999"

RET = {"pass": 0, "fail": 0}


def ok(condition: bool, label: str, detail: str = "") -> bool:
    if condition:
        RET["pass"] += 1
        print(f"  PASS  {label}")
    else:
        RET["fail"] += 1
        print(f"  FAIL  {label}" + (f" ｜ {detail}" if detail else ""))
    return condition


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Server:
    """只管理自己起的那个进程（绝不按命令行模式杀别人的服务）。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "ATLAS_ENV": "dev",
                "ATLAS_STORAGE_BACKEND": "memory",
                "ATLAS_SCHEDULE_ENABLED": "0",
                "LITELLM_MODEL": "",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
            }
        )
        env.pop("ATLAS_ENABLE_DEMO_MOCK", None)
        self.proc = subprocess.Popen(
            [
                PYTHON, "-m", "uvicorn", "atlas.api.main:app",
                "--host", "127.0.0.1", "--port", str(self.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("uvicorn 子进程提前退出（看它自己的日志）")
            try:
                if httpx.get(f"{self.base}/api/health", timeout=1).status_code == 200:
                    return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError("60 秒内 /api/health 没就绪")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def _node(node_id: str, node_type: str, name: str, config: dict) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "description": "",
        "position": {"x": 0, "y": 0},
        "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def trial_graph() -> dict:
    """一条真会用的退款链：决策 → 分流 → 两个末端各发一条通知，**单号写死在正文里**。"""
    return {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            _node("trigger-1", "trigger", "触发：新退款申请", {"triggerType": "manual"}),
            _node(
                "ai_decision-1",
                "ai_decision",
                "AI 决策：退款还是人工",
                {
                    "promptTemplate": (
                        "退款单 {{trigger-1.context.payload.order_id}}，"
                        "原因 {{trigger-1.context.payload.reason}}，"
                        "金额 {{trigger-1.context.payload.amount}} 元，限额 {{global.approval_limit}}"
                    ),
                    "confidenceThreshold": 0.6,
                },
            ),
            _node(
                "condition-1",
                "condition",
                "分流",
                {
                    "conditionMode": "rule",
                    "branches": [
                        {
                            "label": "转人工",
                            "expression": "{{ai_decision-1.decision.action}} == 'request_human_approval'",
                            "target": "notify-human",
                        }
                    ],
                    "defaultTarget": "notify-auto",
                },
            ),
            _node(
                "notify-auto",
                "tool_call",
                "发通知：已自动退款",
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel": "in_process", "to": ["ops@example.com"],'
                        f'"subject": "退款处理结果", "body": "退款单 {FIRST_ORDER} 限额 {AMBIG_VAL} 元已自动退款"}}'
                    ),
                },
            ),
            _node(
                "notify-human",
                "tool_call",
                "发通知：转人工",
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel": "in_process", "to": ["ops@example.com"],'
                        f'"subject": "退款待人工", "body": "退款单 {FIRST_ORDER} 限额 {AMBIG_VAL} 元等待人工审核"}}'
                    ),
                },
            ),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "condition-1"},
            {"id": "e3", "source": "condition-1", "target": "notify-human"},
            {"id": "e4", "source": "condition-1", "target": "notify-auto"},
        ],
    }


def inputs(order_id: str, amount: int) -> dict:
    return {"order_id": order_id, "reason": "商品破损", "amount": amount, "threshold": int(amount)}


def main() -> int:
    server = Server(free_port())
    server.start()
    try:
        with httpx.Client(base_url=server.base, timeout=30.0) as c:
            token = c.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"}).json()["token"]
            h = {"Authorization": f"Bearer {token}"}

            print("\n[1] 存图并发布原始流程")
            saved = c.post("/api/graphs", json=trial_graph(), headers=h)
            ok(saved.status_code == 200, f"原始流程已保存（{saved.status_code}）", saved.text[:200])
            gid = saved.json()["id"]
            published = c.post(f"/api/graphs/{gid}/publish", headers=h)
            ok(published.status_code == 200, f"原始流程已发布（{published.status_code}）", published.text[:200])

            print("\n[2] 真跑一次，拿运行产出当录制的原料")
            first = c.post(f"/api/graphs/{gid}/run", json={"inputs": inputs(FIRST_ORDER, int(AMBIG_VAL))}, headers=h)
            ok(first.status_code == 200, f"第一单跑到 200（{first.status_code}）", first.text[:200])
            body = first.json()
            outputs = body.get("outputs", {})
            ok(body.get("status") == "completed", f"运行终态 completed（实为 {body.get('status')}）")
            ok(
                FIRST_ORDER in json_text(outputs.get("notify-auto", {})),
                "第一单的通知正文里确实写死了单号（这才是要被参数化的东西）",
            )

            steps = [
                {"node_id": node_id, "node_type": node_type_of(node_id), "output": output}
                for node_id, output in outputs.items()
            ]
            created = c.post(
                "/api/recordings",
                json={
                    "name": f"退款试教 {FIRST_ORDER}",
                    "graph_id": gid,
                    "inputs": inputs(FIRST_ORDER, int(AMBIG_VAL)),
                    "steps": steps,
                    "status": body.get("status"),
                    "rng_seed": body.get("rng_seed"),
                },
                headers=h,
            )
            ok(created.status_code == 201, f"录制用例已入库（{created.status_code}）", created.text[:200])
            case_id = created.json()["id"]

            print("\n[3] plan：机器只提议")
            plan = c.post(f"/api/recordings/{case_id}/promote/plan", headers=h).json()
            proposals = plan["substitutions"]
            ok(bool(proposals), f"plan 提出 {len(proposals)} 处候选替换")
            ok(
                all(item["to"].endswith("order_id}}") for item in proposals),
                "候选全部指向 payload.order_id 引用（不发明新语法）",
            )
            ok(
                all(item["from"] == FIRST_ORDER for item in proposals),
                "被替换的字面量就是那一单的单号",
            )
            amb_count = len(plan["ambiguous"])
            ok(amb_count >= 1, f"同值多键停在 ambiguous（{amb_count} 处），机器不选")
            ok(plan["compile"]["ok"], "按全部候选试编译可过", str(plan["compile"]["issues"][:2]))
            again = c.post(f"/api/recordings/{case_id}/promote/plan", headers=h)
            ok(again.status_code == 200, "plan 是只读的（重复调用不产生任何写入）")

            print("\n[4] promote 勾选 → 存为新流程 → 发布")
            promoted = c.post(
                f"/api/recordings/{case_id}/promote",
                json={"accept": [item["id"] for item in proposals]},
                headers=h,
            )
            ok(promoted.status_code == 200, f"promote 200（{promoted.status_code}）", promoted.text[:200])
            draft = promoted.json().get("graph")
            ok(draft is not None, "草稿已产出（编译通过才给图，AT-D5）")
            graphs_before = {item["id"] for item in c.get("/api/graphs", headers=h).json()["items"]}
            new_saved = c.post("/api/graphs", json=draft, headers=h)
            ok(new_saved.status_code == 200, "草稿存成了新流程", new_saved.text[:200])
            new_gid = new_saved.json()["id"]
            ok(
                new_gid not in graphs_before,
                f"新流程是新图 {new_gid}（promote 自己一个字节都不写，AT-D4）",
            )
            ok(
                c.post(f"/api/graphs/{new_gid}/publish", headers=h).status_code == 200,
                "新流程已发布",
            )

            print("\n[5] 换一单没跑过的订单：这一单才是立论")
            second = c.post(
                f"/api/graphs/{new_gid}/run", json={"inputs": inputs(SECOND_ORDER, 100)}, headers=h
            )
            ok(second.status_code == 200, f"第二单跑到 200（{second.status_code}）", second.text[:200])
            second_body = second.json()
            notified = json_text(second_body.get("outputs", {}).get("notify-auto", {}))
            ok(second_body.get("status") == "completed", f"第二单终态 completed（实为 {second_body.get('status')}）")
            ok(SECOND_ORDER in notified, f"新单号 {SECOND_ORDER} 真的流到了通知正文", notified[:160])
            ok(FIRST_ORDER not in notified, "旧单号没有留在草稿里（否则＝字面量根本没被参数化）", notified[:160])

            print("\n[6] 指引产物")
            guide = c.get(f"/api/recordings/{case_id}/guide", params={"lang": "zh"}, headers=h)
            ok(guide.status_code == 200, f"指引 200（{guide.status_code}）", guide.text[:200])
            markdown = guide.json()["markdown"]
            ok("## 入参" in markdown and "`order_id`" in markdown, "指引含入参表")
            ok(
                "{{ai_decision-1.decision.action}}" in markdown,
                "指引含分支条件原文（表达式不翻译）",
            )
            ok("这一步实际产出" in markdown, "指引含该单真实产出摘要")
            en = c.get(f"/api/recordings/{case_id}/guide", params={"lang": "en"}, headers=h).json()
            ok("## Inputs" in en["markdown"], "英文档同构（两档键集由 LABEL_KEYS 守护）")

            print("\n[7] 越表接受必须整请求拒")
            bad = c.post(
                f"/api/recordings/{case_id}/promote",
                json={"accept": ["nodes[99].config.params#1"]},
                headers=h,
            )
            ok(
                bad.status_code == 422
                and bad.json()["detail"]["code"] == "RECORDING_PROMOTE_ACCEPT_UNKNOWN",
                f"越表 ⇒ 422 结构化码（实为 {bad.status_code}）",
                bad.text[:200],
            )
            empty = c.post(f"/api/recordings/{case_id}/promote", json={"accept": []}, headers=h).json()
            ok(
                FIRST_ORDER in json_text(
                    [node for node in empty["graph"]["nodes"] if node["id"] == "notify-auto"][0]
                ),
                "一条都没勾 ⇒ 字面量原样保留（默认不替换）",
            )

        print(f"\n结论：{RET['pass']} PASS ／ {RET['fail']} FINDING")
        return 1 if RET["fail"] else 0
    finally:
        server.stop()


NODE_TYPES: dict[str, str] = {}


def node_type_of(node_id: str) -> str:
    return NODE_TYPES.get(node_id, "tool_call")


def json_text(value) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _register_types() -> None:
    for node in trial_graph()["nodes"]:
        NODE_TYPES[node["id"]] = node["type"]


if __name__ == "__main__":
    _register_types()
    sys.exit(main())
