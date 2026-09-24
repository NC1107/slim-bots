import pytest

from slimbots.limits import Cooldown, Quota, RateLimiter, ValidationError, require_int, require_len, require_range


def test_cooldown_allows_the_first_use():
    clock = iter([0.0])
    cooldown = Cooldown(10, clock=lambda: next(clock))
    assert cooldown.check("u1") is None


def test_cooldown_refuses_a_second_use_within_the_window():
    times = iter([0.0, 1.0])
    cooldown = Cooldown(10, clock=lambda: next(times))
    cooldown.check("u1")
    message = cooldown.check("u1")
    assert message is not None
    assert "9s" in message


def test_cooldown_allows_again_after_the_window():
    times = iter([0.0, 20.0])
    cooldown = Cooldown(10, clock=lambda: next(times))
    cooldown.check("u1")
    assert cooldown.check("u1") is None


def test_cooldown_is_per_user():
    times = iter([0.0, 0.0])
    cooldown = Cooldown(10, clock=lambda: next(times))
    cooldown.check("u1")
    assert cooldown.check("u2") is None


def test_cooldown_reset_clears_state():
    times = iter([0.0, 1.0])
    cooldown = Cooldown(10, clock=lambda: next(times))
    cooldown.check("u1")
    cooldown.reset("u1")
    assert cooldown.check("u1") is None


def test_cooldown_uses_custom_formatter():
    times = iter([0.0, 1.0])
    cooldown = Cooldown(10, clock=lambda: next(times), format_remaining=lambda s: f"{s:.0f} seconds")
    cooldown.check("u1")
    assert "9 seconds" in cooldown.check("u1")


def test_rate_limiter_allows_a_burst_up_to_the_limit():
    limiter = RateLimiter(3, 60, clock=lambda: 0.0)
    assert limiter.check("u1") is None
    assert limiter.check("u1") is None
    assert limiter.check("u1") is None


def test_rate_limiter_refuses_past_the_limit():
    limiter = RateLimiter(2, 60, clock=lambda: 0.0)
    limiter.check("u1")
    limiter.check("u1")
    assert limiter.check("u1") is not None


def test_rate_limiter_forgets_hits_outside_the_window():
    times = iter([0.0, 0.0, 100.0])
    limiter = RateLimiter(2, 60, clock=lambda: next(times))
    limiter.check("u1")
    limiter.check("u1")
    assert limiter.check("u1") is None


def test_quota_check_and_use():
    quota = Quota(2)
    assert quota.check("u1") is None
    quota.use("u1")
    assert quota.check("u1") is None
    quota.use("u1")
    assert quota.check("u1") is not None


def test_quota_release_frees_a_slot():
    quota = Quota(1)
    quota.use("u1")
    assert quota.check("u1") is not None
    quota.release("u1")
    assert quota.check("u1") is None


def test_quota_release_never_goes_negative():
    quota = Quota(1)
    quota.release("u1")
    quota.release("u1")
    quota.use("u1")
    assert quota.check("u1") is not None


def test_quota_set_count_reconciles_after_restart():
    quota = Quota(2)
    quota.set_count("u1", 2)
    assert quota.check("u1") is not None


def test_require_len_rejects_too_long():
    with pytest.raises(ValidationError):
        require_len("x" * 10, max_len=5)


def test_require_len_rejects_too_short():
    with pytest.raises(ValidationError):
        require_len("x", min_len=2, max_len=5)


def test_require_len_accepts_and_returns_the_value():
    assert require_len("hi", max_len=5) == "hi"


def test_require_range_rejects_out_of_bounds():
    with pytest.raises(ValidationError):
        require_range(100, max_value=10)
    with pytest.raises(ValidationError):
        require_range(-1, min_value=0)


def test_require_int_rejects_non_numeric():
    with pytest.raises(ValidationError):
        require_int("nope")


def test_require_int_parses_and_bounds():
    assert require_int("5", min_value=1, max_value=10) == 5
    with pytest.raises(ValidationError):
        require_int("50", max_value=10)
