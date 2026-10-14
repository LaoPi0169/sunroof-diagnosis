"""签名短时令牌服务 (阶段 D 批次 1 生产加固 D1-c)

回链 specs/production-hardening-brief-v1.md D1-c + adr-005 D5 + decision-memo-02 §3 第 2 条:
  * 格式 `sr1.<b64url(payload)>.<b64url(hmac_sha256)>`, 载荷 {sub, role, projects, iat, exp, jti};
  * 过期 (exp) + 吊销 (revoked_tokens 表) + 改签 (HMAC) 三重防护, 全部 401 Default-Deny;
  * 签名秘密: env SUNROOF_TOKEN_SECRET; 缺省进程随机生成 —— 零硬编码字面量 (脱敏口径);
  * 签发 = 静态教学凭证换短时令牌 (身份目录与 application/auth.py 静态映射同值);
  * Default-Deny: 未知用户/坏凭证同封套拒绝 (零身份存在性泄漏)。
诚实边界 (brief §2 非目标 1): 非真实 SSO/OIDC —— "生产形状"教学实现, 上线前仍须按
specs/role-and-permission-matrix.md 以真实身份源重审。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from application.database import get_db_connection
from application.models import TrustedUser

TOKEN_PREFIX = "sr1."
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 12 * 3600
ROLE_ENUM = ("pqe", "quality_supervisor")

# 教学合成身份目录 (与 application/auth.py 静态映射同值; 仅供签发校验, 鉴权裁决仍在 auth.py)
USER_DIRECTORY: Dict[str, Dict[str, Any]] = {
    "pqe_chen_oema": {"credential": "token_pqe_oem_a", "role": "pqe", "projects": ["OEM-A"]},
    "pqe_zhao_oemb": {"credential": "token_pqe_oem_b", "role": "pqe", "projects": ["OEM-B"]},
    "supervisor_li": {
        "credential": "token_qa_supervisor_secret",
        "role": "quality_supervisor",
        "projects": ["*"],
    },
}

_process_secret: Optional[bytes] = None  # 进程内随机秘密缓存 (仅当 env 未提供时)


class TokenError(Exception):
    """结构化令牌错误 (code: UNKNOWN_USER/INVALID_CREDENTIAL/INVALID_TTL/
    MALFORMED/BAD_SIGNATURE/EXPIRED/REVOKED)。用户面统一 401, code 仅服务端/测试可见。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _signing_secret() -> bytes:
    """签名秘密: env SUNROOF_TOKEN_SECRET 优先; 缺省进程随机 (重启即失效, 生产必须显式配置)。"""
    global _process_secret
    env_secret = os.environ.get("SUNROOF_TOKEN_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")
    if _process_secret is None:
        _process_secret = secrets.token_bytes(32)
    return _process_secret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

def issue_token(user_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                credential: str = "") -> Dict[str, Any]:
    """校验 (user_id, credential) 成对匹配后签发 HMAC 签名短时令牌。

    未知用户 / 坏凭证 / 坏 TTL 抛 TokenError —— 端点层统一同封套 401 (零存在性泄漏)。
    常数时间比较 (hmac.compare_digest), 不泄漏凭证前缀匹配进度。
    """
    identity = USER_DIRECTORY.get(user_id)
    if identity is None:
        raise TokenError("UNKNOWN_USER", "unknown user_id")
    if not credential or not hmac.compare_digest(identity["credential"], credential):
        raise TokenError("INVALID_CREDENTIAL", "credential mismatch")
    if not isinstance(ttl_seconds, int) or not (1 <= ttl_seconds <= MAX_TTL_SECONDS):
        raise TokenError("INVALID_TTL", "ttl_seconds out of range")
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": user_id,
        "role": identity["role"],
        "projects": list(identity["projects"]),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex[:16],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_b64 = _b64url(canonical.encode("utf-8"))
    signature = _b64url(
        hmac.new(_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    )
    return {
        "access_token": f"{TOKEN_PREFIX}{payload_b64}.{signature}",
        "token_type": "bearer",
        "expires_in": ttl_seconds,
        "jti": payload["jti"],
        "issued_for": user_id,
    }


def decode_token(token: str) -> Dict[str, Any]:
    """验签 + 验过期, 返回载荷; 畸形/改签/过期抛 TokenError (吊销在 trusted_user_from_token 查表)。"""
    if not token or not token.startswith(TOKEN_PREFIX):
        raise TokenError("MALFORMED", "token prefix mismatch")
    parts = token[len(TOKEN_PREFIX):].split(".")
    if len(parts) != 2:
        raise TokenError("MALFORMED", "token structure invalid")
    payload_b64, signature = parts
    expected = hmac.new(_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        signature_bytes = _b64url_decode(signature)
    except Exception:
        raise TokenError("MALFORMED", "signature not base64url")
    if not hmac.compare_digest(signature_bytes, expected):
        raise TokenError("BAD_SIGNATURE", "signature verification failed")
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        raise TokenError("MALFORMED", "payload not decodable")
    now = int(datetime.now(timezone.utc).timestamp())
    if not isinstance(payload.get("exp"), int) or payload["exp"] < now:
        raise TokenError("EXPIRED", "token expired")
    return payload


def is_revoked(jti: str) -> bool:
    """jti 是否已吊销 (revoked_tokens 表; 空串/缺失视为未吊销, 畸形令牌由 decode 层先拒)。"""
    if not jti:
        return False
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone()
        return row is not None
    finally:
        conn.close()


def revoke_token(jti: str, revoked_by: str, reason: Optional[str] = None) -> bool:
    """吊销 jti (INSERT OR IGNORE 幂等); 返回是否本次新吊销 (重复吊销 False, 仍属成功)。"""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, revoked_by, reason) VALUES (?, ?, ?)",
            (jti, revoked_by, reason),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def payload_to_trusted_user(payload: Dict[str, Any]) -> TrustedUser:
    """签名载荷 -> 可信身份 (载荷由服务端签发, role/projects 非请求体自报; 形状非法即拒)。"""
    role = payload.get("role")
    projects = payload.get("projects")
    if (payload.get("sub") is None or role not in ROLE_ENUM
            or not isinstance(projects, list) or not projects):
        raise TokenError("MALFORMED", "payload identity fields invalid")
    return TrustedUser(
        user_id=str(payload["sub"]),
        user_role=str(role),
        allowed_oem_projects=[str(p) for p in projects],
    )


def trusted_user_from_token(token: str) -> TrustedUser:
    """签名令牌 -> 可信身份: decode (验签+过期) -> 查吊销表 -> 载荷形状校验。任一失败 TokenError。"""
    payload = decode_token(token)
    if is_revoked(str(payload.get("jti", ""))):
        raise TokenError("REVOKED", "token has been revoked")
    return payload_to_trusted_user(payload)
