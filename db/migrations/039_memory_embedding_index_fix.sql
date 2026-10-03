-- 打包 ZT：移除 memory_items 的 IVFFlat ANN 索引（U201 冷启动空集缺陷修复，PR #100 CI）。
-- 根因（集成实测实证，非推断）：006 建的 ivfflat(lists=100) 在 1 行/少量行库上，
-- `ORDER BY embedding <=> :q LIMIT :top_k` 被规划器选为索引扫描，probes=1 命中桶无候选即返回空集
-- （U201 复现；同 SQL 逐子句剥离定位：一加 ORDER BY <=> 即空，换精确检索即命中）。
-- 另：ANN 索引扫描在并列距离（如与查询正交的 score=0 向量）下返回顺序不确定（图搜索序），
-- 与进程内档的稳定排序不可比，破坏两档对拍（U94）。
-- 006 注释已声明降级口径："沙盘小数据量规划器可能顺序扫描、结果等价；
-- 集成实测时降级为暂不建 ANN 索引，精确余弦已够沙盘数据量"（docs/26 §4.4 同口径）。
-- 本迁移将该降级正式化：DROP 后 recall 走精确顺序扫描，U201（冷启动单行命中）与 U94（两档对拍）均成立。
-- 真实数据规模（触发 docs/14 D35 或 ANN 调参批）时另立迁移建 HNSW（vector_cosine_ops）+ 配套 ef_search，
-- 届时同步反转 test_u93 的索引存在性断言与两档对拍口径。

DROP INDEX IF EXISTS idx_memory_items_embedding;
