# prod 首任管理员引导批（打包 L）v1 批契约设计

> **立项 2026-09-25**：起因＝docs/63 §0A 的 **N1（prod 全新安装登录死锁）**，用户「开搞」授权按 AI 推荐方案落地。形状权威＝本文；与 01–08 冲突以规格文档为准，落码偏差在收口注记回填。
>
> 零新依赖、**零新迁移、零新 REST 端点与错误码**；解除零个缓做项（D36/D37 与本批无关）。补的是 **docs/64 打包 J 的 J-1c 收口缺口**——那条把 S2 从"弱口令可登录"改成了"谁都进不去"，本批给它一个合法的进门方式。

## 0. 已核实缺陷（证据级：〔码〕＝逐行读过，〔跑〕＝本会话执行过）

| 环节 | 证据 |
|---|---|
| 应用自己在 import 时播种，entrypoint 的 prod 跳过被架空 | `iam/deps.py:43` 模块级 `user_store.seed()`（注释自陈"否则 PG 首启无管理员、无法登录"）；`scripts/ops/docker-entrypoint.sh:60-65` 只在 prod 少跑一次 `seed_accounts` |
| 种进去的就是仓库里写死的四套口令 | `iam/principals.py:51-60` `SEED_USERS`；`storage/pg.py:469` 与 `iam/accounts.py:66` 均 `hash_password(user.password)` |
| prod 又拒绝这些口令 | `iam/deps.py:74-81`：`read_env_profile()=="prod"` 且提交的口令等于任一种子口令 → 403 `AUTH_SEED_CREDENTIAL`，文案"请先通过管理员改密" |
| 而改密/建号都要先有身份 | `api/main.py:1404-1410`（`change-password` 需 `get_principal`）、`:1432`／`:1479`（建用户与重置需 `administer`） |
| 没有任何带外建号手段 | `scripts/` 下只有 `seed_accounts.py`（同样只播种子账号）；grep `BOOTSTRAP\|FIRST_ADMIN\|CREATE_ADMIN` 在 `src/atlas` **0 命中**〔跑〕 |

⇒ **`ATLAS_ENV=prod` ＋ 新库 ＝ 世界上没有能登录的账号。**

## 1. 决策：一次性引导口令（env 注入），prod 缺即拒绝启动

**选定**：新增环境变量 `ATLAS_ADMIN_BOOTSTRAP_PASSWORD`。prod 下 `security/bootstrap.py` 要求它存在且过 `validate_password` 策略，否则 **fail-closed 拒绝启动**；播种计划改为 **prod 只播各租户的 ADMIN 账号，口令取该环境变量**，不再播 operator/viewer 与任何仓库内明文口令。operator/viewer 由首位 admin 通过既有 `POST /api/users` 建立。

**被否掉的三个方案（记下来，防止下次重提）**

| 方案 | 为什么否 |
|---|---|
| A. 首登允许种子口令＋强制改密令牌 | 要新造一个"一次性令牌"子系统（签发/校验/时效/撤销），而 prod 真正需要的只是"首次能进门"。代价与收益不匹配，且留出一个已知弱口令可登录的窗口——那恰是 S2 原本的缺陷 |
| B. 带外 CLI `create_admin.py` | 能解决问题，但把"装完还要手动跑一条命令"写进部署手册＝多一条会被漏掉的步骤；而且它需要在应用之外持有 DB 连接与哈希实现，凭证面反而扩大 |
| C. 关掉 J-1c 的 prod 种子口令拒绝 | 直接回退已收口的 S2 修复，不可接受 |

**为什么这个不算"又留了个后门"**：引导口令由部署方自己生成、只经 env 注入、从不进仓库，也不写进日志（本批只校验不回显）；它替代的正是"仓库里写死的明文口令"。首次登录后可用既有 `change-password` 轮换，轮换后引导值不再是有效凭证（DB 里存的是新哈希）。

## 2. 形状

**2.1 环境契约（新增 1 个，改 1 个非法缺省）**

```
ATLAS_ENV=dev|test|prod          # .env.example 现写 development → read_env_profile() 抛错（docs/63 N5），本批改回 dev
ATLAS_ADMIN_BOOTSTRAP_PASSWORD=  # 仅 prod 必需；≥8 位且不在弱口令黑名单（复用 iam/passwords.validate_password）
```

**2.2 代码落点（四处，全部不改函数签名的既有语义）**

| 位置 | 改动 |
|---|---|
| `security/bootstrap.py` | 新增 `PROD_BOOTSTRAP_PASSWORD_ENV` 与 `prod_bootstrap_password()`（非 prod 返回 `None`；prod 缺失或不合策略 raise）；`assert_prod_secrets()` 把它并入既有必需项检查（**一个闸门、一处措辞**） |
| `iam/principals.py` | 新增 `seed_plan_for_profile()`：非 prod 原样返回 `SEED_USERS`（**行为逐键不变**），prod 返回"各租户 ADMIN 一条＋口令＝引导值" |
| `iam/deps.py:43`、`scripts/ops/seed_accounts.py` | `seed()` 改传 `seed_plan_for_profile()`——两条播种入口共用同一计划，不留第二套口径 |
| `docker-compose.yml`／`.env.example` | compose 传 `ATLAS_ENV: ${ATLAS_ENV:-dev}` 与引导口令，缺省仍是 dev 语义（演示形态零变化）；`.env.example` 修 `development`→`dev` 并写明引导口令 |

**2.3 prod 行为矩阵（本批要把它钉住）**

| 配置 | 结果 |
|---|---|
| `ATLAS_ENV=prod`、无引导口令 | **拒绝启动**（fail-closed，与缺密钥同一条消息家族） |
| `ATLAS_ENV=prod`、引导口令＝弱口令/超短 | **拒绝启动**（复用口令策略，错误信息说明原因） |
| `ATLAS_ENV=prod`、引导口令合规 | 起；`admin-a@t1`／`admin-b@t2` 可登录；operator/viewer **不存在**，需 admin 建 |
| `ATLAS_ENV=dev`（或缺省） | 与今天逐键相同：四个种子账号照播、`admin123` 可登、`AUTH_SEED_CREDENTIAL` 只在 prod 生效 |

## 3. 契约同步矩阵（收口时逐项回填）

docs/03（`identity_user` 族：prod 播种规则）、04（无）、06（无）、08（立项条＋A 组 L-1 划销）、09（`security/bootstrap.py`/`iam/principals.py` 两行）、12（`POST /api/auth/login` 与 `change-password` 的 prod 口径）、13（U860 起）、14（无新条；D36/D37 不动）、15＋README（部署环境变量清单、`.env.example` 取值修正）、29/34/63（N1 标 ✅ 已修＋docs/63 §0A 追加收口注）、64（J-1c 顶部追加"缺口由打包 L 闭合"注）、CHANGELOG、handoff。

## 4. 测试（U860 起；只许增测，既有认证测试零改动即证非 prod 行为未变）

- U860–U861 prod 缺引导口令／不合策略 → `assert_prod_secrets()` 与 `prod_bootstrap_password()` 双点都拒（fail-closed 有两次机会，任何一条路径都起不来）。
- U862–U863 prod 播种计划＝只有各租户 ADMIN、口令＝引导值；两档 store（内存＋PG 标记）下 `authenticate_login` 用引导口令可登、用 `admin123` 不可登。
- U864 非 prod 计划与 `SEED_USERS` **逐键相同**（防"顺手收紧"把演示形态改坏）。
- U865 幂等：prod 计划播种两次不新增行、不改已改密的账号。
- U866 **反向门**：把 `prod_bootstrap_password()` 的缺失分支短路成"给个默认值"，U860/U861 必须变红——证明这条 fail-closed 真在承重，不是永真断言。
- U867 `.env.example` 的 `ATLAS_ENV` 值必须落在 `read_env_profile()` 合法集内（把 N5 那类"示例即非法"的错误钉成测试）。

## 5. 原子序

①（本批）docs-only 立项：本文＋08 立项条＋03/12/15/README 口径占位＋CHANGELOG＋handoff。
② `feat(iam)`：bootstrap＋seed plan＋两条播种入口＋compose/.env.example。
③ `test(iam)`：U860–U867＋门复跑。
④ `docs(ops)`：收口注回填（docs/63 §0A、docs/64 顶部注、08 划销、13 登记、CHANGELOG、handoff）。
⑤ 真机冒烟（若权限放行）：`ATLAS_ENV=prod` 起 uvicorn，验证"缺口令起不来／有口令能登进来"。

## 6. 残余风险（别当修完了）

1. **prod 仍无"多租户自助开通"**：本批只保证"第一人能进门"。t1/t2 两个演示租户名（`SEED_TENANTS`）在 prod 仍是硬编码，新租户开通仍是代码内动作——真上线要接租户注册流程，另批。
2. **引导口令轮替后的回收**：环境变量改值不会回滚已播种的账号（DB 里是新哈希），但若部署方忘记轮换，引导口令长期有效——需要口令有效期策略（仓库内无此机制，属 D31 之外的新面，未登记，等真有客户再定）。
3. **本批不碰 N2/N3/N4**：真实渠道工具接通、浏览器适配器注册、定时调度器一条都不做，"核心闭环可用"仍不成立。
4. **operator/viewer 在 prod 首启不存在**：若前端或文档假定了这些账号，prod 首启会看到空列表——属预期，但演示脚本不能在 prod 档跑。

---

## 9. 落码收口注记（2026-09-25 同日，✅ 全部四原子完成）

- 原子：`66e977e` `feat(iam)`（bootstrap＋seed plan＋两条播种入口＋compose/.env.example）→ `4f980df` `test(iam)`（U860–U867）→ 本 docs 收口原子。⑤ 真机冒烟**已在收口时执行**（见下"实跑证据"）。
- 与 §2 一致，无形状偏差；**两处按实况补强**：① 缺失与"弱口令"分两条错误消息（U861/U861a 分别断言，排障要能分辨）；② `AUTH_SEED_CREDENTIAL` 在 prod 的拒绝原样保留，本批只补"从哪进"，不放松它。
- 门：`pytest` **1868 passed / 114 skipped / 0 failed**（基线 1856/114，净增本批 12 条常跑；受影响三文件复跑 36 passed；`docker compose config` rc=0）。
- **实跑证据（真进程，非单测）**：`ATLAS_ENV=prod` 无引导口令 → `import atlas.iam` 抛 `RuntimeError: ...缺少 ATLAS_ADMIN_BOOTSTRAP_PASSWORD`；`ATLAS_ADMIN_BOOTSTRAP_PASSWORD=short` → 抛"不合口令策略——密码长度须为 8-128 位"；给合法口令 → `seed_plan_for_profile()`＝`[admin-a@t1, admin-b@t2]`，`authenticate_login("admin-a", 引导口令)` 返回 t1/admin，`admin123` 与 `operator-a` 均被拒。**这三条即 §2.3 行为矩阵的四行实况。**
- **既有测试被改 1 处（契约变更所致，非凑绿）**：`tests/test_security_bootstrap.py::test_assert_prod_secrets_passes_with_both_keys` 补引导口令并在注释指向本文；其旧语义由 U861b 反向覆盖。
- §3 同步矩阵回填结果：**已同步** docs/00（新行）·docs/03（`identity_user` 族播种规则）·docs/12（登录端点 prod 口径，改在单元格内、竖线数不变）·docs/13（U860–U867 登记行）·docs/15（新增 §五 prod 启动前置）·docs/63（§0A N1 标 ✅ 闭合）·docs/64（J-1c 缺口就地注）·README（单副本措辞更新＋prod 三件前置）·CHANGELOG·handoff（索引行／Active #70／Recently shipped 滚 1 条／状态行）。**零改动**＝docs/04·06·09·30·34·62（本批不触其形状）。
- **§6 残余风险全部仍然成立**，其中第 2 条（引导口令无轮换有效期）与第 1 条（prod 无租户自助开通）本批明确不做；**N2／N3／N4 未动 ⇒ docs/63 的"核心闭环可用"仍判否。**
