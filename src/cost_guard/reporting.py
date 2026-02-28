"""Reporting utilities for cost guard JSON, HTML, and PNG outputs."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReportPaths:
    base_dir: Path
    dated_dir: Path
    latest_dir: Path


def ensure_report_directories(base_dir: Path, day_key: str) -> ReportPaths:
    dated_dir = base_dir / day_key
    latest_dir = base_dir / "latest"
    base_dir.mkdir(parents=True, exist_ok=True)
    dated_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    return ReportPaths(base_dir=base_dir, dated_dir=dated_dir, latest_dir=latest_dir)


def write_json_file(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _render_pricing_metadata_section(pricing_metadata: dict[str, Any]) -> str:
    """Render a table showing price source (pricing_api or default_stored_price) and timestamp per rate."""
    if not pricing_metadata:
        return ""
    table_rows = "".join(
        f"<tr><td>{key}</td><td>{m.get('source', '')}</td><td>{m.get('at', '')}</td></tr>"
        for key, m in sorted(pricing_metadata.items())
        if isinstance(m, dict)
    )
    return f"""
  <h2>Price source (per rate)</h2>
  <p>Values are from <code>pricing_api</code> (with pull timestamp) or <code>default_stored_price</code> (with last update date).</p>
  <table>
    <thead><tr><th>Rate key</th><th>Source</th><th>At</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
"""


def render_html_report(
    summary: dict[str, Any],
    resources: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> str:
    rows = []
    for resource in resources:
        rows.append(
            "<tr>"
            f"<td>{resource.get('id')}</td>"
            f"<td>{resource.get('type')}</td>"
            f"<td>{resource.get('identifier')}</td>"
            f"<td>{float(resource.get('estimated_mtd_usd', 0.0)):.4f}</td>"
            f"<td>{float(resource.get('interval_usd', 0.0)):.4f}</td>"
            f"<td>{resource.get('breach_state', 'none')}</td>"
            f"<td>{resource.get('last_action', 'none')}</td>"
            "</tr>"
        )

    event_rows = []
    for item in events:
        event_rows.append(
            "<tr>"
            f"<td>{item.get('timestamp', '')}</td>"
            f"<td>{item.get('severity', '')}</td>"
            f"<td>{item.get('resource_id', '')}</td>"
            f"<td>{item.get('message', '')}</td>"
            "</tr>"
        )

    budget = summary.get("budget", {})
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cost Guard Report</title>
  <style>
    body {{
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      margin: 2rem;
      background: #f7f7f5;
      color: #1f2937;
    }}
    h1, h2 {{
      margin-bottom: 0.5rem;
    }}
    .meta {{
      margin-bottom: 1rem;
      padding: 0.75rem;
      background: #ffffff;
      border-left: 4px solid #0f766e;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin-bottom: 1.25rem;
      background: #ffffff;
    }}
    th, td {{
      border: 1px solid #e5e7eb;
      padding: 0.5rem;
      text-align: left;
      font-size: 0.92rem;
    }}
    th {{
      background: #ecfeff;
    }}
    code {{
      background: #eef2ff;
      padding: 0.1rem 0.3rem;
      border-radius: 4px;
    }}
  </style>
</head>
<body>
  <h1>Cost Guard Report</h1>
  <div class="meta">
    <div><strong>Generated:</strong> {summary.get("generated_at", "")}</div>
    <div><strong>Environment:</strong> {summary.get("environment", "")}</div>
    <div><strong>Budget:</strong> ${float(budget.get("limit_usd", 0.0)):.2f}</div>
    <div><strong>Estimated MTD:</strong> ${float(budget.get("estimated_mtd_usd", 0.0)):.4f}</div>
    <div><strong>Forecast Month-End:</strong> ${float(budget.get("forecast_month_end_usd", 0.0)):.4f}</div>
    <div><strong>Signal:</strong> ${float(summary.get("signal_usd", 0.0)):.4f}</div>
    {f'<div><strong>Pricing state updated:</strong> {summary.get("pricing_state_updated_at", "—")}</div>' if summary.get("pricing_state_updated_at") else ""}
  </div>

  {_render_pricing_metadata_section(summary.get("pricing_metadata") or {})}

  <h2>Resources</h2>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Type</th>
        <th>Identifier</th>
        <th>Estimated MTD USD</th>
        <th>Interval USD</th>
        <th>Breach State</th>
        <th>Last Action</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>

  <h2>Threshold Events</h2>
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Severity</th>
        <th>Resource</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody>
      {''.join(event_rows) if event_rows else '<tr><td colspan="4">No events for selected date</td></tr>'}
    </tbody>
  </table>
</body>
</html>
"""
    return html


def _load_historical_summary_points(base_dir: Path, current_day_key: str, current_value: float) -> tuple[list[str], list[float]]:
    points: dict[str, float] = {}
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name == "latest":
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue
        summary_path = child / "summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            points[child.name] = float(payload.get("budget", {}).get("estimated_mtd_usd", 0.0))
        except Exception:
            continue

    points[current_day_key] = current_value
    labels = sorted(points.keys())[-30:]
    values = [points[label] for label in labels]
    return labels, values


def _generate_png_files(
    summary: dict[str, Any],
    resources: list[dict[str, Any]],
    report_paths: ReportPaths,
    day_key: str,
) -> None:
    try:
        import plotly.graph_objects as go
    except Exception as exc:  # pragma: no cover - dependency/runtime-specific
        raise RuntimeError("plotly is required to generate PNG reports") from exc

    trend_labels, trend_values = _load_historical_summary_points(
        base_dir=report_paths.base_dir,
        current_day_key=day_key,
        current_value=float(summary.get("budget", {}).get("estimated_mtd_usd", 0.0)),
    )
    trend_path = report_paths.dated_dir / "cost_trend_30d.png"
    trend_fig = go.Figure(
        data=[
            go.Scatter(
                x=trend_labels,
                y=trend_values,
                mode="lines+markers",
                line={"color": "#0f766e", "width": 3},
                marker={"size": 7},
                name="Estimated MTD USD",
            )
        ]
    )
    trend_fig.update_layout(
        title="Cost Guard Trend (Last 30 Days)",
        xaxis_title="Date",
        yaxis_title="Estimated MTD USD",
        template="plotly_white",
        width=1100,
        height=520,
        margin={"l": 55, "r": 30, "t": 70, "b": 90},
    )
    trend_fig.update_xaxes(tickangle=45)

    top_resources = sorted(
        resources,
        key=lambda item: float(item.get("estimated_mtd_usd", 0.0)),
        reverse=True,
    )[:10]
    labels = [item.get("id", "unknown") for item in top_resources]
    values = [float(item.get("estimated_mtd_usd", 0.0)) for item in top_resources]
    breakdown_path = report_paths.dated_dir / "resource_breakdown_top10.png"
    breakdown_fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=values,
                marker={"color": "#0ea5e9"},
                name="Estimated MTD USD",
            )
        ]
    )
    breakdown_fig.update_layout(
        title="Top 10 Resources by Estimated MTD Cost",
        xaxis_title="Resource",
        yaxis_title="Estimated MTD USD",
        template="plotly_white",
        width=1100,
        height=560,
        margin={"l": 55, "r": 30, "t": 70, "b": 110},
    )
    breakdown_fig.update_xaxes(tickangle=35)

    try:
        trend_fig.write_image(str(trend_path))
        breakdown_fig.write_image(str(breakdown_path))
    except Exception as exc:  # pragma: no cover - dependency/runtime-specific
        raise RuntimeError(
            "Plotly static image export failed. Install compatible plotly+kaleido dependencies."
        ) from exc


def update_latest_symlink_copy(report_paths: ReportPaths) -> None:
    if report_paths.latest_dir.exists():
        shutil.rmtree(report_paths.latest_dir)
    shutil.copytree(report_paths.dated_dir, report_paths.latest_dir)


def update_index_html(base_dir: Path) -> None:
    rows: list[str] = []
    for child in sorted(base_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        if child.name == "latest":
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue
        rows.append(
            f"<li><a href=\"./{child.name}/report.html\">{child.name} report</a> "
            f"(<a href=\"./{child.name}/summary.json\">summary.json</a>)</li>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cost Reports Index</title>
</head>
<body>
  <h1>Cost Reports</h1>
  <p><a href="./latest/report.html">Open Latest Report</a></p>
  <ul>
    {''.join(rows) if rows else '<li>No dated reports yet</li>'}
  </ul>
</body>
</html>
"""
    (base_dir / "index.html").write_text(html, encoding="utf-8")


def prune_old_report_dirs(base_dir: Path, retention_days: int) -> list[str]:
    removed: list[str] = []
    cutoff = date.today().toordinal() - retention_days
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name == "latest":
            continue
        try:
            day = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day.toordinal() < cutoff:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def write_report_bundle(
    base_dir: Path,
    day_key: str,
    summary: dict[str, Any],
    resources: list[dict[str, Any]],
    events: list[dict[str, Any]],
    retention_days: int,
    formats: list[str] | None = None,
) -> ReportPaths:
    report_paths = ensure_report_directories(base_dir=base_dir, day_key=day_key)
    formats = formats or ["json", "html", "png"]

    summary_path = report_paths.dated_dir / "summary.json"
    resources_path = report_paths.dated_dir / "resource_breakdown.json"
    events_path = report_paths.dated_dir / "threshold_events.json"

    write_json_file(summary_path, summary)
    write_json_file(resources_path, resources)
    write_json_file(events_path, events)

    if "html" in formats:
        html = render_html_report(summary=summary, resources=resources, events=events)
        (report_paths.dated_dir / "report.html").write_text(html, encoding="utf-8")

    if "png" in formats:
        _generate_png_files(summary=summary, resources=resources, report_paths=report_paths, day_key=day_key)

    update_latest_symlink_copy(report_paths)
    prune_old_report_dirs(base_dir=base_dir, retention_days=retention_days)
    update_index_html(base_dir=base_dir)
    return report_paths
