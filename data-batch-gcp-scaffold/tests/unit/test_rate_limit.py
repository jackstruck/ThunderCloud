from worker.rate_limit import FixedWindowRateLimiter


def test_limit_and_window_expiry():
    now = [0.0]
    limiter = FixedWindowRateLimiter(lambda: now[0])
    assert limiter.allow(("user", "read"), 2, 60)
    assert limiter.allow(("user", "read"), 2, 60)
    assert not limiter.allow(("user", "read"), 2, 60)
    now[0] = 61
    assert limiter.allow(("user", "read"), 2, 60)


def test_principals_and_operations_are_independent():
    limiter = FixedWindowRateLimiter(lambda: 0)
    assert limiter.allow(("a", "create"), 1)
    assert not limiter.allow(("a", "create"), 1)
    assert limiter.allow(("a", "read"), 1)
    assert limiter.allow(("b", "create"), 1)
