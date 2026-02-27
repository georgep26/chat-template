from datetime import UTC, datetime, timedelta

from cost_guard.logic import (
    choose_culprit,
    evaluate_budget_signal,
    update_hard_breach_streak,
    warning_in_cooldown,
)


def test_evaluate_budget_signal_with_forecast():
    now = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    signal = evaluate_budget_signal(
        estimated_mtd_usd=10.0,
        budget_amount_usd=20.0,
        warning_percent=80.0,
        hard_percent=100.0,
        now=now,
    )
    assert signal.warning_threshold_usd == 16.0
    assert signal.hard_threshold_usd == 20.0
    # Mid-month projection should exceed MTD value.
    assert signal.forecast_month_end_usd > signal.estimated_mtd_usd


def test_hard_breach_streak_transitions():
    assert update_hard_breach_streak(0, hard_breached=True) == 1
    assert update_hard_breach_streak(1, hard_breached=True) == 2
    assert update_hard_breach_streak(2, hard_breached=False) == 0


def test_warning_cooldown_window():
    now = datetime.now(tz=UTC)
    assert warning_in_cooldown(now - timedelta(minutes=5), now, cooldown_minutes=30)
    assert not warning_in_cooldown(now - timedelta(minutes=45), now, cooldown_minutes=30)


def test_choose_culprit_uses_highest_interval():
    resources = [
        {"id": "a", "interval_usd": 0.4},
        {"id": "b", "interval_usd": 1.2},
        {"id": "c", "interval_usd": 0.2},
    ]
    culprit = choose_culprit(resources)
    assert culprit is not None
    assert culprit["id"] == "b"

