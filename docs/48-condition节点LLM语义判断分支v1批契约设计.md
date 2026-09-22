# 48. condition 节点 LLM 语义判断分支 v1 批契约设计

> 立项：2026-09-23（AI 判断承接「不用管git，你推任务」总授权；docs-only 本原子，落码前不开工）
> 缓做来源：docs/14 **D14**（condition 节点的 LLM 判断分支：自然语言/语义条件，而非规则表达式）
> 性质：**D14 部分取回、不解除**；零新依赖（litellm 已在依赖内）、零数据库迁移、无 ADR、不新增节点类型、EdgeDSL/Graph version 不变、不新增 REST 端点。

## 1. 背景与范围

condition 节点 v1（04 §5.2）只支持规则表达式分支：分支条件必须是布尔白名单表达式（04 §5.1）。大量真实分流场景写不成规则——「客服语气是否愤怒」「退款理由是否合理」「邮件是否在投诉物流」——业务上只能挤进人工审核或放任不分流。

本批取 **conditionMode=llm 的进程内 v1**：节点执行时把运行上下文（全局变量 + 上游节点产出）与各分支的**自然语言描述**一次性交给 LLM，模型返回唯一分支标签，按标签路由；任何异常/无法解析/标签越界一律 **fail-safe 走 defaultTarget**（D14 触发条件的硬约束）。LLM 调用复用 LiteLLM 接入与 `LITELLM_MODEL` 环境开关，未配置模型时确定性回退到默认分支，离线测试注入假分类器保持确定性。

### 范围内

1. DSL：condition config 增 `conditionMode` 与 llm 分支字段、编译校验（§2）。
2. Runtime：新分类器客户端（规则/离线兜底 + LiteLLM 实现）与 loader llm 求值（§3）。
3. 前端：condition 属性面板支持模式切换与语义分支描述编辑、L1 校验（§4）。

### 显式非目标（仍缓做，收口时回写 D14 注记）

- **置信度阈值/多候选/澄清重问**：v1 只接受唯一标签，无 confidence、无多分支同时命中、无二次提问。
- **每分支独立 prompt / 每分支一次 LLM 调用**：v1 每节点恰好一次调用、全部分支共享 prompt。
- **模型可选/多模型路由/按租户配置模型**：沿用全局 `LITELLM_MODEL` 单开关（同 decision client）。
- **发送上下文的字段级脱敏/秘密变量过滤**：v1 仅做大小截断；含秘密变量的节点上游不应接入 llm condition（同既有 ai_decision 节点把业务字段送模的取舍，随 D22/D30 补强）。
- **流式输出、函数调用/structured outputs 协议**：v1 走纯文本 JSON 协议（temperature=0），同 decision.py。
- **语义分支的录制回放确定性锚定**：LLM 返回本身非确定；录制/回放对 llm condition 只记录路由结果不做断言比对（与 shadow 比对里 decisions 字段的展示一致），重跑稳定化随评估层后续批。
- **debug 单步流中的 llm 求值限制**：不限制（LLM 调用快、不挂起），与 wait event 不同。

## 2. DSL 契约（04 §5.2 追加，唯一权威落 04）

```yaml
type: condition
config:
  conditionMode: rule          # rule | llm；缺省 rule（旧图零回归）
  classifierPrompt: "可选的附加判定要求，如：优先保护客户体验"   # llm 可选，≤500 字符
  branches:
    - label: "愤怒投诉"
      description: "客户消息表达强烈不满、指责或威胁投诉升级"  # llm 模式用 description
      target: node-human
    - label: "普通咨询"
      description: "客户仅询问订单状态或物流进度，语气平和"
      target: node-reply
  defaultTarget: node-review
```

字段与校验（dsl.py `_validate_condition_config`；错误前缀沿用 condition 现状，pointer 落对应字段）：

- `conditionMode`：可选，`rule | llm`；非法值 422（pointer `/conditionMode`）；缺省按 rule。
- `rule` 模式：**完全沿用现规则**——每分支 `{label, expression, target}`，expression 经 `validate_expression` 静态校验；旧图、旧测试零回归。
- `llm` 模式：
  - 每分支形状 `{label, description, target}`：
    - `label`：沿用现规则（非空、分支间唯一）。
    - `description`：必填字符串，strip 后非空、长度 ≤300；**不允许 `expression` 字段**（出现即 422，pointer 该分支 `/expression`，防止两模式字段混用）。
    - `target`：沿用现规则（非空、非自身、节点存在、分支间唯一、≠ defaultTarget）。
  - `classifierPrompt`：可选字符串，strip 后 ≤500；超长 422。
  - 分支数量沿用现上下限（至少 1，上限不变）。
- 拓扑约束两模式完全一致：每条出边必须被 branches∪default 覆盖、不允许直连 END、不允许死边；边结构不变。

## 3. 运行时语义（实现 `atlas.graph.loader` + 新分类器）

### 3.1 分类器客户端（新文件 `src/atlas/llm/condition_classifier.py`）

镜像 `atlas.llm.decision` 三层结构：

```python
class ConditionClassifyError(Exception): ...

class ConditionClassifier(Protocol):
    def classify(self, *, branches: list[dict], context_text: str, instruction: str) -> str:
        """返回唯一分支 label；无法判定抛 ConditionClassifyError。"""

class OfflineConditionClassifier:
    # LITELLM_MODEL 未配置时使用：任何调用都抛 ConditionClassifyError
    # （语义判定没有可信的确定性规则；fail-safe 由 loader 落 default）

class LiteLLMConditionClassifier:
    # 懒加载 import litellm；temperature=0；system prompt 要求只输出 JSON {"branch": "<label>"}
    # 正则 re.compile(r"\{.*\}", re.DOTALL) 抽 JSON；解析失败/标签不在分支集 → ConditionClassifyError

def get_condition_classifier() -> ConditionClassifier:
    # 读 os.getenv("LITELLM_MODEL", "").strip()：非空 LiteLLM，否则 Offline
```

prompt 内容（system）：声明任务为按分支描述选择唯一最匹配分支、只能从给定标签中选、无匹配则选 `"__default__"`、只输出 JSON；user 内容＝分支表（label + description）、可选 instruction（classifierPrompt）、context_text。

### 3.2 上下文序列化

`context_text = json.dumps({"global": 全局变量, "nodes": 已完成节点 outputs 浅投影}, ensure_ascii=False, default=str)`：

- 截断上限 **12000 字符**（超出头部保留并追加截断标记）；截断本身不构成错误。
- 序列化异常（default=str 仍失败）→ 视为分类失败，fail-safe（§3.4）。

### 3.3 执行流程（loader `_execute_condition` llm 分支）

1. rule 模式走现有表达式短路逻辑，零改动。
2. llm 模式：序列化上下文 → 调 `classifier.classify(...)`：
   - 返回合法 label（在分支集内）→ `branch=label, target=对应 target`。
   - 返回 `"__default__"`、抛 `ConditionClassifyError`、任何其他异常 → `branch="__default__", target=defaultTarget`，错误文案进 `llm_errors`。
3. 分类器注入：`run_graph(..., condition_classifier: Any | None = None)`，缺省 `get_condition_classifier()`；与 `decision_client` 同样沿编译/执行链透传（含子图重入）；测试可注入假分类器。

节点产出（llm 模式）：

```json
{
  "mode": "llm",
  "branch": "普通咨询",
  "target": "node-reply",
  "evaluation": [
    {"label": "愤怒投诉", "description": "…", "result": false},
    {"label": "普通咨询", "description": "…", "result": true}
  ],
  "llm_errors": []
}
```

fail-safe 时 `branch="__default__"`、evaluation 各项 `result=null`、`llm_errors` 带中文原因（「LLM 未配置」「LLM 返回无法解析」「LLM 返回了未知分支标签」「LLM 调用异常：…」）。`branch`/`target` 两键与 rule 模式保持同位置，路由闭包与下游 `{{condition-x.branch}}`/`{{condition-x.target}}` 引用零改动。trace 行：`condition-x: llm branch=… → target` / `… llm fallback → defaultTarget（原因）`。

### 3.4 失败口径

**无新增失败错误码**：llm condition 不使节点/run 失败，全部异常归一为 defaultTarget 路由（硬 fail-safe）。错误仅在节点产出 `llm_errors` 与 trace 可见。模型提供方网络/配额错误同样按此处理，不向上抛。

## 4. REST

无新增端点、无新增 SSE 帧、无 run inputs 运行控制键。现有 run/监控/trace 投影天然携带新产出字段。

## 5. 前端

- `nodeCatalog.ts`：condition config 增 `conditionMode?: 'rule' | 'llm'`、`classifierPrompt?: string`；`ConditionBranch` 增可选 `description?: string`（llm 模式），expression 在 llm 模式缺省。
- `condition.schema.ts`：
  - 增 `conditionMode`（枚举 widget：规则表达式 / LLM 语义判断，默认 rule）与 `classifierPrompt`（textarea，≤500）。
  - branches item 按 mode 切换字段：rule＝expression（现状）；llm＝description（textarea，≤300，required），不渲染 expression 行。
  - 分支 label/target 唯一性与 defaultTarget 互异等跨字段规则仍由 L1 手写产出。
- `ConditionConfig.tsx`：标题下说明文案随模式变化（llm：单次调用选择最匹配分支，异常自动走默认分支）。
- L1（`validation/l1.ts`）：llm 模式分支跳过 `validateExpression`、改校验 description 非空/≤300；禁止 llm 分支携带非空 expression；classifierPrompt ≤500；纯逻辑落 lib，vitest 覆盖。图级出边覆盖仍由后端 422 兜底。
- 不新增 i18n namespace（ConditionConfig 现行中文硬编码，同现状）。

## 6. 契约同步矩阵

| 文档 | 改动 |
|---|---|
| 04 §5.2 | 追加 llm v1 blockquote（conditionMode/分支形状/校验/运行时/产出/fail-safe）；§二节点总览 condition 行加注 |
| 03 | condition config 形状注释更新（rule/llm 两模式与 description 字段）；指向 04 §5.2 |
| 06 | 新增 §6.23：LLM 语义分支运行时（分类器三层、上下文序列化、注入与 fail-safe） |
| 09 | llm/ 模块映射加 `condition_classifier.py`；run_graph 新增 condition_classifier 参数 |
| 12 | §3 内部接口加 3.18 ConditionClassifier（Protocol/两实现/工厂）；REST 无变动 |
| 13 | U303：分类器纯逻辑（JSON 抽取/标签越界/离线抛错）、DSL 两模式校验、loader fail-safe 四路径、前端 L1 |
| 14 | D14 行加「进程内 llm v1 已落码」部分取回注记（不解除） |
| 08 | 本立项记录；收口条在落码后补 |
| 00 | 文档地图加 docs/48 行 |
| handoff | 顶部 banner + Active work 新编号（立项态） |
| CHANGELOG | 立项条目；落码后在收口原子更新 |

## 7. 原子序（英文 message；无 co-author；不 push）

1. docs 立项（本原子）。
2. `feat(llm): add LLM condition branch classifier with offline fallback` + 分类器单测。
3. `feat(graph): support LLM semantic condition branches`（dsl 校验 + loader + run_graph 透传）+ 后端测。
4. `feat(frontend): enable LLM semantic condition configuration`（schema + ConditionConfig + L1 + vitest）。
5. docs 收口：HTTP/浏览器冒烟（llm 命中路由、假分类器、离线/坏 JSON/未知标签/异常全走 default、旧 rule 图零回归）＋证据截图＋04/06/09/12/13/14/08/48/00/handoff/CHANGELOG 回填。

## 8. 验收门

- 后端：`.venv/bin/pytest`（立项基线 1435 passed / 59 skipped，只许增测）。
- 前端：`cd frontend && pnpm lint && pnpm test && pnpm build`（基线 606 passed / 2 skipped）。
- 冒烟（真实 HTTP，:8000；经注入式假分类器或预置行为，不依赖真实供应商 key）：① llm condition 图分类器返回合法标签 → run completed、路由正确、产出 mode=llm；② 离线分类器（无 LITELLM_MODEL）→ defaultTarget 路由、llm_errors 说明未配置；③ 坏 JSON / 未知标签 / 抛异常三种假分类器均走 defaultTarget 且 run completed；④ 旧 rule 模式 condition 图行为零回归；⑤ 保存期 DSL：llm 分支带 expression 422、description 空/超长 422、conditionMode 非法 422；⑥ 浏览器：模式切换后分支行字段切换、默认分支保存与运行成功、控制台零错误。
