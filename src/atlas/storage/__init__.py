"""Atlas 存储抽象（M5a，docs/24 §1 / 08 M5a 立项条）。

base.py 定义按资源分组的 Repository Protocol 与统一约定；
memory.py 是进程内实现（默认/测试后端）——八个 store 的聚合入口；
PG 实现与中断落库随 M5b（pg.py / recovery.py）。
"""
