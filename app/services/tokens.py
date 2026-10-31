import jwt
from jwt import PyJWKClient

from app.core.config import Settings


class TokenError(Exception):
    pass


class TokenExpiredError(TokenError):
    pass


class TokenVerifierUnavailable(Exception):
    pass


class CognitoTokenVerifier:
    """Verifies Cognito access tokens against the user pool's published JWKS."""

    def __init__(self, settings: Settings):
        self._issuer = settings.cognito_issuer
        self._client_id = settings.cognito_client_id
        self._jwks = PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)

    def verify_access_token(self, token: str) -> dict:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=30,
                options={"require": ["exp", "iat", "sub", "token_use"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError() from exc
        except jwt.PyJWKClientConnectionError as exc:
            raise TokenVerifierUnavailable() from exc
        except jwt.PyJWTError as exc:
            raise TokenError() from exc

        # Access tokens carry client_id instead of aud.
        if claims.get("token_use") != "access" or claims.get("client_id") != self._client_id:
            raise TokenError()
        return claims


def unverified_username(token: str | None) -> str | None:
    """Reads the Cognito username from a (possibly expired) access token.

    Only used to compute SECRET_HASH for a refresh; Cognito itself validates the refresh token.
    """
    if not token:
        return None
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.PyJWTError:
        return None
    return claims.get("username") or claims.get("sub")
