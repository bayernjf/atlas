# -*- coding: utf-8 -*-
"""M8 交互模板系统 三渠道冒烟（真实 :8000 + :5174，admin-a）。

阶段 1（API，`--api-only` 可独立运行）：
  登录 → 保存挂卡审批大图（trigger→ai_decision→human_approval[refund-approval]→两侧 tool）
  → 后台触发 run 挂起 → 轮询 /api/runs 取 resumeToken
  → GET /api/cards 目录 → GET /api/approvals/{token}/card?channel=web|im|email 三渠道断言
  → GET 只读无副作用、非法 channel 422、未知 token 404 → approve 收尾。

阶段 2（浏览器，CDP Chrome 9222 + vite :5174）：
  在画布里搭 trigger→ai→human[refund-approval]→tool_call-1/tool_call-2 挂卡图
  （删默认边、手画连线、card-select 选卡、target-select 选流向、tool_call 作合法末端），
  选大额订单 12346「编译并运行」，
  断言审批 Modal 内 CardRenderer 渲染五字段 + 审批意见 + 同意/拒绝退款按钮，
  截图 docs/assets/m8-smoke.png，填意见点「同意退款」后 run completed，控制台零错误。

用法：python scripts/dev/m8_smoke.py [--api-only]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time

import httpx

BASE_API = "http://localhost:8000"
BASE_WEB = "http://localhost:5174"
SHOTS = "docs/assets"
CARD_ID = "refund-approval"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def trigger_node() -> dict:
    return {
        "id": "trigger-1",
        "type": "trigger",
        "name": "新退款申请",
        "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"},
    }


def card_graph(card_id: str | None = CARD_ID) -> dict:
    human_config = {
        "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
        "approver": "客服主管",
        "timeoutSeconds": 600,
        "onTimeout": "reject",
        "approvedTarget": "tool-approve",
        "rejectedTarget": "tool-reject",
    }
    if card_id is not None:
        human_config["cardTemplateId"] = card_id
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            trigger_node(),
            {"id": "ai_decision-1", "type": "ai_decision", "name": "退款决策",
             "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}}"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批", "config": human_config},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "human-1"},
            {"id": "e3", "source": "human-1", "target": "tool-approve"},
            {"id": "e4", "source": "human-1", "target": "tool-reject"},
        ],
    }


# ------------------------------ 阶段 1：API ------------------------------

def run_api_stage(cli: httpx.Client) -> dict:
    print("=== 阶段 1：API 三渠道渲染 ===")
    cli.post("/api/demo/reset")
    login = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    ok(login.status_code == 200, f"admin-a 登录 {login.status_code}")
    cli.headers["Authorization"] = f"Bearer {login.json()['token']}"

    cards = cli.get("/api/cards")
    ok(cards.status_code == 200, f"GET /api/cards {cards.status_code}")
    ids = [c["id"] for c in cards.json()["items"]]
    ok(CARD_ID in ids, f"目录含 {CARD_ID}（ids={ids}）")

    graph_id = cli.post("/api/graphs", json=card_graph()).json()["id"]
    print(f"  保存挂卡大图 graph_id={graph_id}")
    box: dict = {}

    def _bg() -> None:
        box["resp"] = cli.post(
            f"/api/graphs/{graph_id}/run",
            json={"inputs": {"order_id": "C1001", "amount": 5000, "reason": "商品破损"}},
            timeout=300,
        )

    threading.Thread(target=_bg, daemon=True).start()
    run_id = token = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        suspended = cli.get("/api/runs", params={"status": "suspended"}).json()["items"]
        if suspended:
            run_id = suspended[0]["runId"]
            token = suspended[0]["resumeToken"]
            break
        time.sleep(0.05)
    ok(bool(run_id and token), "run 挂起到 suspended 并取到 resumeToken")

    pending = cli.get("/api/approvals").json()["items"]
    mine = next((it for it in pending if it["token"] == token), None)
    ok(mine is not None and mine.get("cardTemplateId") == CARD_ID, "审批列表项带 cardTemplateId")

    web = cli.get(f"/api/approvals/{token}/card").json()
    fields = {row["label"]: row["value"] for row in web["fields"]}
    ok(fields.get("订单号") == "C1001", f"web 订单号={fields.get('订单号')!r}")
    ok("5000" in f"{fields.get('退款金额')}", "web 退款金额已插值")
    ok("商品破损" in f"{fields.get('退款原因')}", "web 退款原因已插值")
    ok("{{" not in f"{fields.get('AI 建议')}", f"web AI 建议已插值={fields.get('AI 建议')!r}")
    ok("500" in f"{fields.get('审批限额')}", "web 审批限额=500")
    ok([f["name"] for f in web["form"]] == ["comment"], "web form 仅 comment")
    actions = {a["id"]: a for a in web["actions"]}
    ok(actions["approve"]["style"] == "primary" and actions["reject"]["style"] == "danger",
       "web actions approve=primary/reject=danger")

    ib = cli.get(f"/api/approvals/{token}/card", params={"channel": "im"}).json()
    ok(token in ib["detailUrl"], "im detailUrl 带 token")
    ok({b["id"] for b in ib["buttons"]} == {"approve", "reject"}, "im 两按钮")
    ok(all(token in b["url"] and "actionId=" in b["url"] for b in ib["buttons"]),
       "im 按钮 URL 带 token 与 actionId（GET 落地页）")
    print("  --- im.text ---")
    for line in ib["text"].splitlines():
        print(f"    | {line}")

    eb = cli.get(f"/api/approvals/{token}/card", params={"channel": "email"}).json()
    ok("退款审批卡片" in eb["subject"], f"email subject={eb['subject']!r}")
    ok("C1001" in eb["html"], "email html 含订单号")
    ok({l["id"] for l in eb["links"]} == {"approve", "reject"}, "email 两链接")
    print(f"  --- email.subject={eb['subject']}；html {len(eb['html'])} 字符 ---")

    ok(any(it["token"] == token for it in cli.get("/api/approvals").json()["items"]),
       "GET 渲染只读：审批仍挂起")
    ok(cli.get(f"/api/approvals/{token}/card", params={"channel": "fax"}).status_code == 422,
       "非法 channel → 422")
    ok(cli.get("/api/approvals/does-not-exist/card").status_code == 404, "未知 token → 404")
    return {"cli": cli, "run_id": run_id, "token": token}


def finalize_api(ctx: dict, action: str = "approve") -> None:
    cli, token, run_id = ctx["cli"], ctx["token"], ctx["run_id"]
    resp = cli.post(f"/api/approvals/{token}/decision", json={"actionId": action})
    print(f"  收尾决策 {action}: {resp.status_code}")
    detail = {}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = cli.get(f"/api/runs/{run_id}").json()
        if detail.get("status") == "completed":
            print(f"  run {run_id} completed")
            return
        time.sleep(0.05)
    print(f"  WARN run 末态={detail.get('status')}")


# ------------------------------ 阶段 2：浏览器 ------------------------------

def vopts(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')).map(o=>(o.innerText||'').trim())""")


def close_pop(page):
    for _ in range(6):
        mv = [i for i in range(page.locator(".ant-modal").count())
              if page.locator(".ant-modal").nth(i).is_visible()]
        pop = page.locator(".ant-popconfirm:visible, .ant-popover:visible").count()
        if not mv and not pop:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)


def connect(page, src, tgt):
    sb, tb = src.bounding_box(), tgt.bounding_box()
    sx, sy = sb["x"] + sb["width"] / 2, sb["y"] + sb["height"] / 2
    tx, ty = tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2
    page.mouse.move(sx, sy)
    page.wait_for_timeout(150)
    page.mouse.down()
    page.wait_for_timeout(120)
    for k in range(1, 13):
        page.mouse.move(sx + (tx - sx) * k / 12, sy + (ty - sy) * k / 12, steps=10)
        page.wait_for_timeout(26)
    page.wait_for_timeout(180)
    page.mouse.up()
    page.wait_for_timeout(650)


def run_browser_stage(ctx: dict) -> None:
    from playwright.sync_api import sync_playwright
    print("=== 阶段 2：浏览器 card-select 配置 + CardRenderer 运行态 ===")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR {e}"))
        net: list[str] = []
        def _on_resp(r):
            if "/api/" in r.url:
                line = f"{r.request.method} {r.url.replace(BASE_WEB, '').replace(BASE_API, '')} -> {r.status}"
                if r.status >= 400 and "stream" not in r.url:
                    try:
                        line += " body=" + r.text()[:400]
                    except Exception:
                        pass
                net.append(line)
        page.on("response", _on_resp)

        page.goto(BASE_WEB, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        lb = page.get_by_role("button", name=re.compile(r"登\s*录"))
        if lb.count() > 0 and page.locator("input").count() >= 2:
            ins = page.locator("input")
            ins.nth(0).fill("admin-a")
            ins.nth(1).fill("admin123")
            lb.first.click()
            page.wait_for_timeout(1000)
        page.get_by_role("button", name="打开流程编辑器").wait_for(timeout=10000)
        page.get_by_role("button", name="打开流程编辑器").click()
        page.wait_for_timeout(1500)

        # 模板 refund-auto 重置画布
        page.get_by_role("button", name="从模板新建").click()
        page.wait_for_timeout(700)
        page.locator(".ant-modal").last.get_by_role("button", name="使用此模板").nth(0).click()
        page.wait_for_timeout(1200)
        close_pop(page)

        pane = page.locator(".react-flow").first
        pb = pane.bounding_box()
        def tp(x, y):
            return {"x": x - pb["x"], "y": y - pb["y"]}

        panel = page.locator(".ant-card").filter(has_text="节点面板")

        # 删除默认 ai->tool_call-1 边（鼠标点边命中区→Backspace），改由 human 分流
        edge_path = page.locator(".react-flow__edge").nth(1).locator(
            ".react-flow__edge-interaction, .react-flow__edge-path").first
        eb = edge_path.bounding_box()
        page.mouse.click(eb["x"] + eb["width"] / 2, eb["y"] + eb["height"] / 2)
        page.wait_for_timeout(300)
        page.keyboard.press("Backspace")
        page.wait_for_timeout(500)
        ok(page.locator(".react-flow__edge").count() == 1,
           f"删除 ai→tool 边后剩 1 边（{page.locator('.react-flow__edge').count()}）")

        # 拖入 human + 第二个 tool_call（tool_call-1=process_refund 保留作通过侧）
        panel.locator("text=人机协作").first.scroll_into_view_if_needed(timeout=5000)
        panel.locator("text=人机协作").first.drag_to(pane, target_position=tp(830, 450), timeout=4000)
        page.wait_for_timeout(400)
        panel.locator("text=工具调用").first.scroll_into_view_if_needed(timeout=5000)
        panel.locator("text=工具调用").first.drag_to(pane, target_position=tp(1060, 580), timeout=4000)
        page.wait_for_timeout(500)

        trigger = page.locator(".react-flow__node").filter(has_text="新退款申请").first
        ai = page.locator(".react-flow__node").filter(has_text="AI 决策").first
        human = page.locator(".react-flow__node").filter(has_text="人机协作").last
        tool_a = page.locator(".react-flow__node").filter(has_text="执行退款或转人工").first
        tool_b = page.locator(".react-flow__node").filter(has_text="工具调用").last

        # Fit View 归一缩放，再把节点拖到固定屏坐标，避免重叠致连线失败
        page.locator('button[aria-label="Fit View"]').first.click()
        page.wait_for_timeout(600)

        def arrange(node, x, y):
            node.drag_to(pane, target_position=tp(x, y), timeout=3000)
            page.wait_for_timeout(250)

        arrange(trigger, 360, 450)
        arrange(ai, 600, 450)
        arrange(human, 850, 450)
        arrange(tool_a, 1090, 330)
        arrange(tool_b, 1090, 590)
        page.wait_for_timeout(400)

        edge_count = page.locator(".react-flow__edge").count()

        def wire(src, tgt, name):
            nonlocal edge_count
            for _ in range(3):
                connect(page, src, tgt)
                now = page.locator(".react-flow__edge").count()
                if now == edge_count + 1:
                    edge_count = now
                    print(f"  连线 {name} 成功（edges={now}）")
                    return
                page.wait_for_timeout(300)
            raise AssertionError(f"连线 {name} 失败 edges={page.locator('.react-flow__edge').count()}")

        wire(ai.locator(".react-flow__handle-right").first,
             human.locator(".react-flow__handle-left").first, "ai->human")
        hrs = human.locator(".react-flow__handle-right")
        wire(hrs.nth(0), tool_a.locator(".react-flow__handle-left").first, "human->toolA")
        wire(hrs.nth(1), tool_b.locator(".react-flow__handle-left").first, "human->toolB")
        ok(page.locator(".react-flow__edge").count() == 4, "手画后 4 边")

        # tool_b 配置 shop/process_refund（tool_a 模板自带）
        tool_b.click()
        page.wait_for_timeout(500)
        tool_sel = page.locator('[data-pointer="/tool"] .ant-select').first
        if tool_sel.count() == 0:
            tool_sel = page.locator("aside").last.locator(".ant-select").first
        tool_sel.click()
        page.wait_for_timeout(600)
        print("  工具选项含 process_refund:",
              any("process_refund" in o for o in vopts(page)))
        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text="process_refund").first.click()
        page.wait_for_timeout(400)
        close_pop(page)

        # 配置 human：summary + 卡片 + 双目标
        human.click()
        page.wait_for_timeout(500)
        summary = page.locator('[data-pointer="/summary"] textarea').first
        summary.fill("订单 {{trigger-1.context.payload.order_id}} 退款审批")
        page.wait_for_timeout(200)
        page.locator('[data-pointer="/approver"] input').first.fill("客服主管")
        page.wait_for_timeout(200)
        card_sel = page.locator('[data-pointer="/cardTemplateId"] .ant-select').first
        card_sel.click()
        page.wait_for_timeout(600)
        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text="退款审批卡片").first.click()
        page.wait_for_timeout(400)
        close_pop(page)
        for ptr, target_id in [("/approvedTarget", "tool_call-1"),
                               ("/rejectedTarget", "tool_call-2")]:
            sel = page.locator(f'[data-pointer="{ptr}"] .ant-select').first
            sel.click()
            page.wait_for_timeout(500)
            page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
                has_text=target_id).first.click()
            page.wait_for_timeout(400)
            close_pop(page)
        page.wait_for_timeout(300)
        page.screenshot(path=f"{SHOTS}/m8-card-config.png")
        print("  配置态截图 docs/assets/m8-card-config.png")

        # 选大额订单 12346（不想要了，¥5000 → 人工）
        order_sel = page.locator(".ant-select").filter(has_text=re.compile(r"1234[5-9]|¥")).first
        order_sel.click()
        page.wait_for_timeout(600)
        order_opts = vopts(page)
        print("  订单下拉:", order_opts)
        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text="12346").first.click()
        page.wait_for_timeout(300)
        close_pop(page)

        # 编译并运行 → 审批 Modal（CardRenderer），轮询并在失败时 dump 现场
        page.get_by_role("button", name="编译并运行").click()
        modal = page.locator(".ant-modal").filter(has_text="人工审批请求")
        shown = False
        for _ in range(25):
            page.wait_for_timeout(1000)
            if modal.count() and modal.first.is_visible():
                shown = True
                break
        if not shown:
            page.screenshot(path=f"{SHOTS}/m8-run-debug.png")
            print("  !! 审批 Modal 未出现，现场快照：")
            for i in range(page.locator(".ant-modal").count()):
                m = page.locator(".ant-modal").nth(i)
                if m.is_visible():
                    print("   MODAL:", m.inner_text()[:500].replace("\n", " | "))
            for sel in [".ant-message-notice", ".ant-notification-notice", ".ant-alert", ".ant-result"]:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 4)):
                    if loc.nth(i).is_visible():
                        print(f"   {sel}:", loc.nth(i).inner_text()[:300].replace("\n", " | "))
            print("   NET:\n    " + "\n    ".join(net[-20:]))
            print("   ERRORS:", json.dumps(errors, ensure_ascii=False))
            raise AssertionError("审批 Modal 未出现（见 docs/assets/m8-run-debug.png）")
        modal.locator(".approval-card-fields").wait_for(timeout=15000)
        page.wait_for_timeout(500)
        ftext = modal.locator(".approval-card-fields").inner_text()
        print("  --- CardRenderer 字段 ---")
        for line in ftext.splitlines():
            if line.strip():
                print(f"    | {line.strip()}")
        for needle in ["订单号", "12346", "退款金额", "5000", "退款原因", "不想要了",
                       "AI 建议", "审批限额", "500"]:
            ok(needle in ftext, f"卡片含 {needle}")
        ok("{{" not in ftext, "卡片无未插值占位符")
        ok(modal.locator("textarea").count() >= 1, "渲染审批意见 textarea")
        ok(modal.get_by_role("button", name="同意退款").count() == 1, "有「同意退款」按钮")
        ok(modal.get_by_role("button", name="拒绝退款").count() == 1, "有「拒绝退款」按钮")
        page.screenshot(path=f"{SHOTS}/m8-smoke.png")
        print("  运行态卡片截图 docs/assets/m8-smoke.png")

        # 填意见 → 同意退款
        modal.locator("textarea").first.fill("同意，凭证齐全")
        page.wait_for_timeout(200)
        modal.get_by_role("button", name="同意退款").click()
        page.wait_for_timeout(1500)
        ok(modal.count() == 0 or not modal.is_visible(), "提交后审批 Modal 关闭")

        # 经同源 API 轮询 run completed
        token = page.evaluate("localStorage.getItem('atlas.session_token')")
        final_status = ""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            data = page.evaluate(
                """async (t) => {
                  const r = await fetch('/api/runs?limit=5', {headers:{Authorization:'Bearer '+t}});
                  return await r.json();
                }""", token)
            items = data.get("items", [])
            if items and items[0].get("status") == "completed":
                final_status = "completed"
                break
            time.sleep(0.5)  # noqa: PLW0104
        ok(final_status == "completed", "决策后续跑 run completed（经 tool_call 末端结束）")

        page.wait_for_timeout(800)
        app_errors = [e for e in errors if "favicon" not in e]
        print("  CONSOLE/PAGE ERRORS:", json.dumps(app_errors, ensure_ascii=False))
        ok(not app_errors, f"浏览器零应用错误（{len(app_errors)}）")
        page.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-only", action="store_true")
    args = ap.parse_args()
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        ctx = run_api_stage(cli)
        if args.api_only:
            finalize_api(ctx)
            print("=== API 阶段通过（--api-only）===")
            return 0
        try:
            run_browser_stage(ctx)
        finally:
            finalize_api(ctx)
    print("=== M8 三渠道冒烟通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
