"""Embedding 抽象与本地确定性实现（docs/26 §3，ADR T23 决策 1）。

沙盘默认 ``LocalDeterministicEmbedder``：分词 + signed hashing trick + L2 归一化，
纯 stdlib（re/hashlib/math，**不引 numpy**），无随机、无外部调用、无时钟依赖——
同文本永远同向量，满足录制回放（D26）与黄金用例的确定性硬约束。

明确局限（沙盘语义，文档与 UI 均须注明）：这是**词法 / 哈希级**相似度（共享词
命中即相似），不是真语义——同义词、跨语言、纯语义改写不召回。真实语义质量随
商业 embedding provider 解锁（docs/14 D35）。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol, runtime_checkable

# 与迁移 db/migrations/006_memory.sql 的 vector(256) 严格一致；改维度须另立迁移。
EMBED_DIM = 256

# 英文/数字小写词；中文按单字 unigram + 相邻 bigram（bigram 提中文召回）。
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """英文/数字按 ``[a-z0-9]+`` 小写切词；中文 unigram + 相邻 bigram。"""
    lowered = text.lower()
    tokens: list[str] = _WORD_RE.findall(lowered)
    cjk = "".join(_CJK_RE.findall(text))
    tokens.extend(list(cjk))
    tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


def _signed_hash(token: str, dim: int) -> tuple[int, float]:
    """md5 哈希到 dim 个桶；用摘要字节奇偶决定符号 ±1（抵消碰撞偏置）。"""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % dim
    sign = 1.0 if digest[4] & 1 else -1.0
    return bucket, sign


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量提供者接口；商业 provider（LiteLLM embedding）为 D35 缓做接缝。"""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalDeterministicEmbedder:
    """本地确定性 embedder（默认、离线、回放安全），signed hashing trick。"""

    dim = EMBED_DIM

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in tokenize(text):
            bucket, sign = _signed_hash(token, self.dim)
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # 空文本 / 无 token：零向量，cosine 约定为 0.0（见 cosine_similarity）。
            return vector
        return [value / norm for value in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """归一化向量余弦（点积）；任一为零向量返 0.0。本实现结果非负。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


def get_embedding_provider() -> EmbeddingProvider:
    """工厂：v1 恒返回本地确定性 embedder。

    预留 ``ATLAS_EMBEDDING_PROVIDER`` 切换位（缺省 local）；商业 provider 类体
    缓做 D35（接入时须解决维度一致性：投影 256 或向量重建/双写迁移），v1 不注册
    非 local 分支以避免死代码——显式指定非 local 值 fail-closed 报错。
    """
    provider = os.environ.get("ATLAS_EMBEDDING_PROVIDER", "local").strip() or "local"
    if provider != "local":
        raise RuntimeError(
            f"未知 ATLAS_EMBEDDING_PROVIDER={provider!r}；商业 embedding 随 D35 落地"
        )
    return LocalDeterministicEmbedder()
