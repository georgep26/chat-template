"""Pure decision logic for thresholding and culprit selection."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class BudgetSignal:
    signal_usd: float
    estimated_mtd_usd: float
    forecast_month_end_usd: float
    warning_threshold_usd: float
    hard_threshold_usd: float
    warning_breached: bool
    hard_breached: bool


def month_start(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, tzinfo=UTC)


def forecast_month_end(estimated_mtd_usd: float, now: datetime) -> float:
    start = month_start(now)
    elapsed_days = (now - start).total_seconds() / 86400.0
    # Avoid unstable spikes at the very beginning of the month.
    elapsed_days = max(elapsed_days, 1.0 / 288.0)
    days_in_month = monthrange(now.year, now.month)[1]
    return estimated_mtd_usd / elapsed_days * days_in_month


def evaluate_budget_signal(
    estimated_mtd_usd: float,
    budget_amount_usd: float,
    warning_percent: float,
    hard_percent: float,
    now: datetime,
) -> BudgetSignal:
    warning_threshold = budget_amount_usd * (warning_percent / 100.0)
    hard_threshold = budget_amount_usd * (hard_percent / 100.0)
    forecast = forecast_month_end(estimated_mtd_usd, now)
    signal = max(estimated_mtd_usd, forecast)
    return BudgetSignal(
        signal_usd=signal,
        estimated_mtd_usd=estimated_mtd_usd,
        forecast_month_end_usd=forecast,
        warning_threshold_usd=warning_threshold,
        hard_threshold_usd=hard_threshold,
        warning_breached=signal >= warning_threshold,
        hard_breached=signal >= hard_threshold,
    )


def choose_culprit(resources: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not resources:
        return None
    candidates = sorted(
        resources,
        key=lambda item: float(item.get("interval_usd", 0.0)),
        reverse=True,
    )
    culprit = candidates[0]
    if float(culprit.get("interval_usd", 0.0)) <= 0:
        return None
    return culprit


def update_hard_breach_streak(previous: int, hard_breached: bool) -> int:
    if hard_breached:
        return previous + 1
    return 0


def warning_in_cooldown(last_warning_at: datetime | None, now: datetime, cooldown_minutes: int) -> bool:
    if not last_warning_at:
        return False
    delta = (now - last_warning_at).total_seconds() / 60.0
    return delta < cooldown_minutes

