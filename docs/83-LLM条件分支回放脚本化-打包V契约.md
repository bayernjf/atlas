# 打包 V 契约：LLM 语义分支录制回放脚本化（D14 余部切片）

> 形状权威＝本文。落码与本文冲突时先改本文再改代码；只许增测，不改既有契约语义。
> 范围一句话：录制用例里的 conditionMode=llm 分支，在 ``mock_tools=true`` 回放时
> **按录制时的分支标签确定性重放**，不再发起 LLM 调用、不再因未配模型漂到 defaultTarget。
> 不做门禁脚本化、不做多候选/置信度、不改非 mock 回放语义。

## 0. 已核实前提（回代码，不是转抄）

- condition 节点 LLM 分支：`loader.py:1452` ``conditionMode == "llm"`` → `_execute_llm_condition`，
  调全局 ``condition_classifier.classify(branches, context_text, instruction)``；任何异常 fail-safe
  走 defaultTarget（loader.py:1514）。分类器由 `compile_graph`/`run_graph` 参数 ``condition_classifier``
  注入，缺省 `get_condition_classifier()`（loader.py:2270/2694）。
- 分类器两档（`llm/condition_classifier.py`）：配了 ``LITELLM_MODEL`` 走真实 LiteLLM（temperature=0
  但仍是网络调用、供应商侧仍可能抖动）；未配走 OfflineConditionClassifier，**必抛错** → defaultTarget。
- condition 节点 node_end 产出（loader.py:1538）含 ``mode: "llm"``、``branch``（标签或 ``__default__``）、
  ``target``、``evaluation``。录制步骤原样存了这份产出——**录制时选了哪条分支是已知事实**。
- 现有回放（`api/main.py:2813`）：``mock_tools=true`` 仅经 `build_tool_mocks` 给 **tool_call 节点**
  打桩（replay.py:141）；LLM condition 仍走真实/离线分类器 ⇒ 今天 LLM 路由的录制用例**不可确定回放**：
  CI 无模型时必漂 defaultTarget；有模型时门禁/回放可能随供应商抖动。
- 发布门禁（`recording/gate.py:40`）**从不打桩**，LLM condition 一直是真实求值——本批不改变该口径。
- 无迁移、无前端页面改动（报告纯超集加一个字段）。

## 1. 决策（五条；改任何一条先改本文）

- **D-1 分类器 Protocol 扩一个可选入参 ``node_id``**：
  ``classify(self, *, branches, context_text, instruction, node_id: str | None = None)``。
  `_execute_llm_condition` 调时透传 ``node_id=node.id``；Offline/LiteLLM 实现签名补齐但**行为不变**
  （LiteLLM 不把 node_id 发给供应商）。这是纯加法，既有第三方自定义分类器不带该 kwarg 也能跑——
  仅 Scripted 分类器消费它。
- **D-2 新增 `ScriptedConditionClassifier`（llm/condition_classifier.py）**：构造收
  ``dict[node_id, branch_label]``；classify 时——node_id 命中脚本 → 返录制标签（含 ``__default__``）；
  node_id 为 None 或不在脚本 → **抛 ConditionClassifyError**（由 loader 既有 fail-safe 走 defaultTarget，
  compare 据此报分支漂移；不假装知道录制后新增节点的语义）。零网络、零随机、零环境依赖。
- **D-3 replay.py 增 `build_condition_script(case) -> (ScriptedConditionClassifier | None, list[str])`**：
  经 dedupe_steps 取 ``node_type=="condition"`` 且 ``output.get("mode")=="llm"`` 的步骤，
  映射 ``node_id → output["branch"]``（branch 必须是非空 str，否则跳过该节点）；无 LLM condition
  步骤 → ``(None, [])``。与 build_tool_mocks 同口径：用录制事实做桩，回放重算 target，
  compare 两端各自归一化。
- **D-4 仅 ``mock_tools=true`` 回放生效**：replay 端点在 mock_tools 分支同时构造 condition 脚本，
  透传 ``run_graph(..., condition_classifier=scripted)``；报告纯超集加 ``mocked_conditions: list[str]``
  （未启用/无 LLM 条件为 []，异常折叠报告同样带该字段）。**非 mock 回放与发布门禁语义不变**
  （真实 LLM/离线分类器；无模型仍漂 defaultTarget——这是如实的运行时行为，不是 bug）。
- **D-5 零新依赖／无迁移／无新 ADR**；前端本批不动（可选字段不破坏消费方）；
  不解除 D14（置信度阈值/多候选/澄清、每分支独立 prompt、模型按租户可选、字段级脱敏、
  structured outputs、门禁 LLM 抖动仍缓做）。

## 2. 形状

- `llm/condition_classifier.py`：Protocol/Offline/LiteLLM 三签名加 ``node_id``；新 ``ScriptedConditionClassifier``。
- `graph/loader.py`：``_execute_llm_condition`` 的 classify 调用传 ``node_id=node.id``。
- `recording/replay.py`：``build_condition_script(case)``。
- `recording/__init__.py`：导出新符号。
- `api/main.py`：replay 端点构造/透传脚本，报告加 ``mocked_conditions``（正常与异常两条路径）。

## 3. 契约同步矩阵（收口时逐项回填）

- [ ] `docs/03`：replay 响应补 ``mocked_conditions``；ScriptedConditionClassifier 注记。
- [ ] `docs/12`：replay 行更新（mock_tools 同时桩 LLM 分支；响应字段）。
- [ ] `docs/13`：U964–U969 登记。
- [ ] `docs/14 D14`：追记「2026-09-29 取回 LLM 路由录制回放半边（不解除本条）」。
- [ ] `docs/08 §八`：C 组 D14 行更新＋立项/收口注记。
- [ ] `docs/00` 文档地图：docs/83 行。
- [ ] handoff（Project documents＋Recently shipped，旧条目滚入归档）、CHANGELOG。

## 4. 测试与验收（U964 起；落常跑，不依赖真实 LLM 与网络）

- **U964** ScriptedConditionClassifier 纯逻辑：命中返标签（含 __default__）；node_id=None/未知节点
  → ConditionClassifyError。
- **U965** build_condition_script：只收 mode=llm 的 condition 步骤；rule condition 不收；
  branch 缺失/非 str 跳过；无 LLM 条件 → (None, [])；dedupe 口径保末。
- **U966** 端到端 mock 回放：录制一张含 LLM condition（走分支 b1）的用例，**环境无 LITELLM_MODEL**，
  ``POST /replay {"mock_tools": true}`` ⇒ matches=true、replay 路径走 b1、``mocked_conditions`` 含该节点；
  全程无 litellm 调用（可用桩断言 classify 只来自脚本）。
- **U967** 录制时走 __default__ 的 LLM condition：脚本回放仍走 defaultTarget，matches=true。
- **U968** 录制后草稿新增 LLM condition 节点（脚本中无此节点）→ 该节点抛错走 defaultTarget，
  compare 报告不匹配且 note 指向分支漂移；其余节点照常比对。
- **U969** 边界保真：非 mock 回放不注入脚本（无模型时漂 default、matches=false、mocked_conditions=[]）；
  发布门禁结果不出现 mocked_conditions、行为不变。
- 复跑：既有 condition classifier / llm condition graph / recording 全套零改动语义；
  `tests/test_handoff_integrity.py` 绿。

## 5. 原子序

1. `docs(recording): specify scripted LLM condition branches for replay`（本文＋03/12/13/14/08/00 同步登记）。
2. `feat(recording): replay LLM condition branches from recorded labels`（Protocol node_id＋Scripted＋build_condition_script＋端点）。
3. `test(recording): cover scripted LLM condition replay and fallbacks`（U964–U969）。
4. `docs(recording): close out the scripted LLM condition landing`（收口回填＋handoff＋CHANGELOG）。

## 6. 残余风险（别当成「D14 收干净了」）

- 发布门禁仍真实调用 LLM：供应商抖动可使门禁 flaky（temperature=0 不等于跨调用一致）；本批不碰。
- 脚本回放验的是「录制标签在当前图上是否仍导出同一路径/产出」，不验「LLM 今天还会不会这么判」
  ——语义漂移检测要靠非 mock 回放或人工，调用方需理解这层区别。
- 置信度阈值/多候选/澄清重问、每分支独立 prompt 或调用、模型按租户可选、字段级脱敏、
  structured outputs 仍缓做（D14），触发条件不变。
