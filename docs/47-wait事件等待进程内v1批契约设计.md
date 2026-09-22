# 47. wait 节点事件等待进程内 v1 批契约设计

> 立项：2026-09-23（AI 判断承接「不用管git，你推任务」总授权；docs-only 本原子，落码前不开工）
> 缓做来源：docs/14 **D19**（wait 节点事件等待；wait v1 仅 duration 1-600 整数秒）
> 性质：**D19 部分取回、不解除**；零新依赖、零数据库迁移、无 ADR、不新增节点类型、EdgeDSL/Graph version 不变。

## 1. 背景与范围

wait 节点 v1（04 §5.5）只支持 `waitType=duration` 的进程内同步 sleep（1-600 秒）。事件驱动场景（等外部系统回调、等人工在外部系统操作完、等另一个流程发信号）无法表达：业务上只能用 duration 盲等并轮询。

本批取 **waitType=event 的进程内 v1**：图执行到 wait 节点时在 per-tenant 进程内 broker 登记挂起，经登录态 REST 信号（按 eventKey 广播或按 token 直投）或 run inputs 预置恢复，带绝对等待上限与两种超时策略。挂起占用一个 Starlette 线程池工作线程（同 human_approval v1），上限 3600 秒。

### 范围内

1. DSL：wait config 增 event 分支字段与编译校验（§2）。
2. Runtime：`EventWaitBroker` 进程内挂起/信号/超时/取消（§3）。
3. REST：三个新端点（§4）；run inputs 预置 `waitEvents`（§3.3）。
4. 前端：wait 属性面板放开「事件等待」与事件字段配置（§5）。

### 显式非目标（仍缓做，收口时回写 D19 注记）

- **跨重启/多实例的中断-恢复**：不写事件等待中断帧、不做恢复扫描器重绑；进程重启 pending 即失。持久化中断模型随 D19/D20 后续批（T18 B 路线，docs/24）。
- 动态/表达式时长、等到指定绝对时刻、多事件竞速与取消订阅、信号的鉴权来源白名单（v1 仅登录态 operator+，无公开免登录信号口；公开入站触发走 webhook → graph）。
- 信号 payload 的 schema 校验/模板（v1 仅做 JSON 形状与大小限制）。
- wait 节点在 debug 单步流中的挂起（沿用 human_approval：调试流不支持事件等待，编译期若带 debug 请求遇 event wait → 422）。

## 2. DSL 契约（04 §5.5 追加，唯一权威落 04）

```yaml
type: wait
config:
  waitType: event                     # duration | event
  eventKey: "order_paid_{{trigger-1.context.payload.order_id}}"  # event 必填；支持 {{路径}} 插值
  timeoutSeconds: 300                 # event 必填整数 1-3600
  onTimeout: continue                 # continue | fail，默认 continue
```

字段与校验（dsl.py `_validate_wait_config`；错误前缀「等待节点 {id}」，pointer 落对应字段）：

- `waitType`：
  - `duration`：沿用现规则（durationSeconds 1-600 整数），完全不回归。
  - `event`：按下列规则；其余值 422。
- `eventKey`：必填字符串；去空白后非空，长度 ≤128（**静态模板长度**，含 `{{}}` 占位整体 ≤128；编译期不展开）。静态部分（剔除 `{{...}}` 占位后拼接）只允许字符集 `[A-Za-z0-9:_-]`，出现其他字符 422（占位内内容不检查）。运行时插值渲染后再做同一白名单与 1-128 长度校验，不满足 → 节点失败（§3.4）。
- `timeoutSeconds`：必填整数常量 1-3600；不支持变量/表达式。
- `onTimeout`：可选，`continue | fail`；非法值 422。

拓扑约束与 duration 完全一致：恰好 1 条普通出边、不直连 END；event 等待仍是纯透传节点（信号到达/超时 continue 都沿唯一出边走），不引入 conditional 边。

## 3. 运行时语义（实现 `atlas.graph.loader` + 新 broker）

### 3.1 EventWaitBroker（新文件 `src/atlas/collaboration/event_waits.py`）

纯 stdlib（threading.Lock + threading.Event），per-tenant 实例挂 TenantServices（两档存储均为内存实例，同 cancellation_broker）。

```python
class EventWaitBroker:
    def request(self, *, event_key, node_id, graph_id, timeout_seconds) -> str
    def wait(self, token, *, is_cancelled=None) -> bool      # 首末 0.2s 切片轮询取消；返 signaled
    def signal_key(self, event_key, payload) -> int          # 广播：释放全部同 key pending，返释放数
    def signal_token(self, token, payload) -> None           # 直投；未知 token KeyError
    def list_pending(self) -> list[dict]                     # 见 §4 GET
    def reset(self) -> None
```

内部条目：token = `wait-` + uuid4.hex；`_Pending{event, event_key, node_id, graph_id, deadline_monotonic, payload: dict|None, signaled: bool}`；另维护 `event_key → set[token]` 索引。已释放条目在 wait 返回后移除（信号先于 wait：条目保留到 wait 取走，payload/ signaled 已写，wait 一次即返 True）。

### 3.2 执行流程（loader wait 分支）

1. 渲染 `eventKey = interpolate(模板, context)`，strip；做白名单/长度校验（§2）。
2. `token = broker.request(...)`；node_start 事件携带 `wait:{token, eventKey, timeoutSeconds, onTimeout}`（前端可展示等待态；不新增前端阻塞交互）。
3. `signaled = broker.wait(token, is_cancelled=is_cancelled)`：
   - 等待期取消（协作式，节点边界语义同 B 包）→ 移除条目并抛 `RunCancelled`。
   - broker 内部按 monotonic deadline 判定超时；返回 False。
4. 输出与后续：
   - **signaled=True**（resolvedBy `signal`）：取信号 payload 沿出边继续。
   - **超时 + onTimeout=continue**（resolvedBy `timeout`）：沿出边继续，`signaled=false`，payload=`{}`。
   - **超时 + onTimeout=fail**（resolvedBy `timeout`）：节点失败——节点产出 `status:"failed"` 与错误 `WAIT_TIMEOUT_FAILED`（中文「等待事件 {eventKey} 超时」），run 标记 failed（与普通工具节点失败同构，不沿出边继续）。

节点产出：

```json
{
  "mode": "wait",
  "waitType": "event",
  "eventKey": "order_paid_7788",
  "signaled": true,
  "payload": {"paidAt": "2026-09-23T10:00:00Z"},
  "waitedSeconds": 12,
  "resolvedBy": "signal",
  "token": "wait-..."
}
```

可经 `{{wait-x.signaled}}`、`{{wait-x.payload.*}}`、`{{wait-x.resolvedBy}}` 引用（路径规则同 04 §6.3）。trace 行：`wait-x: event order_paid_7788 signaled after 12s` / `… timeout after 300s (continue)` / `… timeout failed`。

### 3.3 run inputs 预置（非交互/测试）

`inputs.waitEvents = {"<wait 节点 id>": <payload 对象>}`：节点不登记 broker、不阻塞，直接产出 `signaled:true, resolvedBy:"input", payload:<预置值>`（非对象 payload 归一为 `{}`；预置键不匹配任何节点忽略，同 approvals 预置）。waitEvents 是运行控制键，不进全局变量、不回写记录 inputs（同 approvals）。

### 3.4 失败口径

| 情形 | 形态 |
|---|---|
| 渲染后 eventKey 空白/超长/含白名单外字符 | 节点失败，code `WAIT_EVENT_KEY_INVALID`，不登记 broker |
| 超时 onTimeout=fail | 节点/run 失败，code `WAIT_TIMEOUT_FAILED` |
| 等待中协作取消 | run cancelled（RunCancelled，同 B 包） |
| debug 流遇 event wait | 编译/运行请求 422「事件等待不支持单步调试」 |

## 4. REST（新增三端点；登记 12 §5）

| 方法/路径 | 权限 | 契约 |
|---|---|---|
| `POST /api/waits/events` | operate | 请求体 `{eventKey: string（必填，1-128，白名单字符集）, payload?: object}`；payload 限 JSON 对象、序列化 ≤4096 字节、顶层键 ≤50；返 `{released: n}`（同 key 多等待全部释放；无 pending 返 0，信号不保留——v1 无先发信号后登记语义）。eventKey 非法/payload 超限 → 422 `WAIT_EVENT_KEY_INVALID` / `WAIT_EVENT_PAYLOAD_INVALID` |
| `POST /api/waits/{token}/signal` | operate | 请求体 `{payload?: object}`（同上限制）；200 返 `{token, released: true}`；未知/已取走 token → 404 `WAIT_TOKEN_NOT_FOUND`；重复信号（条目已 signaled 待取走）→ 409 `WAIT_ALREADY_SIGNALED` |
| `GET /api/waits` | read | 本租户 pending：`{items:[{token, eventKey, nodeId, graphId, timeoutSeconds, deadlineAt（UTC ISO）}], }`；倒序不保证，供调试/队列展示 |

不新增 SSE 帧类型（node_start 上的 wait 载荷走既有帧）。

## 5. 前端

- `nodeCatalog.ts`：`waitType?: 'duration' | 'event'`；新增 `eventKey?: string`；event 复用 `timeoutSeconds?` 字段；新增 `onTimeout?: 'continue' | 'fail'`。
- `WaitConfig.tsx`（保留手写，不进 schema 表单）：放开 event Radio（去 Tooltip）；event 选中时渲染：
  - eventKey `Input`（说明支持 `{{路径}}`、字符规则）；
  - timeoutSeconds `InputNumber` 1-3600；
  - onTimeout Radio：继续（超时沿出边继续）/ 失败（超时使流程失败）。
  - duration 选中时维持现有 UI（1-600）。
- L1 校验：新增 event 分支手写规则，镜像 §2（waitType/eventKey 静态白名单与长度、timeout 1-3600、onTimeout 枚举）；纯逻辑落 lib，vitest 覆盖；图级出边规则仍由后端 422 兜底。
- 画布节点等待态、信号投递均无需新 UI（信号经 API/外部系统调用）；不新增 i18n namespace（WaitConfig 现行中文硬编码，同现状）。

## 6. 契约同步矩阵

| 文档 | 改动 |
|---|---|
| 04 §5.5 | 追加 event v1 blockquote（config/校验/运行时/产出/失败口径）；§二节点总览表「等待节点」行更新 |
| 03 | wait config 形状注释更新（duration/event 两分支）；新增 event_wait 契约（broker/三端点/错误码字段概览） |
| 06 | 新增小节：事件等待运行时（broker 接线、线程模型、取消与超时） |
| 09 | collaboration/ 目录与模块映射加 `event_waits.py`；TenantServices 字段 |
| 12 | 内部接口加 EventWaitBroker；REST 表加三行 |
| 13 | 候选用例：broker 纯逻辑、DSL/loader、REST 三端点与权限、预置 waitEvents、取消 |
| 14 | D19 行加「进程内 event v1 已落码」部分取回注记（不解除） |
| 08 | 本立项记录；收口条在落码后补 |
| 00 | 文档地图加 docs/47 行 |
| handoff | 顶部 banner + Active work 43（立项态） |
| CHANGELOG | 立项条目；落码后在收口原子更新 |

## 7. 原子序（英文 message；无 co-author；不 push）

1. docs 立项（本原子）。
2. `feat(collaboration): add in-process event wait broker` + broker 单测。
3. `feat(graph): support event waits with timeout policy`（dsl 校验 + loader + TenantServices 接线）+ 后端测。
4. `feat(api): expose wait signal endpoints`（三 REST + 权限/审计）+ API 测。
5. `feat(frontend): enable event wait configuration`（WaitConfig + L1 + vitest）。
6. docs 收口：HTTP/浏览器冒烟（信号放行、超时 continue/fail、预置、404/409、取消）+ 证据截图 + 04/06/09/12/13/14/08/47/00/handoff/CHANGELOG 回填。

## 8. 验收门

- 后端：`.venv/bin/pytest`（立项基线 1398 passed / 59 skipped，只许增测）。
- 前端：`cd frontend && pnpm lint && pnpm test && pnpm build`（基线 602/2）。
- 冒烟（真实 HTTP，:8000）：① event wait 图在 node_start 挂起 → POST events 信号 → run completed 且产出 payload 正确；② 超时 continue：短超时 wait 完成、signaled=false、沿出边继续；③ 超时 fail：run failed WAIT_TIMEOUT_FAILED；④ token 直投 200 → 重复 409 → 未知 404；⑤ run inputs waitEvents 预置秒过 resolvedBy=input；⑥ 等待中 cancel → cancelled；⑦ 非法 eventKey（运行时渲染出白名单外字符）失败不挂起；GET /api/waits 挂起期间可见。
