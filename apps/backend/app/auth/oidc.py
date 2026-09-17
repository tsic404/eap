"""OpenID Connect client: discovery, PKCE authorization code flow, id_token
verification, and userinfo (architecture §33).

The IdP is configured by ``oidc_issuer``; everything else (authorization,
token, userinfo, JWKS endpoints) is discovered from the issuer's
``.well-known/openid-configuration``. An ``httpx.AsyncClient`` is used directly
(rather than authlib) to stay consistent with the rest of the backend, which
already speaks HTTP through httpx and signs/verifies JWTs with PyJWT.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

from app.config import Settings
from app.core.exceptions import AuthError

_HTTP_TIMEOUT = httpx.Timeout(15.0)


def generate_state() -> str:
    """Return a random, unguessable OAuth state value (CSRF protection)."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Return a random OIDC nonce bound to the id_token (replay protection)."""
    return secrets.token_urlsafe(32)


def generate_code_verifier() -> str:
    """Return a PKCE code verifier (43–128 URL-safe chars per RFC 7636)."""
    return secrets.token_urlsafe(64)


def code_challenge_from_verifier(verifier: str) -> str:
    """Derive the S256 code challenge from a code verifier."""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcClient:
    """Minimal OIDC relying party for the authorization-code + PKCE flow."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._issuer = settings.oidc_issuer.rstrip("/")
        self._client_id = settings.oidc_client_id
        self._client_secret = settings.oidc_client_secret
        self._redirect_uri = settings.oidc_redirect_uri
        self._client = client
        self._owns_client = client is None
        self._config: dict[str, Any] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._issuer and self._client_id)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def discover(self) -> dict[str, Any]:
        """Fetch and cache the issuer's OpenID configuration."""
        if self._config is not None:
            return self._config
        client = await self._http()
        url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The IdP being unreachable/erroring is a service dependency fault,
            # not a caller error — surface 503 so the client can retry.
            raise AuthError(503, "OIDC_UNAVAILABLE", "OIDC discovery failed") from exc
        self._config = self._json(response)
        return self._config

    async def build_authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Return the IdP authorization URL to redirect the browser to."""
        config = await self.discover()
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{config['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange an authorization code for the IdP token response."""
        config = await self.discover()
        client = await self._http()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "code_verifier": code_verifier,
        }
        # A public client (PKCE) does not send the secret; send it only when
        # configured so confidential-client IdPs still authenticate us.
        if self._client_secret:
            response = await client.post(
                config["token_endpoint"],
                data=data,
                auth=(self._client_id, self._client_secret),
            )
        else:
            response = await client.post(config["token_endpoint"], data=data)
        if response.status_code != 200:
            raise AuthError(400, "OIDC_TOKEN_EXCHANGE_FAILED", "IdP token exchange failed")
        return self._json(response)

    def verify_id_token(
        self,
        id_token: str,
        *,
        nonce: str,
        jwks: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify the id_token signature and claims; returns the decoded claims.

        ``jwks`` is the pre-fetched keyset (see :meth:`fetch_jwks`) so signature
        verification never blocks the event loop on synchronous key retrieval.
        """
        # get_unverified_header and decode both raise PyJWTError on malformed
        # tokens, so the whole parse+verify runs inside the try — a non-JWT
        # id_token must surface as 400 INVALID_ID_TOKEN, not a 500.
        try:
            unverified = jwt.get_unverified_header(id_token)
            signing_key = self._signing_key(jwks, unverified.get("kid"))
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                # 30s clock-skew tolerance: a few seconds of drift between the
                # backend and the IdP must not fail otherwise-valid logins.
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(400, "INVALID_ID_TOKEN", "OIDC id_token validation failed") from exc
        if claims.get("nonce") != nonce:
            raise AuthError(400, "INVALID_STATE", "OIDC nonce mismatch")
        return claims

    async def fetch_jwks(self) -> dict[str, Any]:
        config = await self.discover()
        client = await self._http()
        try:
            response = await client.get(config["jwks_uri"])
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthError(503, "OIDC_UNAVAILABLE", "OIDC signing keys unavailable") from exc
        return self._json(response)

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        config = await self.discover()
        client = await self._http()
        response = await client.get(
            config["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise AuthError(400, "OIDC_USERINFO_FAILED", "IdP userinfo request failed")
        return self._json(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        """Parse a JSON body, mapping malformed JSON to ``OIDC_UNAVAILABLE``.

        A ``200`` with a non-JSON body means the IdP endpoint is misconfigured —
        the same service-dependency fault class as an unreachable IdP, so it
        must surface as 503 rather than a raw ``JSONDecodeError`` (500).
        """
        try:
            return cast(dict[str, Any], response.json())
        except ValueError as exc:
            raise AuthError(503, "OIDC_UNAVAILABLE", "IdP returned invalid JSON") from exc

    @staticmethod
    def _signing_key(jwks: dict[str, Any], kid: str | None) -> Any:
        keys = jwks.get("keys", [])
        for jwk_data in keys:
            if kid is None or jwk_data.get("kid") == kid:
                return PyJWK(jwk_data).key
        raise AuthError(400, "INVALID_ID_TOKEN", "IdP signing key not found")
