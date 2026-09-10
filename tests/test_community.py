import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient

from edutictac_community.db import connect
from edutictac_community.migrations import current_version
from edutictac_community.community import Identity, create_community_router


def make_app(db_path, resolver):
    app = FastAPI()
    app.include_router(create_community_router(db_path, resolver), prefix="/api/community")
    return app


def make_game_key_app(db_path, resolver):
    app = FastAPI()
    app.include_router(
        create_community_router(
            db_path,
            resolver,
            key_field="game_key",
            db_key_column="game_key",
            admin_hide_path="/admin/resources/hide",
        ),
        prefix="/api/community",
    )
    return app


def anon_resolver(uid):
    def _resolver(request: Request, response: Response):
        return Identity(uid=uid, admin=False)

    return _resolver


def admin_resolver(uid="admin-1"):
    def _resolver(request: Request, response: Response):
        return Identity(uid=uid, admin=True)

    return _resolver


def async_anon_resolver(uid):
    async def _resolver(request: Request, response: Response):
        return Identity(uid=uid, admin=False)

    return _resolver


async def client_for(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.anyio
async def test_preferences_empty(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, anon_resolver("u1"))) as client:
        r = await client.get("/api/community/preferences")
    assert r.status_code == 200
    body = r.json()
    assert body["favorites"] == []
    assert body["ratings"] == {}
    assert body["reports"] == []
    assert body["admin"] is False


@pytest.mark.anyio
async def test_accepts_async_identity_resolver(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, async_anon_resolver("u1"))) as client:
        r = await client.get("/api/community/preferences")
    assert r.status_code == 200
    assert r.json()["admin"] is False


@pytest.mark.anyio
async def test_toggle_favorite(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, anon_resolver("u1"))) as client:
        assert (await client.post("/api/community/favorites/toggle", json={"item_key": "k"})).json()["favorite"] is True
        assert (await client.post("/api/community/favorites/toggle", json={"item_key": "k"})).json()["favorite"] is False
        prefs = (await client.get("/api/community/preferences")).json()
    assert prefs["favorites"] == []


@pytest.mark.anyio
async def test_rating_aggregation(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, anon_resolver("u1"))) as a:
        r = (await a.post("/api/community/ratings", json={"item_key": "k", "value": 5})).json()
        assert (r["avg"], r["count"]) == (5.0, 1)

        r = (await a.post("/api/community/ratings", json={"item_key": "k", "value": 3})).json()
        assert (r["avg"], r["count"]) == (3.0, 1)

        async with await client_for(make_app(db, anon_resolver("u2"))) as b:
            r = (await b.post("/api/community/ratings", json={"item_key": "k", "value": 4})).json()
            assert (r["avg"], r["count"]) == (3.5, 2)

        summary = (await a.get("/api/community/preferences")).json()["rating_summary"]
    assert summary["k"] == {"avg": 3.5, "count": 2}


@pytest.mark.anyio
async def test_report_and_admin_hide(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, anon_resolver("u1"))) as anon:
        assert (await anon.post("/api/community/reports", json={"item_key": "k"})).json()["count"] == 1

        async with await client_for(make_app(db, admin_resolver())) as admin:
            assert (await admin.post("/api/community/admin/hide", json={"item_key": "k"})).json()["admin_reported"] is True

        broken = (await anon.get("/api/community/preferences")).json()["broken_reports"]
    assert broken["k"] == {"count": 1, "admin_reported": True}


@pytest.mark.anyio
async def test_admin_hide_forbidden_for_anon(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_app(db, anon_resolver("u1"))) as anon:
        r = await anon.post("/api/community/admin/hide", json={"item_key": "k"})
    assert r.status_code == 403


@pytest.mark.anyio
async def test_game_key_adapter_uses_existing_column_names(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_game_key_app(db, anon_resolver("u1"))) as client:
        assert (await client.post("/api/community/favorites/toggle", json={"game_key": "g"})).json()["favorite"] is True
        rating = (await client.post("/api/community/ratings", json={"game_key": "g", "value": 5})).json()
        report = (await client.post("/api/community/reports", json={"game_key": "g"})).json()
        prefs = (await client.get("/api/community/preferences")).json()

    assert rating == {"value": 5, "avg": 5.0, "count": 1}
    assert report == {"count": 1, "admin_reported": False}
    assert prefs["favorites"] == ["g"]
    assert prefs["ratings"] == {"g": 5}
    assert prefs["reports"] == ["g"]
    assert prefs["rating_summary"] == {"g": {"avg": 5.0, "count": 1}}
    assert prefs["broken_reports"] == {"g": {"count": 1, "admin_reported": False}}


@pytest.mark.anyio
async def test_game_key_adapter_preserves_admin_hide_path(tmp_path):
    db = str(tmp_path / "c.db")
    async with await client_for(make_game_key_app(db, admin_resolver())) as client:
        response = await client.post("/api/community/admin/resources/hide", json={"game_key": "g"})

    assert response.status_code == 200
    assert response.json() == {"game_key": "g", "count": 0, "admin_reported": True}


def test_rejects_unsafe_key_column(tmp_path):
    with pytest.raises(ValueError):
        create_community_router(
            str(tmp_path / "c.db"),
            anon_resolver("u1"),
            key_field="game_key",
            db_key_column="game_key; DROP TABLE ratings",
        )


def test_community_schema_has_migration_namespace(tmp_path):
    db = str(tmp_path / "c.db")
    make_game_key_app(db, anon_resolver("u1"))

    with connect(db) as conn:
        assert current_version(conn, "community:game_key") == 1
