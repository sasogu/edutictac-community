import pytest

from edutictac_community.oidc import OIDCClient


def make_client(**kwargs):
    defaults = dict(
        client_id="cid",
        client_secret="cs",
        issuer="https://id.example.test/application/o/app/",
        redirect_uri="https://app.example.test/cb",
        session_secret="s3cret",
    )
    defaults.update(kwargs)
    return OIDCClient(**defaults)


def test_enabled():
    assert make_client().enabled() is True
    assert make_client(client_id="").enabled() is False
    assert make_client(session_secret="").enabled() is False


def test_is_admin():
    c = make_client(admin_subjects={"sub-1"}, admin_emails={"A@B.C"})
    assert c.is_admin("sub-1") is True
    assert c.is_admin("sub-2") is False
    assert c.is_admin("sub-2", "a@b.c") is True


def test_state_cookie_roundtrip():
    c = make_client()
    token = c._encode({"state": "s", "code_verifier": "v"})
    assert c._decode(token) == {"state": "s", "code_verifier": "v"}
    assert c._decode(token + "x") is None


def test_jwks_cache_does_not_shadow_method(monkeypatch):
    c = make_client()
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": []}

    def fake_get(url, timeout, headers):
        calls.append({"url": url, "timeout": timeout, "headers": headers})
        return FakeResponse()

    monkeypatch.setattr("edutictac_community.oidc.httpx.get", fake_get)
    monkeypatch.setattr(
        "edutictac_community.oidc.JsonWebKey.import_key_set",
        lambda payload: {"imported": payload},
    )

    first = c._jwks({"jwks_uri": "https://id.example.test/jwks"})
    second = c._jwks({"jwks_uri": "https://id.example.test/jwks"})

    assert first == second == {"imported": {"keys": []}}
    assert len(calls) == 1


def test_callback_rejects_missing_state():
    c = make_client()
    with pytest.raises(ValueError):
        c.handle_callback("state", "code", None)
