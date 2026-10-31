"""Server-side wrapper around the Cognito user pool.

The browser never talks to Cognito directly: all auth calls go through the API, and tokens are kept in
HttpOnly cookies. Only public (unsigned) Cognito operations are used, so no AWS credentials are needed.
"""

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int


class AuthServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


_RATE_LIMITED = (429, "rate_limited", "Too many attempts. Please wait a moment and try again.")

# Cognito error code -> (HTTP status, API error code, user-facing message or None to use Cognito's message)
_DEFAULT_ERRORS: dict[str, tuple[int, str, str | None]] = {
    "NotAuthorizedException": (401, "invalid_credentials", "Incorrect email or password."),
    "UserNotFoundException": (401, "invalid_credentials", "Incorrect email or password."),
    "UserNotConfirmedException": (403, "user_not_confirmed", "Please verify your email before signing in."),
    "UsernameExistsException": (409, "account_exists", "An account with this email already exists."),
    "AliasExistsException": (409, "account_exists", "An account with this email or phone number already exists."),
    "InvalidPasswordException": (400, "invalid_password", None),
    "CodeMismatchException": (400, "invalid_code", "That verification code is incorrect."),
    "ExpiredCodeException": (400, "expired_code", "That code has expired. Request a new one."),
    "CodeDeliveryFailureException": (502, "code_delivery_failed", "We couldn't send the verification code. Try again."),
    "LimitExceededException": _RATE_LIMITED,
    "TooManyRequestsException": _RATE_LIMITED,
    "TooManyFailedAttemptsException": _RATE_LIMITED,
    "PasswordResetRequiredException": (403, "password_reset_required", "Please reset your password to continue."),
}


class CognitoService:
    def __init__(self, settings: Settings):
        self._client = boto3.client(
            "cognito-idp",
            region_name=settings.resolved_cognito_region,
            config=Config(signature_version=UNSIGNED, retries={"max_attempts": 3, "mode": "standard"}),
        )
        self._client_id = settings.cognito_client_id
        self._client_secret = settings.cognito_client_secret

    # -- helpers -------------------------------------------------------------------------------

    def _secret_hash(self, username: str) -> dict[str, str]:
        if not self._client_secret:
            return {}
        digest = hmac.new(
            self._client_secret.encode(), (username + self._client_id).encode(), hashlib.sha256
        ).digest()
        return {"SECRET_HASH": base64.b64encode(digest).decode()}

    def _secret_hash_param(self, username: str) -> dict[str, str]:
        # Non-initiate_auth operations take the hash as a top-level SecretHash argument.
        secret = self._secret_hash(username)
        return {"SecretHash": secret["SECRET_HASH"]} if secret else {}

    def _call(self, operation: str, overrides: dict[str, tuple[int, str, str | None]] | None = None, **kwargs):
        try:
            return getattr(self._client, operation)(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            cognito_message = exc.response.get("Error", {}).get("Message", "")
            mapping = (overrides or {}).get(code) or _DEFAULT_ERRORS.get(code)
            if code == "InvalidParameterException":
                mapping = (400, "invalid_input", _invalid_parameter_message(cognito_message))
            if mapping is None:
                logger.warning("Cognito %s failed with %s", operation, code)
                raise AuthServiceError(502, "auth_unavailable", "Authentication service error. Try again.") from exc
            status, api_code, message = mapping
            raise AuthServiceError(status, api_code, message or cognito_message) from exc
        except BotoCoreError as exc:
            logger.warning("Cognito %s unreachable: %s", operation, type(exc).__name__)
            raise AuthServiceError(503, "auth_unavailable", "Authentication service is unavailable. Try again.") from exc

    @staticmethod
    def _tokens(result: dict) -> AuthTokens:
        if "ChallengeName" in result:
            raise AuthServiceError(
                400, "challenge_required", "This account needs an extra sign-in step that isn't supported yet."
            )
        auth = result["AuthenticationResult"]
        return AuthTokens(
            access_token=auth["AccessToken"],
            refresh_token=auth.get("RefreshToken"),
            expires_in=int(auth.get("ExpiresIn", 3600)),
        )

    # -- operations ----------------------------------------------------------------------------

    def sign_up(self, *, email: str, password: str, phone_number: str, full_name: str) -> bool:
        """Registers the user. Returns True when the pool auto-confirmed them (no code needed)."""
        result = self._call(
            "sign_up",
            ClientId=self._client_id,
            Username=email,
            Password=password,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "phone_number", "Value": phone_number},
                {"Name": "name", "Value": full_name},
            ],
            **self._secret_hash_param(email),
        )
        return bool(result.get("UserConfirmed"))

    def confirm_sign_up(self, *, email: str, code: str) -> None:
        self._call(
            "confirm_sign_up",
            overrides={
                "NotAuthorizedException": (409, "already_confirmed", "This account is already verified. Please sign in.")
            },
            ClientId=self._client_id,
            Username=email,
            ConfirmationCode=code,
            **self._secret_hash_param(email),
        )

    def resend_confirmation(self, *, email: str) -> None:
        try:
            self._call(
                "resend_confirmation_code",
                ClientId=self._client_id,
                Username=email,
                **self._secret_hash_param(email),
            )
        except AuthServiceError as exc:
            # Don't reveal whether an account exists.
            if exc.code != "invalid_credentials":
                raise

    def login(self, *, email: str, password: str) -> AuthTokens:
        result = self._call(
            "initiate_auth",
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=self._client_id,
            AuthParameters={"USERNAME": email, "PASSWORD": password, **self._secret_hash(email)},
        )
        return self._tokens(result)

    def refresh(self, *, refresh_token: str, username: str | None) -> AuthTokens:
        params = {"REFRESH_TOKEN": refresh_token}
        if self._client_secret:
            if not username:
                raise AuthServiceError(401, "session_expired", "Your session has expired. Please sign in again.")
            params.update(self._secret_hash(username))
        result = self._call(
            "initiate_auth",
            overrides={"NotAuthorizedException": (401, "session_expired", "Your session has expired. Please sign in again.")},
            AuthFlow="REFRESH_TOKEN_AUTH",
            ClientId=self._client_id,
            AuthParameters=params,
        )
        tokens = self._tokens(result)
        return AuthTokens(tokens.access_token, tokens.refresh_token or None, tokens.expires_in)

    def get_user_attributes(self, access_token: str) -> dict[str, str]:
        result = self._call(
            "get_user",
            overrides={"NotAuthorizedException": (401, "session_expired", "Your session has expired. Please sign in again.")},
            AccessToken=access_token,
        )
        attributes = {attr["Name"]: attr["Value"] for attr in result.get("UserAttributes", [])}
        attributes.setdefault("sub", result.get("Username", ""))
        return attributes

    def forgot_password(self, *, email: str) -> None:
        try:
            self._call(
                "forgot_password",
                ClientId=self._client_id,
                Username=email,
                **self._secret_hash_param(email),
            )
        except AuthServiceError as exc:
            if exc.code != "invalid_credentials":
                raise

    def confirm_forgot_password(self, *, email: str, code: str, new_password: str) -> None:
        self._call(
            "confirm_forgot_password",
            overrides={"UserNotFoundException": (400, "invalid_code", "That verification code is incorrect.")},
            ClientId=self._client_id,
            Username=email,
            ConfirmationCode=code,
            Password=new_password,
            **self._secret_hash_param(email),
        )

    def revoke(self, refresh_token: str) -> None:
        params = {"Token": refresh_token, "ClientId": self._client_id}
        if self._client_secret:
            params["ClientSecret"] = self._client_secret
        self._call("revoke_token", **params)


def _invalid_parameter_message(cognito_message: str) -> str:
    lowered = cognito_message.lower()
    if "phone" in lowered:
        return "Enter the phone number in international format, e.g. +94771234567."
    if "password" in lowered:
        return "Password doesn't meet the requirements."
    if "email" in lowered:
        return "Enter a valid email address."
    return "Some of the details you entered are invalid."
