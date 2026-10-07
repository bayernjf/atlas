#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A4 的浏览器冒烟：审批人字段的那句新提示真的渲染到屏幕上了吗（docs/100 U1205）.

跑法（从一个**一次性卷**的独占 compose 项目起的应用）：
    .venv/bin/python scripts/dev/approver_hint_smoke.py http://127.0.0.1:8121

为什么需要它：A4 的判据在 lib 面（vitest）已过，但「label 文案出现在配置抽屉里」是渲染面，
vitest/tsc 都看不到。本脚本用真 Chromium 走一次操作员路径：登录 → 进编辑器 → 选中审批节点 →
断言提示文案可见。
"""

from __future__ import annotations

import sys

import httpx
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8121"
USERNAME = "admin-a"
PASSWORD = "admin123"
# A4 新写的 label（nodeUiSchemas.ts 的 humanApprovalUiSchema.labels.approver）
HINT_NEEDLE = "留空=任何人可决；非空时决策须匹配"

passed: list[str] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    if not ok:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}", flush=True)


def seed_graph_with_approval() -> str:
    """后端里放一张含 human_approval 的图（内置模板），供浏览器选中。"""
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        r = c.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        check("API 登录可用（一次性卷的独占实例）", r.status_code == 200, f"http {r.status_code}")
        token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        t = c.get("/api/templates/approval-timeout-reject", headers=h)
        check("内置模板 approval-timeout-reject 可读", t.status_code == 200, f"http {t.status_code}")
        graph = dict(t.json()["graph"])
        graph["name"] = "smoke-a4-approver"
        apps = [n for n in graph["nodes"] if n["type"] == "human_approval"]
        check("模板里确实有 human_approval 节点", bool(apps), f"{len(apps)} 个")
        node_name = apps[0]["name"]
        created = c.post("/api/graphs", json=graph, headers=h)
        check("图已建（浏览器要用它）", created.status_code == 200,
              f"http {created.status_code} {created.text[:120] if created.status_code >= 400 else ''}")
        return node_name


def main() -> int:
    seed_graph_with_approval()  # 顺带证明模板可读、图可建；渲染断言不依赖它
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE, wait_until="networkidle")

        # 1) 登录（dev 种子账号，全新卷 ⇒ 只有本脚本能用它）
        page.get_by_placeholder(USERNAME).fill(USERNAME)
        page.get_by_placeholder(PASSWORD).fill(PASSWORD)
        page.get_by_role("button", name="登 录").click()
        page.get_by_role("button", name="打开流程编辑器").wait_for(state="visible", timeout=20000)
        check("真浏览器登录成功并回到工作台", True, "工作台按钮可见")

        # 2) 进编辑器
        page.get_by_role("button", name="打开流程编辑器").click()
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        print("---- 编辑器首屏文本（前 600 字，供归因）----")
        print(body[:600].replace("\n\n", "\n"))
        print("---- 以上为诊断输出 ----")
        check("编辑器已加载", "流程" in body or "节点" in body or "Graph" in body, "")

        # 3) 真实操作员路径：从节点面板把「人机协作」拖进画布（NodePanel 用 HTML5 DnD）
        palette = page.locator("[draggable='true']", has_text="人机协作").first
        palette.wait_for(state="visible", timeout=10000)
        check("节点面板里能找到可拖拽的「人机协作」项", True)
        nodes_before = page.locator(".react-flow__node").count()
        canvas = page.locator(".react-flow__pane").first
        box = canvas.bounding_box()
        check("画布面板可定位", box is not None, f"box={box}")
        palette.drag_to(canvas, target_position={"x": int(box["width"] * 0.5),
                                                 "y": int(box["height"] * 0.45)})
        page.wait_for_timeout(2000)
        nodes_after = page.locator(".react-flow__node").count()
        check("拖入后画布多出一个节点", nodes_after == nodes_before + 1,
              f"{nodes_before} → {nodes_after}")

        # 4) 点选刚加入的审批节点，打开属性面板
        page.locator(".react-flow__node").last.click()
        page.wait_for_timeout(1200)

        # 5) 断言 A4 的新提示渲染出来了
        drawer = page.inner_text("body")
        check("审批人字段显示 A4 的新提示文案", HINT_NEEDLE in drawer,
              f"提示片段＝{HINT_NEEDLE[:18]}…")
        page.screenshot(path="docs/screenshots/a4-approver-hint.png", full_page=False)
        print("PASS  截图已存  docs/screenshots/a4-approver-hint.png")
        browser.close()

    print(f"\n全部通过：{len(passed)} 项")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本要把失败原样打出来
        print(f"\nSMOKE 失败：{exc}", file=sys.stderr)
        sys.exit(1)
