"""只读 SQL 静态审查纯函数（docs/32 §4，P0 批 3）。

外部数据源的 ``query`` 通道语义为只读；本模块在 SQL 触达数据库之前做静态
审查，防止「借只读通道执行写 / DDL」与多语句堆叠。绑定参数（params）仍是
防 SQL 注入的主路径，guard 不做参数值检查。

规则（fail-closed）：

- 去除 ``--`` 行注释与 ``/* */`` 块注释（词法感知，字符串/标识符内的注释
  符号原样保留）；
- 字符串字面量（单引号，含 ``''`` 转义）、双引号/反引号标识符整体替换为
  占位，其内部的 ``;`` 与写动词不作为语法判定；
- 拒绝多语句（字符串外的 ``;`` 之后仍有有效 token）；
- 起始关键字仅允许 ``SELECT`` 或 ``WITH``；``WITH`` CTE 链最终主语句必须是
  只读，且 CTE 体内出现任何写动词（可写 CTE）也拒绝；
- 字符串外出现写 / DDL / 事务 / 连接管理动词即拒（INSERT/UPDATE/DELETE/
  MERGE/REPLACE/CREATE/ALTER/DROP/TRUNCATE/RENAME/PRAGMA/ATTACH/DETACH/
  VACUUM/REINDEX/COPY/GRANT/REVOKE/CALL/EXEC/SET/RESET/SHOW/DO/INTO/
  BEGIN/START/COMMIT/ROLLBACK/SAVEPOINT 等）；
- 紧跟 ``(`` 的同名 token 视为函数调用（如 ``replace(...)``），不误判。

这是应用侧第一道闸，不替代数据库账号最小权限与 PG ``postgresql_readonly``
连接级纵深（docs/32 §4.2、§9 风险 4）。
"""

from __future__ import annotations

import re

READ_ONLY_START = frozenset({"SELECT", "WITH"})

# 字符串外出现即拒绝的写 / DDL / 管理动词（大写匹配）
_WRITE_VERBS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "REPLACE",
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "RENAME",
        "PRAGMA",
        "ATTACH",
        "DETACH",
        "VACUUM",
        "REINDEX",
        "CLUSTER",
        "COPY",
        "GRANT",
        "REVOKE",
        "CALL",
        "DO",
        "SET",
        "RESET",
        "SHOW",
        "EXEC",
        "EXECUTE",
        "INTO",  # PG SELECT ... INTO 建表为写
        "BEGIN",
        "START",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "RELEASE",
        "PREPARE",
        "CHECKPOINT",
        "ANALYZE",
        "LOCK",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")


class ReadOnlyViolation(Exception):
    """只读通道检测到写/DDL/多语句；code 固定 DB_SQL_NOT_READ_ONLY。"""

    code = "DB_SQL_NOT_READ_ONLY"


def _mask_non_sql_text(sql: str) -> str:
    """把字符串字面量、引号标识符、注释替换为等长空白/占位，仅保留裸 SQL。

    等长替换（占位为空格）以保留原始位置，注释与字符串内部内容不参与
    语法判定。单引号字符串内 ``''`` 为转义；不展开 dollar-quoting（其仅常见
    于 CREATE FUNCTION 等已被起始关键字拒绝的语句）。
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        # 行注释 -- ...
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue
        # 块注释 /* ... */（未闭合则到结尾，按注释处理）
        if ch == "/" and nxt == "*":
            close = sql.find("*/", i + 2)
            end = n if close == -1 else close + 2
            out.append(" " * (end - i))
            i = end
            continue
        # 单引号字符串（'' 转义）
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(" " * (j - i))
            i = j
            continue
        # 双引号标识符（PG/SQL 标准）与反引号标识符（SQLite/MySQL）
        if ch in ('"', "`"):
            close = sql.find(ch, i + 1)
            end = n if close == -1 else close + 1
            out.append(" " * (end - i))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def assert_read_only_sql(sql: str) -> str:
    """断言 SQL 为单条只读 ``SELECT`` / ``WITH…SELECT``；否则抛异常。

    返回原始 ``sql``（仅校验、不改写，执行仍走参数绑定）。
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ReadOnlyViolation("SQL 为空")

    bare = _mask_non_sql_text(sql)

    # 多语句：字符串外分号之后仍有有效 token
    segments = [segment for segment in bare.split(";") if segment.strip()]
    if len(segments) > 1:
        raise ReadOnlyViolation("只读通道仅允许单条语句，检测到多语句堆叠")

    tokens = _TOKEN_RE.findall(bare)
    if not tokens:
        raise ReadOnlyViolation("SQL 无有效关键字")
    start = tokens[0].upper()
    if start not in READ_ONLY_START:
        raise ReadOnlyViolation(f"只读通道仅允许 SELECT/WITH 起始，检测到：{start}")

    # 字符串外扫描写/DDL 动词；紧跟 ( 的同名 token 按函数调用放行
    for match in _TOKEN_RE.finditer(bare):
        word = match.group(0).upper()
        if word not in _WRITE_VERBS:
            continue
        tail = bare[match.end():]
        if tail[:1] == "(" or tail.lstrip()[:1] == "(":
            continue  # replace(...) 等函数调用
        raise ReadOnlyViolation(f"只读通道不允许写/DDL 关键字：{word}")

    return sql
