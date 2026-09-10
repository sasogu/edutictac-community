"""Cookies firmadas HMAC-SHA256 para sesiones ligeras (anónimas u OIDC)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import Response


class SignedSession:
    def __init__(
        self,
        secret: str,
        cookie_name: str,
        *,
        cookie_secure: bool = True,
        cookie_domain: str = "",
    ):
        self.secret = secret
        self.cookie_name = cookie_name
        self.cookie_secure = cookie_secure
        self.cookie_domain = cookie_domain

    def _sign(self, data: str) -> str:
        if not self.secret:
            raise RuntimeError("session secret is not configured")
        return hmac.new(self.secret.encode(), data.encode(), hashlib.sha256).hexdigest()

    def encode(self, payload: dict[str, Any]) -> str:
        data = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        return f"{data}.{self._sign(data)}"

    def decode(self, raw: str | None) -> dict[str, Any] | None:
        if not raw or not self.secret:
            return None
        try:
            data, sig = raw.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(data)):
                return None
            return json.loads(base64.urlsafe_b64decode(data.encode()).decode())
        except Exception:
            return None

    def set_cookie(
        self, response: Response, payload: dict[str, Any], max_age: int | None = None
    ) -> None:
        kwargs: dict[str, Any] = {
            "httponly": True,
            "secure": self.cookie_secure,
            "samesite": "lax",
            "path": "/",
        }
        if self.cookie_domain:
            kwargs["domain"] = self.cookie_domain
        if max_age is not None:
            kwargs["max_age"] = max_age
        response.set_cookie(self.cookie_name, self.encode(payload), **kwargs)

    def delete_cookie(self, response: Response, path: str = "/") -> None:
        kwargs: dict[str, Any] = {"path": path}
        if self.cookie_domain:
            kwargs["domain"] = self.cookie_domain
        response.delete_cookie(self.cookie_name, **kwargs)
