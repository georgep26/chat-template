from pathlib import Path

import pytest

from cost_guard.reporting import prune_old_report_dirs, write_report_bundle


def test_write_report_bundle_generates_json_html(tmp_path: Path):
    summary = {
        "generated_at": "2026-02-25T00:00:00Z",
        "environment": "dev",
        "signal_usd": 12.5,
        "budget": {
            "limit_usd": 20.0,
            "warning_percent": 80,
            "hard_percent": 100,
            "estimated_mtd_usd": 8.0,
            "forecast_month_end_usd": 12.5,
        },
    }
    resources = [
        {
            "id": "rag_lambda",
            "type": "lambda",
            "identifier": "chat-template-dev-rag-chat",
            "estimated_mtd_usd": 5.0,
            "interval_usd": 0.5,
            "forecast_month_end_usd": 8.2,
            "breach_state": "none",
            "last_action": "none",
            "last_action_at": None,
        }
    ]
    events = []

    write_report_bundle(
        base_dir=tmp_path,
        day_key="2026-02-25",
        summary=summary,
        resources=resources,
        events=events,
        retention_days=90,
        formats=["json", "html"],
    )

    day_dir = tmp_path / "2026-02-25"
    assert (day_dir / "summary.json").exists()
    assert (day_dir / "resource_breakdown.json").exists()
    assert (day_dir / "threshold_events.json").exists()
    assert (day_dir / "report.html").exists()
    assert (tmp_path / "latest" / "summary.json").exists()
    assert (tmp_path / "index.html").exists()


def test_prune_old_report_dirs(tmp_path: Path):
    (tmp_path / "2020-01-01").mkdir(parents=True)
    (tmp_path / "latest").mkdir(parents=True)
    removed = prune_old_report_dirs(base_dir=tmp_path, retention_days=90)
    assert "2020-01-01" in removed
    assert (tmp_path / "latest").exists()


def test_write_report_bundle_generates_png_when_plotly_available(tmp_path: Path):
    summary = {
        "generated_at": "2026-02-25T00:00:00Z",
        "environment": "dev",
        "signal_usd": 12.5,
        "budget": {
            "limit_usd": 20.0,
            "warning_percent": 80,
            "hard_percent": 100,
            "estimated_mtd_usd": 8.0,
            "forecast_month_end_usd": 12.5,
        },
    }
    resources = [
        {
            "id": "rag_lambda",
            "type": "lambda",
            "identifier": "chat-template-dev-rag-chat",
            "estimated_mtd_usd": 5.0,
            "interval_usd": 0.5,
            "forecast_month_end_usd": 8.2,
            "breach_state": "none",
            "last_action": "none",
            "last_action_at": None,
        }
    ]

    try:
        import plotly  # noqa: F401
    except Exception:
        pytest.skip("plotly not available")

    write_report_bundle(
        base_dir=tmp_path,
        day_key="2026-02-25",
        summary=summary,
        resources=resources,
        events=[],
        retention_days=90,
        formats=["json", "html", "png"],
    )
    day_dir = tmp_path / "2026-02-25"
    assert (day_dir / "cost_trend_30d.png").exists()
    assert (day_dir / "resource_breakdown_top10.png").exists()
