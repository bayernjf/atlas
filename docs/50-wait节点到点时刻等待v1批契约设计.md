# 50. wait 节点到点时刻等待 v1 批契约设计

> 立项：2026-09-23（AI 判断承接「不用管git，你推任务」总授权；docs-only 本原子，落码前不开工）
> 缓做来源：docs/14 **D19** 余部（到点时刻等待；动态时长已随 docs/49 落码、event 等待随 docs/47）
> 性质：**D19 部分取回、不解除**；零新依赖、零数据库迁移、无 ADR、不新增节点类型、Graph version 不变、不新增 REST 端点。

## 1. 背景与范围

定时等待现支持写死秒数（static）与运行期表达式秒数（dynamic，docs/49），但两者都是「相对时长」。真实流程常需「等到今天 18:00」「等到上游给出的截止时刻」——相对秒数无法表达固定钟点，用户只能自行心算秒数差。

本批取 **durationMode=absolute 的进程内 v1**：config 增 `absoluteTime`（ISO 8601 日期时间，或 epoch 秒；支持 `{{路径}}` 插值），节点执行时解析出目标时刻、计算与当前时刻的差值再等待；无法解析、目标已过点或差值越界（>600 秒）时 **确定性失败（WAIT_ABSOLUTE_TIME_INVALID）**，不 sleep 错误时长。旧图与 static/dynamic 两模式零回归。

### 范围内

1. DSL：wait duration config 的 `durationMode` 增枚举值 `absolute` 与 `absoluteTime`、编译校验（§2）。
2. Runtime：loader duration 分支 absolute 解析与差值等待（§3）。
3. 前端：WaitConfig 面板三模式切换与 absoluteTime 编辑、L1 校验（§5）。

### 显式非目标（仍缓做，收口时回写 D19 注记）

- **超过 600 秒的绝对时刻等待**：受同步 sleep 工作线程模型约束，absolute 差值仍须 1-600；分钟级以上长等待随 D19 中断模型余部/部署期。
- **时区/日历控件**：v1 只收 ISO 字符串与 epoch 秒，不提供日期选择器、不做本地时区换算 UI（naive datetime 一律按 UTC，契约写明）。
- **event wait timeoutSeconds 的表达式化/绝对时刻化**：本批只覆盖定时等待。
- **周期/循环时刻（cron 式「等到下一个整点」语义）**：v1 不做周期推导，目标时刻必须显式给出。
- **等待竞速取消、多事件**：仍随 D19/D31。

## 2. DSL 契约（04 §5.5 追加，唯一权威落 04）

```yaml
type: wait
config:
  waitType: duration
  durationMode: static            # static | dynamic | absolute；缺省 static（旧图零回归）
  durationSeconds: 5              # static：整数 1-600（现状不变）
  durationExpression: "{{global.slaHours}} * 3600"  # dynamic：必填，≤200（现状不变）
  absoluteTime: "2026-09-23T18:00:00+08:00"          # absolute：必填，≤64 字符
```

字段与校验（dsl.py wait duration 分支；pointer 落对应字段）：

- `durationMode` 枚举增 `absolute`；非法值 422（pointer `/durationMode`）；缺省仍按 static。
- `absolute` 模式：
  - `absoluteTime`：必填字符串，strip 后非空、长度 ≤64；空/超长 422（pointer `/absoluteTime`）。
  - `durationSeconds`/`durationExpression`：**允许保留但不校验、不使用**（面板切换时隐藏并保留旧值，不做「字段混用」422，与 docs/49 同策略）。
  - 编译期不做时刻解析（无运行时钟）；语法/语义错误在运行时按 WAIT_ABSOLUTE_TIME_INVALID 处理（§3）。
- static/dynamic 两模式规则完全沿用 docs/49；单出边拓扑三模式一致，不直连结束。

## 3. 运行时语义（实现 `atlas.graph.loader`）

### 3.1 解析与差值

duration 分支增 `duration_mode == "absolute"`（非续跑）：

1. 渲染：取 `config["absoluteTime"]` 文本；含 `{{` 时先经 `interpolate(text, context)` 插值（与 eventKey 同路径，支持 `{{路径}}` 取值但不做算术）；渲染结果 strip。
2. 解析目标时刻（渲染后字符串 `s`）：
   - 数值形态（正则 `^-?\d+(\.\d+)?$`）→ epoch 秒 `datetime.fromtimestamp(float(s), tz=UTC)`；
   - 否则按 ISO 8601 解析：`datetime.fromisoformat(s.replace("Z", "+00:00"))`；得到 naive datetime 时 **按 UTC 解释**。
3. 差值：`delta = (target - now).total_seconds()`，`now` 取注入时钟（loader 既有 `now` 参数，缺省 `datetime.now(UTC)`）。
4. 约束：`delta` 必须有限且 `1 <= round(delta) <= 600`；满足时 `seconds = int(round(delta))`。
5. 以下情况抛 `WaitNodeFailure`（节点/run failed，不进入 sleep）：
   - 插值/解析抛任何异常（含未知变量路径、坏 ISO、epoch 数值越出 datetime 范围）→ code `WAIT_ABSOLUTE_TIME_INVALID`，「等待节点 X 目标时刻无法解析：…」；
   - 目标已过点（delta < 1，含 0/负值）或 delta > 600 → code `WAIT_ABSOLUTE_TIME_INVALID`，「目标时刻非法（需为未来 1-600 秒内，当前差值 …s）」。

### 3.2 帧与续跑

`seconds` 同时作为暂停帧 `timeout_seconds`（deadline 照此构建，与 static/dynamic 完全同路径）；续跑走现有 `remaining_seconds(resume.deadline_at)`，与模式无关、零改动。

### 3.3 节点产出

absolute：

```json
{
  "mode": "wait",
  "waitType": "duration",
  "durationMode": "absolute",
  "absoluteTime": "2026-09-23T10:00:02+00:00",
  "durationSeconds": 2
}
```

- `absoluteTime` 回写**渲染后**的目标时刻字符串（epoch 输入统一回写为 ISO，naive 回写带 +00:00 的 ISO）；`durationSeconds` 为实际等待差值。
- static/dynamic 产出形状不变（docs/49；旧断言零回归）。trace：`wait-x: waited 2s until <absoluteTime>`；失败时 run failed、错误带 WAIT_ABSOLUTE_TIME_INVALID。

## 4. REST

无新增端点、无新增 SSE 帧、无 run inputs 运行控制键。SSE wait node_end data 在 absolute 模式为 §3.3 形状（比 duration 基线另含 `durationMode/absoluteTime`）。

## 5. 前端

- `nodeCatalog.ts`：`durationMode` 类型增 `'absolute'`；NodeConfig 增 `absoluteTime?: string`；常量 `MAX_ABSOLUTE_TIME_LENGTH = 64`（经 nodeCatalog re-export）。
- `wait.schema.ts` duration oneOf 分支：durationMode enum 增 absolute；增 `absoluteTime`（string，minLength 1、maxLength 64，x-widget 文本输入）；required 仍 `['waitType']`。
- `WaitConfig.tsx`：时长模式 Radio 增「到点时刻」；absolute 渲染文本输入，placeholder `2026-09-23T18:00:00+08:00 或 epoch 秒，支持 {{}}`，说明文案「运行时解析，须为未来 1-600 秒内的时刻」。
- `nodeUiSchemas.ts` wait hiddenWhen 增两条：absoluteTime 仅 durationMode=absolute 显示；durationSeconds/durationExpression 在 absolute 隐藏（rootScoped）。
- L1（`validation/l1.ts` wait case）：absolute 校验 absoluteTime 非空/≤64，不产 durationSeconds/durationExpression 诊断；static/dynamic 不产 absoluteTime 诊断。纯逻辑 vitest。
- `x-outputSchema` duration 产出增 `absoluteTime` 键，同步 nodeSchemas 测试 EXPECTED_OUTPUT_KEYS.wait。

## 6. 契约同步矩阵

| 文档 | 改动 |
|---|---|
| 04 §5.5 | 追加到点时刻 blockquote（durationMode=absolute/absoluteTime/解析口径/失败/产出） |
| 03 | wait config 形状注释更新（absolute 与 absoluteTime）；指向 04 §5.5 |
| 06 | §6 wait 运行时叙述补 absolute 解析＋WAIT_ABSOLUTE_TIME_INVALID |
| 09 | wait 无新包；graph 模块条目补 absolute 时刻（无目录变化） |
| 12 | §3 新增 wait absolute 内部接口小节；WAIT_ABSOLUTE_TIME_INVALID 列入 wait 失败码；REST 无变动 |
| 13 | 新 U305：DSL 三模式、loader 解析成功/异常/过点/越界、前端 L1 |
| 14 | D19 行加「到点时刻 v1 已立项」部分取回注记（不解除） |
| 08 | 本立项记录；收口条在落码后补 |
| 00 | 文档地图加 docs/50 行 |
| handoff | 顶部 banner + Active work 新编号（立项态） |
| CHANGELOG | 立项条目；落码后在收口原子更新 |

## 7. 原子序（英文 message；无 co-author；不 push）

1. docs 立项（本原子）。
2. `feat(graph): support waiting until an absolute point in time`（dsl 校验 + loader 解析 + 测试）。
3. `feat(frontend): enable absolute-time wait configuration`（schema + WaitConfig + UISchema + L1 + vitest）。
4. docs 收口：HTTP/浏览器冒烟（absolute 路由、static/dynamic 零回归、坏时刻/过点/越界 run failed WAIT_ABSOLUTE_TIME_INVALID）＋证据截图＋04/06/09/12/13/14/08/50/00/handoff/CHANGELOG 回填。

## 8. 验收门

- 后端：`.venv/bin/pytest`（立项基线 **1472 passed / 59 skipped**，只许增测）。
- 前端：`cd frontend && pnpm lint && pnpm test && pnpm build`（基线 **621 passed / 2 skipped / 46 文件**）。
- 冒烟（真实 HTTP，:8000，不依赖真实供应商）：① absolute 图 absoluteTime=now+2s（ISO，带时区）→ completed、实际等待约 2 秒；② epoch 秒数值形态 → 正确等待；③ `{{}}` 插值渲染目标时刻成功；④ 坏 ISO/未知变量/目标已过点/差值 >600 → run failed WAIT_ABSOLUTE_TIME_INVALID 且不 sleep；⑤ static/dynamic 两模式零回归（产出形状）；⑥ DSL：absolute + absoluteTime 空/超长 422、durationMode 非法 422；⑦ 浏览器：三模式切换字段与错误同步、控制台零错误。
