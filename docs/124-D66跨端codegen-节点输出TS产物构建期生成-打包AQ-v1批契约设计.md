# 打包 AQ：D66 跨端 codegen — 节点静态输出 TS 产物构建期生成（v1 批契约设计）

> 号段：U1367–U1372（docs/13 预留）｜承接 docs/14 D66 余部「跨端 codegen 生成前端 TS」｜2026-10-12 docs-only 立项
> 状态：✅ **已落码收口（2026-10-12，三原子 `cee64e3`/`c1aa9b5`/`b7206d1`，U1367–U1372 转正式见 docs/13）**；选型裁决＝ADR T35（docs/10 §4，方案 a：构建期生成 TS 提交入仓＋后端守产物最新）
> 形状权威＝本文。落码与本文的偏差见 §9 收口注记。

## 9. 收口注记（2026-10-12）

- 原子序按 §6 执行：`cee64e3` chore 生成器 → `c1aa9b5` feat 生成物入仓＋scope re-export → `b7206d1` test 新鲜度守护＋两重对拍源切换（docs 收口随第四原子）。
- **一处实现修正**：§3.2 原文写 scope.ts「改 re-export（导出名与消费点零改）」，实测纯 `export ... from` 不会把名字带进文件内作用域，scope.ts 内部 5 处引用被 tsc 判 5×TS2304；最终形态＝顶部常规 `import { STATIC_OUTPUT_KEYS } from './schemas/generated/nodeOutputs'` 加一行同名 `export`，对外 import 面与文件内消费点都零改。
- **U1371 对拍源切换的细节**：`nodeSchemas.test.ts` 原 `EXPECTED_OUTPUT_KEYS` 同时承担「普通节点根键」与 trigger/tool_call 两特例（trigger 是 context 嵌套五键、tool_call 是动态 outputSchema）。生成物只有 11 类普通节点，故特例表改名 `SPECIAL_CASE_OUTPUT_KEYS` 保留，普通节点改对拍生成物，并加「kind 既不在特例也不在生成物即 fail」断言；排除项恰好 2 类的元用例不动（剩下的就是这两类）。
- **门（实跑）**：后端全量 2703 passed／181 skipped，首跑 4 failed 全为沙箱 DNS 拦截（example.com 解析失败，提权复跑 4 passed，本批零产品代码改动）；分域 node_output_contract 5＋graph_ref_validation 20 共 25 passed。前端 vitest 993／2（与基线一致）、oxlint 0/0、tsc 0、build 过；前端三门全程未调 Python。反向门实测：生成物尾加 `// tamper` ⇒ 新鲜度守护红，还原即绿。
- 深层子键白名单、完整 x-outputSchema 生成、方案 b/c 仍按 §4/§8 缓做；D66 随本批闭合（codegen 余部落地，剩余重开判据在 §8）。

## 1. 背景与问题

D66（docs/14）登记的问题：同一套「某节点类型能被 `{{...}}` 引用哪些输出根键」的事实历史上活在**三处手抄表**：

1. 后端 `src/atlas/graph/dsl.py::_STATIC_OUTPUT_KEYS`——L2 编译期引用复查真正拒人的表；
2. 前端 `frontend/src/lib/scope.ts::STATIC_OUTPUT_KEYS`——画布变量补全与前端 L2 诊断；
3. 前端 `frontend/src/lib/schemas/nodes/*.schema.ts::x-outputSchema.properties`——对外声明的产出形状。

打包 AP 以「loader 各 mode 真实产出的并集」为权威把三处改齐；批 D（`84c4aa1d`/`951516ab`）把**后端**收敛为单源 `src/atlas/graph/node_outputs.py`（`STATIC_NODE_OUTPUTS` ＋只读投影 `project_static_outputs()`），`dsl.py` 改派生，并立跨端对拍守护 `tests/test_node_output_contract.py`（后端解析前端 `scope.ts` 文本逐字对拍），前端侧另有 `nodeSchemas.test.ts` 对拍 scope.ts ↔ 各 x-outputSchema。

**现状仍是「两重测试锁三份手抄」**：`scope.ts` 的 `STATIC_OUTPUT_KEYS` 与 12 份 `x-outputSchema` 仍是人工抄写，对拍只能在漂移发生后变红，不能让漂移在结构上不可能。本批做 D66 的余部：**前端两份不再手抄，改吃由后端单源投影生成的 TS 产物**。

## 2. 选型裁决（ADR T35 摘要，全文见 docs/10 §4）

三方案：

- **a. 构建期生成 TS、产物提交入仓、后端守产物最新**（采纳）
- b. 前端 `pnpm build` 依赖 Python（生成物不提交，构建时现算）
- c. 运行时契约端点（`GET /api/...` 下发，前端启动拉取）

裁决 a。关键理由：

- **前端工具链不引入 Python 依赖**：pnpm/CI/纯前端开发机不需要 Python 环境即可 build/test（否 b 的决定性理由；前端 vitest 993 例与 build 必须保持零 Python 即可跑）。
- **无新运行时面**：不加端点、不加鉴权面、不加启动期网络依赖，画布补全在离线/单测里照常工作（否 c；c 还把编译期事实变成运行时可用性问题）。
- **产物入仓＋后端守最新**＝既有「两重对拍」纪律的升级而不是替换：生成器是确定性纯函数（投影已具备 `project_static_outputs()`），产物可 review、可 blame；守护测试把「忘记重新生成」变红，漂移在 CI 不可过。
- 与项目既有惯例同族：T16/T17/T30「自研最小、零新依赖」；前端 build 链 Vite 不扩。

**代价（诚实登记）**：生成物是入仓文件，改单源后必须手跑一次生成脚本（忘跑由守护变红兜住，不会静默）；生成器自身是 Python→TS 的字符串模板，需钉死格式与排序。

## 3. v1 范围（做什么）

### 3.1 生成器（后端，stdlib 零新依赖）

- 新文件 `scripts/dev/gen_node_outputs_ts.py`（与 `scripts/dev/m8_smoke.py`、`m9_smoke.py` 同族的开发脚本位；不放 `src/atlas`——它是开发期工具不是运行时）。
- 输入：`atlas.graph.node_outputs.project_static_outputs()`（唯一权威出口；**不得**另读 dsl/loader）。
- 输出：一份 TS 文件，导出 `STATIC_OUTPUT_KEYS: Record<string, readonly string[]>`，节点类型按字典序、根键按单源声明序（声明序承载 loader mode 并集的人工注释语义，不重排）。
- 文件带确定性头注释：生成自哪个模块、`DO NOT EDIT`、重新生成命令。
- 幂等：同输入逐字节同输出（无时间戳、无随机序），保证守护与 blame 友好。

### 3.2 生成物（前端，入仓）

- 新文件 `frontend/src/lib/schemas/generated/nodeOutputs.ts`（新目录 `generated/`，命名明示生成物；加目录级说明或头注释「勿手改」）。
- `frontend/src/lib/scope.ts` 删除本地 `STATIC_OUTPUT_KEYS` 字面量，改为 `export { STATIC_OUTPUT_KEYS } from './schemas/generated/nodeOutputs'`（**导出名与消费点零改**：scope.ts 内引用处与外部 import 面保持不变；仅移除定义、补 re-export）。
- `scope.ts` 内与该表相邻的历史注释（打包 AP/AN② 的出处注记）迁生成物头注释或保留一行指针，不丢可追溯性。

### 3.3 x-outputSchema 第二份手抄的处理（v1 边界，重要）

`x-outputSchema.properties` 不只是根键名——它还携带 JSON Schema 元信息（type/description/oneOf 等），**根键集合只是其中一个切面**。因此 v1：

- **不**从根键投影生成完整 x-outputSchema（后端单源没有 type/description 事实，硬生成会编造契约）。
- 改为升级**既有前端对拍** `nodeSchemas.test.ts`：各 schema 的 `x-outputSchema` 根键集合必须等于生成物 `STATIC_OUTPUT_KEYS[kind]`（现在对拍的是手抄 scope 表，改为对拍生成物；比较口径不变：根键集合逐字相等）。
- 即：三份事实变「**一份生成（scope 消费面）＋ 一份带元信息的声明（x-outputSchema）由测试钉齐**」，声明里的 type/description 仍人工维护、根键不再可能各写一套。

### 3.4 守护（后端，防「忘重新生成」）

- `tests/test_node_output_contract.py` 增例：按生成器同一渲染逻辑（或直接 import 生成器的 render 函数）算出期望文本，与入仓生成物逐字节相等；不等时报错信息含重新生成命令。
- 为避免「测试里复制一份渲染器」（又变成两份），生成脚本的渲染函数设计为可 import 的纯函数 `render(script_path: Path) -> str`（放 `scripts/dev/` 下可导入模块；脚本 `main()` 只做读投影＋写文件），测试与 CLI 共用同一实现。
- 既有「后端解析 scope.ts 文本」的对拍例：scope.ts 改为 re-export 后表不在该文件，**该例改为解析生成物文件**（解析器对两个文件同族适用，或直接 import 渲染结果比 TS 文本）；守护意图（跨语言两份一致）不变。
- 前端 `nodeSchemas.test.ts` 对拍源切到生成物后，现有「排除项 5 类逐条带理由＋元用例」机制原样保留（condition/wait/loop 等若仍有声明超集历史包袱，不借本批悄悄改变对拍口径；批 AP 后理论上已齐，落码时以实跑为准——若对拍已可全量，删除排除表并让元用例随之收口）。

## 4. 非目标（明确不做）

- 不生成完整 x-outputSchema（type/description/oneOf 仍在 schema 文件维护，见 §3.3）。
- 不引入运行时契约端点（方案 c；登记为重开项）。
- 不让前端 build/test 依赖 Python（方案 b 否决）。
- 不做深层路径（子键）的 codegen：`AI_DECISION_KEYS`/`AI_MEMORY_INJECTED_KEYS` 等一层子键白名单是 dsl.py 显式枚举的另一族事实，不在 `STATIC_NODE_OUTPUTS` 投影内；本批只生成根键表，子键保持现状（后续若漂移成实际问题另立批）。
- 不新增 npm/uv 依赖、不加 git hook（CI/后端守护已够；hook 属开发者环境强制，不做）。
- 不改 L2 运行期行为：生成物与现手抄表逐字相等是本批验收前提，编译/运行语义零变化。

## 5. 受影响面清单

| 面 | 文件 | 改动 |
|---|---|---|
| 后端生成器 | `scripts/dev/gen_node_outputs_ts.py`（新） | 纯函数 render＋CLI main，stdlib |
| 后端守护 | `tests/test_node_output_contract.py` | 新增产物新鲜度例；跨端对拍源改生成物 |
| 前端生成物 | `frontend/src/lib/schemas/generated/nodeOutputs.ts`（新，入仓） | 生成、提交 |
| 前端消费 | `frontend/src/lib/scope.ts` | 删字面量、re-export 生成物，消费点零改 |
| 前端守护 | `frontend/src/lib/schemas/__tests__/nodeSchemas.test.ts` | x-outputSchema 对拍源＝生成物；排除表按实跑收口 |
| 文档 | docs/03（节点输出契约：codegen 出口与生成物位置）、docs/09（scripts/dev 新脚本＋generated 目录）、docs/12（内部接口：投影→生成器消费关系，如有对应行）、docs/14 D66（注记收口）、docs/00 地图、CHANGELOG、handoff | 收口批同步 |

docs/03 同步口径：节点输出根键的唯一权威仍是 `graph/node_outputs.py`；新增一行事实流向「投影 → `scripts/dev/gen_node_outputs_ts.py` → 入仓 `nodeOutputs.ts` → scope.ts re-export」，Schema 契约规则（AGENTS.md 改 Schema 同步所有引用处）据此把「改根键必须跑生成器」写成机械步骤。

## 6. 原子提交计划

1. `chore(scripts)`：新增 `scripts/dev/gen_node_outputs_ts.py`（render 纯函数＋CLI；此时不接前端），带脚本自身的最小自测或由后续测试覆盖。
2. `feat(frontend)`：生成物 `generated/nodeOutputs.ts` 首次入仓（内容＝现 scope.ts 手抄表逐字搬运，先证明零差异）＋scope.ts 改 re-export；前端 vitest/tsc/build 过门。
3. `test(graph)`：后端守护增产物新鲜度例＋跨端对拍源切生成物；前端 nodeSchemas 对拍源切生成物（排除表按实跑处理）。
4. `test(...)` 或并入 3：反向门验证（改单源一个键不跑生成器 ⇒ 后端守护红；手改生成物 ⇒ 新鲜度红；删 scope re-export ⇒ tsc/消费点红）。
5. `docs`：03/09/12/14/00/13（U1367–U1372 转正式）/CHANGELOG/handoff 收口。

脚本与生成物是否同一原子：按「依赖与产物同事实」惯例（T31），**生成物首次入仓与生成器同批但可分原子**（原子 1 脚本、原子 2 生成物＋接线），保证每个原子过门。

## 7. 验收用例（U1367–U1372 预留，落码时转正式）

- **U1367** 生成器幂等：同投影连跑两次输出逐字节一致；节点序字典序、根键声明序；头注释含重新生成命令。
- **U1368** 生成物与单源一致：渲染结果等于入仓 `nodeOutputs.ts`（新鲜度守护）；故意改 `STATIC_NODE_OUTPUTS` 不重跑 ⇒ 红，报错指引生成命令。
- **U1369** scope 消费面零行为变化：scope.ts re-export 后，引用校验/补全相关既有全部前端测试不变（生成物内容＝原子 2 前手抄表的逐字对拍）。
- **U1370** 手改生成物即红：在生成物加一个键 ⇒ 新鲜度守护红（防绕过生成器直接改产物）。
- **U1371** x-outputSchema 对拍源切换：nodeSchemas 对拍改吃生成物后，既有排除表机制/元用例行为按实跑记录；摘任一侧键仍红（守护能红的自证）。
- **U1372** 全量门零回归：后端全量 pytest、前端 vitest＋oxlint＋tsc＋build 全绿；后端测试运行不依赖前端构建、前端测试运行不依赖 Python（方案 a 的核心承诺各证一次）。

## 8. 重开判据（方案 b/c 何时复评）

- **b 复评**：前端工具链整体迁入需要 Python 的代码生成（如更多契约从后端投影、多语言客户端生成），且 CI 镜像已稳定含 Python 时，携「生成物不入仓、build 现算」的实际收益评估。
- **c 复评**：出现画布/编辑器需要**运行时**拿到最新契约的真实消费者（如第三方可视化接入、插件市场、未随前端发版的节点类型动态注册），此时静态生成物无法满足，携鉴权与多租户分区设计另立 ADR。
- **x-outputSchema 全量生成复评**：后端单源开始承载产出键的 type/description 事实（而非仅键名）时，§3.3 的半生成边界重评。

