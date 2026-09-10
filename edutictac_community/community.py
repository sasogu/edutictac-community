"""Núcleo de comunidad: favoritos, valoraciones y avisos por ítem.

Cada aplicación provee su base de datos y su forma de resolver la identidad del
visitante (anónima por cookie, OIDC o credencial pseudónima de alumnado). Este
módulo no centraliza datos: sólo reutiliza la lógica de datos de comunidad.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import re

from pydantic import BaseModel

from .db import connect

SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT NOT NULL,
    {key_column} TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (user_id, {key_column})
);
CREATE TABLE IF NOT EXISTS ratings (
    user_id TEXT NOT NULL,
    {key_column} TEXT NOT NULL,
    value INTEGER NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (user_id, {key_column})
);
CREATE TABLE IF NOT EXISTS reports (
    user_id TEXT NOT NULL,
    {key_column} TEXT NOT NULL,
    reported_at TEXT,
    PRIMARY KEY (user_id, {key_column})
);
CREATE TABLE IF NOT EXISTS rating_summary (
    {key_column} TEXT PRIMARY KEY,
    sum INTEGER NOT NULL DEFAULT 0,
    count INTEGER NOT NULL DEFAULT 0,
    avg REAL NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS broken_reports (
    {key_column} TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    admin_reported INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
"""


@dataclass
class Identity:
    uid: str
    admin: bool = False


IdentityResolver = Callable[[Request, Response], Identity | Awaitable[Identity]]
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


def _valid_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def _payload_key(payload: BaseModel | dict, key_field: str) -> str:
    if isinstance(payload, BaseModel):
        value = getattr(payload, key_field, None)
    else:
        value = payload.get(key_field)
    return _valid_key(str(value or ""))


def create_community_router(
    db_path: str,
    resolve_identity: IdentityResolver,
    *,
    rate_limited: RateLimitFn | None = None,
    key_field: str = "item_key",
    db_key_column: str = "item_key",
) -> APIRouter:
    """Construye un APIRouter con los endpoints de comunidad.

    `resolve_identity` recibe `(Request, Response)` y devuelve la identidad
    actual (uid + admin), pudiendo crear una identidad anónima y fijar su
    cookie en `response`. Si `rate_limited` se provee, se aplica antes de cada
    escritura; recibe una clave (p. ej. la IP) y devuelve True si hay que
    rechazar la petición con 429.
    """
    key_field = _valid_identifier(key_field)
    key_column = _valid_identifier(db_key_column)
    schema = SCHEMA_TEMPLATE.format(key_column=key_column)

    with connect(db_path) as conn:
        conn.executescript(schema)

    router = APIRouter()

    async def _identity_dependency(request: Request, response: Response) -> Identity:
        identity = resolve_identity(request, response)
        if inspect.isawaitable(identity):
            identity = await identity
        return identity

    def _guard(request: Request) -> None:
        if rate_limited is not None:
            key = request.client.host if request.client else "?"
            if rate_limited(key):
                raise HTTPException(status_code=429, detail="too many requests")

    @router.get("/preferences")
    async def preferences(identity: Identity = Depends(_identity_dependency)) -> dict:
        uid = identity.uid
        with connect(db_path) as conn:
            favorites = [r["item_key"] for r in conn.execute(
                f"SELECT {key_column} AS item_key FROM favorites WHERE user_id = ?", (uid,)
            )] if uid else []
            ratings = {r["item_key"]: r["value"] for r in conn.execute(
                f"SELECT {key_column} AS item_key, value FROM ratings WHERE user_id = ?", (uid,)
            )} if uid else {}
            reports = [r["item_key"] for r in conn.execute(
                f"SELECT {key_column} AS item_key FROM reports WHERE user_id = ?", (uid,)
            )] if uid else []
            rating_summary = {
                r["item_key"]: {"avg": r["avg"], "count": r["count"]}
                for r in conn.execute(f"SELECT {key_column} AS item_key, avg, count FROM rating_summary")
            }
            broken_reports = {
                r["item_key"]: {"count": r["count"], "admin_reported": bool(r["admin_reported"])}
                for r in conn.execute(
                    f"SELECT {key_column} AS item_key, count, admin_reported FROM broken_reports"
                )
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
    async def toggle_favorite(
        payload: dict,
        request: Request,
        identity: Identity = Depends(_identity_dependency),
    ) -> dict:
        _guard(request)
        item_key = _payload_key(payload, key_field)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")
        with connect(db_path) as conn:
            row = conn.execute(
                f"SELECT 1 FROM favorites WHERE user_id = ? AND {key_column} = ?",
                (identity.uid, item_key),
            ).fetchone()
            if row:
                conn.execute(
                    f"DELETE FROM favorites WHERE user_id = ? AND {key_column} = ?",
                    (identity.uid, item_key),
                )
                return {"favorite": False}
            conn.execute(
                f"INSERT INTO favorites (user_id, {key_column}, created_at) VALUES (?, ?, ?)",
                (identity.uid, item_key, _now()),
            )
            return {"favorite": True}

    @router.post("/ratings")
    async def set_rating(
        payload: dict,
        request: Request,
        identity: Identity = Depends(_identity_dependency),
    ) -> dict:
        _guard(request)
        item_key = _payload_key(payload, key_field)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")
        value = max(0, min(5, int(payload.get("value", 0))))

        with connect(db_path) as conn:
            current_row = conn.execute(
                f"SELECT value FROM ratings WHERE user_id = ? AND {key_column} = ?",
                (identity.uid, item_key),
            ).fetchone()
            current = current_row["value"] if current_row else 0
            next_val = 0 if current == value else value

            summary = conn.execute(
                f"SELECT sum, count FROM rating_summary WHERE {key_column} = ?", (item_key,)
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
                    f"INSERT INTO ratings (user_id, {key_column}, value, updated_at) VALUES (?, ?, ?, ?) "
                    f"ON CONFLICT(user_id, {key_column}) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (identity.uid, item_key, next_val, _now()),
                )
            else:
                conn.execute(
                    f"DELETE FROM ratings WHERE user_id = ? AND {key_column} = ?",
                    (identity.uid, item_key),
                )

            if c <= 0:
                conn.execute(f"DELETE FROM rating_summary WHERE {key_column} = ?", (item_key,))
                avg, count = 0.0, 0
            else:
                avg = round(s / c, 2)
                conn.execute(
                    f"INSERT INTO rating_summary ({key_column}, sum, count, avg, updated_at) VALUES (?, ?, ?, ?, ?) "
                    f"ON CONFLICT({key_column}) DO UPDATE SET sum=excluded.sum, count=excluded.count, avg=excluded.avg, updated_at=excluded.updated_at",
                    (item_key, s, c, avg, _now()),
                )
                count = c

        return {"value": next_val, "avg": avg, "count": count}

    @router.post("/reports")
    async def report_broken(
        payload: dict,
        request: Request,
        identity: Identity = Depends(_identity_dependency),
    ) -> dict:
        _guard(request)
        item_key = _payload_key(payload, key_field)
        if not identity.uid:
            raise HTTPException(status_code=401, detail="identity required")

        with connect(db_path) as conn:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO reports (user_id, {key_column}, reported_at) VALUES (?, ?, ?)",
                (identity.uid, item_key, _now()),
            )
            inserted_report = cur.rowcount > 0
            row = conn.execute(
                f"SELECT count, admin_reported FROM broken_reports WHERE {key_column} = ?", (item_key,)
            ).fetchone()
            count = (row["count"] if row else 0) + (1 if inserted_report else 0)
            admin_reported = bool(row["admin_reported"]) if row else False
            if identity.admin:
                admin_reported = True
            conn.execute(
                f"INSERT INTO broken_reports ({key_column}, count, admin_reported, updated_at) VALUES (?, ?, ?, ?) "
                f"ON CONFLICT({key_column}) DO UPDATE SET count=excluded.count, admin_reported=excluded.admin_reported, updated_at=excluded.updated_at",
                (item_key, count, 1 if admin_reported else 0, _now()),
            )
        return {"count": count, "admin_reported": admin_reported}

    @router.post("/admin/hide")
    async def admin_hide(
        payload: dict,
        request: Request,
        identity: Identity = Depends(_identity_dependency),
    ) -> dict:
        _guard(request)
        if not identity.admin:
            raise HTTPException(status_code=403, detail="admin required")
        item_key = _payload_key(payload, key_field)

        with connect(db_path) as conn:
            row = conn.execute(
                f"SELECT count FROM broken_reports WHERE {key_column} = ?", (item_key,)
            ).fetchone()
            count = row["count"] if row else 0
            conn.execute(
                f"INSERT INTO broken_reports ({key_column}, count, admin_reported, updated_at) VALUES (?, ?, 1, ?) "
                f"ON CONFLICT({key_column}) DO UPDATE SET admin_reported=1, updated_at=excluded.updated_at",
                (item_key, count, _now()),
            )
        return {key_field: item_key, "count": count, "admin_reported": True}

    return router
