"""Model config request/masking/sealing logic moved out of api/main.py.

Behavior is identical to the former private helpers; routes now only
wire requests to these functions. Key material is encrypted via the
process-wide secret provider and views expose at most a `***last4` mask.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from atlas.llm.config import ModelConfig


class ModelConfigRequest(BaseModel):
    model: str | None = None
    apiKey: str | None = None  # 明文 key，写入即 AES-GCM 加密；空/未传保留原信封
    baseUrl: str | None = None
    enabled: bool | None = None


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def masked_view(cfg: ModelConfig) -> dict[str, Any]:
    """HTTP 脱敏视图：api_key 回 ``***``＋**明文**尾 4 位（docs/93 §2.3）。

    public_view() 只够在 store 层防御性用（信封尾 4 位无意义）；产品语义要的是
    明文 key 尾 4 位，故在此解密一次。解密失败（密钥轮换/坏信封）只回 ***。
    """
    view = cfg.public_view()
    if cfg.api_key_enc:
        from atlas.connections.service import get_secret_provider

        try:
            plain = get_secret_provider().decrypt(cfg.api_key_enc)
            view["apiKey"] = f"***{plain[-4:]}" if plain else ""
        except Exception:  # 坏信封/密钥不可用：只回占位，绝不泄漏
            view["apiKey"] = "***"
    return view


def seal_api_key(api_key: str | None, existing: str | None) -> str | None:
    """明文 key → AES-GCM 信封；None/空串保留原信封（照 connection.client_secret 语义）。"""
    if api_key is None or not api_key.strip():
        return existing  # 未传/空 → 不动（前端脱敏回显不会回明文）
    from atlas.connections.service import get_secret_provider

    return get_secret_provider().encrypt(api_key.strip())


def build_model_config(
    mode: str, raw: ModelConfigRequest, existing: ModelConfig | None,
    *, updated_by: str,
) -> ModelConfig:
    """由请求体构造 ModelConfig；api_key 写即加密，缺省字段沿用现状。"""
    prev = existing
    model = (raw.model or "").strip() or (prev.model if prev else "")
    if not model:
        raise HTTPException(status_code=422, detail="模型名不能为空")
    api_key_enc = seal_api_key(raw.apiKey, prev.api_key_enc if prev else None)
    return ModelConfig(
        mode=mode,
        model=model,
        api_key_enc=api_key_enc or "",
        base_url=(raw.baseUrl or "").strip() or (prev.base_url if prev else None),
        enabled=raw.enabled if raw.enabled is not None else (prev.enabled if prev else True),
        updated_by=updated_by,
        updated_at=now_iso_utc(),
    )
