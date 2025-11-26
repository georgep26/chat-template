# evals/outputs.py

import json
import csv
from pathlib import Path
from collections import defaultdict
from .stats_utils import aggregate_metric


def build_aggregate_summary(per_sample_results):
    """
    per_sample_results: list of dicts:
      { "id", "metric", "score", "extra" }
    """
    scores_by_metric = defaultdict(list)
    for r in per_sample_results:
        scores_by_metric[r["metric"]].append(r["score"])
    
    metrics_summary = {
        metric: aggregate_metric(scores)
        for metric, scores in scores_by_metric.items()
    }
    
    return {"metrics": metrics_summary}


def write_json_summary(summary: dict, base_dir: Path, experiment_name: str):
    out_dir = base_dir / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


def write_csv_results(per_sample_results, base_dir: Path, experiment_name: str):
    out_dir = base_dir / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.csv"
    
    fieldnames = ["id", "metric", "score", "explanation"]
    
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in per_sample_results:
            writer.writerow({
                "id": r["id"],
                "metric": r["metric"],
                "score": r["score"],
                "explanation": r.get("extra", {}).get("explanation", ""),
            })
    
    return path


def write_html_report(summary: dict, base_dir: Path, experiment_name: str):
    out_dir = base_dir / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    
    rows = ""
    for metric, stats in summary["metrics"].items():
        rows += f"""
        <tr>
          <td>{metric}</td>
          <td>{stats['mean']:.4f}</td>
          <td>{stats['std']:.4f}</td>
          <td>{stats['median']:.4f}</td>
          <td>{stats['min']:.4f}</td>
          <td>{stats['max']:.4f}</td>
          <td>[{stats['ci_lower']:.4f}, {stats['ci_upper']:.4f}]</td>
        </tr>
        """
    
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>RAG Evaluation Report - {experiment_name}</title>
  <style>
    body {{ font-family: sans-serif; margin: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; }}
    th {{ background: #f4f4f4; }}
  </style>
</head>
<body>
  <h1>RAG Evaluation Report</h1>
  <h2>Aggregate Metrics</h2>
  <table>
    <thead>
      <tr>
        <th>Metric</th>
        <th>Mean</th>
        <th>Std</th>
        <th>Median</th>
        <th>Min</th>
        <th>Max</th>
        <th>95% CI (mean)</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""
    
    path.write_text(html, encoding="utf-8")
    return path
