"""D14 condition LLM 语义分支 v1 HTTP smoke against live :8000 (docs/48 §8).

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/llm_condition_smoke.py
"""

from __future__ import annotations

import copy

import httpx

BASE = "http://127.0.0.1:8000"

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


def graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "cond-1", "type": "condition", "name": "分流",
             "position": {"x": 2, "y": 0},
             "config": {
                 "conditionMode": "llm",
                 "classifierPrompt": "优先保护客户体验",
                 "branches": [
                     {"label": "愤怒投诉",
                      "description": "客户表达强烈不满或威胁升级",
                      "target": "tool-a"},
                     {"label": "普通咨询",
                      "description": "客户语气平和地询问进度",
                      "target": "tool-b"},
                 ],
                 "defaultTarget": "tool-default",
             }},
            {"id": "tool-a", "type": "tool_call", "name": "A",
             "config": {"tool": "op-a"}},
            {"id": "tool-b", "type": "tool_call", "name": "B",
             "config": {"tool": "op-b"}},
            {"id": "tool-default", "type": "tool_call", "name": "默认",
             "config": {"tool": "op-default"}},
        ],
        "edges": [
            {"id": "e0", "source": "trigger-1", "target": "cond-1"},
            {"id": "e1", "source": "cond-1", "target": "tool-a"},
            {"id": "e2", "source": "cond-1", "target": "tool-b"},
            {"id": "e3", "source": "cond-1", "target": "tool-default"},
        ],
    }


def rule_graph() -> dict:
    payload = graph()
    config = payload["nodes"][1]["config"]
    config.pop("classifierPrompt")
    config["conditionMode"] = "rule"
    config["branches"] = [
        {"label": "大额", "expression": "{{global.amount}} > 100",
         "target": "tool-a"},
        {"label": "小额", "expression": "{{global.amount}} <= 100",
         "target": "tool-b"},
    ]
    payload["variables"] = [
        {"name": "amount", "type": "number", "value": "0", "scope": "global"}
    ]
    return payload


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)
    login = client.post("/api/auth/login",
                        json={"username": "admin-a", "password": "admin123"})
    check("login admin-a", login.status_code == 200)
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    client.post("/api/demo/reset")

    def create(payload: dict) -> httpx.Response:
        return client.post("/api/graphs", json=payload)

    # ① offline classifier (no LITELLM_MODEL on server) → defaultTarget, run completed
    resp = create(graph())
    check("① save llm graph", resp.status_code == 200, resp.text)
    gid = resp.json()["id"]
    run = client.post(f"/api/graphs/{gid}/run", json={})
    body = run.json()
    out = body["outputs"]["cond-1"]
    check("① offline fail-safe routes defaultTarget",
          run.status_code == 200
          and body["status"] == "completed"
          and out["mode"] == "llm"
          and out["branch"] == "__default__"
          and out["target"] == "tool-default"
          and "tool-default" in body["outputs"]
          and "tool-a" not in body["outputs"]
          and "LLM 未配置" in out["llm_errors"][0],
          str({"branch": out["branch"], "target": out["target"],
               "llm_errors": out["llm_errors"]}))
    check("① evaluation items carry labels/descriptions",
          [item["label"] for item in out["evaluation"]] == ["愤怒投诉", "普通咨询"]
          and all("description" in item for item in out["evaluation"]))

    # ② old rule graph zero regression
    resp = create(rule_graph())
    check("② save rule graph", resp.status_code == 200, resp.text)
    run = client.post(f"/api/graphs/{resp.json()['id']}/run",
                      json={"inputs": {"amount": 200}})
    out = run.json()["outputs"]["cond-1"]
    check("② rule mode routes expression target",
          run.status_code == 200 and out["branch"] == "大额"
          and out["target"] == "tool-a" and "mode" not in out,
          str(out))

    # ③ DSL 422 cases
    bad_cases = [
        ("expression forbidden", lambda g: _branch(g, 0).__setitem__("expression", "true")),
        ("description empty", lambda g: _branch(g, 0).__setitem__("description", "")),
        ("description overlong", lambda g: _branch(g, 1).__setitem__("description", "描" * 301)),
        ("classifierPrompt overlong", lambda g: g["nodes"][1]["config"].__setitem__("classifierPrompt", "要" * 501)),
        ("conditionMode invalid", lambda g: g["nodes"][1]["config"].__setitem__("conditionMode", "semantic")),
    ]
    for label, mutate in bad_cases:
        payload = graph()
        mutate(payload)
        resp = create(payload)
        check(f"③ 422 {label}", resp.status_code == 422, f"http={resp.status_code} {resp.text[:120]}")

    print(f"\nALL {len(passed)} SMOKE CHECKS PASSED")


def _branch(payload: dict, index: int) -> dict:
    return payload["nodes"][1]["config"]["branches"][index]


if __name__ == "__main__":
    main()
