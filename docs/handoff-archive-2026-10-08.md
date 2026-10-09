# Handoff Archive — 2026-10-08

> 2026-10-09 从 handoff.md「Recently shipped」滚出（保持最近 5 条上限；逐字搬移、原文不改写）。

## 打包 BM 落码收口＝docs/18 首次被执行（照 TRIAL.md 逐字干跑）

✅ **打包 BM＝docs/18 第一次被执行：照 TRIAL.md 逐字跑一遍试用（2026-10-08，承接用户「该产品做多久了，怎么还没把产品原型做出来」＋「那你继续搞」；执行记录权威 docs/18 §6.2；docs/13 打包 BM 小节 U1206–U1212；docs/14 新增 D57/D58/D59；后端 2443/166/0、PG 集成 99/1/0；未 push）**：一次性卷独占 compose 项目（空库首启）＋真 Chromium＋真 LLM 走完三场景，采集器 `scripts/dev/trial_dryrun.py`（**不是断言脚本**：每步失败记 FINDING 继续走，真人卡住时不抛异常、他只是放弃）→ 终局 **13 PASS／3 FINDING**、"打开浏览器到 12345 从待处理消失"19 秒。改掉的：指南三条不实（没给平台登录账号／匿名 `curl /api/demo/reset` 实测 401 而 docs/12 本就写着 admin only＝**指南错**／"数据在内存里重启即清空"与 compose 写死 `pg`＋named volume 冲突）＋**演示后台把「AI 已退款」和「AI 转人工」分开显示**（旧响应只有 pending，两种结果都表现为"单子消失"，场景 B 要演示的边界在页面上不存在；`processed` 段 additive、`orders` 逐键不变，U1206 带正控）＋刷新不再掉回登录框。**跑出来的事故**：模型配了但端点不可达时首次运行抛 `_DeadlockError`（litellm **进程内首次导入落在 worker 线程**，与事件循环线程的日志过滤器互相等模块锁），此后**整个进程不再响应任何请求**（含未鉴权 `/api/health`，CPU 0.37%＝阻塞非空转）→ lifespan `warm_litellm()` 开始服务前导完（环境还原按键集合差，保住 conftest 那条防线；导不起来只 warning 不炸启动），**同臂对照实跑**修前 10/10 超时、修后 2.8s 普通供应商错误＋10/10 通过。**度量自省**：第一版 6 条 FINDING 里 2 条是脚本自己的错（拿运行状态判场景 B、拿**节点数**判草稿载入——草稿恰好也是 3 个同名节点），改比对导出图 JSON 字节才拿到真结论。**只登记不擅自动**：演示图接真「人机协作」节点＝产品方向（D57，指南已明说"演示到此为止"）、自助重置按钮需新能力位端点（D59）、SSE error 不带 code 且第三方库内部字符串进客户界面（D58）。零迁移、零新依赖、无新 ADR；镜像构建自干净工作区 `9d310e9`。详见 Active #129。

## 打包 A4 落码收口

✅ **打包 A4 落码收口＝D20 动态审批人与审批指派校验（2026-10-08，五原子 `d2d5e2a`→`7ebd2c9`；docs/100 §7 注记；后端 2436/166/0、前端 820/2、oxlint 0/0、build 过；A 档四打包 A1–A4 全部收口）**：approver `{{变量}}` 插值两翼（编译 422 `APPROVER_REF_UNRESOLVED`＋运行期残留→空串＋告警）＋决策端点指派校验（`user:<username>` 匹配 principal、邮箱恒 403 因 iam 无 email、空 approver 任何人可决）＋前端表单提示与错误码 i18n（U1205）；两处偏差照实见 docs/100 §7；零迁移、无 ADR。〔**2026-10-08 接力追加**：本批自报缺的**浏览器冒烟已补**——一次性卷的独占 compose 项目＋真 Chromium，拖入「人机协作」后节点数 3→4、属性面板渲染出 A4 新提示，10 项全 PASS；脚本 `scripts/dev/approver_hint_smoke.py`＋截图 `docs/screenshots/a4-approver-hint.png`，证据细节见 docs/100 §7 追加段〕

✅ **打包 BM 缺陷批落码收口＝D57/D58/D59 三缺陷同日闭合（2026-10-08；docs/101 §7 注记；docs/13 U1213–U1217；docs/14 D57/D58/D59 翻 ✅）**：D57 refund-auto 模板 7 节点真转人工（condition 分流＋human_approval-1 真挂起＋execute_refund/reject_refund 双出口，U1214 断言 resolve 前线程不结束）；D58 未知异常 message 收口四处（SSE/同步/续跑/trigger）；D59 health 能力位＋前端 DemoResetButton＋i18n 3 键。定向 74 passed＋前端 103 passed、build 过；零新依赖／零迁移／无 ADR。详见 Active #129。
