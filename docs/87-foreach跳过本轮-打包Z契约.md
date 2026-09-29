# 打包 Z 契约：foreach skip-current（跳过本轮）（D16 切片）

> 形状权威＝本文。落码与本文冲突时先改本文再改代码；只许增测，不改既有契约语义。
> 范围一句话：docs/45（foreach 批）落了数组逐项、回边聚合与 break，但循环体内
> 没有"跳过当前元素、不聚合本轮"的手段——本批补 skip-current。
> 零迁移、零新依赖、零新端点、零新错误码、无 ADR；前端零结构改动（只连边）。

## 0. 已核实前提（回代码，不是转抄）

- foreach 运行时在 `graph/loader.py:_execute_foreach`：首轮冻结 items 并放行进
  bodyTarget；体内节点经回边（连回 loop 节点）重入时读 `collectTarget` 产出追加进
  results、index+1，再放下一项；末项走完 ⇒ exitTarget/`exitReason="completed"`。
- break 已有完整先例：体内 condition 分支直连 `exitTarget` 的边，编译期 retarget 到
  合成节点 `__break__{loopId}`（`BREAK_GATE_PREFIX`，`_make_break_gate`），gate
  补收本轮结果后写 `exitReason="break"` 放行进退出目标。
- while 模式下体内 condition 连回 loop 节点＝continue（重算 continueExpression
  重入），该语义在 dsl/loader 两侧均有测试，本批不动。
- DSL 校验（`dsl.py:_validate_loop_config`）：体内节点连回 loop 节点即合法
  returner（不报 stranded、计 backedge）；非 condition 直连 exitTarget 仍属非法逃逸。
- 迁移最新 **034**；本批不加迁移。

## 1. 决策（六条；改任何一条先改本文）

- **D-1 skip 的表达方式＝foreach 体内 condition 节点连一条分支回 loop 节点**：
  与 break（condition→exitTarget）同构。编译期把边 `(condition, loopId)` retarget
  到合成节点 `__skip__{loopId}`（新前缀常量 `SKIP_GATE_PREFIX="__skip__"`）。
  非 condition 体内节点连回 loop 仍是**普通聚合回边**，语义不变。
  - **落码补记（2026-09-29）**：本条使「condition 连回 loop＝skip」成为唯一语义，
    因此 foreach 体内 condition 的**普通 continue（要聚合本轮）不能直连 loop**——
    须经一个非 condition 透传节点回 loop（边＝透传节点→loop，按普通聚合回边处理）。
    D17 时代「condition defaultTarget 直连 loop 作 continue」的画法由此废止
    （旧 foreach break 测试夹具已按新画法迁移；模板目录经扫描零受影响）。
- **D-2 skip gate 语义：只推进、不聚合**——
  - `results` 原样保留：**不读** collectTarget，collectTarget 有产出也不追加；
    不产生 `FOREACH_COLLECT_MISSING`（与普通回边的区别仅此一条）。
  - index+1、`item=items[next_index]`，放行进 bodyTarget（下一项）；
    跳过的恰是末项（next_index==len(items)）⇒ 放行进 exitTarget、
    `exitReason="completed"`（不是新理由：所有元素都已处理，只是未全部聚合）。
  - gate 以 loop 节点自身补发 `node_end`（同 break gate），输出挂在 loop 节点 id 下。
  - gate 有两个可能去向 ⇒ 经 `add_conditional_edges` 装配（bodyTarget/exitTarget）。
- **D-3 不新增 exitReason 档位**：跳过中间轮时该轮输出 exitReason=None；
  终态理由集仍为 `completed | empty | expression_error | items_too_large | break`。
  results 长度可能小于实际处理轮数，这是 skip 的预期效果而非异常。
- **D-4 while 模式一字不改**：condition→loop 仍是重算 continueExpression 的
  continue，**不创建** skip gate；skip gate 只在 `mode=="foreach"` 时装配。
- **D-5 无新增 DSL 校验**：condition→loop 本就是合法 returner。非 condition
  节点的 exit 逃逸、嵌套循环、体内 trigger 等既有规则全部不变。
- **D-6 break 与 skip 可共存于同一 loop**：同一 condition 可同时有连回 loop 的
  skip 分支与连向 exitTarget 的 break 分支，运行时按 condition 路由结果各走各门；
  两条 retarget 互不覆盖。

## 2. 形状

- `graph/loader.py`：新增 `SKIP_GATE_PREFIX`、`_make_skip_gate(loop_node, emit)`；
  compile_graph 在 break gate 装配之后、foreach loop 上推导 skip 源
  （body 内 condition 且 loop.id 在其 outgoing），add gate 节点＋retarget
  ＋gate 的 conditional edges。
- `graph/dsl.py`：无改动。
- 前端：无改动（condition→loop 边本就能画）；用法写进文档，不加入口/按钮。

## 3. 契约同步矩阵（收口时逐项回填）

- [x] `docs/04`：§5.3 foreach blockquote 补「skip-current 已取回」并改正
  「不做」列里的 skip-current 字样。
- [x] `docs/12`：编译期内部节点清单/loader 说明补 `__skip__ gate`。
- [x] `docs/13`：U986–U992 登记，验收结果回填。
- [x] `docs/14 D16`：追记「2026-09-29 再取回 skip-current 半边，本条不解除」。
- [x] `docs/08`：立项/收口注记。
- [x] `docs/00` 文档地图：docs/87 行。
- [x] docs/45：非目标表的 skip-current 行加「已由 docs/87 打包 Z 取回」注记。
- [x] handoff（Project documents＋Recently shipped）、CHANGELOG。
- 无 docs/03 项：foreach 输出形状零变化（无新字段、无新表）。

## 4. 测试候选（U986 起；落码时定号）

- **U986 跳过中间元素**：items=[a,b,c]，condition 在 b 轮选 skip ⇒
  最终 results 恰含 a、c 两轮聚合值、长度 2、index=3、exitReason=completed。
- **U987 skip 不聚合**：skip 轮 collectTarget 有产出也不进 results；输出
  expression_errors 为空（无 FOREACH_COLLECT_MISSING）。
- **U988 跳过末项**：gate 直接去 exitTarget，bodyTarget 不再被激活。
- **U989 普通回边回归**：非 condition 节点连回 loop 仍读 collectTarget 聚合。
- **U990 while 不受影响**：while 体内 condition→loop 仍走 continueExpression
  重算；编译产物中无 `__skip__` 节点。
- **U991 break＋skip 共存**：同图两条分支，分别触发时各进各门、输出正确。
- **U992 全部跳过**：每轮都 skip ⇒ results=[]、exitReason=completed。
- **浏览器验收（2026-09-29 已做）**：真实后端 :8000 上经登录→保存→编译→
  `/run/stream` SSE 跑通 skip-middle 图——index 1→2 时 results 保持
  ['item=a'] 不变，终态 ['item=a','item=c'] / completed；浏览器 :5174
  登录、编辑器加载、编译并运行退款 Demo（SSE 上屏、结果 refunded、控制台零错）。
  未做项：未在画布上手工拖画 condition→loop 边（节点面板仅支持原生拖拽，
  DevTools 无法模拟 dataTransfer）；该边属前端既有通用连线、本批零前端改动。

## 5. 原子序

1. `docs(loop): specify foreach skip-current contract`（本文＋00/14/08/handoff 立项）。
2. `feat(engine): add foreach skip-current gate`（gate＋编译装配）＋U986–U992；
   `.venv/bin/pytest`。
3. `docs(loop): close out the foreach skip-current landing`（真实浏览器验收＋
   收口回填＋handoff＋CHANGELOG）。

## 6. 不做（缓做，仍以 docs/14 D16 触发条件为准）

嵌套循环、并行 map-reduce／动态支路、裸 item 全局短名、>100 数组分批触发。
skip 不支持"跳过前 N 项 / 跳到指定下标"等跳转语义（continue 只有"本轮"一档）。
