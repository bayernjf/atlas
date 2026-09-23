# 49. wait 节点定时等待动态时长 v1 批契约设计

> 立项：2026-09-23（AI 判断承接「不用管git，你推任务」总授权；docs-only 本原子，落码前不开工）
> 缓做来源：docs/14 **D19** 余部（动态/表达式时长；event 等待已随 docs/47 落码）
> 性质：**D19 部分取回、不解除**；零新依赖、零数据库迁移、无 ADR、不新增节点类型、Graph version 不变、不新增 REST 端点或错误码以外的运行控制。

## 1. 背景与范围

wait 节点定时等待（04 §5.5）的时长只能是保存期写死的整数秒 1-600。真实流程里等待时长常由运行期数据决定：「按 SLA 等级等待 N 小时」「等待上游算出的缓冲秒数」——写死时长只能为每种取值建一张图。

本批取 **定时等待 durationMode=dynamic 的进程内 v1**：config 增一条时长表达式（变量插值＋算术，复用 D15 表达式引擎），节点执行时求值出秒数再等待；表达式无法求值或结果非法时 **确定性失败（WAIT_DURATION_INVALID）**，不等待错误时长。旧图（无 durationMode）一律按 static，零回归。

### 范围内

1. DSL：wait duration config 增 `durationMode` 与 `durationExpression`、编译校验（§2）。
2. Runtime：loader duration 分支求值表达式（§3）。
3. 前端：WaitConfig 面板支持静态/动态切换与表达式编辑、L1 校验（§5）。

### 显式非目标（仍缓做，收口时回写 D19 注记）

- **到点时刻等待（waitUntil：等到某个绝对时间戳/日期）**：v1 只做时长（相对秒数），不做绝对时刻。
- **事件等待 timeoutSeconds 的表达式化**：本批只覆盖定时等待；event 的 timeoutSeconds 仍须静态整数。
- **超长等待上限放宽**：动态结果仍受 1-600 秒约束，MAX_WAIT_SECONDS 不变。
- **跨重启/多实例中断帧与恢复扫描器**：动态时长只改变帧的 deadline 取值，持久化恢复语义仍随 D19/D20 中断模型余部。
- **多表达式/区间随机抖动（jitter）/多单位（分钟·小时控件）**：v1 表达式只返回秒数整数，单位换算由用户在表达式内书写。

## 2. DSL 契约（04 §5.5 追加，唯一权威落 04）

```yaml
type: wait
config:
  waitType: duration
  durationMode: static            # static | dynamic；缺省 static（旧图零回归）
  durationSeconds: 5              # static：整数 1-600（现状不变）
  durationExpression: "{{global.slaHours}} * 3600"  # dynamic：必填，≤200 字符
```

字段与校验（dsl.py `_validate_wait_config` duration 分支；pointer 落对应字段）：

- `durationMode`：可选，`static | dynamic`；非法值 422（pointer `/durationMode`）；缺省按 static。
- `static` 模式（含缺省）：**完全沿用现规则**——`durationSeconds` 必须为非 bool 整数、1-600；旧图、旧测试零回归。
- `dynamic` 模式：
  - `durationExpression`：必填字符串，strip 后非空、长度 ≤200；空/超长 422（pointer `/durationExpression`）。
  - `durationSeconds`：**允许保留但不校验、不使用**（面板切换时隐藏并保留旧值，便于切回；不做「字段混用」422）。
  - 编译期不做表达式求值（无运行上下文）；语法/语义错误在运行时按 WAIT_DURATION_INVALID 处理（§3）。
- 单出边拓扑约束两模式完全一致，不直连结束。

## 3. 运行时语义（实现 `atlas.graph.loader`）

### 3.1 求值

duration 分支（loader 现 `waitType == "duration"` 路径）：

1. static：`seconds = int(config["durationSeconds"])`，现状零改动。
2. dynamic：`seconds = evaluate_expression(config["durationExpression"], context)`（复用 `atlas.graph.conditions` 引擎，支持 `{{路径}}` 变量与 D15 算术/白名单函数），结果必须为：
   - 数值（int，或整数值 float 如 `60 / 2`→30.0；bool 拒绝），且
   - `1 <= round(seconds) <= 600`。
   
   满足时 `seconds = int(round(seconds))`。
3. 以下情况抛 `WaitNodeFailure`（节点/run failed，不进入 sleep）：
   - 表达式抛 `ConditionEvalError`/任何异常 → code `WAIT_DURATION_INVALID`，「等待时长表达式无法求值：…」；
   - 结果非数值、bool、非有限值、超出 1-600 → code `WAIT_DURATION_INVALID`，「等待时长表达式结果非法（需为 1-600 秒，当前 …）」。

### 3.2 帧与续跑

动态模式求出的 `seconds` 同时作为暂停帧的 `timeout_seconds`（`_emit_frame` deadline 照此构建）；续跑 `remaining_seconds` 路径与模式无关、零改动。

### 3.3 节点产出

dynamic：

```json
{
  "mode": "wait",
  "waitType": "duration",
  "durationMode": "dynamic",
  "durationExpression": "{{global.slaHours}} * 3600",
  "durationSeconds": 7200
}
```

static 产出形状不变（不新增 durationMode 键，旧断言零回归）。trace：`wait-x: waited 7200s`（与现状同形），动态求值失败时 run failed、错误带 WAIT_DURATION_INVALID。

## 4. REST

无新增端点、无新增 SSE 帧、无 run inputs 运行控制键。

## 5. 前端

- `nodeCatalog.ts`：wait config 增 `durationMode?: 'static' | 'dynamic'`、`durationExpression?: string`；默认 config 不变（durationSeconds:5、无 durationMode＝static）。
- `wait.schema.ts` duration oneOf 分支：增 `durationMode`（enum static|dynamic，默认 static，x-widget radio）与 `durationExpression`（string，≤200，x-variable）；required 收缩为 `['waitType']`，两模式各自必填由 L1 手写产出。
- `WaitConfig.tsx`：等待时长下增静态/动态单选——static 为现有 InputNumber；dynamic 渲染表达式 Input（变量插入同其他 x-variable 输入的既有路径），说明文案「运行时求值，须为 1-600 秒」。
- `nodeUiSchemas.ts`：新增 wait UISchema，`hiddenWhen` rootScoped 两条（durationSeconds 在 dynamic 隐藏、durationExpression 在非 dynamic 隐藏），供 validateGraph 隐藏字段诊断过滤（custom 面板渲染自行控制）。
- L1（`validation/l1.ts` wait case）：dynamic 校验 durationExpression 非空/≤200、不再对 durationSeconds 产诊断；static 保留 durationSeconds schema 诊断、不校验 durationExpression；纯逻辑 vitest 覆盖。
- `x-outputSchema` 增 `durationMode`/`durationExpression` 两键，同步更新 nodeSchemas 测试 EXPECTED_OUTPUT_KEYS.wait。

## 6. 契约同步矩阵

| 文档 | 改动 |
|---|---|
| 04 §5.5 | 追加动态时长 blockquote（durationMode/表达式/求值口径/失败/产出） |
| 03 | wait config 形状注释更新（static/dynamic 与 durationExpression）；指向 04 §5.5 |
| 06 | §6 运行时补动态时长小节（表达式求值＋WAIT_DURATION_INVALID，并入 wait 运行时叙述） |
| 09 | wait 无新包；loader 运行时条目补 dynamic 时长（无目录变化） |
| 12 | §3 wait 内部接口补 durationExpression 求值；WAIT_DURATION_INVALID 列入 wait 失败码；REST 无变动 |
| 13 | 新 U 编号：DSL 两模式、loader 求值成功/异常/非法结果三路径、前端 L1 |
| 14 | D19 行加「定时等待动态时长 v1 已落码」部分取回注记（不解除） |
| 08 | 本立项记录；收口条在落码后补 |
| 00 | 文档地图加 docs/49 行 |
| handoff | 顶部 banner + Active work 新编号（立项态） |
| CHANGELOG | 立项条目；落码后在收口原子更新 |

## 7. 原子序（英文 message；无 co-author；不 push）

1. docs 立项（本原子）。
2. `feat(graph): support dynamic duration expressions in wait nodes`（dsl 校验 + loader 求值 + 测试）。
3. `feat(frontend): enable dynamic wait duration configuration`（schema + WaitConfig + UISchema + L1 + vitest）。
4. docs 收口：HTTP/浏览器冒烟（动态求值路由、静态零回归、坏表达式/非法结果 run failed WAIT_DURATION_INVALID）＋证据截图＋04/06/09/12/13/14/08/49/00/handoff/CHANGELOG 回填。

## 8. 验收门

- 后端：`.venv/bin/pytest`（立项基线 1457 passed / 59 skipped，只许增测）。
- 前端：`cd frontend && pnpm lint && pnpm test && pnpm build`（基线 616 passed / 2 skipped）。
- 冒烟（真实 HTTP，:8000，不依赖真实供应商）：① dynamic 图 `durationExpression="{{global.waitSecs}}"`，run inputs waitSecs=2 → completed、实际等待约 2 秒、产出 durationSeconds=2；② 算术表达式 `"{{global.waitSecs}} * 2 + 1"` 求值正确；③ 表达式引用缺失变量/语法坏 → run failed WAIT_DURATION_INVALID；④ 结果 0 / 601 / 非数值字符串 → WAIT_DURATION_INVALID；⑤ 旧 static 图与旧默认图零回归；⑥ DSL：dynamic + durationExpression 空/超长 422、durationMode 非法 422；⑦ 浏览器：static/dynamic 切换字段与错误同步、动态图运行成功、控制台零错误。
