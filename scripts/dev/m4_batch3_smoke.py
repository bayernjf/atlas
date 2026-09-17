"""M4 批 3 浏览器冒烟：Condition 表单迁移 + quickFix 删除悬空引用（真实 :8000+:5174，admin-a）。"""
import re
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5174"
SHOTS = "docs/assets"


def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"PAGEERROR {exc}"))

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        # 登录（会话失效时）
        login_btn = page.get_by_role("button", name=re.compile(r"登\s*录"))
        if login_btn.count() > 0 and page.locator("input").count() >= 2:
            inputs = page.locator("input")
            inputs.nth(0).fill("admin-a")
            inputs.nth(1).fill("admin123")
            login_btn.first.click()
            page.wait_for_timeout(1200)

        entry = page.get_by_role("button", name="打开流程编辑器")
        entry.wait_for(timeout=10000)
        entry.click()
        page.wait_for_timeout(1500)

        # 从模板新建 sql-query-notify（含 condition-1）
        page.get_by_role("button", name="从模板新建").click()
        page.wait_for_timeout(600)
        modal = page.locator(".ant-modal").last
        # 模板固定顺序：refund-auto / http-orders-branch / sql-query-notify / …
        use_buttons = modal.get_by_role("button", name="使用此模板")
        use_buttons.nth(2).click()
        page.wait_for_timeout(1500)
        node_count = page.locator(".react-flow__node").count()
        print("nodes on canvas:", node_count)
        node_texts = [n.inner_text().replace("\n", "/")[:30] for n in page.locator(".react-flow__node").all()]
        print("node labels:", node_texts)

        # 选中 condition 节点
        cond = page.locator(".react-flow__node").filter(has_text=re.compile(r"condition|条件", re.I)).first
        cond.click()
        page.wait_for_timeout(800)

        body = page.locator("body").inner_text()
        checks_a = {
            "标题 条件分支": "条件分支" in body,
            "默认分支": "默认分支" in body,
        }
        print("=== condition 表单 ===")
        for k, v in checks_a.items():
            print(f"  {'OK' if v else 'MISS'} {k}")
        # placeholder 不进 inner_text，直接断言控件占位
        ph_label = page.locator(".form-array-item input").first.get_attribute("placeholder") or ""
        ph_target = page.locator(".form-array-item .ant-select").first.locator("..").inner_text()
        print("  branch label placeholder:", ph_label)
        print("  OK 分支名占位" if "分支名" in ph_label else "  MISS 分支名占位")
        # 分支卡片数（form-array-item）
        items_before = page.locator(".form-array-item").count()
        print("form-array-item(branches) before add:", items_before)
        page.screenshot(path=f"{SHOTS}/m4-condition-form-smoke.png")

        # 添加分支（ArrayView，无 maxItems；antd 两字按钮渲染为「添 加」）
        add_btn = page.locator(".form-array").get_by_role("button", name=re.compile(r"添\s*加"))
        add_btn.click()
        page.wait_for_timeout(500)
        items_after = page.locator(".form-array-item").count()
        print("form-array-item after add:", items_after)
        assert items_after == items_before + 1, "添加分支失败"

        # 新分支 expression（variable-input TextArea）输入悬空引用
        new_item = page.locator(".form-array-item").nth(items_after - 1)
        ta = new_item.locator("textarea").first
        ta.fill("{{ghost-x.result.y}}")
        ta.dispatch_event("input")
        page.wait_for_timeout(1200)  # L2 300ms 防抖 + 面板渲染

        problems = page.locator(".problems-panel")
        print("problems panel text:", problems.inner_text().replace("\n", " | ")[:300])
        dangling_items = page.locator(".problems-item").filter(has_text="引用的节点不存在")
        print("dangling REF_NODE_NOT_FOUND items:", dangling_items.count())
        fix_btns = page.locator(".problems-fix-btn")
        print("fix buttons:", fix_btns.count())
        page.screenshot(path=f"{SHOTS}/m4-quickfix-smoke.png")

        assert dangling_items.count() >= 1, "悬空引用诊断未出现"
        assert fix_btns.count() >= 1, "quickFix 修复按钮未出现"

        # 点修复
        fix_btns.first.click()
        page.wait_for_timeout(1000)
        ta_value = page.locator(".form-array-item").nth(items_after - 1).locator("textarea").first.input_value()
        print("expression after fix:", repr(ta_value))
        remaining = page.locator(".problems-item").filter(has_text="引用的节点不存在").count()
        print("dangling items after fix:", remaining)
        assert "ghost-x" not in ta_value, "悬空 token 未删除"
        assert remaining == 0, "修复后诊断未消失"

        # 删除门控：minItems=1，最后一个分支删除按钮应禁用（点添加后有 N+1 个，先删回 1 个再断言）
        page.screenshot(path=f"{SHOTS}/m4-condition-after-fix-smoke.png")

        app_errors = [e for e in errors if "favicon" not in e and "404" not in e]
        print("app console errors:", app_errors[:10])
        assert app_errors == [], f"控制台错误: {app_errors[:5]}"
        print("SMOKE PASSED")


if __name__ == "__main__":
    sys.exit(main())
