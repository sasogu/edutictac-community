from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from edutictac_community.community import Identity, create_community_router


def make_app(db_path, resolver):
    app = FastAPI()
    app.include_router(create_community_router(db_path, resolver), prefix="/api/community")
    return TestClient(app)


def anon_resolver(uid):
    def _resolver(request: Request, response: Response):
        return Identity(uid=uid, admin=False)
    return _resolver


def admin_resolver(uid="admin-1"):
    def _resolver(request: Request, response: Response):
        return Identity(uid=uid, admin=True)
    return _resolver


def test_preferences_empty(tmp_path):
    db = str(tmp_path / "c.db")
    client = make_app(db, anon_resolver("u1"))
    r = client.get("/api/community/preferences")
    assert r.status_code == 200
    body = r.json()
    assert body["favorites"] == []
    assert body["ratings"] == {}
    assert body["reports"] == []
    assert body["admin"] is False


def test_toggle_favorite(tmp_path):
    db = str(tmp_path / "c.db")
    client = make_app(db, anon_resolver("u1"))
    assert client.post("/api/community/favorites/toggle", json={"item_key": "k"}).json()["favorite"] is True
    assert client.post("/api/community/favorites/toggle", json={"item_key": "k"}).json()["favorite"] is False
    prefs = client.get("/api/community/preferences").json()
    assert prefs["favorites"] == []


def test_rating_aggregation(tmp_path):
    db = str(tmp_path / "c.db")
    a = make_app(db, anon_resolver("u1"))
    b = make_app(db, anon_resolver("u2"))

    r = a.post("/api/community/ratings", json={"item_key": "k", "value": 5}).json()
    assert (r["avg"], r["count"]) == (5.0, 1)

    r = a.post("/api/community/ratings", json={"item_key": "k", "value": 3}).json()
    assert (r["avg"], r["count"]) == (3.0, 1)

    r = b.post("/api/community/ratings", json={"item_key": "k", "value": 4}).json()
    assert (r["avg"], r["count"]) == (3.5, 2)

    summary = a.get("/api/community/preferences").json()["rating_summary"]
    assert summary["k"] == {"avg": 3.5, "count": 2}


def test_report_and_admin_hide(tmp_path):
    db = str(tmp_path / "c.db")
    anon = make_app(db, anon_resolver("u1"))
    admin = make_app(db, admin_resolver())

    assert anon.post("/api/community/reports", json={"item_key": "k"}).json()["count"] == 1
    assert admin.post("/api/community/admin/hide", json={"item_key": "k"}).json()["admin_reported"] is True

    broken = anon.get("/api/community/preferences").json()["broken_reports"]
    assert broken["k"] == {"count": 1, "admin_reported": True}


def test_admin_hide_forbidden_for_anon(tmp_path):
    db = str(tmp_path / "c.db")
    anon = make_app(db, anon_resolver("u1"))
    r = anon.post("/api/community/admin/hide", json={"item_key": "k"})
    assert r.status_code == 403
