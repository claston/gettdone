import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.application.access_control import AccessControlService
from app.application.errors import GoogleOAuthExchangeError, GoogleOAuthNotConfiguredError, GoogleOAuthStateError

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    frontend_base_url: str
    state_ttl_seconds: int = 600


class GoogleOAuthService:
    def __init__(
        self,
        *,
        config: GoogleOAuthConfig,
        access_control_service: AccessControlService,
    ) -> None:
        self.config = config
        self.access_control_service = access_control_service

    def build_authorization_url(self, *, next_path: str) -> str:
        self._assert_configured()
        safe_next = self._normalize_next_path(next_path)
        state, code_verifier = self.access_control_service.create_google_oauth_state(
            next_path=safe_next,
            ttl_seconds=self.config.state_ttl_seconds,
        )
        code_challenge = self._build_code_challenge(code_verifier)
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    def build_callback_redirect_url(self, *, code: str, state: str) -> str:
        self._assert_configured()
        oauth_state = self.access_control_service.consume_google_oauth_state(state=state)
        if oauth_state is None:
            raise GoogleOAuthStateError

        token_payload = self._exchange_code_for_token(
            code=code,
            code_verifier=oauth_state["code_verifier"],
        )
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise GoogleOAuthExchangeError("Missing access_token from Google token endpoint.")

        profile = self._fetch_google_profile(access_token=access_token)
        provider_user_id = str(profile.get("sub") or "").strip()
        email = str(profile.get("email") or "").strip().lower()
        name = str(profile.get("name") or "").strip()
        email_verified = bool(profile.get("email_verified"))

        if not provider_user_id or not email or not email_verified:
            raise GoogleOAuthExchangeError("Google profile is missing required verified identity fields.")

        user = self.access_control_service.register_or_authenticate_google_user(
            provider_user_id=provider_user_id,
            email=email,
            name=name or email.split("@", 1)[0],
        )

        params = urlencode(
            {
                "user_token": user.token,
                "next": self._normalize_next_path(str(oauth_state["next_path"])),
                "provider": "google",
            }
        )
        return f"{self.config.frontend_base_url}/auth-callback.html?{params}"

    def _exchange_code_for_token(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        payload = urlencode(
            {
                "code": code,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")

        request = Request(
            GOOGLE_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise GoogleOAuthExchangeError(f"Google token exchange failed: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GoogleOAuthExchangeError("Invalid JSON from Google token endpoint.") from exc

    def _fetch_google_profile(self, *, access_token: str) -> dict[str, Any]:
        request = Request(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise GoogleOAuthExchangeError(f"Google userinfo fetch failed: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GoogleOAuthExchangeError("Invalid JSON from Google userinfo endpoint.") from exc

    def _assert_configured(self) -> None:
        if not self.config.client_id or not self.config.client_secret or not self.config.redirect_uri:
            raise GoogleOAuthNotConfiguredError

    def _normalize_next_path(self, next_path: str | None) -> str:
        raw = str(next_path or "").strip()
        if not raw.startswith("/"):
            return "/client-area.html"
        return raw

    def _build_code_challenge(self, code_verifier: str) -> str:
        # codeql[py/weak-sensitive-data-hashing]: PKCE (RFC 7636) requires SHA-256 for code_challenge (S256), not password hashing.
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
