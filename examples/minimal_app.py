"""App mínima que usa `edutictac_community` (identidad anónima por cookie).

Demuestra cómo una aplicación propia monta el router de comunidad y resuelve la
identidad del visitante (aquí anónima; en producción podría ser OIDC o la
credencial pseudónima de alumnado).

Ejecutar:
    COMMUNITY_SECRET=dev-secret COMMUNITY_COOKIE_SECURE=0 \
    uvicorn examples.minimal_app:app --host 127.0.0.1 --port 8010
"""
import os
import secrets

from fastapi import FastAPI, Request, Response

from edutictac_community.community import Identity, create_community_router
from edutictac_community.ratelimit import RateLimiter
from edutictac_community.session import SignedSession

DB_PATH = os.environ.get("COMMUNITY_DB", "/tmp/edutictac-community-example.db")
SESSION_SECRET = os.environ.get("COMMUNITY_SECRET", "dev-secret")
COOKIE_SECURE = os.environ.get("COMMUNITY_COOKIE_SECURE", "0") != "0"

session = SignedSession(SESSION_SECRET, "example_session", cookie_secure=COOKIE_SECURE)
rate_limit = RateLimiter(max_calls=60, window_seconds=60)

app = FastAPI(title="EduTicTac Community Example")


def resolve_identity(request: Request, response: Response) -> Identity:
    payload = session.decode(request.cookies.get(session.cookie_name))
    if payload and payload.get("uid"):
        return Identity(uid=payload["uid"], admin=bool(payload.get("admin")))
    uid = secrets.token_hex(16)
    session.set_cookie(response, {"uid": uid, "admin": False}, max_age=365 * 24 * 60 * 60)
    return Identity(uid=uid, admin=False)


app.include_router(
    create_community_router(DB_PATH, resolve_identity, rate_limited=rate_limit),
    prefix="/api/community",
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
