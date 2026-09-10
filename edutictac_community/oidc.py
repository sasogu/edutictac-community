"""Cliente OIDC reutilizable (Authentik): authorization code + PKCE (authlib)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from authlib.jose import JsonWebKey, jwt


class OIDCClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        issuer: str,
        redirect_uri: str,
        *,
        scope: str = "openid",
        session_secret: str,
        state_cookie_name: str = "oidc_state",
        user_agent: str = "EduTicTac/0.1",
        admin_subjects: set[str] | None = None,
        admin_emails: set[str] | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.issuer = issuer.rstrip("/")
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.session_secret = session_secret
        self.state_cookie_name = state_cookie_name
        self.user_agent = user_agent
        self.admin_subjects = admin_subjects or set()
        self.admin_emails = {e.lower() for e in (admin_emails or set()) if e}
        self._meta: dict | None = None
        self._jwks = None

    def enabled(self) -> bool:
        return bool(
            self.session_secret
            and self.client_id
            and self.client_secret
            and self.issuer
            and self.redirect_uri
        )

    def _metadata(self) -> dict:
        if self._meta is None:
            url = self.issuer + "/.well-known/openid-configuration"
            resp = httpx.get(url, timeout=20, headers={"User-Agent": self.user_agent})
            resp.raise_for_status()
            self._meta = resp.json()
        return self._meta

    def _jwks(self, meta: dict):
        if self._jwks is None:
            resp = httpx.get(
                meta["jwks_uri"], timeout=20, headers={"User-Agent": self.user_agent}
            )
            resp.raise_for_status()
            self._jwks = JsonWebKey.import_key_set(resp.json())
        return self._jwks

    def _client(self) -> OAuth2Client:
        return OAuth2Client(
            self.client_id,
            self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=self.scope,
            token_endpoint_auth_method="client_secret_post",
        )

    def is_admin(self, sub: str, email: str = "") -> bool:
        return sub in self.admin_subjects or email.lower() in self.admin_emails

    def _sign(self, data: str) -> str:
        return hmac.new(self.session_secret.encode(), data.encode(), hashlib.sha256).hexdigest()

    def _encode(self, payload: dict) -> str:
        data = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return f"{data}.{self._sign(data)}"

    def _decode(self, cookie: str | None) -> dict | None:
        if not cookie or not self.session_secret:
            return None
        try:
            data, sig = cookie.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(data)):
                return None
            return json.loads(base64.urlsafe_b64decode(data.encode()).decode())
        except Exception:
            return None

    def build_login_url(self) -> tuple[str, str]:
        """Devuelve (url de autorización, cookie de estado firmada con code_verifier)."""
        meta = self._metadata()
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        uri, _ = self._client().create_authorization_url(
            meta["authorization_endpoint"],
            state,
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )
        cookie = self._encode({"state": state, "code_verifier": code_verifier})
        return uri, cookie

    def _userinfo_from_id_token(self, token: dict, meta: dict) -> dict:
        id_token = token.get("id_token") if isinstance(token, dict) else None
        if not id_token:
            return {}
        claims = jwt.decode(
            id_token,
            self._jwks(meta),
            claims_options={
                "iss": {"essential": True, "value": meta["issuer"]},
                "aud": {"essential": True, "value": self.client_id},
                "sub": {"essential": True},
                "exp": {"essential": True},
            },
        )
        claims.validate(leeway=60)
        return dict(claims)

    def handle_callback(self, state: str, code: str, state_cookie: str | None) -> dict:
        saved = self._decode(state_cookie)
        if not saved or saved.get("state") != state:
            raise ValueError("state mismatch")

        meta = self._metadata()
        client = self._client()
        token = client.fetch_token(
            meta["token_endpoint"],
            grant_type="authorization_code",
            code=code,
            redirect_uri=self.redirect_uri,
            code_verifier=saved.get("code_verifier", ""),
        )
        userinfo = self._userinfo_from_id_token(token, meta)
        if "sub" not in userinfo:
            resp = client.get(meta["userinfo_endpoint"])
            resp.raise_for_status()
            try:
                userinfo = resp.json()
            except ValueError as exc:
                raise ValueError("userinfo returned non-json response") from exc
            if not isinstance(userinfo, dict):
                raise ValueError("userinfo returned invalid response")
            if "sub" not in userinfo and isinstance(token, dict) and isinstance(token.get("userinfo"), dict):
                userinfo = token["userinfo"]
        sub = str(userinfo.get("sub", ""))
        if not sub:
            raise ValueError("missing subject")
        email = (userinfo.get("email") or "").lower()
        return {
            "sub": sub,
            "email": email,
            "name": userinfo.get("name", "") or userinfo.get("preferred_username", ""),
            "admin": self.is_admin(sub, email),
        }
