"""反思进化模块 v1（L2 单运营体反思；契约 docs/88）。

一次 **pass** ＝「读证据 → 出建议 → 收尾成报告」：证据全部来自既有只读投影
（`monitoring`／`recording` 的 ring），建议只落在进程内 ring，**不写图、不发布、
不改版本、不动路由**——采纳仍走既有人工流程（改图 → publish → canary 手动 promote）。

v1 边界（docs/88 §1）：零新节点类型、零 DSL 改动、零新依赖、零新错误码、零前端新页；
反思报告与候选为进程内 ring（PG 化随真实需求，D-6）；未配 LLM 时确定性降级（D-7）。
"""

from .adapter import LiteLLMSummarizer, NullSummarizer, Summarizer, get_summarizer
from .candidate import (
    Change,
    ReflectionCandidate,
    ReflectionReport,
    ReflectionStore,
    ReflectionStatus,
    evidence_digest,
    run_pass,
    validate_changes,
)
from .evidence import (
    AlertStat,
    EvidenceWindow,
    FailedNodeStat,
    ReflectionEvidence,
    ReplayStat,
    ShadowStat,
    VersionStat,
    build_evidence,
)
from .tunables import TUNABLE_WHITELIST, Bounds, TunableParam, TunableScope, in_bounds, is_whitelisted

__all__ = [
    "AlertStat",
    "Bounds",
    "Change",
    "EvidenceWindow",
    "FailedNodeStat",
    "LiteLLMSummarizer",
    "NullSummarizer",
    "ReflectionCandidate",
    "ReflectionEvidence",
    "ReflectionReport",
    "ReflectionStatus",
    "ReflectionStore",
    "ReplayStat",
    "ShadowStat",
    "Summarizer",
    "TUNABLE_WHITELIST",
    "TunableParam",
    "TunableScope",
    "VersionStat",
    "build_evidence",
    "evidence_digest",
    "get_summarizer",
    "in_bounds",
    "is_whitelisted",
    "run_pass",
    "validate_changes",
]
