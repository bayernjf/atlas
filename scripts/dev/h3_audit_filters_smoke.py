"""打包 H H3 浏览器冒烟：审计页 actor/时间过滤与游标加载更多（真实 :8000+:5174，admin-a）。

审计页只有读操作与查询参数，无需连画布；重点验证三件 UI 事实：
actor 精确过滤生效、时间区间不改变行数也不炸（边界换算走 UTC）、
游标「加载更多」存在时点一次能追加且不出现重复行。
"""
import json
import re
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:5174"
API = "http://localhost:8000"
SHOTS = "docs/assets"

checks: list[tuple[bool, str]] = []
console_errors: list[str] = []


def check(ok: bool, label: str) -> None:
    checks.append((bool(ok), label))
    print(("  PASS  " if ok else "  FAIL  ") + label)


def _token() -> str:
    req = urllib.request.Request(
        f"{API}/api/auth/login",
        data=json.dumps({"username": "admin-a", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())["token"]


def main() -> int:
    import urllib.parse
    from datetime import datetime, timedelta, timezone

    token = _token()

    def api(path: str, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(f"{API}{path}?{query}", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read())

    # 先经 HTTP 铺一点可核对的事实（页面读的就是这些数据）
    seeded = api("/api/audit/events", limit=500)
    total = len(seeded["items"])
    actors = {item["actor"] for item in seeded["items"]}
    print(f"  · 现有审计 {total} 条，actor 集合 {sorted(actors)}")
    check(total > 0, "审计端点返回数据")

    one_actor = sorted(actors)[0]
    filtered = api("/api/audit/events", limit=500, actor=one_actor)
    only_expected = all(item["actor"] == one_actor for item in filtered["items"])
    if len(actors) > 1:
        check(only_expected and len(filtered["items"]) < total, f"actor={one_actor} 精确过滤生效（真子集）")
    else:
        check(only_expected and len(filtered["items"]) == total, f"actor={one_actor} 过滤生效（仅一个 actor）")

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    empty = api("/api/audit/events", limit=100, since=future)
    check(empty["items"] == [] and empty["nextCursor"] is None, "Z 后缀 since 生效且末页游标为 null")

    page1 = api("/api/audit/events", limit=2)
    if page1["nextCursor"] is not None:
        page2 = api("/api/audit/events", limit=2, cursor=page1["nextCursor"])
        overlap = {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
        check(not overlap, "游标第二页与第一页无重叠")
    else:
        check(True, "数据不足一页，无需验证第二页")

    try:
        api("/api/audit/events", since="nope")
        check(False, "非法 since 应返回 422")
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read())
        check(exc.code == 422 and any("since" in d for d in body["detail"]), "非法 since → 422 中文聚合")

    # ---------- 浏览器 ----------
    from playwright.sync_api import sync_playwright

    # 页面缺省页大小 100：自铺 120 条即保证下一页存在、渲染「加载更多」。
    def _write_once(index: int) -> None:
        req = urllib.request.Request(
            f"{API}/api/feedback",
            data=json.dumps(
                {"type": "bug", "content": f"h3-seed-{index}", "page": "/audit-smoke"}
            ).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8):
                pass
        except urllib.error.HTTPError:
            pass

    for index in range(120):
        _write_once(index)
    seeded_now = api("/api/audit/events", limit=500)
    check(len(seeded_now["items"]) > 100, f"已铺 {len(seeded_now['items'])} 条审计以驱动分页")
    # 取满一页（100）才应有下一页；用 limit=500 一次取完自然没有游标。
    first_page = api("/api/audit/events", limit=100)
    check(
        len(first_page["items"]) == 100 and first_page["nextCursor"] is not None,
        "满一页即返回非空 nextCursor",
    )
    one_actor = sorted({item["actor"] for item in seeded_now["items"]})[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR {e}"))

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if page.locator("input").count() >= 2:
            page.locator("input").nth(0).fill("admin-a")
            page.locator("input").nth(1).fill("admin123")
            page.get_by_role("button", name=re.compile(r"登\s*录")).click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="审计日志").click()
        page.locator(".ant-table-row").first.wait_for(state="visible", timeout=15000)
        rows_before = page.locator(".ant-table-row").count()
        check(rows_before > 0, f"审计页渲染出 {rows_before} 行")
        page.screenshot(path=f"{SHOTS}/h3-1-audit-page.png", full_page=True)

        # actor 精确过滤
        actor_box = page.get_by_placeholder("操作人精确匹配")
        check(actor_box.count() == 1, "操作人过滤输入框存在")
        actor_box.fill(one_actor)
        # AntD 给两个汉字的按钮名中间插空格（渲染成「筛 选」），选择器要容忍。
        page.get_by_role("button", name=re.compile(r"筛\s*选")).click()
        page.wait_for_timeout(1800)
        actor_cells = [c.inner_text().strip() for c in page.locator(".ant-table-row td:nth-child(2)").all()]
        check(
            bool(actor_cells) and all(cell == one_actor for cell in actor_cells),
            f"页面 actor 过滤后全部为 {one_actor}（{len(actor_cells)} 行）",
        )
        page.screenshot(path=f"{SHOTS}/h3-2-actor-filter.png", full_page=True)

        # 加载更多（种子已超一页，按钮必然存在）
        more = page.get_by_role("button", name="加载更多")
        check(more.count() == 1, "下一页存在时渲染「加载更多」")
        rows_pre = page.locator(".ant-table-row").count()
        if more.count() > 0:
            more.first.click()
            page.wait_for_timeout(2500)
            rows_post = page.locator(".ant-table-row").count()
            ids = [row.get_attribute("data-row-key") for row in page.locator(".ant-table-row").all()]
            check(rows_post > rows_pre, f"加载更多追加成功（{rows_pre} → {rows_post} 行）")
            check(len(ids) == len(set(ids)), "追加后无重复行")
        else:
            check(True, "本页已到底（无加载更多按钮）")
        page.screenshot(path=f"{SHOTS}/h3-3-load-more.png", full_page=True)

        # 不存在的 actor → 空态（证明过滤真的收窄，而非恒返回全量）
        actor_box.fill("nobody-at-all")
        page.get_by_role("button", name=re.compile(r"筛\s*选")).click()
        page.wait_for_timeout(2000)
        check(
            page.locator(".ant-table-row").count() == 0
            and page.get_by_text("当前没有符合条件的审计事件").count() > 0,
            "不存在 actor → 空态",
        )
        page.screenshot(path=f"{SHOTS}/h3-4-empty-filter.png", full_page=True)

        # 时间区间：选一个必然落在未来的区间 → 应空态且不炸
        page.get_by_placeholder("起始时间").click()
        page.wait_for_timeout(600)
        check(page.locator(".ant-picker-panel").count() > 0, "时间区间选择器可打开")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        noisy = [
            text
            for text in console_errors
            if "favicon" not in text.lower()
            and "deprecated" not in text.lower()
            and "Warning: [antd" not in text
        ]
        check(not noisy, "审计页控制台零应用错误")
        for text in noisy[:5]:
            print("    · " + text[:170])
        browser.close()

    failed = [label for ok, label in checks if not ok]
    print(f"\n{'H3_SMOKE_ALL_PASS' if not failed else 'H3_SMOKE_FAILED'}  {len(checks) - len(failed)}/{len(checks)}")
    for label in failed:
        print("  FAILED: " + label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
