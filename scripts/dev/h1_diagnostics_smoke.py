"""打包 H H1 浏览器冒烟：后端编译 422 的 locations 侧车是否真的逐条上屏并可定位。

真实 :8000 + :5174，admin-a。绕开画布连线（docs/46 已记录浏览器内无法合成连线）：
从内置模板载入合法图 → 只改坏属性面板里的一个字段 → 点运行 →
运行前编译 422 → Problems 面板应出现带「后端编译」标记的逐条诊断，
点击应居中节点并闪烁 data-pointer 命中的字段。
"""
import re
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5174"
SHOTS = "docs/assets"
PASSWORD = "admin123"

checks: list[tuple[bool, str]] = []
console_errors: list[str] = []


def check(ok: bool, label: str) -> None:
    checks.append((bool(ok), label))
    print(("  PASS  " if ok else "  FAIL  ") + label)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR {exc}"))

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if page.locator("input").count() >= 2:
            page.locator("input").nth(0).fill("admin-a")
            page.locator("input").nth(1).fill(PASSWORD)
            page.get_by_role("button", name=re.compile(r"登\s*录")).click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="打开流程编辑器").click()
        page.locator(".react-flow").first.wait_for(state="visible", timeout=15000)
        check(True, "编辑器已打开")

        # 从模板新建：approval-timeout-reject 含 human_approval 节点（摘要字段 pointer=/summary）
        page.get_by_role("button", name="从模板新建").click()
        page.wait_for_timeout(1500)
        modal = page.locator('[role="dialog"]')
        check(modal.get_by_text("审批超时默认拒绝演示").count() > 0, "模板弹窗列出超时审批模板")
        # approval-timeout-reject 是 /api/templates 的第 5 项 → 末行按钮
        modal.get_by_role("button", name="使用此模板").last.click()
        page.wait_for_timeout(1800)
        node_count = page.locator(".react-flow__node").count()
        check(node_count >= 2, f"模板已整画布载入（节点 {node_count}）")
        page.screenshot(path=f"{SHOTS}/h1-1-template-loaded.png")

        # 选中审批节点 → 属性面板
        approval = page.locator(".react-flow__node", has_text="人机协作").first
        approval.click()
        page.locator('[data-pointer="/summary"]').first.wait_for(state="visible", timeout=15000)
        check(True, "属性面板渲染了 data-pointer=/summary 锚点")

        # 只改坏 summary（不碰连线）
        page.locator('[data-pointer="/summary"]').first.fill("")
        page.wait_for_timeout(700)

        # 「编译并运行」直达 save→compile，422 即在 catch 落快照
        page.get_by_role("button", name="编译并运行").click()
        page.wait_for_timeout(3000)
        closer = page.get_by_role("button", name=re.compile(r"^\s*(关\s*闭|取\s*消)\s*$"))
        if closer.count() > 0:
            closer.last.click()
            page.wait_for_timeout(500)

        server_rows = page.locator(".problems-item-server")
        rows = page.locator(".problems-item")
        print(f"  · Problems 条目 {rows.count()} 条，其中「后端编译」{server_rows.count()} 条")
        check(server_rows.count() >= 1, "后端编译诊断逐条上屏（带「后端编译」标记）")
        check(
            page.locator(".problems-item-pointer").count() >= 1,
            "逐条诊断展示了 RFC6901 pointer（旧 join 串做不到）",
        )
        body_text = page.locator(".problems-panel").inner_text()
        check("后端编译" in body_text, "标记文案为中文「后端编译」")
        page.screenshot(path=f"{SHOTS}/h1-2-server-diagnostics.png", full_page=True)

        # 点击首个带 pointer 的条目 → 居中 + 字段闪烁
        clicked_pointer = None
        for index in range(rows.count()):
            row = rows.nth(index)
            pointer = row.locator(".problems-item-pointer")
            if pointer.count() > 0 and pointer.first.inner_text():
                clicked_pointer = pointer.first.inner_text()
                row.click()
                break
        if clicked_pointer is None:
            rows.first.click()
        page.wait_for_timeout(200)
        # 闪烁只活 1.6s、且属性面板重渲染会抹掉 class → 轮询等，不用固定 sleep。
        try:
            page.locator(".problems-field-flash").first.wait_for(state="attached", timeout=5000)
            flashing = page.locator(".problems-field-flash").count()
        except Exception:  # noqa: BLE001 - 超时即视为未定位
            flashing = 0
        print(f"  · 点击条目 pointer={clicked_pointer or '(无，图级)'}，闪烁字段 {flashing} 个")
        check(flashing >= 1 if clicked_pointer else True, "点击 pointer 条目后目标字段闪烁定位")
        page.screenshot(path=f"{SHOTS}/h1-3-locate-flash.png", full_page=True)

        # 英文态：同一快照应按语言重渲染（store 存 code/params，渲染期解析）
        account = page.get_by_role("button", name=re.compile(r"账\s*号")).first
        menu = page.locator(".ant-dropdown-menu")
        for _ in range(3):
            # Dropdown 偶发不开（点击竞态），重试而不是拉长固定 sleep。
            account.click()
            try:
                menu.first.wait_for(state="visible", timeout=3000)
                break
            except Exception:  # noqa: BLE001 - 重试
                continue
        menu.get_by_text("English", exact=True).first.click()
        page.wait_for_timeout(1500)
        panel_en = page.locator(".problems-panel").inner_text()
        han = re.findall(r"[一-鿿]", panel_en)
        print(f"  · 英文态 Problems 残留汉字 {len(han)} 个：{sorted(set(han))[:12]}")
        check("Backend compile" in panel_en, "英文态标记渲染为 Backend compile")
        check("Problems" in panel_en, "英文态面板 chrome 已切英文")
        # 残留汉字必须全部来自业务数据（模板节点名），不能是 UI chrome（D13 豁免口径）。
        chrome_han = [w for w in ("问题", "警告", "全图", "点击定位", "后端编译") if w in panel_en]
        check(not chrome_han, f"英文态无中文 chrome 残留（命中 {chrome_han}）")
        page.screenshot(path=f"{SHOTS}/h1-4-english.png", full_page=True)

        noisy = [
            text
            for text in console_errors
            if "favicon" not in text.lower()
            and "deprecated" not in text.lower()
            and "Warning: [antd" not in text
            # 本冒烟刻意触发编译 422，浏览器必然把它记成一条资源错误——非应用错误。
            and "status of 422" not in text
        ]
        check(not noisy, f"控制台零应用错误（{len(noisy)} 条噪声已过滤）")
        for text in noisy[:6]:
            print("    · " + text[:180])

        browser.close()

    failed = [label for ok, label in checks if not ok]
    print(f"\n{'H1_SMOKE_ALL_PASS' if not failed else 'H1_SMOKE_FAILED'}  {len(checks) - len(failed)}/{len(checks)}")
    for label in failed:
        print("  FAILED: " + label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
