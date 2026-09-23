# 循环 foreach 批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：04 §5.3 的 loop 节点 v1 只落了条件循环 while（break/continue 已随 D17 补齐），但**遍历循环 foreach** 仍是空白——批量处理订单、逐用户发通知、逐项退款等真实业务的第一写法都是「对数组里每一项执行同一子流程」，当前只能用 while + 手工 index 计数 + 手工数组下标模拟，且没有结果聚合。本批取回 **D16 主体**：数组逐项 + item 变量 + 结果聚合，复用现有 loop 重入执行器与回边模型。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准（04 §5.3 同步追加 foreach 契约段）。**零新依赖；不新增 ADR**。
>
> **边界**：foreach 与 while 同属一个 loop 节点、由 `mode` 切换；v1 数组在**首次重入时求值一次并冻结**（运行期长度不变），长度上限沿用 `MAX_LOOP_ITERATIONS = 100`；不做嵌套循环（既有拓扑约束：体内不得含其他 loop 节点，维持）。

## 1. 范围

### A. DSL 配置与校验（`src/atlas/graph/dsl.py` `_validate_loop_config`）

- `mode` 取值集：`"while" | "foreach"`；缺省仍按 `"while"`。
- foreach 配置形状：

  ```yaml
  type: loop
  config:
    mode: "foreach"
    itemsExpression: string        # 表达式（§5.1 白名单语法），求值结果须为数组
    itemName: "item"               # 可选，标识符 ^[a-zA-Z_][a-zA-Z0-9_]*$，默认 item
    collectTarget: node_id | ""    # 可选；结果聚合的来源节点（见 C），空串＝不聚合
    bodyTarget: node_id            # 同 while：循环体入口
    exitTarget: node_id            # 同 while：退出目标
  ```

- 校验规则：
  - `itemsExpression` 非空且**语法可解析**（`conditions.parse`；只做语法校验，不要求顶层布尔——foreach 的顶层是数组）；
  - `itemName` 缺省 `"item"`；非标识符 → 422，pointer `/itemName`；
  - `collectTarget` 可空；非空时必须存在、≠自身、∈循环体节点集合；
  - `bodyTarget`/`exitTarget` 的既有规则（必填、存在、非自环、互异、恰好两条出边、回边白名单、体内无 trigger/其他 loop）全部沿用；
  - foreach **不使用** `continueExpression`/`maxIterations`：两字段缺失/为空不报错（前端切换 mode 时隐藏）；既有 while 规则仅在 mode=while 时生效。
- break 语义（D17）对 foreach 同样适用：体内 condition 直连 exitTarget → 编译期 retarget 到 `__break__<loop-id>`，exitReason=`break`。

### B. 执行（`src/atlas/graph/loader.py` `_execute_loop`）

- 首次进入（自身无产出）时对 `itemsExpression` **求值一次**，结果写入自身产出 `items` 并整轮冻结；后续重入直接读缓存，不重新求值（避免数组在循环中途变化导致跳项/重复）。
- 路由（输出仍为单 target 的控制 dict，沿用 conditional edges）：
  1. 求值异常 / 变量缺失 → exitTarget，exitReason=`expression_error`（fail-safe 退出，错误进 expression_errors，运行仍 completed）；
  2. 结果非数组 → exitTarget，exitReason=`expression_error`，expression_errors 记「遍历对象必须是数组，实际为 {type}」；
  3. 数组长度 > 100 → exitTarget，exitReason=`items_too_large`，expression_errors 记「遍历数组长度 {n} 超过上限 100」；
  4. 空数组 → exitTarget，exitReason=`empty`，index=0，results=[]；
  5. 否则取当前 0-based 游标 `index`：把 `items[index]` 暴露为本轮 item，路由 bodyTarget；体内最后一条回边重入本节点时游标加 1；游标到达数组长度 → exitTarget，exitReason=`completed`。
- foreach 节点产出形状：

  ```json
  {
    "mode": "foreach",
    "items": [ /* 冻结的数组，长度 ≤100 */ ],
    "index": 0,            // 当前已进入体的轮次计数（0-based；退出时等于数组长度）
    "iterations": 0,       // 与 index 同值（沿用 while 字段名，供画布/计数统一）
    "item": "…",           // 当前轮的数组元素；首次进入前为 null
    "results": [],         // 见 C
    "target": "node_id",
    "exitReason": "completed | empty | expression_error | items_too_large | break | null",
    "expression_errors": []
  }
  ```

- 体内引用方式（零作用域改动，item 挂在 loop 节点自身产出上）：
  - 当前元素：`{{loop-x.item}}`（元素为对象时 `{{loop-x.item.amount}}`）；
  - 当前轮次（0-based）：`{{loop-x.index}}`；
  - 已聚合结果：`{{loop-x.results}}`；
  - `itemName` 仅作为前端变量选择器中的展示别名（默认显示 `item`），**不改变运行时路径**——避免向全局作用域注入易冲突的短名，与「context = global + outputs」的现有装配保持一致。
- recursion_limit 派生公式沿用 while：`2*节点总数 + 2*len(items)*(循环体节点数+1) + 10`（len(items) 在 foreach 即冻结数组长度；break 出口 +2）。

### C. 结果聚合（collectTarget）

- `collectTarget` 指定循环体内一个节点；每轮**体内执行完毕、回边重入 loop 节点时**，把该节点当前产出（`state.outputs[collectTarget]`，整个对象）**追加**到 loop 节点 `results` 数组。
- 追加时机在「推进游标之前」：第 1 轮回边 → 收集第 1 个结果 → 游标 1 → 暴露第 2 个元素。
- 该节点产出缺失（理论不可达，防御）→ 跳过本轮收集且 expression_errors 记一条，不中断循环。
- `collectTarget` 为空 → `results` 恒为 `[]`，仅做逐项执行。
- 聚合的是**节点产出对象**（不做字段挑选/拍平）；下游可经 output_schema 路径或 `{{loop-x.results}}` 取用。

### D. 前端（零新依赖）

- `nodeCatalog.ts`：`NodeConfig.mode` 类型放宽 `'while' | 'foreach'`；新增 `itemsExpression?: string`、`itemName?: string`、`collectTarget?: string`；`defaultConfig('loop')` 不变（默认 while）。
- `loop.schema.ts`：properties 增 `itemsExpression`（x-variable）、`itemName`（string）、`collectTarget`（target-select）；required 按 mode 分化不由 schema 表达（L1 手写校验承接，同既有跨字段规则先例）；`x-outputSchema` 增 `items/item/results`（超集，不破坏 while）。
- `nodeUiSchemas.ts` loopUiSchema：
  - `mode` 由隐藏字段改为可见 Select（选项：条件循环 while / 遍历循环 foreach）；
  - `hiddenWhen`：mode=foreach 时隐藏 continueExpression/maxIterations、显示 itemsExpression/itemName/collectTarget；mode=while 时相反；
  - labels/placeholders 与示例（itemsExpression 示例 `{{trigger-1.context.payload.order_ids}}`）。
- `LoopConfig.tsx`：标题文案随 mode 分化（其余仍 schema 驱动，零结构改动）。
- i18n：新文案进既有 `editor` namespace（zh-CN 填实、en-US 不新增 key 照既有空占位约定）。

## 2. 非目标

- 嵌套循环（体内其他 loop 节点；维持拒绝）；数组长度 >100 的分批处理；运行期数组增量/变化感知（v1 冻结）。
- 向全局作用域注入裸 `item` 短名、自定义 item 的运行时路径（itemName 仅展示别名）。
- 并行遍历（map-reduce 并发分支）——随 D18 动态分支 / Phase 3，foreach 为**串行**逐项。
- 聚合结果的字段挑选/拍平/去重、按 item 失败续跑或重试策略（沿用节点级 retry）。
- break 之外的 continue-skip（跳过当前单项）语义；while→foreach 的图自动迁移（手工切 mode）。

## 3. Schema 契约（03 同步）

- loop config 契约（03 指向 04 §5.3）：增 foreach 字段集 `itemsExpression/itemName/collectTarget` 与产出字段 `items/item/results`；形状权威 04 §5.3 追加段 + 本文。
- exitReason 取值集扩为 foreach：`completed | empty | expression_error | items_too_large | break`（while 集不变）。
- **无新错误码、无新 REST 端点、无新迁移**（纯执行器/前端扩展；图 JSON 仍是既有保存端点）。

## 4. 运行时语义

- item 明文（可能含业务数据）只存在于 loop 节点产出与单次体内执行栈；foreach 不新增任何持久化面，监控记录/录制快照口径与既有 loop 一致。
- 数组冻结保证回放确定：录制回放时 items 取自冻结快照（图快照内的 loop 产出），不重新求值。
- 并发：图执行以运行为单位，loop 产出随单运行 state 流转，无跨运行共享。

## 5. 测试矩阵（13 落号）

- DSL 校验单测（扩 `tests/test_graph_dsl.py`，~6）：mode=foreach 必填 itemsExpression/body/exit、itemsExpression 语法错误 422、itemName 非法标识符、collectTarget 不存在/不在体内、while 规则在 foreach 下不误报、foreach 下 break 出口合法。
- 执行单测（扩 `tests/test_graph_loader.py`，~8）：3 元素串行 3 轮 + item 逐轮暴露 + results 聚合顺序、空数组 exitReason=empty、非数组/表达式异常 expression_error、长度 101 items_too_large、数组仅首轮求值一次（可变来源注入计数）、collectTarget 空时不聚合、break 中途退出 exitReason=break 且 results 保留已收集、index/iterations 同步。
- 回归：while 既有全部用例零改动通过；d26 录制回放、m11 记忆两档 smoke 过。
- 前端（扩 schema/uiSchema/表单测试，~4）：loop schema 新字段、uiSchema hiddenWhen 两档分化、mode 切换表单显隐、outputSchema 超集。
- 浏览器冒烟（≥2 截图）：webhook payload 带 `order_ids:[1,2,3]` → foreach 逐项工具调用（SIMULATED 或 mock 上游）→ 运行完成，loop 产出 items/results 长度 3、exitReason=completed；空数组立即走退出目标。

## 6. 原子序

1. docs-only 立项：本文＋04 §5.3 追加段＋03＋06＋13＋14（D16）＋08＋00 地图＋handoff＋CHANGELOG；
2. `feat(graph): support foreach loop mode with frozen items and item exposure` ＋DSL/执行器单测；`.venv/bin/pytest`；
3. `feat(frontend): add foreach mode fields and mode-switched loop form` ＋i18n；`cd frontend && pnpm lint && pnpm test && pnpm build`；
4. docs 收口：浏览器冒烟（截图）＋CHANGELOG＋handoff/08/本文落码注记。

## 7. 落码注记（2026-09-23 收口）

- 原子序：①docs 立项 `efe5de4`；②`feat(graph): support foreach loop mode with frozen items and item exposure`（`1e79121`，DSL mode 分支校验 + foreach 执行器 + collectTarget 回边聚合 + break 网关补在途结果 + mode 感知静态输出键 + 递归预算）；③`feat(frontend): add foreach mode fields and mode-switched loop form`（`01296d5`，loop schema oneOf 两档 + hiddenWhen 字段显隐 + L1 foreach 手写规则 + parseExpression 纯语法校验 + reverseDeps /collectTarget）；④本文＋冒烟。
- 收口门：后端 **1391 passed / 59 skipped**（净增 16 常跑：DSL 7、执行器 9）；前端 **597 passed / 2 skipped**、oxlint **45 passed / 1 skipped（46 files）**、`pnpm build` 干净。
- 浏览器冒烟（http://localhost:5174，内存档 uvicorn :8000；3 截图 `docs/smoke-shots/foreach45-*`）：后端 HTTP 直跑 graph-1——`order_ids:["ORD-1","ORD-2","ORD-3"]` 得 exitReason=completed、results 长度 3 且工具 params 按序渲染 `item=ORD-1&index=0`…`item=ORD-3&index=2`，traces 三轮回边；`order_ids:[]` 得 exitReason=empty、循环体不执行。画布冒烟：经编辑器「编译并运行」跑同图——3 元素时 trigger/loop/tool-body/tool-exit 依次 completed；空数组时 tool-body 保持 idle、loop 与 tool-exit completed（立即走退出目标）。
- 零新依赖/零迁移/零新 REST·错误码/无 ADR，与立项一致。D16 整体缓做不解除：嵌套循环、并行 map-reduce、裸 item 全局短名、skip-current、>100 分批触发条件不变（见 docs/14）。
