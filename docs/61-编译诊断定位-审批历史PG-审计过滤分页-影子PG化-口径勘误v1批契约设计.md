# 编译诊断定位 / 审批历史 PG / 审计过滤分页 / 影子运行 PG / 口径勘误 v1 批契约设计（打包 H）

> **✅ 落码收口注记（2026-09-25，打包 H 五项全部落码；push 状态唯一权威见 handoff 顶部收口块（批 H 已全部在 origin/dev，未合 main）；本注记为最终事实，与下文正文冲突处以本注记为准）**
>
> **提交链（15 个提交自 `8ba3a29` 起，逐项 feat/test/docs 分原子）**：H1 `adb9b7c`(fix 定位 bug) → `bbb9a2c`(feat) → `eea76ad`(test)；H2 `33a5998`(feat) → `528f6b1`(test) → `e380f0b`(feat frontend 投影)；H3 `8a9f7df`(feat 后端) → `36a3cbb`(feat 前端) → `a07794c`(test)；H4 `2bb6d3c`(feat) → `3eac4af`(test)；H5 `55c1553`(docs 注释勘误)；**收口后追加的 RangePicker 回退** `6fac767`(refactor frontend) + `bdcb15f`(test 双向冒烟) + `a53f36f`/`c7583de`(docs 数字与 push 状态刷新)；另有立项与其后两条 git 状态注记共 3 个 docs 提交。**本链以哈希为准，不记原子数——原子数会随每次追加过期。**
>
> **三道门实跑**：后端内存档 **1799 passed / 105 skipped / 0 failed**（213.73s；基线 1758/86，净增 41 常跑＋19 PG skip）；PG 直连（approval history＋shadow＋audit filters＋原六文件）**89 passed**（迁移 027/028 由 `storage/migrations.py` glob+sorted 自动 apply，`scripts/ops/apply_migrations.py` 已登记 028）；前端 vitest **720 passed / 2 skipped / 53 文件**（基线 694/2/52，净增 26 测：compileDiagnostics 15＋auditFilters 9＋approvals ISO 2）、tsc 0 error、oxlint **0 error / 6 既有 warning（零新增）**、`pnpm build` 过。**bundle 与日期控件决策（收口后追加）**：H3 时间边界最终用**原生 `datetime-local` 双输入**：先按 AntD `DatePicker.RangePicker` 实现并落码，但实测两条硬事实导致回退——① 主 chunk 1708.29→1822.37 kB（**+112 kB**，全仓首个日期组件把 dayjs 一并拖入）；② RangePicker 的月/星期名取自 **dayjs 全局 locale**，而 dayjs 在 pnpm 隔离布局下**不可解析**（只存在于 `.pnpm/dayjs@1.11.23`，`require.resolve` 报 MODULE_NOT_FOUND），要正经本地化就得把 dayjs 提升为**声明的直接依赖**（AGENTS.md 要求的选型决策，不在本批擅改范围），否则中文后台渲染出 `2026年Sep` + `Su Mo Tu`。回退后 bundle **1712.68 kB / gzip 531.12 kB（相对基线仅 +4.4 kB / +1.4 kB gz）**，缺陷与体积同时消失、能力不丢。。chunk>500 kB 提示为既有。
>
> **冒烟**：`scripts/dev/h1_diagnostics_smoke.py` **12/12**（真 :8000+:5174，模板载入→只清空 `/summary`→「编译并运行」触发运行前编译 422→Problems 面板出现带「后端编译」标记的逐条条目与 RFC6901 pointer→点击命中字段闪烁→英文态 chrome 全英文、残留汉字仅业务数据；4 截图 `docs/assets/h1-*.png`；绕开画布连线，见 docs/46 该限制记录；连跑 13 次、末 9 次全绿，早期一次为语言下拉点击竞态，已改为开合重试）。`scripts/dev/h3_audit_filters_smoke.py` **16/16**（HTTP 层：actor 精确、Z 后缀 since、游标第二页不重叠、非法 since→422 中文；UI 层：自铺 120 条写操作使默认 100 条页真有下一页→「加载更多」100→200 行且 id 无重复、不存在 actor 出空态、控制台零应用错误；4 截图 `docs/assets/h3-*.png`）。H2/H4 以 PG 直连「新 store 实例读同一库」等价验证跨重启可见（未跑真实容器重启）。
>
> **本批挖出并修掉的既有 bug（非本批引入）**：M4 的 Problems 面板「点击按 pointer 定位并闪烁字段」**从来没生效过**——`.side-card` 被节点面板/变量面板/属性面板共用，`document.querySelector('.side-card')` 恒命中节点面板，而 `[data-pointer]` 锚点只存在于属性面板表单内；且原实现只在 `selectNode` 后固定等 60ms 查一次、查不到静默 return。修法＝`findFieldAnchor` 逐面板查找（`ProblemsPanel.tsx:26`）＋有界重试 1.2s（`:19` `FLASH_RETRY_MS`、`:55`）。由 H1 冒烟首轮「闪烁字段 0 个」暴露。
>
> **落码偏差（6 条，均已在所在文档同步；锚点为落码后实测行号）**：
> ① **H1 快照落位改 `validationStore`、陈旧判定改 revision 门控**（正文 §2.2 写 `editorStore` + 「编译成功/切图/图变更显式清」）。理由＝`validationStore.ts:1-5` 自述是「校验结果在此汇聚」，且只有它能读 `computedRevision`。现＝`serverIssues`＋`serverIssuesRevision`（`validationStore.ts:23/25`），写入时记下当时的 `computedRevision`（`:73`），`useProblems` 一旦发现引擎重算（＝图已变更）即整体隐藏后端快照（`:102`）。**严于**正文口径：任何编辑都失效，不只是 loadGraph；编译失败仍整体替换、每次尝试开始处显式清（`Editor.tsx` run 起始）。
> ② **H1 用 `DiagnosticLayer` 加 `'server'` 值**（正文 §2.3 写 `source: 'server'`）——共用既有 `Diagnostic` 判别轴，避免平行字段。
> ③ **H1 给 `t()` 新增可选 `lng`**（正文未提）。动机＝oxlint `react-hooks/exhaustive-deps` 正确指出「`language` 是 memo 的**多余依赖**」：语言在 memo 内是**隐式全局读**，依赖是假的。改为渲染路径显式传语言（`locales/index.ts:51` 加 `lng?: Locale`、`:216` `options?.lng ?? language`；`apiClient.ts:118` `compileIssueMessage(issue, lng)`），语言成为真依赖、缺省行为对全部既有调用方零变化。
> ④ **H2 落定点是两个方法共享 `_record_history`**（正文 §3.4 字面「单一落定点」）。实测四类决策来源已全部收敛进 `resolve()`（人工 REST／邮件一键决策／run inputs 预置）与 `complete_timeout()`（超时），故两处各调一次共用 helper（`approvals.py:154/174`，helper `:194`，锁外写、异常只 warning）；`list_decided` 纯委托 store（`:238`），`reset` 连带清历史（`:273`）。§3.3 预告的**破坏性投影变更已执行**（`createdAt` float→UTC ISO＋新增 `resolvedAt`）：实测消费点确实只有 `pages/Approvals.tsx`「已处理」Tab 一处，未触发退化预案；`formatCreatedAt` 改为同时接受 epoch 秒与 ISO 串（pending Tab 仍拿 float）。
> ⑤ **H3 `list()` 保持返回 `list`、`nextCursor` 在 API 层推导**（正文 §4.1 写返回 `(items, nextCursor)`）——tuple 会打破 3 个现有调用点（2 个测试＋1 个端点）。现＝`main.py:570` 满页才给下一页游标。参数名 store 层沿用既有 `action_prefix`、HTTP 层仍是 `action`。
> ⑥ **H3 时间边界按时刻比、不按 TEXT 字典序**（正文 §4.1 写「TEXT 字典序即时间序」）。该前提**只是侥幸成立**：`now_iso()` 在微秒为 0 时会省略小数位，`"+00:00"` 与 `".500000+00:00"` 的字典序恰好同向，属隐式依赖；且前端 `Date.toISOString()` 带 `Z` 后缀、与存储侧 `+00:00` 不可直比。现＝`audit.py:51 parse_bound`（接受 Z、naive 按 UTC）两档统一按 aware datetime 比；PG 侧走 `at::timestamptz >= CAST(:since AS timestamptz)`（`pg.py:2103/2106`，绑参必须 CAST 形式，`:x::type` 会被 SQLAlchemy 当参数名吃掉——照 M11 教训）。游标 `seq < :cursor`（`pg.py:2098`）。
> ⑦ **H4 `inputs` 列改为可空 JSONB**（正文 §5.2 写 `NOT NULL DEFAULT '{}'`）——内存档把「无入参」存 `None`，落成 `{}` 会让两档在前端留空的那个字段上投影分叉；`pg_shadow.py:98` 原样存 NULL，并有一条专门的对拍用例锁死（`test_null_inputs_stays_null_across_tiers`）。
>
> **测试初稿自误 4 处（均为改测试、产品正确，留痕防「测试即规格」误读）**：H2 两条把 `createdAt` 仍按 float 断言（ISO 变更预期内，已改判 ISO 可解析＋`resolvedAt >= createdAt`）；H3 翻页用例漏算 fixture 内 login 自身那条审计＋末页 `nextCursor=null` 时又请求了一页；H3 `_entry`/PG 租户参数各写错一次；H4 夹具用了分类器不认的工具名 `shop/create_refund` 并期望 `match=True`（`_refund_class` 只认 `shop/execute_refund` 等，且无人工结果时 `match` 恒为 `None`）。
>
> **未解除缓做、余部不变**：D12 i18next/navigator 探测/复数、D13 元数据多语言、D20 pending 挂起 PG 化之外的多实例决策竞态·评论流·动态审批人、D11 SIEM·保留归档·`resource_id` 维度·给 GET 补记审计、D26 影子自动旁路·SSE·报表趋势·跨租户共享、D22 YAML/oauth2。

> 立项时间：2026-09-25。AI 承接「打包 1–5 先做，出一份 docs-only 契约立项」授权。docs-only 契约先行，落码按本文 §9 原子序。

> 部分取回 docs/14 的 D11 / D20 / D26 余部，并收口 docs/20 M2 的 `locations` 侧车契约遗留；**全部进程内/单库可闭环、零外部资源、零新依赖（不引 PyYAML/定时器/OTel/SIEM 客户端），新增迁移 027/028，不新增 ADR，不解除任何缓做条目**。
> 本文是打包 H 五项的唯一形状权威；与既有契约冲突以 01–08 规格文档为准，落码偏差在「落码收口注记」回填。

## 0. 已核实的现状缺口（2026-09-25 对活代码）

| 项 | 现状（磁盘核实） | 缺口 |
|---|---|---|
| H1 编译诊断定位 | 后端 422 body **已含四路并行信息**：`{"detail":[中文…],"codes":[…],"params":[…],"locations":[稀疏]}`（`api/main.py:307-319` GraphValidationError handler；侧车由 `graph/dsl.py:155-169 _Issues.add` 生成 `{"index", nodeId?, pointer?}`，pointer 经 RFC6901 转义 `dsl.py:67-69`）。前端 `apiClient.ts:253/268〔**立项时行号**；H1 落码后两处 throw 已移至 `apiClient.ts:344/359`〕` **只读 `body.detail/codes/params`，`locations` 被丢弃**；`resolveValidationList`（`apiClient.ts:31-61`）把逐条映射结果 **join 成单串** 抛 `new Error(...)`。Problems 面板数据源是本地 `useProblems()`（`components/canvas/ProblemsPanel.tsx:12/37`，来自 `store/validationStore`），但**面板已具备按 pointer 定位能力**（`ProblemsPanel.tsx:23-24` 用 `[data-pointer="…"]` / 前缀匹配 + `FLASH_CLASS` 闪烁）。 | M2 设计的 `locations` 侧车从未被前端消费：编译 422 错误无法逐条定位到节点/字段，只能读一坨 join 串 |
| H2 已决审批历史 | `collaboration/approvals.py`：`_Pending`（:20-38）字段全集含 `created_at: float`（:28，`time.time()` epoch）与 `resolved_at: float`（:38）；`list_decided`（:188-194）clamp 1-200、按 resolved_at 倒序；投影 `_decided`（:203-218）出 token/node_id/graph_id/summary/approver/createdAt/decision/resolvedBy/comment(+cardTemplateId)，**不含 resolved_at/notify_recipients/card_context/action_id**。端点 `GET /api/approvals/decided`（`api/main.py:3011-3019`，`require("read")`）。装配 `iam/registry.py:132` **两档都注入内存 `ApprovalBroker()`**；PG 档只有挂起帧经 `storage/recovery.py:16-45 make_frame_sink` 落 `interruptions` 表。 | 已决历史纯进程内，reset/重启即失；PG 档跨实例不可见（D20 显式遗留） |
| H3 审计过滤分页 | `observability/audit.py:51-61` AuditEvent **恰好 8 字段**（id/tenantId/actor/action/statusCode/path/ip/at），**无 seq**；`at` 为 TEXT UTC ISO（:47-48 `datetime.now(timezone.utc).isoformat()`）。内存 `list`（:117-126）clamp 1-500、`reversed` 倒序、仅 `action.startswith(prefix)`；`export_jsonl`（:128-134）正序无 LIMIT。PG `storage/pg.py:2002-2107`：表 `013_audit_events.sql:15-29` **已有 `seq BIGINT` 列**与 `idx_audit_events_tenant_seq (tenant_id, seq DESC)`，`_escape_prefix` LIKE 转义（:2072-2074），`list` `ORDER BY seq DESC LIMIT`（:2083-2087）。端点 `GET /api/audit/events`（`api/main.py:505-518`，administer，query 仅 `limit`/`action`）、`GET /api/audit/export`（:521-537，非 jsonl 422 中文）。前端 `pages/AuditLog.tsx` 仅 action 前缀 Input（:107-120）+ limit Select 50/100/200（:121-129）+ blob 导出（:57-75），Table `pagination={false}`。**项目无全局 RequestValidationError 中文处理器**（仅 GraphValidationError:307 / WaitNodeFailure:322 / ConditionEvalError:339）。 | 无 actor 过滤、无时间范围、无分页（500 条硬顶，超出即不可见）；内存档无 seq 故无法与 PG 档共用游标 |
| H4 影子运行 | `recording/shadow.py:264 ShadowStore`：`SHADOW_RING_SIZE=100`（:31）、`sr-N` 单调计数（:287-288）、`deque`+`threading.Lock`（:268）、方法 add(:272)/get(:306)/list(:311 clamp 1-200)/attach_outcome(:323)/reset(:333)；模型 `ShadowRun`（:78-90）+ `ToolIntent`(:45)/`ShadowDecision`(:56)/`HumanOutcome`(:64)/`ShadowComparison`(:71)。四端点 `api/main.py:1779`(POST operate)/`:1855`/`:1867`(read)/`:1876`(POST operate)。**`registry.py:141` 与 `:169` 两档都注入 `ShadowStore()`**，`shadow.py:11` 明写「不 PG 化」。 | PG 档影子记录重启即失、跨实例不可见（D26 遗留）。**注：同批的 ReportStore 已 PG 化**（迁移 `022_release_reports.sql` + `recording/pg_reports.py:35 PgReportStore` + `registry.py:140` PG 档注入），可直接照抄形状 |
| H5 口径勘误 | 三处**实现已 PG 化但注释/docstring/文档仍写「不 PG 化」**：①`monitoring/alerts.py:74/76/78` 注释称 rule_name/escalated_at/assignee「PG 不持久化/读回 None」，但迁移 `021_alert_lifecycle_columns.sql:7-9` 已加三列、`pg.py:1040-1042 _ALERT_COLS` 已含、`:1033-1038 _alert_from_row` 已读回、`:937-941` INSERT 已带、`:1082-1088` UPDATE 已写 escalated_at；②`monitoring/silences.py:5-6` 同类过时表述；③`api/main.py:1704〔**立项时行号**；实修位置为 `main.py:1765` 报告看板 docstring，`55c1553` 已改〕` docstring 称「报告进程内 ring 不 PG 化」，与迁移 022 + `registry.py:140` 矛盾。docs/33 批 3/批 4 与 docs/14 D26/D28 行内亦有同源过时表述。 | 注释与实现矛盾会误导后续落码（本批勘察即因此差点重复实现 H4 的 ReportStore 与「告警三字段 PG 化」两项已完成工作） |

> **勘察纠正**：立项前候选清单中的「告警 escalated_at/assignee/rule_name PG 化」与「发布报告 ReportStore PG 化」**均已于打包 B（迁移 021）/打包 C（迁移 022）落码完成**，不在本批范围，仅由 H5 清理其过时口径；「Graph DSL 422 code 化＋前端按 code i18n」**已于 i18n 第二批债落码完成**（`dsl.py` 170 码、`validation.json` dsl 块 172 键、`apiClient.resolveValidationList`），H1 只补其真正缺口＝`locations` 侧车前端接线与逐条定位。

## 1. 范围与非目标

### 1.1 本批范围（五项）

1. **H1 编译 422 诊断逐条定位（前端接线 locations 侧车）**：apiClient 新增 `CompileIssue` 与纯函数 `buildCompileIssues`（按 index 把稀疏 locations 归位到等长 detail/codes/params），抛携带 `issues[]` 的 `CompileValidationError` 子类（`message` 仍为 join 串，零回归）；editorStore 增 serverIssues 通道；Problems 面板合并渲染后端诊断并**复用既有 `[data-pointer]` 闪烁/定位机制**；本地与后端诊断按三元组去重。**后端零改动**。
2. **H2 已决审批历史 PG 化**：新模块 `collaboration/history.py`（`ApprovalHistoryStore` Protocol + InMemory ring 200 + Pg 版），迁移 027 `approval_history`，broker 单一决策落定点写历史，投影纯超集补 `resolvedAt` 且 `createdAt` 统一 ISO 串，registry PG 档注入，REST 端点不变。
3. **H3 审计 actor/时间过滤 + 游标分页**：`AuditEvent` 增 `seq`（两档对齐，PG 已有列），两档 `list` 统一增 `actor`（精确）/`since`/`until`（TEXT ISO 字典序）/`cursor`（`seq < cursor`）过滤，响应超集增 `nextCursor`；export 同步受过滤但不受 cursor；端点内手工 422 中文聚合；前端加 actor 输入 + RangePicker + 游标「加载更多」。**零迁移**。
4. **H4 影子运行 PG 化**：新模块 `recording/pg_shadow.py PgShadowStore`（照 `pg_reports.py` 全套抄形），迁移 028 `shadow_runs`（四模型走 JSONB），ring 100 惰性裁剪照 PgDeliveryStore 先例，`TenantServices.shadow_store` 改联合类型，registry PG 档注入。**REST 四端点与前端零改动**。
5. **H5 实现/文档口径勘误**：修 §0 表列出的三处注释/docstring，并按治理规则给 docs/33 与 docs/14 D26/D28 加勘误注记（不改写历史正文）。**零行为变化**。

### 1.2 非目标（继续缓做，触发条件不变）

- H1：不改后端 422 契约（detail/codes/params/locations 形状不动）、不做后端码的 quickFix、不改 422 中文 detail 兜底、不引 i18next；节点目录 label/description 与模板元数据多语言仍属 D13。
- H2：**pending 挂起审批不动**（已由 `storage/recovery.py` 帧表 + `interruptions` 承担）；多实例决策竞态/外部审批信号、通知重发、审批评论流、动态审批人与节点级角色、历史归档与长保留、账号邮箱绑定仍缓做（D20）。
- H3：SIEM/外部转发、保留策略与表分区归档、`resource_id`/对象维度过滤、角色维度、CSV 导出、跨租户平台级审计、给读操作（GET）补记审计仍缓做（D11）；v1 不做 actor 模糊搜索（取值域固定四类）。
- H4：影子自动旁路、影子 SSE、影子报表趋势、跨租户共享、长保留/归档仍缓做（D26）；PG 档沿用 ring 语义（每租户最近 100，与内存一致）。
- H5：不重写 docs/33 历史正文，只加勘误注记；不做全仓注释普查。
- 多实例/NATS/Go 网关/真实渠道联调/OTel 正式栈触发条件不变。

## 2. H1 编译 422 诊断逐条定位契约

### 2.1 apiClient 纯函数与错误类型（`frontend/src/lib/apiClient.ts`）

```ts
export type CompileIssue = {
  index: number            // 对齐 detail 下标（后端 locations.index 同源）
  code?: string            // 后端 codes[i]，可能缺（兜底 GRAPH_VALIDATION_FAILED）
  message: string          // 后端中文 detail[i]，作为 i18n 兜底真相
  params?: Record<string, unknown>
  nodeId?: string          // locations 稀疏侧车，图级错误无此字段
  pointer?: string         // RFC6901，图级错误无此字段
}

export class CompileValidationError extends Error {
  readonly issues: CompileIssue[]   // message 仍为 join 串，既有 toast/日志零回归
}

export function buildCompileIssues(
  detail: string[], codes: unknown, params: unknown, locations: unknown,
): CompileIssue[]
```

- `buildCompileIssues` 纪律：以 **detail 下标为主键**（detail 是等长真相，locations 稀疏）；`locations` 按 `index` 建 Map 后归位；`codes`/`params` 非等长数组时整列忽略（沿用 `resolveValidationList:37` 的 `hasCodes` 判定）；**message 存后端中文原文，不在此处 t() 解析**（见 §2.4 语言切换）。
- `request` 两路径（`apiClient.ts:253/268〔**立项时行号**；H1 落码后两处 throw 已移至 `apiClient.ts:344/359`〕`）：当 `Array.isArray(body?.detail)` 时改抛 `CompileValidationError`，其 `message` 继续走 `resolveErrorMessage`（＝现有 join 串），`issues` 走 `buildCompileIssues`；非数组 detail（运行时 `{code,message}` 对象、字符串）路径完全不变。
- `resolveValidationList` 保留导出与其单条映射逻辑（`validation.dsl.<code>` + params 插值 + 缺键回退中文），`CompileIssue` 的**渲染期**解析复用它抽出的单条映射函数，避免两套映射漂移。

### 2.2 editorStore 通道

- 新增 `serverIssues: CompileIssue[]` 与 `setServerIssues(issues)` / `clearServerIssues()`；与本地 `useProblems()` 物理分离（后端诊断是「上一次编译结果」快照，本地是「当前画布实时」）。
- Editor.tsx 编译/保存失败 catch：`err instanceof CompileValidationError` → `setServerIssues(err.issues)`；编译成功、图变更、切图、语言无关的重新编译前 → `clearServerIssues()`（陈旧诊断不得残留）。

### 2.3 Problems 面板合并渲染（`components/canvas/ProblemsPanel.tsx`）

- 列表＝本地 `useProblems()` ＋ serverIssues 映射项（`source: 'server'`、`severity: 'error'`、带「后端编译」Tag 区分）。
- **去重三元组** `(nodeId, pointer, code)`：本地优先（本地更快且带 quickFix），后端项仅补本地没有的（跨图 `SUBREF_*`、深层子图 `owner` 归并、数据环等本地 L3 覆盖不到的码）。
- 点击定位：有 `nodeId` → 选中并滚动到节点（复用面板既有 selectNode 路径）；有 `pointer` → 复用 `ProblemsPanel.tsx:23-24` 的 `[data-pointer]` 精确/前缀匹配 + `FLASH_CLASS` 闪烁；**图级错误（无 nodeId 且无 pointer）不可点击**，仅展示。

### 2.4 语言切换

- store 只存 `code`/`params`/中文 `message`，**展示时** `t('dsl.'+code, {ns:'validation', defaultValue: 中文})` 解析 → 切语言自动重渲染，无需 `engine.invalidateForLocale()`（该机制服务本地 L1/L2/L3 签名缓存，与后端快照无关）。
- 缺码/未知码/params 缺失一律回退后端中文，**绝不泄漏 i18n key、绝不渲染空白**（与 G1 `runtimeError.ts:163-177` 同纪律，含「插值后仍残留 `{{` 则回退中文」）。
- i18n 三守护（`locales/__tests__/i18n.test.ts:1070-1108`）：本批**不新增键**（170 码已在 `validation.dsl`），若落码发现缺码需补键，则 zh/en 必须同批补齐并进 `PARITY_PAIRS`。

### 2.5 测试

纯函数：稀疏 locations 归位（图级无条目、节点级有 nodeId/pointer、混合）、codes/params 非等长整列忽略、缺码回退中文、`CompileValidationError.message` 与既有 join 串逐字一致（防回归）；去重三元组本地优先；语言切换后同一 issue 渲染英文；图级错误不可点击（声明式 JSX + 构建覆盖）。

## 3. H2 已决审批历史 PG 化契约

### 3.1 存储抽象（新模块 `collaboration/history.py`）

```python
class ApprovalHistoryStore(Protocol):
    def record(self, entry: ApprovalHistoryEntry) -> None: ...
    def list(self, limit: int = 50) -> list[dict]: ...   # resolved_at 倒序、clamp 1-200
    def clear(self) -> None: ...                          # reset 语义（本租户）
```

- `ApprovalHistoryEntry`：token/node_id/graph_id/summary/approver/decision/resolved_by/comment/card_template_id/created_at/resolved_at（**created_at/resolved_at 为 UTC ISO-8601 TEXT**）。
- `InMemoryApprovalHistoryStore`：`deque(maxlen=200)` + `threading.Lock`，搬现 `_Pending` 已决集合语义（现状是 broker 内 `_decided` 累积，本批抽出为 store，broker 缺省注入内存版 → 既有测试零回归）。
- `PgApprovalHistoryStore`：持 engine + tenant_id，行内 `WHERE tenant_id = :tenant_id`；`seq` 取 `nextval('storage_id_seq')`（照 `pg_reports.py:74`）；`list` `ORDER BY seq DESC LIMIT`；ring 200 惰性裁剪照 `message/deliveries.py:110-117` 的 `OFFSET :keep` DELETE 先例；`clear` 只删本租户。

### 3.2 迁移 027（`db/migrations/027_approval_history.sql`，手写幂等；`002_storage.sql` 同步）

```sql
CREATE TABLE IF NOT EXISTS approval_history (
  tenant_id        TEXT NOT NULL,
  token            TEXT NOT NULL,
  seq              BIGINT NOT NULL,
  node_id          TEXT NOT NULL,
  graph_id         TEXT NOT NULL DEFAULT '',
  summary          TEXT NOT NULL DEFAULT '',
  approver         TEXT NOT NULL DEFAULT '',
  decision         TEXT NOT NULL,
  resolved_by      TEXT NOT NULL DEFAULT '',
  comment          TEXT NULL,
  card_template_id TEXT NULL,
  created_at       TEXT NOT NULL,     -- UTC ISO-8601（对齐 002/024 TEXT 约定）
  resolved_at      TEXT NOT NULL,
  PRIMARY KEY (tenant_id, token)
);
CREATE INDEX IF NOT EXISTS idx_approval_history_tenant_seq
  ON approval_history (tenant_id, seq DESC);
```

> 运行器 `storage/migrations.py:35-36` 用 `glob("*.sql") + sorted()` 自动发现，**新文件落进目录即被自动 apply，无清单需改**。

### 3.3 时间列决策与投影变更

- **决策**：`_Pending.created_at/resolved_at` 现为 `time.time()` float epoch（`approvals.py:28/38`），落库与投影**统一转 UTC ISO-8601 TEXT**，对齐 002/024 主约定（不采用 025 `sent_at TIMESTAMPTZ` 的例外）。broker 内存态可继续持 float，仅在写 history / 出投影时转换（单一转换函数，可单测）。
- **投影纯超集 + 一处破坏性变更**：`_decided` 增 `resolvedAt`（新增，安全）；`createdAt` 由 float epoch **改为 ISO 串**（破坏性）。取舍依据：该端点 2026-09-22（docs/37）才上线、消费点仅 `pages/Approvals.tsx`「已处理」Tab 一处，代价可控；落码须 grep 全部消费点同批改（含前端格式化与测试夹具）。若落码发现消费点超过两处，则退化为**并存**（保留 `createdAt` float + 新增 `createdAtIso`），偏差回填本文。
- `notify_recipients`/`card_context`/`action_id` **不入投影**（沿用 docs/37「不泄露 card_context」口径）。

### 3.4 写入点与装配

- **单一落定点**：四类决策来源（人工 REST / 邮件链接 `email-decision` / 超时 / run inputs 预置）全部经 broker 内部同一决策提交函数写 history，**不在四处散写**；写入 fail-safe（store 异常只 warning，绝不阻断审批放行主链路，照 G5 `_safe_record` 先例）。
- `ApprovalBroker(history_store: ApprovalHistoryStore | None = None)` 缺省内存版；`registry.py:132` PG 档注入 `PgApprovalHistoryStore`、内存档显式注入内存版。
- reset：`approval_broker` 已在 `registry.py:190-205` 清理清单内 → history 随之清本租户（**与现状一致：reset 清已决历史**），跨重启保留。
- REST `GET /api/approvals/decided`（`api/main.py:3011-3019`，read，limit clamp 1-200）**签名与权限零改动**。

## 4. H3 审计 actor/时间过滤 + 游标分页契约

### 4.1 模型与两档 store

- `AuditEvent` 增 `seq: int = 0`（**带默认值**，避免既有夹具全改）：内存档由 store 侧单调计数器赋值（`itertools.count` 或 `self._seq += 1`，持锁），PG 档读回 `013_audit_events.sql:17` 既有 `seq` 列。投影回传 `seq` 供前端游标。
- 两档 `list` 签名统一：
  ```python
  def list(self, limit: int = 100, *, action: str | None = None, actor: str | None = None,
           since: str | None = None, until: str | None = None, cursor: int | None = None) -> tuple[list[dict], int | None]
  # 返回 (items, nextCursor)；nextCursor 为本页最后一条 seq，无更多则 None
  ```
  - `action`：前缀匹配（现状沿用；PG 侧 `LIKE :prefix ESCAPE '\'`，`pg.py:2072-2080` 已有转义）。
  - `actor`：**精确等值**（取值域固定：用户名 / `anonymous` / `email-link` / `shopify-webhook`；见 `audit.py:107`、`main.py:377/677/1188/3203/803`）。
  - `since`/`until`：UTC ISO-8601 字符串，**按 TEXT 字典序比较**，闭区间。安全前提＝全部 `at` 由同一 `datetime.now(timezone.utc).isoformat()` 生成（同格式同 `+00:00` 时区 → 字典序＝时间序）；**该前提须以不变量测试锁死**。
  - `cursor`：`seq < cursor`（倒序取更早一页）；首页不传。
  - `limit`＝页大小，clamp 1-500，默认 100（内存 `audit.py:123` / PG `pg.py:2083-2087` 现状一致）。
- `export_jsonl` 增 `action/actor/since/until` 同套过滤，**不接受 cursor**（导出全量匹配），正序 `ORDER BY seq ASC` 不变。
- `AuditRepository` Protocol（`audit.py:64`）与 `storage/base.py` 对应声明同步新签名。

### 4.2 REST（`api/main.py`）

| 方法/路径 | 权限 | 变更 |
|---|---|---|
| `GET /api/audit/events` | administer（不变） | 增 query `actor` / `since` / `until` / `cursor`；响应超集 `{items, limit, nextCursor}`，items 每条增 `seq` |
| `GET /api/audit/export` | administer（不变） | 增 query `actor` / `since` / `until`（无 cursor）；`format!=jsonl` 仍 422 中文 |

- **422 口径**：项目无全局 RequestValidationError 中文处理器，故在端点内手工校验并抛中文聚合 422（照 G3 静默 PUT 先例）：`since`/`until` 非法 ISO-8601、`since > until`、`cursor` 非正整数。`limit` 维持静默 clamp（现状，不改为 422）。
- 审计中间件（`main.py:358-385`）与登录显式记一条（`:1183-1195`）**零改动**；GET 读操作仍不记（非目标）。

### 4.3 前端

- `lib/apiClient.ts`：`AuditEventItem` 增 `seq`；`listAuditEvents(limit, action, opts?)` 增 `actor/since/until/cursor` 并返回 `{items, nextCursor}`；`exportAuditJsonl(action, opts?)` 同步。
- `pages/AuditLog.tsx`：actor 输入框、AntD `RangePicker`（**本地时区选择 → 转 UTC ISO 传参**，转换抽为纯函数可单测）、Table 由 `pagination={false}` 改**游标「加载更多」**（累积 items，`nextCursor === null` 时禁用并显示「无更多」）；导出沿用 blob 下载并带当前过滤条件。
- `locales/*/audit.json` 补键（zh/en 结构对齐、en 零汉字、进 `PARITY_PAIRS` 三守护）。

## 5. H4 影子运行 PG 化契约

### 5.1 PgShadowStore（新模块 `recording/pg_shadow.py`）

- **照 `recording/pg_reports.py:35 PgReportStore` 全套抄形**：`__init__(engine, tenant_id)`、行内租户过滤、方法签名与 `ShadowStore`（`shadow.py:264`）一比一 —— `add / get / list(clamp 1-200) / attach_outcome / reset`。
- id 生成：`nextval('storage_id_seq')` → `sr-{n}`（与内存 `shadow.py:287-288` 的 `sr-N` 同形，数字部分另存 `seq` 列供排序，照 `pg_reports.py:6/74` 口径）。
- ring 100 惰性裁剪：`add` 后 `DELETE … WHERE seq IN (SELECT seq … ORDER BY seq DESC OFFSET :keep)`，`keep=SHADOW_RING_SIZE`（照 `message/deliveries.py:110-117`）；并发下短暂超 100 可接受（照 G5 口径，不做强一致裁剪）。
- `attach_outcome` 写 `human_outcome` / `comparison` 两 JSONB 列；未知 sid 返 None（与内存同语义）。
- `reset` 只删本租户。

### 5.2 迁移 028（`db/migrations/028_shadow_runs.sql`，手写幂等；`002_storage.sql` 同步）

```sql
CREATE TABLE IF NOT EXISTS shadow_runs (
  tenant_id     TEXT NOT NULL,
  id            TEXT NOT NULL,
  seq           BIGINT NOT NULL,
  graph_id      TEXT NOT NULL,
  inputs        JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL,
  error         TEXT NULL,
  decisions     JSONB NOT NULL DEFAULT '[]',   -- ShadowDecision[]
  tool_intents  JSONB NOT NULL DEFAULT '[]',   -- ToolIntent[]
  trace_id      TEXT NULL,
  auto_action   TEXT NULL,
  human_outcome JSONB NULL,                    -- HumanOutcome
  comparison    JSONB NULL,                    -- ShadowComparison
  created_at    TEXT NOT NULL,                 -- UTC ISO-8601（shadow.py:301 _now_iso）
  PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_tenant_seq
  ON shadow_runs (tenant_id, seq DESC);
```

- 四个 pydantic 子模型走 JSONB，读写用 `model_dump()` / `model_validate()`（照 `openapi_imports.operations` 与 `monitoring_runs.spans` 先例）。

### 5.3 装配

- `TenantServices.shadow_store: ShadowStore | PgShadowStore`（联合类型，照 `registry.py:74 report_store` 先例）；`registry.py:141` PG 档换 `PgShadowStore(backend.engine, tenant_id)`、`:169` 内存档不变。
- reset：`shadow_store` 已在 `registry.py:204` 清理清单内，PG 版 `reset()` 删本租户。
- **REST 四端点（`main.py:1779/1855/1867/1876`）与前端零改动**（照 ReportStore 先例）；`shadow.py:11` 的「不 PG 化」注释随本批更新。

## 6. H5 口径勘误清单（零行为变化）

| 位置 | 现表述 | 改为 |
|---|---|---|
| `monitoring/alerts.py:74/76/78` | rule_name/escalated_at/assignee「PG 不持久化 / 读回 None」 | 已随迁移 `021_alert_lifecycle_columns.sql` PG 化（`pg.py:1040-1042/1033-1038/937-941/1082-1088`），打包 B 落码 |
| `monitoring/silences.py:5-6` | 静默/值班「进程内不 PG 化」 | 已随迁移 `024_monitoring_silences_oncall.sql` PG 化（打包 F F-2），内存档仍为 OpsStore |
| `api/main.py:1704〔**立项时行号**；实修位置为 `main.py:1765` 报告看板 docstring，`55c1553` 已改〕` docstring | 「报告进程内 ring 不 PG 化」 | 已随迁移 `022_release_reports.sql` + `recording/pg_reports.py` PG 化（打包 C），PG 档不做 ring 淘汰 |
| docs/33 批 3/批 4、docs/14 D26/D28 行内同源表述 | 「ring 不 PG 化」「escalated_at/assignee 不持久化」 | **加勘误注记**（不改写历史正文，遵 00 治理规则） |

## 7. 迁移 / Schema / 接口汇总

- **新增迁移**：`027_approval_history.sql`（H2）、`028_shadow_runs.sql`（H4）；`db/002_storage.sql`（新装库建表脚本）同步两表。运行器自动发现，无清单改动。
- **Schema 变更（docs/03）**：`AuditEvent` 增 `seq`；审批已决投影增 `resolvedAt`、`createdAt` 由 float epoch 改 ISO 串（破坏性，见 §3.3 退化预案）；`ShadowRun` 落库形状（四子模型 JSONB）；`ApprovalHistoryEntry` 新增。
- **API（docs/12）**：**零新端点**。`GET /api/audit/events` 与 `/api/audit/export` 增 `actor/since/until/cursor` 参数与 `nextCursor` 响应字段；`GET /api/approvals/decided` 投影超集；影子四端点与编译 422 形状均不变。
- **错误码**：**零新增**（H1 复用既有 170 编译码；H3 的 422 为中文聚合校验，不引入码）。
- **权限**：全部不变（audit 仍 administer、decided 仍 read、shadow 仍 operate/read、编译仍按图权限）。
- **依赖**：零新增（`pyproject.toml` / `frontend/package.json` 不变）。

## 8. 测试契约（候选 U743 起，docs/13 收口回填定稿）

- **H1（候选 U743–U754）**：`buildCompileIssues` 稀疏 locations 归位（图级无条目 / 节点级 nodeId+pointer / 混合）、codes·params 非等长整列忽略、缺码回退中文、`CompileValidationError.message` 与既有 join 串逐字一致、三元组去重本地优先、语言切换渲染英文、图级错误不可点击。
- **H2（候选 U755–U770）**：内存/PG 两档 record·list 倒序·clamp 1-200·ring 200 惰性裁剪·clear；四决策来源（人工/邮件链接/超时/预置）均落历史；float→ISO 转换纯函数；投影含 `resolvedAt`；PG 直连往返 + 跨「重启」（新 store 实例同库）可见 + reset 清本租户；迁移 027 apply；store 异常 fail-safe 不阻断审批放行。
- **H3（候选 U771–U790）**：actor 精确匹配；since/until 字典序边界（含闭区间端点）；**`at` 全 UTC 同格式不变量**；cursor 翻页（多页连贯、末页 nextCursor=null）；四条件组合；export 受过滤不受 cursor、仍正序；422（非法 ISO / since>until / cursor 非正整数）中文聚合；内存与 PG 两档对拍；前端 RangePicker→UTC 纯函数与游标累积、audit 键 zh/en 对齐与 en 零汉字。
- **H4（候选 U791–U806）**：PgShadowStore add/get/list/attach_outcome/reset；四子模型 JSONB 往返等价（两档对拍）；`sr-N` 与 seq 排序；ring 100 惰性裁剪；跨「重启」可见；reset 清本租户；registry 两档装配；迁移 028 apply；四端点 PG 档黑盒。
- **H5**：无新增测试，靠三道门零回归 + 注释断言不适用（纯注释/文档）。
- 纪律：前端沿用 vitest 纯函数 + i18n 三守护（无 jsdom、不引新依赖）；后端 PG 例用 `ATLAS_RUN_INTEGRATION=1` + `DATABASE_URL` 直连；**只许增测，不许减**。
- **立项基线（打包 G 收口实跑）**：后端内存门 **1758 passed / 86 skipped / 0 failed**、PG 直连集成（migrations/storage/database/openapi/monitoring/message 六文件）**55 passed**、前端 vitest **694 passed / 2 skipped / 52 文件**、tsc 0 error、oxlint **0 error / 6 既有 warning**（cards `react-hooks/set-state-in-effect`，本批不碰）、build 过（1708.29 kB / gzip 529.73 kB）。收口须重跑同套门 + PG 直连（新增 027/028 两表文件自动 apply）。

## 9. 原子提交序（feat/test/docs 分开，英文 message，不 push）

1. `docs(runtime): propose batch H contract (compile diagnostics/approval history pg/audit filters/shadow pg/doc corrections)` — 本文 + docs/08 立项条 + docs/14 D11/D20/D26 注记（部分取回不解除）+ docs/00 地图 + docs/03 骨架占位 + handoff 进度 + CHANGELOG。
2. H1：`feat(frontend): surface backend compile diagnostics with pointer locations`（apiClient + editorStore + ProblemsPanel + Editor）→ `test(frontend): cover compile issue assembly and dedupe (U743-U754)`。
3. H2：`feat(collaboration): persist decided approval history in memory and postgres`（history.py + 迁移 027 + 002 + broker 接线 + registry）→ `test(collaboration): cover approval history stores (U755-U770)` → `feat(frontend): show resolved time in decided approvals`。
4. H3：`feat(observability): add audit actor and time filters with cursor pagination`（audit.py + pg.py + base.py + 两端点）→ `test(observability): cover audit filtering and pagination (U771-U790)` → `feat(frontend): audit filter and cursor pagination UI`。
5. H4：`feat(recording): add postgres shadow run store`（pg_shadow.py + 迁移 028 + 002 + registry + shadow.py 注释）→ `test(recording): cover pg shadow store (U791-U806)`。
6. H5：`docs(monitoring): correct stale in-memory-only comments after pg migrations`（三处注释/docstring + docs/33·14 勘误注记）。
7. 收口：三道门实跑（后端全量 + PG 直连 + 前端 vitest/tsc+build/oxlint）→ 真实浏览器冒烟（编译失败逐条定位闪烁、审计过滤翻页、已处理 Tab resolvedAt、影子跨重启）→ 文档矩阵回填（03/04/06/09/12/13/14/00/08/CHANGELOG/handoff，含门数字与偏差）→ `docs(runtime): close out batch H with gate results`。

> handoff 进度可在每个功能原子后按惯例补小 docs 提交（参照打包 F/G），但不计入功能原子。
>
> **收口时顺带订正（2026-09-25 立项后核对 git 状态时发现，用户已同意随本批收口一并改）**：handoff 顶部状态块与 Active work 若干条仍写「打包 G 14 个功能原子在 dev、**未 push**」「合 main PR 须用户明确授权」，但实测两条均已过时：`origin/dev` 已含到 `8ba3a29`（打包 G 全批含收口 docs 原子均在远端），且 `git merge-base --is-ancestor 8ba3a29 origin/main` 为真＝**打包 G 已合并 main**。属状态措辞过时、非待办丢失；收口原子内按实测 `git rev-list --left-right --count origin/dev...dev` 与 `--is-ancestor` 重测后订正，并同步检查同段其余 push/合并表述。

## 10. 契约同步矩阵（立项原子内完成骨架，收口回填门数字）

| 文档 | 立项时 | 收口时 |
|---|---|---|
| docs/08 | 加打包 H 立项条（范围/§3.3 投影决策/测试候选/基线门数字） | 加落码收口条（提交链/门数字/偏差/仍缓做） |
| docs/14 | D11（审计过滤分页）/D20（审批历史 PG）/D26（影子 PG）加「部分取回、不解除」注记；D26/D28 加 H5 勘误注记 | 补落码结果与剩余余部 |
| docs/00 | 文档地图加 docs/61 | 状态更新为已落码收口 |
| docs/03 | 记 AuditEvent.seq、审批已决投影 createdAt/resolvedAt、ApprovalHistoryEntry、ShadowRun 落库形状（占位） | 定稿 |
| docs/12 | 记 audit 两端点新参与 nextCursor、decided 投影超集、零新端点结论 | 定稿 |
| docs/04/06/09 | Problems 面板后端诊断合并落点、审计过滤运行时口径、新模块（collaboration/history.py、recording/pg_shadow.py）骨架 | 定稿 |
| docs/11 | 027/028 两表登记与读写路径 | 定稿 |
| docs/13 | — | §9 落码表回填正式 U 编号与文件 |
| docs/17 | H1 复用 validation.dsl 键、不新增 namespace 的结论 | 若补键则定稿 |
| docs/20 | M2 `locations` 侧车前端接线遗留收口 | 定稿 |
| CHANGELOG | [Unreleased] 加打包 H 立项条目（倒序） | 门数字与偏差 |
| handoff.md | Active work 加打包 H、Project documents 加 docs/61 索引 | 归档完成项、Recently shipped、门数字 |

## 11. 风险与回滚

- **H1 诊断噪声/重复**：本地 L1/L2/L3 与后端 422 会对同一问题各报一条（M4 已做同构对拍，重合是常态）→ 以 `(nodeId, pointer, code)` 三元组去重、本地优先、后端项加 Tag 区分；`CompileValidationError` 必须保持 `message` 为既有 join 串，否则 toast/日志/既有断言全线回归（以逐字一致单测锁死）。陈旧诊断残留风险 → 编译成功/切图/图变更即 `clearServerIssues()`。
- **H2 投影破坏性变更**：`createdAt` float→ISO 影响前端格式化与测试夹具。缓解＝消费点仅一处（docs/37 才上线）+ 落码先 grep 全量消费点 + §3.3 并存退化预案；PG 档写入 fail-safe（try/except 只 warning），**绝不因历史落库失败阻断审批放行**（照 G5 `_safe_record` 教训）。
- **H3 TEXT 字典序时间比较**：依赖「全部 `at` 为同一 UTC ISO 格式」不变量，一旦有非 UTC 或不同精度写入即静默错序 → 加不变量测试 + 端点内校验入参格式；`seq` 新字段带默认值 0 以免既有夹具全改，但**内存档必须由 store 赋真值**，否则游标翻页在内存档失效（以两档对拍测试锁死）。
- **H4 JSONB 往返**：四子模型字段增删易漏 → 两档对拍等价测试；ring 裁剪 SQL 并发下短暂超 100 可接受（v1 不做强一致）。
- **迁移**：027/028 均为新增表，向前兼容；回滚＝drop 表 + 回退代码，不影响既有数据。H5 为纯注释/文档，回滚即 revert。
- **批次内耦合**：五项彼此无依赖，任一原子可独立回退；H5 建议放最后以免与功能原子的注释改动冲突。
