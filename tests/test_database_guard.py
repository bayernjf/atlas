"""只读 SQL 静态审查纯函数测试（docs/32 §4，13 文档 U236）。"""

from __future__ import annotations

import pytest

from atlas.database.guard import ReadOnlyViolation, assert_read_only_sql


# --- 放行：只读 SELECT / WITH ---

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select order_id, reason from orders where amount > :min",
        "  SELECT * FROM orders ORDER BY order_id  ",
        "SELECT\n*\nFROM\torders",
        "SELECT COUNT(*) AS n FROM orders",
        "SELECT * FROM orders WHERE order_id = :oid",
        "SELECT o.order_id FROM orders o JOIN refunds r ON r.order_id = o.order_id",
        "SELECT * FROM (SELECT order_id FROM orders) sub",
        "WITH recent AS (SELECT order_id FROM orders) SELECT * FROM recent",
        "with recent as (select * from orders where amount > 100) select count(*) from recent",
        "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b",
        "SELECT 1;",  # 末尾单分号
        "SELECT 1 -- 行注释\n",
        "/* 块注释 */ SELECT 1",
        "SELECT 'INSERT INTO t VALUES (1)' AS label",  # 字符串内写动词不算
        "SELECT 'a;b;c' AS s",  # 字符串内分号不算多语句
        "SELECT replace(name, 'a', 'b') FROM orders",  # replace() 函数
        "SELECT lower(reason), trim(reason) FROM orders",
        'SELECT "update", "delete" FROM orders',  # 引号标识符内的写动词不算
        "SELECT json_extract(payload, '$.id') FROM orders",
    ],
)
def test_read_only_sql_allowed(sql):
    assert assert_read_only_sql(sql) == sql


# --- 拒绝：写 / DDL / 管理动词起句 ---

@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (order_id) VALUES ('x')",
        "UPDATE orders SET status = 'x' WHERE order_id = '1'",
        "DELETE FROM orders WHERE order_id = '1'",
        "MERGE INTO orders t USING src ON 1=0 WHEN NOT MATCHED THEN INSERT VALUES (1)",
        "REPLACE INTO orders (order_id) VALUES ('x')",
        "CREATE TABLE t (id int)",
        "ALTER TABLE orders ADD COLUMN x int",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "RENAME TABLE a TO b",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE 'x.db' AS x",
        "DETACH DATABASE x",
        "VACUUM",
        "REINDEX orders",
        "COPY orders FROM PROGRAM 'x'",
        "CALL do_refund('1')",
        "SET statement_timeout = 1000",
        "SHOW max_connections",
        "GRANT SELECT ON orders TO ro",
        "REVOKE SELECT ON orders FROM ro",
        "EXPLAIN ANALYZE UPDATE orders SET status='x'",
        "BEGIN",
        "START TRANSACTION",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT sp",
    ],
)
def test_write_and_ddl_rejected(sql):
    with pytest.raises(ReadOnlyViolation) as exc_info:
        assert_read_only_sql(sql)
    assert exc_info.value.code == "DB_SQL_NOT_READ_ONLY"


# --- 多语句与注释藏写 ---

def test_multiple_statements_rejected():
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql("SELECT 1; INSERT INTO orders (order_id) VALUES ('x')")


def test_trailing_semicolon_then_write_after_comment_rejected():
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql("SELECT 1; /* ok */ DROP TABLE orders")


def test_line_comment_hiding_write_still_caught():
    # 分号后注释之外还有一条 DROP（多语句）
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql("SELECT 1; DROP TABLE orders -- ignore")


def test_block_commented_write_alone_is_not_statement():
    # 仅注释包裹写动词、实际语句是 SELECT → 放行（注释不是语句）
    assert assert_read_only_sql("/* INSERT INTO x */ SELECT 1") == "/* INSERT INTO x */ SELECT 1"


# --- 可写 CTE / SELECT INTO ---

def test_writable_cte_delete_rejected():
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(
            "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d"
        )


def test_writable_cte_insert_rejected():
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(
            "WITH ins AS (INSERT INTO orders (order_id) VALUES ('x') RETURNING *) "
            "SELECT * FROM ins"
        )


def test_select_into_rejected():
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql("SELECT * INTO backup_orders FROM orders")


# --- 形态 ---

@pytest.mark.parametrize("sql", ["", "   ", "-- only comment", "/* x */"])
def test_empty_or_comment_only_rejected(sql):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_sql(sql)
