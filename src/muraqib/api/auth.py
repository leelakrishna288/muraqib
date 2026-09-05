"""OAuth 2.1 / OIDC bearer-token authentication and role-based authorisation.

Design:

* **Asymmetric verification against the IdP's JWKS.** The API never holds a
  client secret and never sees a password. It fetches the issuer's public keys,
  caches them, and validates signature, `iss`, `aud`, `exp` and `nbf`.
  Works unchanged against Microsoft Entra ID, Google, Okta, Auth0 and Keycloak.
* **Roles from claims.** Scopes/roles are read from `roles`, `scp`, `scope` or
  `permissions` - the four spellings the major IdPs actually use.
* **A dev mode that is loudly a dev mode.** HS256 with a local secret, enabled
  only when MURAQIB_DEV_HS256_SECRET is set, so the API can be exercised
  end-to-end with no tenant. It refuses to run if auth is enabled and neither a
  JWKS URI nor a dev secret is configured, rather than silently allowing all.

Deliberately NOT implemented: any password handling, any token minting, any
credential storage. This service is a resource server. Issuing identity is the
IdP's job and doing it here would be the wrong answer to NDMO.SP.01.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Settings

log = logging.getLogger("muraqib.auth")

bearer_scheme = HTTPBearer(auto_error=False)

ROLE_ADMIN = "muraqib.admin"
ROLE_ASSESSOR = "muraqib.assess"
ROLE_READER = "muraqib.read"

_ROLE_IMPLIES: dict[str, set[str]] = {
    ROLE_ADMIN: {ROLE_ADMIN, ROLE_ASSESSOR, ROLE_READER},
    ROLE_ASSESSOR: {ROLE_ASSESSOR, ROLE_READER},
    ROLE_READER: {ROLE_READER},
}


@dataclass(slots=True)
class Principal:
    subject: str
    issuer: str = ""
    roles: set[str] = field(default_factory=set)
    claims: dict[str, Any] = field(default_factory=dict)
    anonymous: bool = False

    def effective_roles(self) -> set[str]:
        out: set[str] = set()
        for r in self.roles:
            out |= _ROLE_IMPLIES.get(r, {r})
        return out

    def has(self, role: str) -> bool:
        return self.anonymous or role in self.effective_roles()

    def audit_identity(self) -> str:
        """Stable, non-PII identity string for the audit ledger."""
        return "anonymous" if self.anonymous else f"{self.issuer}#{self.subject}"


class _JwksCache:
    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._uri = ""

    def get(self, uri: str, ttl: int) -> dict[str, Any]:
        now = time.time()
        if uri != self._uri or now - self._fetched_at > ttl or not self._keys:
            resp = httpx.get(uri, timeout=10)
            resp.raise_for_status()
            self._keys = resp.json()
            self._fetched_at = now
            self._uri = uri
            log.info("jwks refreshed", extra={"keys": len(self._keys.get("keys", []))})
        return self._keys


_jwks = _JwksCache()


def _roles_from_claims(claims: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for key in ("roles", "permissions"):
        value = claims.get(key)
        if isinstance(value, list):
            roles |= {str(v) for v in value}
        elif isinstance(value, str):
            roles |= set(value.split())
    for key in ("scp", "scope"):
        value = claims.get(key)
        if isinstance(value, str):
            roles |= set(value.split())
        elif isinstance(value, list):
            roles |= {str(v) for v in value}
    return roles


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    options = {"require": ["exp", "iat"], "verify_aud": bool(settings.oidc_audience)}
    common: dict[str, Any] = {
        "audience": settings.oidc_audience or None,
        "issuer": settings.oidc_issuer or None,
        "options": options,
        # Small clock-skew allowance only. A large leeway silently extends the
        # life of every expired token, which defeats short-lived access tokens.
        "leeway": 10,
    }
    if settings.oidc_jwks_uri:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        jwks = _jwks.get(settings.oidc_jwks_uri, settings.jwks_cache_seconds)
        for key in jwks.get("keys", []):
            if kid is None or key.get("kid") == kid:
                public_key = jwt.PyJWK(key).key
                return jwt.decode(
                    token, public_key, algorithms=["RS256", "ES256", "RS512"], **common
                )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no matching signing key for token kid")
    if settings.dev_hs256_secret:
        log.warning("using HS256 development token verification - do not use in production")
        return jwt.decode(token, settings.dev_hs256_secret, algorithms=["HS256"], **common)
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "auth is enabled but neither MURAQIB_OIDC_JWKS_URI nor MURAQIB_DEV_HS256_SECRET is configured",
    )


async def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return Principal(subject="anonymous", anonymous=True)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(credentials.credentials, settings)
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired") from exc
    except jwt.InvalidTokenError as exc:
        # Never echo the token or the raw error - both leak material.
        log.warning("token rejected", extra={"reason": type(exc).__name__})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    return Principal(
        subject=str(claims.get("sub", "unknown")),
        issuer=str(claims.get("iss", "")),
        roles=_roles_from_claims(claims),
        claims={k: v for k, v in claims.items() if k not in ("sub",)},
    )


def require_role(role: str):  # noqa: ANN201
    async def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role '{role}'")
        return principal

    return _dep
