-- docs/125 §3.3（项目级审查发现 #5）：openapi_imports 内容指纹唯一约束。
-- 023 建的 idx_openapi_imports_hash 为非唯一索引，add 的「SELECT 预检 → INSERT」
-- 之间存在 TOCTOU 窗口：并发同形导入可双双通过预检并双写。本迁移：
--   1) 存量去重：同 (tenant_id, 非空 content_hash) 的未删多行，保留 seq 最小行，
--      其余软删（置 deleted_at，UTC ISO 文本，与软删列同族；不 DELETE，保历史/审计）；
--      content_hash='' 的 023 前老行不参与（唯一索引谓词同样排除，见下）。
--   2) DROP 023 的非唯一索引；
--   3) 建同名部分唯一索引：未删且指纹非空。谓词带 content_hash <> ''——
--      否则 023 前多个 '' 老行会让 CREATE UNIQUE INDEX 直接失败；
--      023 后新导入指纹恒为 sha256 hex，不受影响。
-- 全脚本幂等可重入：去重 CTE 二跑零行受影响；DROP/CREATE 均带 IF [NOT] EXISTS。

WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY tenant_id, content_hash
               ORDER BY seq
           ) AS rn
    FROM openapi_imports
    WHERE deleted_at IS NULL
      AND content_hash <> ''
)
UPDATE openapi_imports
SET deleted_at = to_char(
    now() AT TIME ZONE 'UTC',
    'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'
)
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

DROP INDEX IF EXISTS idx_openapi_imports_hash;

CREATE UNIQUE INDEX IF NOT EXISTS idx_openapi_imports_hash
    ON openapi_imports (tenant_id, content_hash)
    WHERE deleted_at IS NULL
      AND content_hash <> '';
