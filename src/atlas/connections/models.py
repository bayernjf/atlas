# -*- coding: utf-8 -*-
"""OAuth2 连接数据模型（docs/35 §4.2，T4；D22 generic OAuth2 子集，平台无关）。

Connection 是租户内一条 OAuth2 授权码流程连接配置；client_secret 与 access/refresh
token 一律以 SecretProvider 信封（envelope）落库，``public_view`` 投影删除全部信封与
明文秘密，列表/详情端点只返回投影。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ConnectionStatus = Literal["draft", "connected", "error"]

STATUS_DRAFT = "draft"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"


@dataclass
class Connection:
    id: str
    tenant_id: str
    provider: str
    display_name: str
    auth_url: str
    token_url: str
    client_id: str
    # 以下为秘密/敏感字段：信封落库，绝不进 public_view
    client_secret_envelope: str | None = None
    scopes: list[str] = field(default_factory=list)
    redirect_uri: str = ""
    status: ConnectionStatus = STATUS_DRAFT
    access_token_envelope: str | None = None
    refresh_token_envelope: str | None = None
    token_type: str | None = None
    expires_at: str | None = None  # UTC iso；None 视为不过期
    last_error: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def public_view(self) -> dict[str, object]:
        """对外投影：只暴露非秘密字段 + hasClientSecret，绝不回传信封/明文。"""
        return {
            "id": self.id,
            "provider": self.provider,
            "displayName": self.display_name,
            "authUrl": self.auth_url,
            "tokenUrl": self.token_url,
            "clientId": self.client_id,
            "hasClientSecret": bool(self.client_secret_envelope),
            "scopes": list(self.scopes),
            "redirectUri": self.redirect_uri,
            "status": self.status,
            "tokenType": self.token_type,
            "expiresAt": self.expires_at,
            "lastError": self.last_error,
            "createdBy": self.created_by,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
