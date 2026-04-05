"""Authentication middleware and utilities.

Handles JWT token validation against AWS Cognito for
protecting API endpoints. Provides FastAPI dependency
injection for authenticated routes.

In development mode (APP_ENV=development), token verification
is skipped and a mock user is returned.
"""

import logging
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt

from src.config import get_settings

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

# In-memory cache for the JWKS fetched from Cognito.
_jwks_cache: dict[str, Any] | None = None


def _get_jwks_url() -> str:
    """Build the JWKS URL for the configured Cognito user pool."""
    settings = get_settings()
    region = settings.AWS_REGION
    pool_id = settings.COGNITO_USER_POOL_ID
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"


def _get_issuer() -> str:
    """Build the expected issuer URL for the configured Cognito user pool."""
    settings = get_settings()
    region = settings.AWS_REGION
    pool_id = settings.COGNITO_USER_POOL_ID
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


async def _fetch_jwks() -> dict[str, Any]:
    """Fetch and cache the JSON Web Key Set from the Cognito endpoint.

    The result is cached in a module-level variable so subsequent
    calls do not make additional HTTP requests.

    Returns:
        The parsed JWKS dictionary.

    Raises:
        HTTPException: If the JWKS endpoint is unreachable.
    """
    global _jwks_cache  # noqa: PLW0603

    if _jwks_cache is not None:
        return _jwks_cache

    url = _get_jwks_url()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            _jwks_cache = response.json()
            logger.info("Fetched JWKS from %s", url)
            return _jwks_cache
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch JWKS from %s: %s", url, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify credentials: identity provider unavailable",
        ) from exc


def _find_signing_key(jwks: dict[str, Any], kid: str) -> dict[str, Any]:
    """Find the key in the JWKS that matches the given key ID.

    Args:
        jwks: The JWKS dictionary.
        kid: The ``kid`` header from the JWT.

    Returns:
        The matching JWK dictionary.

    Raises:
        HTTPException: If no matching key is found.
    """
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token signing key not found",
    )


async def verify_cognito_token(token: str) -> dict[str, Any]:
    """Verify a JWT issued by AWS Cognito.

    Fetches the JWKS (cached), matches the signing key by ``kid``,
    then verifies the signature, expiry, audience, and issuer.

    Args:
        token: The raw JWT string.

    Returns:
        The decoded token claims as a dictionary.

    Raises:
        HTTPException: If verification fails for any reason.
    """
    settings = get_settings()

    try:
        unverified_headers = jwt.get_unverified_headers(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
        ) from exc

    kid = unverified_headers.get("kid")
    if kid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing key ID",
        )

    jwks = await _fetch_jwks()
    signing_key = _find_signing_key(jwks, kid)

    try:
        public_key = jwk.construct(signing_key)
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.COGNITO_APP_CLIENT_ID,
            issuer=_get_issuer(),
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
        ) from exc

    return claims


def _mock_user() -> dict[str, Any]:
    """Return a mock user for development mode."""
    return {
        "sub": "dev-user-001",
        "email": "dev@localhost",
        "cognito:username": "dev-user",
        "token_use": "access",
    }


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency that extracts and verifies the current user.

    In development mode (``APP_ENV=development``), verification is
    skipped and a mock user is returned regardless of the token.

    Args:
        credentials: The ``Authorization: Bearer <token>`` header,
            extracted automatically by FastAPI.

    Returns:
        Decoded JWT claims (or mock claims in dev mode).

    Raises:
        HTTPException: If no token is provided or verification fails.
    """
    settings = get_settings()

    if not settings.is_production:
        logger.debug("Development mode: returning mock user")
        return _mock_user()

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await verify_cognito_token(credentials.credentials)
