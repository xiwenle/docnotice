"""安全组件：密码哈希（bcrypt）与 JWT 签发/校验。

- 密钥 / 算法 / 过期时间全部来自配置（app.core.config），禁止硬编码（项目规范 §29）
- JWT 载荷（docs/DESIGN.md §4.1）：
  { sub, user_id, username, role, iss, aud, iat, exp, jti }
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from passlib.context import CryptContext  # type: ignore[import-untyped]

from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()

# bcrypt 密码哈希（bcrypt 固定 4.0.1，见 requirements.txt 注释）。
# rounds 默认 12（安全默认）；低配服务器可通过 BCRYPT_ROUNDS 调低登录延迟，
# 已有哈希按前缀自适应校验，调低只影响新哈希。
pwd_context = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=settings.BCRYPT_ROUNDS
)

JWT_ISSUER = "company-doc-notice-system"
JWT_AUDIENCE = "company-doc-notice-system"


def hash_password(plain: str) -> str:
    """bcrypt 哈希（自动加盐）。"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与哈希是否匹配（异常一律视为 False，避免信息泄露）。"""
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001
        return False


def create_access_token(
    *,
    user_id: int,
    username: str,
    role: str,
    token_version: int = 0,
    expires_minutes: Optional[int] = None,
) -> str:
    """签发 JWT（HS256）。ver = 签发时的 token_version（单点登录锚点）。"""
    now = datetime.now(timezone.utc)
    expire_minutes = expires_minutes or settings.JWT_EXPIRE_MINUTES
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "user_id": user_id,
        "username": username,
        "role": role,
        "ver": token_version,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """校验并解码 JWT；无效/过期统一抛 401 UNAUTHORIZED。"""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise AppError("UNAUTHORIZED", "登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise AppError("UNAUTHORIZED", "无效的登录凭证")
    return payload