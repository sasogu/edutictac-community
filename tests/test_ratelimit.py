from edutictac_community.ratelimit import RateLimiter


def test_allows_up_to_limit():
    limiter = RateLimiter(max_calls=3, window_seconds=60)
    assert not limiter("ip-a")
    assert not limiter("ip-a")
    assert not limiter("ip-a")
    assert limiter("ip-a")


def test_keys_are_independent():
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    assert not limiter("ip-a")
    assert not limiter("ip-b")
    assert limiter("ip-a")
