"""Núcleo de comunidad: favoritos, valoraciones y avisos por ítem.

Cada aplicación provee su base de datos y su forma de resolver la identidad del
visitante (anónima por cookie, OIDC o credencial pseudónima de alumnado). Este
módulo no centraliza datos: sólo reutiliza la lógica de datos de comunidad.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS ratings (
    user_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    value INTEGER NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS reports (
    user_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    reported_at TEXT,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS rating_summary (
    item_key TEXT PRIMARY KEY,
    sum INTEGER NOT NULL DEFAULT 0,
    count INTEGER NOT NULL DEFAULT 0,
    avg REAL NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS broken_reports (
    item_key TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    admin_reported INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
"""


@dataclass
class Identity:
    uid: str
    admin: bool = False


IdentityResolver = Callable[[Request, Response], Identity]
RateLimitFn = Callable[[str], bool]


class ItemKeyIn(BaseModel):
    item_key: str


class RatingIn(BaseModel):
    item_key: str
    value: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_key(item_key: str) -> str:
    key = (item_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="invalid item_key")
    return key


def create_community_router(
    db_path: str,
    resolve_identity: IdentityResolver,
    *,
    rate_limited: RateLimitFn | None = None,
) -> APIRouter:
    """Construye un APIRouter con los endpoints de comunidad.

    `resolve_identity` recibe `(Request, Response)` y devuelve la identidad
    actual (uid + admin), pudiendo crear una identidad anónima y fijar su
    cookie en `response`. Si `rate_limited` se provee, se aplica antes de cada
    escritura; recibe una clave (p. ej. la IP) y devuelve True si hay que
    rechazar la petición con 429.
    """
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)

    router = APIRouter()

    def _guard(request: Request) -> None:
        if rate_limited is not None:
            key = request.client.host if request.client else "?"
            if rate_limited(key):
                raise HTTPException(status_code=429, detail="too many requests")

    @router.get("/preferences")
    def preferences(request: Request, identity: Identity = Depends(resolve_identity)) -> dict:
        uid = identity.uid
        with connect(db_path) as conn:
            favorites = [r["item_key"] for r in conn.execute(
                "SELECT item_key FROM favorites WHERE user_id = ?", (uid,)
            )] if uid else []
            ratings = {r["item_key"]: r["value"] for r in conn.execute(
                "SELECT item_key, value FROM ratings WHERE user_id = ?", (uid,)
            )} if uid else {}
            reports = [r["item_key"] for r in conn.execute(
                "SELECT item_key FROM reports WHERE user_id = ?", (uid,)
            )] if uid else []
            rating_summary = {
                r["item_key"]: {"avg": r["avg"], "count": r["count"]}
                for r in conn.execute("SELECT item_key, avg, count FROM rating_summary")
            }
            broken_reports = {
                r["item_key"]: {"count": r["count"], "admin_reported": bool(r["admin_reported"])}
                for r in conn.execute("SELECT item_key, count, admin_reported FROM broken_reports")
            }
        return {
            "admin": identity.admin,
            "favorites": favorites,
            "ratings": ratings,
            "reports": reports,
            "rating_summary": rating_summary,
            "broken_reports": broken_reports,
        }

    @router.post("/favorites/toggle")
    def toggle_favorite(
        payload: ItemKeyIn,
        request: Request,
        identity: Identity = Depends(resolve_identity),
    ) -> dict:
        _guard(request)
        item_key = _valid_key(payload.item_key)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM favorites WHERE user_id = ? AND item_key = ?",
                (identity.uid, item_key),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM favorites WHERE user_id = ? AND item_key = ?",
                    (identity.uid, item_key),
                )
                return {"favorite": False}
            conn.execute(
                "INSERT INTO favorites (user_id, item_key, created_at) VALUES (?, ?, ?)",
                (identity.uid, item_key, _now()),
            )
            return {"favorite": True}

    @router.post("/ratings")
    def set_rating(
        payload: RatingIn,
        request: Request,
        identity: Identity = Depends(resolve_identity),
    ) -> dict:
        _guard(request)
        item_key = _valid_key(payload.item_key)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")
        value = max(0, min(5, payload.value))

        with connect(db_path) as conn:
            current_row = conn.execute(
                "SELECT value FROM ratings WHERE user_id = ? AND item_key = ?",
                (identity.uid, item_key),
            ).fetchone()
            current = current_row["value"] if current_row else 0
            next_val = 0 if current == value else value

            summary = conn.execute(
                "SELECT sum, count FROM rating_summary WHERE item_key = ?", (item_key,)
            ).fetchone()
            s = summary["sum"] if summary else 0
            c = summary["count"] if summary else 0

            if current > 0:
                s -= current
                c -= 1
            if next_val > 0:
                s += next_val
                c += 1
                conn.execute(
                    "INSERT INTO ratings (user_id, item_key, value, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, item_key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (identity.uid, item_key, next_val, _now()),
                )
            else:
                conn.execute(
                    "DELETE FROM ratings WHERE user_id = ? AND item_key = ?",
                    (identity.uid, item_key),
                )

            if c <= 0:
                conn.execute("DELETE FROM rating_summary WHERE item_key = ?", (item_key,))
                avg, count = 0.0, 0
            else:
                avg = round(s / c, 2)
                conn.execute(
                    "INSERT INTO rating_summary (item_key, sum, count, avg, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(item_key) DO UPDATE SET sum=excluded.sum, count=excluded.count, avg=excluded.avg, updated_at=excluded.updated_at",
                    (item_key, s, c, avg, _now()),
                )
                count = c

        return {"value": next_val, "avg": avg, "count": count}

    @router.post("/reports")
    def report_broken(
        payload: ItemKeyIn,
        request: Request,
        identity: Identity = Depends(resolve_identity),
    ) -> dict:
        _guard(request)
        item_key = _valid_key(payload.item_key)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")

        with connect(db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO reports (user_id, item_key, reported_at) VALUES (?, ?, ?)",
                (identity.uid, item_key, _now()),
            )
            inserted_report = cur.rowcount > 0
            row = conn.execute(
                "SELECT count, admin_reported FROM broken_reports WHERE item_key = ?", (item_key,)
            ).fetchone()
            count = (row["count"] if row else 0) + (1 if inserted_report else 0)
            admin_reported = bool(row["admin_reported"]) if row else False
            if identity.admin:
                admin_reported = True
            conn.execute(
                "INSERT INTO broken_reports (item_key, count, admin_reported, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(item_key) DO UPDATE SET count=excluded.count, admin_reported=excluded.admin_reported, updated_at=excluded.updated_at",
                (item_key, count, 1 if admin_reported else 0, _now()),
            )
        return {"count": count, "admin_reported": admin_reported}

    @router.post("/admin/hide")
    def admin_hide(
        payload: ItemKeyIn,
        request: Request,
        identity: Identity = Depends(resolve_identity),
    ) -> dict:
        _guard(request)
        if not identity.admin:
            raise HTTPException(status_code=403, detail="admin required")
        item_key = _valid_key(payload.item_key)

        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT count FROM broken_reports WHERE item_key = ?", (item_key,)
            ).fetchone()
            count = row["count"] if row else 0
            conn.execute(
                "INSERT INTO broken_reports (item_key, count, admin_reported, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(item_key) DO UPDATE SET admin_reported=1, updated_at=excluded.updated_at",
                (item_key, count, _now()),
            )
        return {"item_key": item_key, "count": count, "admin_reported": True}

    return router
