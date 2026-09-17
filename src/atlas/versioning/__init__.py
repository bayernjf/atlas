"""Graph 版本化（M6，docs/20 §4.1 / 08 M6 立项条 / ADR T19）。

publish 把 latest 草稿冻结为不可变发布版本，并递归钉版 subgraph 引用
（`graphId@vN`）；版本号不可变故钉版本号即等价冻结引用内容，不复制子图 JSON。
"""
