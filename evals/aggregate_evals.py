#!/usr/bin/env python3
"""
Aggregate evaluation results from multiple runs and generate visualization plots.

This script reads summary.json files from evaluation runs and creates line plots
showing how metrics change over time. The plots are saved to docs/evaluation_results.md.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go
from plotly.subplots import make_subplots


def load_summary_json(summary_path: Path) -> Optional[Dict]:
    """Load and parse a summary.json file."""
    try:
        with open(summary_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {summary_path}: {e}", file=sys.stderr)
        return None


def collect_local_results(results_dir: Path) -> List[Dict]:
    """
    Collect all summary.json files from subdirectories in results_dir.
    
    Returns a list of summary dictionaries, each with an added 'source_path' and 'file_mtime' key.
    """
    summaries = []
    
    if not results_dir.exists():
        print(f"Error: Results directory does not exist: {results_dir}", file=sys.stderr)
        return summaries
    
    # Look for summary.json files in subdirectories
    for summary_path in results_dir.glob("*/summary.json"):
        summary = load_summary_json(summary_path)
        if summary:
            summary['source_path'] = str(summary_path)
            # Get file modification time as fallback timestamp
            summary['file_mtime'] = datetime.fromtimestamp(summary_path.stat().st_mtime)
            summaries.append(summary)
    
    return summaries


def extract_metric_data(summaries: List[Dict]) -> Tuple[Dict[str, List[tuple]], List[str]]:
    """
    Extract metric data from summaries.
    
    Returns:
        - Dictionary mapping metric_name -> list of (run_name, timestamp, mean_value) tuples
        - List of all run names in order
    """
    metric_data = defaultdict(list)
    all_run_names = set()
    
    for summary in summaries:
        run_info = summary.get('run', {})
        run_name = run_info.get('evaluation_run_name', 'unknown')
        all_run_names.add(run_name)
        timestamp_str = run_info.get('run_timestamp', '')
        
        # Try to parse timestamp, fall back to file modification time, then None
        try:
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                # Use file modification time as fallback
                timestamp = summary.get('file_mtime')
        except (ValueError, AttributeError):
            # If parsing fails, use file modification time as fallback
            timestamp = summary.get('file_mtime')
        
        metrics = summary.get('metrics', {})
        for metric_name, metric_stats in metrics.items():
            mean_value = metric_stats.get('mean')
            if mean_value is not None:
                metric_data[metric_name].append((run_name, timestamp, mean_value))
    
    # Sort each metric's data by timestamp (oldest first)
    for metric_name in metric_data:
        metric_data[metric_name].sort(key=lambda x: (x[1] if x[1] else datetime.min, x[0]))
    
    # Get ordered list of run names sorted chronologically (oldest first)
    # Create a mapping of run_name to earliest timestamp across all metrics
    run_to_timestamp = {}
    for metric_name in metric_data:
        for run_name, timestamp, _ in metric_data[metric_name]:
            if run_name not in run_to_timestamp:
                run_to_timestamp[run_name] = timestamp
            elif timestamp and (not run_to_timestamp[run_name] or timestamp < run_to_timestamp[run_name]):
                run_to_timestamp[run_name] = timestamp
    
    # Sort runs by timestamp (oldest first), then by name if no timestamp
    ordered_runs = sorted(
        run_to_timestamp.keys(),
        key=lambda x: (run_to_timestamp[x] if run_to_timestamp[x] else datetime.min, x)
    )
    
    return metric_data, ordered_runs


def create_combined_plot(metric_data: Dict[str, List[tuple]], ordered_runs: List[str], output_path: Path) -> Path:
    """
    Create a combined line plot with all metrics on the same chart.
    
    Returns the path to the saved image.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    fig = go.Figure()
    
    # Determine x-axis values (use run names as categorical)
    x_values = ordered_runs
    
    # Add a trace for each metric
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', 
              '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
    
    for idx, (metric_name, data_points) in enumerate(metric_data.items()):
        if not data_points:
            continue
        
        # Create a mapping from run_name to value
        run_to_value = {point[0]: point[2] for point in data_points}
        
        # Extract values in the order of ordered_runs
        values = [run_to_value.get(run, None) for run in ordered_runs]
        
        # Format metric name for display
        display_name = metric_name.replace('_', ' ').title()
        
        # Get color for this metric (cycle through colors if needed)
        color = colors[idx % len(colors)]
        
        fig.add_trace(go.Scatter(
            x=x_values,
            y=values,
            mode='lines+markers',
            name=display_name,
            line=dict(width=2, color=color),
            marker=dict(size=8, color=color),
            hovertemplate=f'<b>{display_name}</b><br>Run: %{{x}}<br>Value: %{{y:.3f}}<extra></extra>'
        ))
    
    fig.update_layout(
        title=dict(
            text='All Metrics Over Time',
            font=dict(size=18, family='Arial, sans-serif')
        ),
        xaxis=dict(
            title='Evaluation Run',
            tickangle=-45
        ),
        yaxis=dict(
            title='Mean Value'
        ),
        hovermode='x unified',
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=1.01
        ),
        width=1200,
        height=600,
        template='plotly_white'
    )
    
    # Save as static image
    safe_filename = 'eval_all_metrics_combined.png'
    image_path = output_path.parent / safe_filename
    fig.write_image(image_path, width=1200, height=600, scale=2)
    
    return image_path


def create_individual_metric_plot(metric_name: str, data_points: List[tuple], ordered_runs: List[str], output_path: Path) -> Optional[Path]:
    """
    Create an individual line plot for a single metric.
    
    Returns the path to the saved image, or None if no data points.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not data_points:
        return None
    
    # Create a mapping from run_name to value
    run_to_value = {point[0]: point[2] for point in data_points}
    
    # Extract values in the order of ordered_runs
    x_values = ordered_runs
    values = [run_to_value.get(run, None) for run in ordered_runs]
    
    # Format metric name for display
    display_name = metric_name.replace('_', ' ').title()
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=x_values,
        y=values,
        mode='lines+markers+text',
        name=display_name,
        line=dict(width=3, color='#1f77b4'),
        marker=dict(size=10, color='#1f77b4'),
        text=[f'{v:.3f}' if v is not None else '' for v in values],
        textposition='top center',
        textfont=dict(size=10),
        hovertemplate=f'<b>{display_name}</b><br>Run: %{{x}}<br>Value: %{{y:.3f}}<extra></extra>'
    ))
    
    fig.update_layout(
        title=dict(
            text=f'{display_name} Over Time',
            font=dict(size=16, family='Arial, sans-serif')
        ),
        xaxis=dict(
            title='Evaluation Run',
            tickangle=-45
        ),
        yaxis=dict(
            title='Mean Value',
            range=[0, 1.1] if max(v for v in values if v is not None) <= 1.0 else None
        ),
        width=1000,
        height=600,
        template='plotly_white',
        showlegend=False
    )
    
    # Save as static image
    safe_metric_name = metric_name.replace('/', '_').replace('\\', '_')
    image_filename = f'eval_metric_{safe_metric_name}.png'
    image_path = output_path.parent / image_filename
    fig.write_image(image_path, width=1000, height=600, scale=2)
    
    return image_path


def create_metric_table(metric_name: str, data_points: List[tuple], ordered_runs: List[str]) -> str:
    """
    Create a markdown table showing metric values for each run.
    
    Returns a markdown table string.
    """
    # Create a mapping from run_name to value
    run_to_value = {point[0]: point[2] for point in data_points}
    
    # Build table
    table_lines = []
    table_lines.append(f"| Run Name | {metric_name.replace('_', ' ').title()} |")
    table_lines.append("|----------|" + "-" * (len(metric_name) + 10) + "|")
    
    for run in ordered_runs:
        value = run_to_value.get(run, None)
        if value is not None:
            table_lines.append(f"| {run} | {value:.4f} |")
        else:
            table_lines.append(f"| {run} | N/A |")
    
    return "\n".join(table_lines)


def get_summary_timestamp(summary: Dict) -> datetime:
    """Get timestamp for a summary, using run_timestamp or file_mtime as fallback."""
    run_info = summary.get('run', {})
    timestamp_str = run_info.get('run_timestamp', '')
    
    try:
        if timestamp_str:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        pass
    
    # Use file modification time as fallback
    file_mtime = summary.get('file_mtime')
    if file_mtime:
        return file_mtime
    
    return datetime.min


def generate_markdown_report(
    combined_plot_path: Path,
    individual_plots: List[Tuple[str, Path]],
    metric_tables: List[Tuple[str, str]],
    summaries: List[Dict],
    output_path: Path
):
    """
    Generate a markdown report with embedded plots and summary information.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Sort summaries by timestamp (oldest first)
    sorted_summaries = sorted(summaries, key=get_summary_timestamp)
    
    with open(output_path, 'w') as f:
        f.write("# Evaluation Results Over Time\n\n")
        f.write(f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        
        # Evaluations Summary section
        f.write("## Evaluations Summary\n\n")
        
        # Summary table of runs (ordered chronologically, oldest first)
        f.write("| Run Name | Mode | Timestamp |\n")
        f.write("|----------|------|-----------|\n")
        
        for summary in sorted_summaries:
            run_info = summary.get('run', {})
            run_name = run_info.get('evaluation_run_name', 'unknown')
            mode = run_info.get('mode', 'unknown')
            timestamp_str = run_info.get('run_timestamp', '')
            if timestamp_str:
                timestamp = timestamp_str
            else:
                # Use file modification time as fallback
                file_mtime = summary.get('file_mtime')
                if file_mtime:
                    timestamp = file_mtime.isoformat()
                else:
                    timestamp = 'N/A'
            f.write(f"| {run_name} | {mode} | {timestamp} |\n")
        
        f.write("\n")
        
        # Combined plot
        f.write(f"![All Metrics Combined]({combined_plot_path.name})\n\n")
        
        # Evaluation Metric Details section
        f.write("## Evaluation Metric Details\n\n")
        
        # Individual metric plots and tables
        for (metric_name, plot_path), (_, table) in zip(individual_plots, metric_tables):
            display_name = metric_name.replace('_', ' ').title()
            f.write(f"### {display_name}\n\n")
            f.write(f"![{display_name}]({plot_path.name})\n\n")
            f.write(f"{table}\n\n")
    
    print(f"Markdown report generated: {output_path}")


def aggregate_local_results(results_dir: Path, output_dir: Path):
    """
    Aggregate evaluation results from local directory and generate plots.
    """
    print(f"Collecting results from: {results_dir}")
    summaries = collect_local_results(results_dir)
    
    if not summaries:
        print("No evaluation results found!", file=sys.stderr)
        return
    
    print(f"Found {len(summaries)} evaluation runs")
    
    # Extract metric data
    metric_data, ordered_runs = extract_metric_data(summaries)
    
    if not metric_data:
        print("No metrics found in evaluation results!", file=sys.stderr)
        return
    
    print(f"Found {len(metric_data)} unique metrics")
    
    # Create output path
    output_path = output_dir / "evaluation_results.md"
    
    # Create combined plot
    print("Creating combined metrics plot...")
    combined_plot_path = create_combined_plot(metric_data, ordered_runs, output_path)
    
    # Create individual plots and tables
    print("Creating individual metric plots and tables...")
    individual_plots = []
    metric_tables = []
    
    for metric_name, data_points in sorted(metric_data.items()):
        plot_path = create_individual_metric_plot(metric_name, data_points, ordered_runs, output_path)
        if plot_path:
            individual_plots.append((metric_name, plot_path))
            table = create_metric_table(metric_name, data_points, ordered_runs)
            metric_tables.append((metric_name, table))
    
    # Generate markdown report
    generate_markdown_report(
        combined_plot_path,
        individual_plots,
        metric_tables,
        summaries,
        output_path
    )
    
    print(f"Successfully generated report with {len(individual_plots)} metric plots")


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate evaluation results and generate visualization plots"
    )
    parser.add_argument(
        "--eval-results-dir",
        type=str,
        required=True,
        help="Path to directory containing all evaluation results to be aggregated (subdirectories with summary.json files)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for the aggregation script (default: ../docs relative to script location)"
    )
    parser.add_argument(
        "--github-action",
        action="store_true",
        help="Enable GitHub Actions mode (not yet implemented)"
    )
    
    args = parser.parse_args()
    
    # Resolve paths
    script_dir = Path(__file__).parent
    results_dir = Path(args.eval_results_dir).resolve()
    
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = (script_dir.parent / "docs").resolve()
    
    if args.github_action:
        print("GitHub Actions mode not yet implemented", file=sys.stderr)
        sys.exit(1)
    
    aggregate_local_results(results_dir, output_dir)


if __name__ == "__main__":
    main()

