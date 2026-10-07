#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""照 TRIAL.md 逐字走一遍三个场景，记录「一个真人第一次用会卡在哪」（docs/18 的种子客户验证首次执行）。

跑法（应用要从一个一次性卷的独占 compose 项目起，别碰共享库）：
    .venv/bin/python scripts/dev/trial_dryrun.py http://127.0.0.1:8123

这不是断言脚本，是**摩擦采集**：试用者卡住时不会抛异常，他只会放弃，所以每一步失败都记成
FINDING 然后继续往下走，最后按 PASS／FINDING 汇总。两个取向值得说明：
- 每个"消失/变化"类断言都先取**运行前的基线**（正控），否则"12345 不见了"可能是表格压根没渲染；
- 界面事实来自实跑探测而不是照文档想象，所以「指南写错」和「产品做错」在这里能分辨。
"""

from __future__ import annotations

import sys
import time

import httpx
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOP = f"{BASE}/demo/shop"
NL_SENTENCE = "收到退款申请后，让 AI 判断退款原因和金额，合理就自动退款，不合理就转人工。"
RUN_WAIT_S = 150

passes: list[str] = []
findings: list[str] = []


def ok(msg: str, evidence: str = "") -> None:
    line = f"PASS    {msg}" + (f"  〔{evidence}〕" if evidence else "")
    passes.append(line)
    print(line, flush=True)


def bad(msg: str, evidence: str = "") -> None:
    line = f"FINDING {msg}" + (f"  〔{evidence}〕" if evidence else "")
    findings.append(line)
    print(line, flush=True)


def head(msg: str) -> None:
    print(f"\n======== {msg}", flush=True)


def say(msg: str) -> None:
    print(f"        {msg}", flush=True)


def visible_modal_text(page) -> str:
    """只取当前**可见**弹窗的正文：antd 关掉的 Modal 可能还留在 DOM 里。"""
    for loc in page.locator(".ant-modal-body").all():
        if loc.is_visible():
            return loc.inner_text()
    return ""


def close_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)


def select_order(page, order_id: str) -> bool:
    """顶部运行区下拉选单——指南 §2A 第 2 步的字面动作。"""
    page.locator(".ant-select").first.click()
    page.wait_for_timeout(400)
    opt = page.locator(".ant-select-item-option", has_text=order_id)
    if opt.count() == 0:
        bad(f"运行区下拉里没有退款单 {order_id}", "下拉打开但无该选项")
        page.keyboard.press("Escape")
        return False
    opt.first.click()
    page.wait_for_timeout(300)
    return True


def run_selected(page) -> dict:
    """点「编译并运行」并等结果弹窗，返回 {status, raw, elapsed, timeout}。"""
    t0 = time.time()
    page.get_by_role("button", name="编译并运行").click()
    while time.time() - t0 < RUN_WAIT_S:
        page.wait_for_timeout(1000)
        text = visible_modal_text(page)
        if "运行状态" in text:
            status = ""
            for ln in text.splitlines():
                if ln.startswith("运行状态"):
                    status = ln.split("：", 1)[-1].strip()
            return {"status": status, "raw": text, "elapsed": time.time() - t0, "timeout": False}
    alerts = [a.inner_text().replace("\n", " ") for a in page.locator(".ant-alert").all() if a.is_visible()]
    return {"status": "", "raw": (alerts[0] if alerts else ""), "elapsed": time.time() - t0, "timeout": True}


def open_shop(page) -> bool:
    """按指南进商家后台并停在表格上；返回是否成功进入。"""
    page.goto(SHOP, wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    if page.locator("#panel").is_visible():
        return True
    btn = page.locator("#loginBox button")
    if btn.count() == 0:
        bad("商家后台页面结构变了：既没有已展开的面板也没有登录框", page.inner_text("body")[:120])
        return False
    btn.first.click()
    page.wait_for_timeout(1500)
    return page.locator("#panel").is_visible()


def shop_pending(page) -> list[str] | None:
    """待处理表格的订单号列。面板没展开时返回 None（区别于"展开但 0 行"）。"""
    if not page.locator("#panel").is_visible():
        return None
    rows = page.locator("#rows tr")
    return [rows.nth(i).locator("td").first.inner_text().strip() for i in range(rows.count())]


def shop_processed(page) -> dict[str, str]:
    """「AI 已处置」表：订单号 → 处置结果文案。这一列是场景 B 的全部看点。"""
    out: dict[str, str] = {}
    rows = page.locator("#done tr")
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        if cells.count() >= 4:
            out[cells.nth(0).inner_text().strip()] = cells.nth(3).inner_text().strip()
    return out


def canvas_signature(page) -> list[str]:
    """画布指纹＝节点文本集合。**不能用节点数**判"草稿载入没有"：
    生成的退款草稿恰好也是 3 个节点，用计数会得到一条假 FINDING（本脚本第一版就是这么错的）。
    连节点标题也不够稳（草稿和示例图都叫"触发/AI 决策/工具"），所以场景 C 用导出的图 JSON 对比。
    """
    return sorted(n.inner_text().replace("\n", "/") for n in page.locator(".react-flow__node").all())


def graph_json(page) -> str:
    """点「导出 Graph JSON」读那份定义——草稿有没有真的载入，只有内容说了算。"""
    page.get_by_role("button", name="导出 Graph JSON").click()
    page.wait_for_timeout(800)
    text = ""
    for loc in page.locator(".ant-modal-body pre").all():
        if loc.is_visible():
            text = loc.inner_text()
            break
    close_modal(page)
    page.wait_for_timeout(500)
    return text


T0 = time.time()


def since_start() -> str:
    return f"自打开浏览器起 {time.time() - T0:.0f}s"


def main() -> int:
    head("指南 §1 启动：docker compose up 之后")
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{BASE}/api/ready")
    ok("应用可达（指南只说看到 Uvicorn 日志就打开浏览器）", f"GET /api/ready → {r.status_code}")

    # 指南 §1 与场景 B 都让试用者敲这条 curl，原样执行看会不会撞墙。
    with httpx.Client(timeout=20.0) as c:
        rr = c.post(f"{BASE}/api/demo/reset")
    if rr.status_code == 200:
        ok("指南 §1 的 curl -X POST /api/demo/reset 可用", f"http {rr.status_code}")
    else:
        bad(f"指南让试用者敲的 curl /api/demo/reset 返回 {rr.status_code}",
            str(rr.text)[:110].replace("\n", " "))

    # 陪同人员版（带平台凭证）——顺带把店铺恢复成 5 笔待处理，让本脚本可重复跑。
    with httpx.Client(timeout=30.0) as c:
        tok = c.post(f"{BASE}/api/auth/login",
                     json={"username": "admin-a", "password": "admin123"}).json().get("token")
        staff = c.post(f"{BASE}/api/demo/reset", headers={"Authorization": f"Bearer {tok}"})
    if staff.status_code == 200:
        ok("带平台凭证的 reset 可用＝指南那条 curl 缺的是凭证、不是功能", f"http {staff.status_code}")
    else:
        bad("带凭证的 reset 也失败了", f"http {staff.status_code} {str(staff.text)[:100]}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1512, "height": 950})

        head("指南 §0/§1：打开 http://localhost:8000")
        t0 = time.time()
        page.goto(BASE, wait_until="networkidle")
        landed = page.inner_text("body")[:70].replace("\n", " / ")
        if page.get_by_placeholder("admin-a").count():
            bad("指南说这一页就是「流程编辑器」，实际先到登录页，而全文没给平台账号密码", f"页首={landed}")
            page.get_by_placeholder("admin-a").fill("admin-a")
            page.get_by_placeholder("admin123").fill("admin123")
            page.locator("button[type=submit]").click()
            page.wait_for_timeout(2500)
            ok("登录页面上印着种子账号，试用者能自己找到（文档没写，页面兜住了）", f"{time.time() - t0:.1f}s")
        else:
            ok("打开根路径直接进编辑器（与指南一致）", f"{time.time() - t0:.1f}s")

        # ---------- 场景 A ----------
        head("场景 A：AI 自动退款（选 12345 → 编译并运行 → 看后台）")
        shop = browser.new_page(viewport={"width": 1280, "height": 900})
        if open_shop(shop):
            before = shop_pending(shop)
            say(f"A-0 运行前待处理＝{before}")
            if before and "12345" in before:
                ok("A-0 后台初始有 5 笔待处理退款单，12345 在列（指南 §0 的承诺，也是下面的正控）",
                   f"{len(before)} 单")
            else:
                bad("A-0 后台待处理列表里没有 12345，场景 A 的前提不成立", f"{before}")
        else:
            bad("A-0 商家后台登不进去（demo/demo）", "")
            before = None

        page.get_by_role("button", name="打开流程编辑器").click()
        page.wait_for_timeout(4000)
        node_n = page.locator(".react-flow__node").count()
        if node_n >= 3:
            ok("A-1 编辑器里已有一条示例流程", f"画布 {node_n} 个节点")
        else:
            bad("A-1 指南承诺的示例流程不在画布上", f"画布只有 {node_n} 个节点")

        result_a = None
        if select_order(page, "12345"):
            result_a = run_selected(page)
            say(f"A-2 耗时 {result_a['elapsed']:.1f}s status={result_a['status'] or '-'} "
                f"raw={result_a['raw'][:90]!r}")
            if result_a["timeout"]:
                bad("A-2 点「编译并运行」后 150s 内没出结果弹窗", result_a["raw"])
            else:
                close_modal(page)
                if result_a["status"] in ("completed", "succeeded", "success"):
                    ok("A-2 12345 跑完", f"运行状态={result_a['status']}")
                else:
                    bad(f"A-2 12345 的运行状态不是完成，而是 {result_a['status']!r}",
                        result_a["raw"][:150].replace("\n", " | "))
                body = result_a["raw"]
                if any(k in body for k in ("判断", "理由", "reason", "decision")):
                    ok("A-3 结果里能看到 AI 的判断内容（指南说「调试台显示判断理由」）", "")
                else:
                    bad("A-3 看不到指南承诺的「AI 判断理由」", body[:130].replace("\n", " | "))
        page.screenshot(path="docs/screenshots/trial-A-run.png", full_page=True)

        # 指南 A-4：刷新商家后台，12345 应已从待处理列表消失。
        if before:
            reloaded_back_to_login = False
            shop.bring_to_front()
            shop.reload(wait_until="domcontentloaded")
            shop.wait_for_timeout(600)
            if not shop.locator("#panel").is_visible():
                reloaded_back_to_login = True
                open_shop(shop)
            after = shop_pending(shop)
            say(f"A-4 刷新后待处理＝{after}")
            if reloaded_back_to_login:
                bad("A-4 指南说「刷新」就能看到列表变化，实际刷新后回到登录框（页面没有刷新按钮）",
                    "要点第二次「登录」才出表格")
            else:
                ok("A-4 刷新后直接停在表格上（不用二次登录）", since_start())
            if after is None:
                bad("A-4 刷新后读不到待处理表格", "")
            elif "12345" not in after:
                ok("A-4 12345 已从待处理列表消失（指南承诺的结果）", f"剩 {len(after)} 单：{after}")
            else:
                bad("A-4 12345 还在待处理列表里", f"{after}")

        # ---------- 场景 B ----------
        head("场景 B：敏感情况转人工（12346）")
        page.bring_to_front()
        result_b = None
        if select_order(page, "12346"):
            result_b = run_selected(page)
            say(f"B-1 耗时 {result_b['elapsed']:.1f}s status={result_b['status'] or '-'} "
                f"raw={result_b['raw'][:90]!r}")
            if result_b["timeout"]:
                bad("B-1 12346 运行 150s 内没出结果", result_b["raw"])
            else:
                close_modal(page)
                raw_b = result_b["raw"]
                # 「转人工」的判据是工具节点的产出，不是运行状态：演示图里只到
                # ai_decision → tool_call，标记 human_review 也算正常跑完（completed）。
                if "human_review" in raw_b:
                    ok("B-1 12346 被判为转人工（工具产出 status=human_review）",
                       f"运行状态={result_b['status']}")
                elif "refunded" in raw_b:
                    bad("B-1 场景 B 的边界没成立：¥5000／不想要了 被 AI 直接退了款",
                        raw_b[:160].replace("\n", " | "))
                else:
                    bad("B-1 结果里读不出 12346 的处理动作", raw_b[:160].replace("\n", " | "))

        if before and "12346" in before:
            shop.bring_to_front()
            shop.reload(wait_until="domcontentloaded")
            shop.wait_for_timeout(1200)
            if not shop.locator("#panel").is_visible():
                open_shop(shop)
            after_b = shop_pending(shop)
            done_b = shop_processed(shop)
            say(f"B-2 后台待处理＝{after_b}／已处置＝{done_b}")
            if after_b is None:
                bad("B-2 读不到后台表格", "")
            elif "12346" in done_b and "人工" in done_b.get("12346", ""):
                ok("B-2 12346 在「AI 已处置」里显式标成转人工（和已退款分得开）", done_b["12346"])
            elif "12346" not in after_b:
                bad("B-2 12346 既不在待处理也不在已处置里，试用者看不到它的下落",
                    f"待处理={after_b} 已处置={done_b}")
            else:
                bad("B-2 12346 的处置结果文案不对", str(done_b.get("12346")))
            shop.screenshot(path="docs/screenshots/trial-B-shop.png", full_page=True)

            # 场景 B 真正要回答的问题：AI 说"这事得找人"之后，有没有人真的被叫到？
            page.bring_to_front()
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(1500)
            queue_btn = page.get_by_role("button", name="审批队列")
            if queue_btn.count() == 0:
                bad("B-3 工作台里找不到「审批队列」入口", "")
            else:
                queue_btn.first.click()
                page.wait_for_timeout(3000)
                qtext = page.inner_text("body")
                if "12346" in qtext:
                    ok("B-3 转人工的单出现在审批队列里（人真的被叫到了）", "")
                elif "当前没有待处理的审批请求" in qtext:
                    bad("B-3 审批队列是空的：「转人工」只是给店铺打了个标记，"
                        "没有任何人收到待办",
                        "指南 §B 问『AI 拿不准就找人，这个边界让你放心吗？』——"
                        "按现在的演示，答案是它只是说了句'要找'")
                else:
                    bad("B-3 审批队列页面读不出结果", qtext[:150].replace("\n", " / "))
                page.screenshot(path="docs/screenshots/trial-B-queue.png", full_page=True)
        shop.close()

        # ---------- 场景 C ----------
        head("场景 C：自然语言生成流程")
        page.bring_to_front()
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)
        editor_btn = page.get_by_role("button", name="打开流程编辑器")
        if editor_btn.count():
            editor_btn.first.click()
            page.wait_for_timeout(4000)
        json_before = graph_json(page)
        nl_btn = page.get_by_role("button", name="自然语言生成")
        if nl_btn.count() == 0:
            bad("C-1 编辑器顶部找不到「自然语言生成」", "")
        else:
            nl_btn.first.click()
            page.wait_for_timeout(1200)
            area = page.locator(".ant-modal textarea")
            if area.count() == 0:
                bad("C-1 弹窗打开了但没有输入框", visible_modal_text(page)[:120])
            else:
                say(f"C-2 输入框预填：{area.first.input_value()[:40]!r}")
                area.first.fill(NL_SENTENCE)
                gen = page.get_by_role("button", name="生成并载入画布")
                if gen.count() == 0:
                    bad("C-2 弹窗里没有「生成并载入画布」按钮", visible_modal_text(page)[:120])
                else:
                    t1 = time.time()
                    gen.first.click()
                    nl_error = ""
                    while time.time() - t1 < 150:
                        page.wait_for_timeout(1500)
                        for loc in page.locator(".ant-modal .ant-alert").all():
                            if loc.is_visible():
                                nl_error = loc.inner_text().replace("\n", " ")[:160]
                        if graph_json(page) != json_before:
                            break
                        if not visible_modal_text(page) and not nl_error:
                            break
                    close_modal(page)
                    page.wait_for_timeout(1000)
                    json_after = graph_json(page)
                    sig_after = canvas_signature(page)
                    took = time.time() - t1
                    if json_after and json_after != json_before:
                        ok("C-2 草稿自动载入画布（指南 §C 第 2 步，按导出的图 JSON 对比）",
                           f"{took:.1f}s，{len(json_before)}B → {len(json_after)}B，"
                           f"节点标题={[s.split('/')[0] for s in sig_after]}")
                        page.screenshot(path="docs/screenshots/trial-C-draft.png", full_page=True)
                        # 指南 §C 第 3 步：改完点「编译并运行」，选一单试试。
                        if select_order(page, "12347"):
                            c = run_selected(page)
                            close_modal(page)
                            if c["timeout"]:
                                bad("C-3 自然语言生成的草稿点了运行但没出结果", c["raw"])
                            else:
                                ok("C-3 生成的草稿能直接编译并运行",
                                   f"运行状态={c['status']}，耗时 {c['elapsed']:.1f}s")
                    else:
                        bad("C-2 生成后画布上的图定义没变（指南承诺自动载入）",
                            f"等待 {took:.0f}s，弹窗错误={nl_error!r}，"
                            f"弹窗文本={visible_modal_text(page)[:130]!r}")
                        page.screenshot(path="docs/screenshots/trial-C-fail.png", full_page=True)

        browser.close()

    head("试用者视角汇总")
    for line in passes:
        print(line)
    for line in findings:
        print(line)
    print(f"\nPASS {len(passes)} 条／FINDING {len(findings)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
