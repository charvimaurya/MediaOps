import time
from datetime import datetime, timedelta, timezone

from agent.aht import AHTTimer, EscalationPolicy, EscalationPath, CONFIG


def test_normal_at_zero_failures_and_low_elapsed():
    policy_engine = EscalationPolicy()
    assert policy_engine.decide(attempts_failed=0, elapsed_seconds=0.1) == EscalationPath.NORMAL


def test_one_failure_gives_fast_even_at_zero_elapsed():
    policy_engine = EscalationPolicy()
    assert policy_engine.decide(attempts_failed=1, elapsed_seconds=0.0) == EscalationPath.FAST


def test_two_failures_give_failsafe():
    policy_engine = EscalationPolicy()
    assert policy_engine.decide(attempts_failed=2, elapsed_seconds=0.0) == EscalationPath.FAILSAFE
    assert policy_engine.decide(attempts_failed=3, elapsed_seconds=0.0) == EscalationPath.FAILSAFE


def test_high_elapsed_alone_gives_fast_or_failsafe_with_zero_failures():
    policy_engine = EscalationPolicy()
    assert policy_engine.decide(attempts_failed=0, elapsed_seconds=CONFIG["fast_elapsed_seconds"] + 0.1) == EscalationPath.FAST
    assert (
        policy_engine.decide(attempts_failed=0, elapsed_seconds=CONFIG["failsafe_elapsed_seconds"] + 0.1)
        == EscalationPath.FAILSAFE
    )


def test_more_severe_of_the_two_inputs_wins():
    policy_engine = EscalationPolicy()
    # 1 failure alone -> FAST, but elapsed alone is already FAILSAFE-level
    assert (
        policy_engine.decide(attempts_failed=1, elapsed_seconds=CONFIG["failsafe_elapsed_seconds"] + 0.1)
        == EscalationPath.FAILSAFE
    )
    # elapsed alone is NORMAL, but failure count alone says FAILSAFE
    assert policy_engine.decide(attempts_failed=2, elapsed_seconds=0.0) == EscalationPath.FAILSAFE
    # elapsed says FAST, failures say NORMAL -- FAST should win
    assert policy_engine.decide(attempts_failed=0, elapsed_seconds=CONFIG["fast_elapsed_seconds"]) == EscalationPath.FAST


def test_aht_timer_starts_from_created_at_not_from_start_call():
    created_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    timer = AHTTimer()
    timer.start(created_at)
    assert timer.elapsed() >= 5.0


def test_aht_timer_stop_freezes_elapsed():
    timer = AHTTimer()
    timer.start(datetime.now(timezone.utc))
    stopped = timer.stop()
    time.sleep(0.05)
    assert timer.elapsed() == stopped


def test_aht_timer_elapsed_is_zero_before_start():
    timer = AHTTimer()
    assert timer.elapsed() == 0.0
