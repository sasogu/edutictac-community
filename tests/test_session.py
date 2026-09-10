import pytest

from edutictac_community.session import SignedSession


def test_roundtrip():
    s = SignedSession("secret", "c", cookie_secure=False)
    token = s.encode({"uid": "abc", "admin": False})
    assert s.decode(token) == {"uid": "abc", "admin": False}


def test_tampered_token_is_rejected():
    s = SignedSession("secret", "c", cookie_secure=False)
    token = s.encode({"uid": "abc"})
    data, sig = token.split(".", 1)
    forged = data + "x"
    assert s.decode(f"{forged}.{sig}") is None


def test_wrong_secret_is_rejected():
    s = SignedSession("secret", "c", cookie_secure=False)
    token = s.encode({"uid": "abc"})
    other = SignedSession("other", "c", cookie_secure=False)
    assert other.decode(token) is None


def test_missing_secret_raises_on_encode():
    s = SignedSession("", "c", cookie_secure=False)
    with pytest.raises(RuntimeError):
        s.encode({"uid": "abc"})
