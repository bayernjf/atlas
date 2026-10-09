# 打包 AF（D15 余部）：choice/加权采样 ＋ 命名时区 — v1 批契约设计

> 状态：📝 立项（docs-only，落码另立批）｜号段 U1293–U1305｜形状权威：本文｜承接 docs/14 D15 剩余「choice/采样、命名时区」｜2026-10-09
>
> 前置：[docs/84](84-随机函数种子化与回放契约-打包W.md)（打包 W＝random/randint/uuid 种子化）；[docs/27](27-可注入回放时钟与非确定日期函数-C包契约.md)（now/today 可注入时钟）。
>
> 命名沿用专业术语，无比喻。表达式语言不支持数组/对象字面量（语法见 `conditions.py` 头注释，函数参数仅 `or_expr`），故多值函数一律用**可变位置参数**。

---

## 0. 背景与目标

D15（condition 表达式函数库）已取回：算术与白名单函数（`ac8c718`）、可注入时钟与 now/today（docs/27）、random/randint/uuid＋回放种子化（docs/84 打包 W）。docs/14 D15 行最后注记仍缓做四项：**choice/采样、客户端钉种子、断点续跑帧携种子、命名时区**。

本批（打包 AF）取回其中两片，均为工程内可闭环、零新依赖：

1. **choice / weightedChoice**：在表达式内做随机选择——均匀随机与按权重随机。用于随机分流、A/B、灰度放量比例、随机话术等。
2. **命名时区**：把 UTC 时刻换算到 IANA 命名时区，取其**日期/小时分量**。用于按店铺/客户时区判定工作时间、SLA 日界。

**客户端钉种子、断点续跑帧携种子**不在本批（属运行期状态持久化，与断点/续跑族联动，触发条件不同）。

---

## 1. 范围

**In（本批）**

- 后端 `conditions.py`：新增 4 个随机/选择相关中的 2 个函数（`choice`、`weightedChoice`）＋ 4 个命名时区函数（`dateOfInZone`、`hourOfInZone`、`todayInZone`、`hourInZone`）。
- 前端 `conditions.ts`：函数表/非确定集合/类型面同步；确定性时区函数纯常量折叠与后端同值；非确定函数不折叠。
- 新增 1 个错误码 `COND_INVALID_TIMEZONE`（zh/en 文案）。
- 种子化与回放：随机选择复用既有注入 RNG，经 `rng_seed` 确定复现。

**Out（非目标，见 §5）**

- 客户端钉种子、断点续跑帧携种子；时区的分钟/秒分量函数；返回"带时区标签的 datetime"；夏令时规则自定义；非 IANA 时区（固定偏移缩写如 "CST"）。

---

## 2. 函数规格

### 2.1 Part 1：随机选择（非确定，种子化）

#### `choice(*items)` — 均匀随机

- 参数：**≥1 个**位置参数（`min_args=1, max_args=None`）。
- 返回：注入 RNG 均匀随机选中的一个参数值。
- **同类型约束**：所有候选项必须为同一类型（同为 string / number / bool / date / datetime）。异构（如 `choice("a", 1)`、`choice(date(...), "x")`）→ `COND_TYPE_MISMATCH`。单参数返回其自身（不耗随机也可，实现上允许耗一次随机但结果唯一）。
- 静态返回类型标记：`any`（非确定多态，见 §3.3）。

#### `weightedChoice(*pairs)` — 按权重随机

- 参数：**偶数个、≥2** 位置参数，按 `(item, weight, item, weight, …)` 交替。
  - 奇数位（第 1、3、5…个）为候选项 `item`；偶数位（第 2、4、6…个）为对应权重 `weight`。
- 权重规则：
  - 每个 `weight` 必须为 **number、非 bool、≥ 0**；非数值/bool/负数 → `COND_TYPE_MISMATCH`。
  - 权重总和必须 **> 0**；全 0 → `COND_INVALID_RANGE`。
  - 权重 0 的项永不被选中。
- item 规则：所有 item 同 `choice` 的同类型约束；异构 → `COND_TYPE_MISMATCH`。
- 参数个数为奇数（无法配对）→ `COND_TYPE_MISMATCH`，消息写明"参数必须按 (项, 权重) 成对"。
- 选中概率正比于权重；以注入 RNG 的加权采样实现（`rng.choices` 等价的累积权重法，避免浮点总数边界问题）。
- 静态返回类型标记：`any`。

> 说明：不用数组字面量是表达式语法的硬约束。交替参数是无数组语法下的明确选择；文档与节点目录须给出示例 `weightedChoice("auto", 9, "manual", 1)`。

### 2.2 Part 2：命名时区

IANA 时区名（如 `Asia/Shanghai`、`America/New_York`、`UTC`）。后端 stdlib `zoneinfo.ZoneInfo`；前端 `Intl.DateTimeFormat(..., { timeZone })` 取分量。**不扩展 DateTimeValue 存储**——所有时区函数返回现有 `date`/`number` 类型。

#### 确定性（可静态折叠）

- `dateOfInZone(dt, zone)`（2 参数）：`dt` 为 UTC `datetime`，换算到 `zone`，返回该时区墙上**日期**（`date`）。
- `hourOfInZone(dt, zone)`（2 参数）：同上，返回该时区墙上**小时**（`number`，0–23）。

#### 非确定（依赖当前时钟，不静态折叠）

- `todayInZone(zone)`（1 参数）：注入时钟下，`zone` 的当前**日期**（`date`）。等价 `dateOfInZone(now(), zone)`。
- `hourInZone(zone)`（1 参数）：注入时钟下，`zone` 的当前**小时**（`number` 0–23）。等价 `hourOfInZone(now(), zone)`。

**跨日/跨日界用例（测试必须锁）**：

| UTC 时刻 | Asia/Shanghai (+8) | America/New_York (UTC−4，夏令时) |
| --- | --- | --- |
| 2026-06-15 16:00 | 06-16 00:00（date 次日、hour 0） | 06-15 12:00 |
| 2026-06-15 04:00 | 06-15 12:00 | 06-14 24:00→06-14 00:00（date 前一日、hour 0） |

> 仅取 date/hour 分量即可覆盖工作时间窗、SLA 日界等绝大多数场景（如 `hourOfInZone(now(), "Asia/Shanghai") >= 9 && hourOfInZone(now(), "Asia/Shanghai") < 18`）。分钟/秒分量列为非目标，需要时以同构方式补 `minuteOfInZone` 等，成本极低。

---

## 3. 错误码、前端与类型

### 3.1 新增错误码（仅 1 个）

- `COND_INVALID_TIMEZONE`：未知/非法 IANA 时区名（后端 `ZoneInfoNotFoundError`、空串、非 string）。登记 frontend `locales/{zh-CN,en-US}/validation.json`（照同族 `COND_*` 先例，不走 docs/03）。

### 3.2 复用既有码

- 参数个数（choice 零参、weightedChoice <2）：现有 arity 校验（parse 期）。
- 异构/权重非数/权重负/成对错误：`COND_TYPE_MISMATCH`（params 带 `func`，消息写明具体原因）。
- 权重全 0：`COND_INVALID_RANGE`（randint 已用同码）。

### 3.3 `any` 返回类型

- `choice`/`weightedChoice` 在 `_FUNCTIONS` 的 return_type 登记为 `any`；`_infer_type` 对其返回 `any`。
- 类型系统对表达式的错误暴露以**纯常量折叠**为主（`_static_type_errors`），这两个函数属 `_NONDETERMINISTIC`、静态不折叠，故 `any` 标签不引发误报；运行期返回实际类型值。相等比较 `==`/`!=` 容忍任意类型，有序比较由运行期同类型值保证。
- 前端 FUNCTIONS 同步登记 `any`，处理方式同构。

### 3.4 前端改动清单

- `FUNCTIONS`：`choice: [1, null, 'any']`、`weightedChoice: [2, null, 'any']`、`dateOfInZone: [2,2,'date']`、`hourOfInZone: [2,2,'number']`、`todayInZone: [1,1,'date']`、`hourInZone: [1,1,'number']`。
- `NONDETERMINISTIC` 增加：`choice`、`weightedChoice`、`todayInZone`、`hourInZone`。
- `evalFunction`：
  - 确定性时区函数 `dateOfInZone/hourOfInZone` 用 `Intl.DateTimeFormat([], { timeZone, year/month/day/hour, hour12:false })` 实现，纯常量折叠与后端同值。
  - 非确定函数给兜底实现（`new Date()` / `Math.random`），但静态不折叠、真实值以后端为准（照 random 先例）。
  - 非法时区在 `Intl` 抛 RangeError 处映射为与后端同文案的错误。
- 节点目录/函数提示若有函数清单（节点目录多语言 docs 已做），补 6 个函数说明（zh/en）。

---

## 4. 种子化与回放

- choice/weightedChoice 的随机性**只来自**注入 RNG（`evaluate_expression(..., rng=rng)`，loader L268 已对 condition 表达式注入 `rng`）。不出现 `random.Random()` 裸新建（确定性兜底仅在未注入时，与 random 同族）。
- 同一 `rng_seed` 下，含 choice/weightedChoice 的同表达式多次运行，选中序列**完全一致**；录制用例冻结 seed，replay/gate 经 seed_anchor 确定重放（docs/84 既有机制，零新增）。
- 命名时区的确定性函数不耗随机；非确定函数依赖注入时钟（docs/27 既有 now_override）。

---

## 5. 非目标（仍缓做 / 明确不做）

- **客户端钉种子、断点续跑帧携种子**：D15 余部另两项，属运行期状态持久化，与断点/续跑族联动，本批不做。
- **分钟/秒时区分量**（`minuteOfInZone` 等）：需要时按同构补，成本极低，本批只做 date/hour。
- **带时区标签的 datetime / DateTimeValue 扩展 `z` 字段**：本批用"取分量"规避，不引入跨时区 datetime 比较与归一。
- **固定偏移缩写**（CST/PST 等歧义名）：仅支持 IANA 名；固定偏移可用 `UTC` 或显式算术。
- weightedChoice 不支持相对权重归一化以外的高级采样（去重、无放回抽样）。

D15 整体在本批后**仍不解除**（客户端钉种子、断点续跑帧携种子仍缓做）。

---

## 6. 验收号段（U1293–U1305）

**Part 1 随机选择**

- **U1293**（choice 均匀）：固定种子下 `choice(1,2,3)` 多次结果落在集合内、大样本近似均匀；单参 `choice("x")` 恒为 `"x"`。
- **U1294**（choice 同类型/参数）：异构 `choice("a",1)`、`choice(date(2026,1,1),"x")` → COND_TYPE_MISMATCH；零参 → arity 错误。
- **U1295**（weightedChoice 比例）：固定种子，`weightedChoice("a",9,"b",1)` 大样本 "a" 占比 ≈ 0.9；返回值仅为奇数位 item。
- **U1296**（weightedChoice 形状）：奇数参（无法配对）→ COND_TYPE_MISMATCH（成对提示）；<2 参 → arity 错误。
- **U1297**（weightedChoice 权重）：权重非数/bool/负数 → COND_TYPE_MISMATCH；全 0 权重 → COND_INVALID_RANGE；0 权重项永不选中。
- **U1298**（种子化回放）：同 `rng_seed` 两次 evaluate 含 choice/weightedChoice 的表达式结果序列完全一致；不同 seed 可不同。
- **U1299**（weightedChoice item 同类型）：奇数位 item 异构 → COND_TYPE_MISMATCH。

**Part 2 命名时区**

- **U1300**（dateOfInZone）：§2.2 表中跨日界两 UTC 时刻 × Shanghai/NewYork，返回日期正确（含次日/前一日）。
- **U1301**（hourOfInZone）：同表，小时分量正确（含 0 点）。
- **U1302**（todayInZone）：注入 `now_override`，返回值与 `dateOfInZone(now(), zone)` 一致；跨日界用例正确。
- **U1303**（hourInZone）：注入时钟，与 `hourOfInZone(now(), zone)` 一致。
- **U1304**（非法时区）：`"Not/AZone"`、空串、非 string（如数字）→ COND_INVALID_TIMEZONE；四个时区函数均覆盖。

**前端/静态**

- **U1305**（前端同构）：FUNCTIONS/NONDETERMINISTIC 同步；`dateOfInZone/hourOfInZone` 纯常量折叠与后端 §2.2 表同值；非法时区前端报同文案；`COND_INVALID_TIMEZONE` zh/en 文案齐、en 无汉字；节点目录补 6 函数说明。

---

## 7. 原子提交序（落码批，照执行）

1. `docs: propose package AF for weighted choice and named timezones`（本文件＋docs/08 立项块＋docs/14 注记）。
2. `feat(engine): add choice and weighted choice functions`（后端 Part 1）。
3. `test(engine): cover choice and weighted choice`（U1293–U1299）。
4. `feat(engine): add named timezone functions`（后端 Part 2，含 COND_INVALID_TIMEZONE）。
5. `test(engine): cover named timezone functions`（U1300–U1304）。
6. `feat(frontend): add choice and timezone functions and error copy`（U1305）。
7. `test(frontend): cover choice and timezone functions`。
8. `docs: close out package AF for weighted choice and named timezones`（CHANGELOG/handoff/docs 13/14/08/112）。

门：后端全量、前端 vitest/oxlint/tsc/build、守护门；数字先跑后写。零新依赖（stdlib `zoneinfo`、JS `Intl`）/零迁移/无 ADR。
