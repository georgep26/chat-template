#!/usr/bin/env python3
"""
Aggregate evaluation results from multiple runs and generate visualization plots.

This script reads summary.json files from evaluation runs and creates line plots
showing how metrics change over time. The plots are saved to docs/evaluation_results.md.

Can aggregate from local directories or fetch artifacts from GitHub Actions runs.
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go
import requests
from dotenv import load_dotenv
from github import Auth, Github
from plotly.subplots import make_subplots


def load_summary_json(summary_path: Path) -> Optional[Dict]:
    """Load and parse a summary.json file."""
    try:
        with open(summary_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {summary_path}: {e}", file=sys.stderr)
        return None


def load_environment_variables():
    """Load environment variables from .env file if it exists."""
    # Try to find .env file in project root (parent of evals directory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    env_file = project_root / ".env"
    
    if env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded environment variables from: {env_file}")
    else:
        # Also try current directory
        load_dotenv()
    
    # Verify GITHUB_TOKEN is available if needed
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        print("GITHUB_TOKEN found in environment")
    else:
        print("Warning: GITHUB_TOKEN not found in environment (required for GitHub Actions fetching)")


def fetch_github_actions_artifacts(owner: str, repo: str, max_runs: int, workflow_name: str = "Run Evaluations") -> Path:
    """
    Fetch evaluation artifacts from GitHub Actions workflow runs.
    
    Args:
        owner: GitHub repository owner
        repo: GitHub repository name
        max_runs: Maximum number of successful runs to fetch
        workflow_name: Name of the workflow (default: "Run Evaluations")
    
    Returns:
        Path to temporary directory containing extracted artifacts
    """
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable is required for fetching GitHub Actions artifacts")
    
    print(f"Connecting to GitHub repository: {owner}/{repo}")
    # Use the new auth method to avoid deprecation warning
    auth = Auth.Token(github_token)
    g = Github(auth=auth)
    repository = g.get_repo(f"{owner}/{repo}")
    
    # Find the workflow
    workflows = repository.get_workflows()
    target_workflow = None
    for workflow in workflows:
        if workflow.name == workflow_name:
            target_workflow = workflow
            break
    
    if not target_workflow:
        raise ValueError(f"Workflow '{workflow_name}' not found in repository")
    
    print(f"Found workflow: {workflow_name} (ID: {target_workflow.id})")
    
    # Get successful workflow runs
    # Fetch more runs than max_runs to account for expired artifacts
    # GitHub Actions artifacts expire after 90 days by default (or retention_days if set)
    runs_to_check = max(max_runs * 2, 20)  # Check at least 2x max_runs or 20, whichever is larger
    runs = list(target_workflow.get_runs(status="success")[:runs_to_check])
    print(f"Found {len(runs)} successful workflow run(s) to check")
    print(f"Fetching artifacts from up to {max_runs} successful runs (checking {len(runs)} most recent runs for valid artifacts)...")
    
    # Create temporary directory for artifacts
    temp_dir = Path(tempfile.mkdtemp(prefix="github_artifacts_"))
    print(f"Downloading artifacts to: {temp_dir}")
    
    artifact_count = 0
    runs_processed = 0
    for run in runs:
        if artifact_count >= max_runs:
            break
        
        runs_processed += 1
        artifact_found = False
        
        try:
            artifacts = run.get_artifacts()
            for artifact in artifacts:
                if artifact.name == "eval-results":
                    artifact_found = True
                    print(f"  Downloading artifact from run #{run.run_number} (ID: {run.id})...")
                    
                    try:
                        # Download artifact
                        artifact_path = temp_dir / f"run_{run.run_number}_{run.id}"
                        artifact_path.mkdir(parents=True, exist_ok=True)
                        
                        # Download the artifact zip file using the download URL
                        # The artifact object has a url property that points to the download endpoint
                        zip_path = artifact_path / "artifact.zip"
                        
                        # Get the download URL - PyGithub provides this via the artifact's url
                        # We need to use the GitHub API directly with authentication
                        download_url = f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts/{artifact.id}/zip"
                        headers = {
                            "Authorization": f"token {github_token}",
                            "Accept": "application/vnd.github.v3+json"
                        }
                        
                        # Download the zip file
                        response = requests.get(download_url, headers=headers, stream=True)
                        
                        # Handle expired artifacts (410 Gone) gracefully
                        if response.status_code == 410:
                            print(f"    Artifact expired (410 Gone) for run #{run.run_number} - skipping")
                            # Clean up the directory we created
                            if artifact_path.exists():
                                import shutil
                                shutil.rmtree(artifact_path)
                            break  # Try next artifact or run
                        
                        response.raise_for_status()
                        
                        with open(zip_path, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        # Extract the zip file
                        with zipfile.ZipFile(zip_path, "r") as zip_ref:
                            zip_ref.extractall(artifact_path)
                        
                        # Remove the zip file
                        zip_path.unlink()
                        
                        # GitHub Actions artifacts preserve the directory structure from the upload
                        # The artifact contains evals/eval_outputs/, so we need to find summary.json
                        # files in the extracted structure. The collect_local_results function
                        # will handle finding them with the glob pattern */summary.json
                        
                        # Add run metadata to summary.json files if they exist
                        for summary_path in artifact_path.rglob("summary.json"):
                            try:
                                with open(summary_path, "r") as f:
                                    summary = json.load(f)
                                
                                # Add GitHub Actions run metadata if not present
                                if "run" not in summary:
                                    summary["run"] = {}
                                
                                run_info = summary["run"]
                                if "evaluation_run_name" not in run_info:
                                    run_info["evaluation_run_name"] = f"github-actions-run-{run.run_number}"
                                if "run_timestamp" not in run_info:
                                    # Use workflow run created_at timestamp
                                    run_info["run_timestamp"] = run.created_at.isoformat()
                                if "mode" not in run_info:
                                    run_info["mode"] = "github-actions"
                                
                                # Write back the updated summary
                                with open(summary_path, "w") as f:
                                    json.dump(summary, f, indent=2)
                            except (json.JSONDecodeError, IOError) as e:
                                print(f"    Warning: Failed to update {summary_path}: {e}", file=sys.stderr)
                        
                        artifact_count += 1
                        print(f"    Successfully downloaded artifact from run #{run.run_number}")
                        break  # Only process first eval-results artifact per run
                    except requests.exceptions.HTTPError as e:
                        if e.response.status_code == 410:
                            print(f"    Artifact expired (410 Gone) for run #{run.run_number} - skipping")
                        else:
                            print(f"    HTTP error downloading artifact from run #{run.run_number}: {e}", file=sys.stderr)
                        # Clean up the directory we created
                        if artifact_path.exists():
                            import shutil
                            shutil.rmtree(artifact_path)
                        break  # Try next artifact or run
                    except Exception as e:
                        print(f"    Error downloading artifact from run #{run.run_number}: {e}", file=sys.stderr)
                        # Clean up the directory we created
                        if artifact_path.exists():
                            import shutil
                            shutil.rmtree(artifact_path)
                        break  # Try next artifact or run
            
            if not artifact_found:
                print(f"  No 'eval-results' artifact found for run #{run.run_number}")
        except Exception as e:
            print(f"  Warning: Failed to process run #{run.run_number}: {e}", file=sys.stderr)
            continue
    
    if artifact_count == 0:
        print("Warning: No artifacts found in successful workflow runs", file=sys.stderr)
    else:
        print(f"Successfully downloaded {artifact_count} artifact(s) from {runs_processed} run(s) processed")
    
    return temp_dir


def collect_local_results(results_dir: Path) -> List[Dict]:
    """
    Collect all summary.json files from subdirectories in results_dir.
    
    Returns a list of summary dictionaries, each with an added 'source_path' and 'file_mtime' key.
    """
    summaries = []
    
    if not results_dir.exists():
        print(f"Error: Results directory does not exist: {results_dir}", file=sys.stderr)
        return summaries
    
    # Look for summary.json files in subdirectories (recursively to handle GitHub Actions artifacts)
    for summary_path in results_dir.rglob("**/summary.json"):
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


def create_combined_plot(metric_data: Dict[str, List[tuple]], ordered_runs: List[str], run_to_num_questions: Dict[str, Optional[int]], output_path: Path) -> Path:
    """
    Create a combined line plot with all metrics on the same chart.
    Marker sizes are proportional to the number of validation questions.
    
    Returns the path to the saved image.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    fig = go.Figure()
    
    # Determine x-axis values (use run names as categorical)
    x_values = ordered_runs
    
    # Calculate marker sizes based on num_validation_questions
    # Normalize to a reasonable range (min 6, max 20)
    num_questions_list = [run_to_num_questions.get(run) for run in ordered_runs]
    valid_questions = [n for n in num_questions_list if n is not None]
    
    def get_marker_size(num_questions: Optional[int]) -> int:
        if num_questions is None:
            return 8  # Default size when not provided
        if not valid_questions:
            return 8  # Default size when no valid questions available
        min_questions = min(valid_questions)
        max_questions = max(valid_questions)
        size_range = max_questions - min_questions if max_questions > min_questions else 1
        if size_range == 0:
            return 12  # Default if all same size
        # Scale from 6 to 20 based on number of questions
        normalized = (num_questions - min_questions) / size_range
        return int(6 + normalized * 14)
    
    marker_sizes = [get_marker_size(run_to_num_questions.get(run)) for run in ordered_runs]
    
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
            marker=dict(size=marker_sizes, color=color, sizemode='diameter'),
            hovertemplate=f'<b>{display_name}</b><br>Run: %{{x}}<br>Value: %{{y:.3f}}<br>Questions: %{{customdata}}<extra></extra>',
            customdata=[run_to_num_questions.get(run, 'N/A') for run in ordered_runs]
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


def create_individual_metric_plot(metric_name: str, data_points: List[tuple], ordered_runs: List[str], run_to_num_questions: Dict[str, Optional[int]], output_path: Path) -> Optional[Path]:
    """
    Create an individual line plot for a single metric.
    Marker sizes are proportional to the number of validation questions.
    
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
    
    # Calculate marker sizes based on num_validation_questions
    # Normalize to a reasonable range (min 8, max 24)
    num_questions_list = [run_to_num_questions.get(run) for run in ordered_runs]
    valid_questions = [n for n in num_questions_list if n is not None]
    
    def get_marker_size(num_questions: Optional[int]) -> int:
        if num_questions is None:
            return 10  # Default size when not provided
        if not valid_questions:
            return 10  # Default size when no valid questions available
        min_questions = min(valid_questions)
        max_questions = max(valid_questions)
        size_range = max_questions - min_questions if max_questions > min_questions else 1
        if size_range == 0:
            return 14  # Default if all same size
        # Scale from 8 to 24 based on number of questions
        normalized = (num_questions - min_questions) / size_range
        return int(8 + normalized * 16)
    
    marker_sizes = [get_marker_size(run_to_num_questions.get(run)) for run in ordered_runs]
    
    # Format metric name for display
    display_name = metric_name.replace('_', ' ').title()
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=x_values,
        y=values,
        mode='lines+markers+text',
        name=display_name,
        line=dict(width=3, color='#1f77b4'),
        marker=dict(size=marker_sizes, color='#1f77b4', sizemode='diameter'),
        text=[f'{v:.3f}' if v is not None else '' for v in values],
        textposition='top center',
        textfont=dict(size=10),
        hovertemplate=f'<b>{display_name}</b><br>Run: %{{x}}<br>Value: %{{y:.3f}}<br>Questions: %{{customdata}}<extra></extra>',
        customdata=[run_to_num_questions.get(run, 'N/A') for run in ordered_runs]
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
    run_to_num_questions: Dict[str, Optional[int]],
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
        f.write("| Run Name | Mode | Timestamp | Num Questions |\n")
        f.write("|----------|------|-----------|--------------|\n")
        
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
            
            # Get num_validation_questions from summary
            num_questions = summary.get('num_validation_questions')
            num_questions_str = str(num_questions) if num_questions is not None else ''
            
            f.write(f"| {run_name} | {mode} | {timestamp} | {num_questions_str} |\n")
        
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
    
    # Extract num_validation_questions for each run
    run_to_num_questions = {}
    for summary in summaries:
        run_info = summary.get('run', {})
        run_name = run_info.get('evaluation_run_name', 'unknown')
        num_questions = summary.get('num_validation_questions')
        run_to_num_questions[run_name] = num_questions
    
    # Create output path
    output_path = output_dir / "evaluation_results.md"
    
    # Create combined plot
    print("Creating combined metrics plot...")
    combined_plot_path = create_combined_plot(metric_data, ordered_runs, run_to_num_questions, output_path)
    
    # Create individual plots and tables
    print("Creating individual metric plots and tables...")
    individual_plots = []
    metric_tables = []
    
    for metric_name, data_points in sorted(metric_data.items()):
        plot_path = create_individual_metric_plot(metric_name, data_points, ordered_runs, run_to_num_questions, output_path)
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
        run_to_num_questions,
        output_path
    )
    
    print(f"Successfully generated report with {len(individual_plots)} metric plots")


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate evaluation results and generate visualization plots. "
                    "Can aggregate from local directory or fetch from GitHub Actions."
    )
    parser.add_argument(
        "--eval-results-dir",
        type=str,
        default=None,
        help="Path to directory containing all evaluation results to be aggregated "
             "(subdirectories with summary.json files). If not provided, will fetch from GitHub Actions."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for the aggregation script (default: ../docs relative to script location)"
    )
    parser.add_argument(
        "--owner",
        type=str,
        default=None,
        help="GitHub repository owner (required when fetching from GitHub Actions)"
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="GitHub repository name (required when fetching from GitHub Actions)"
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=10,
        help="Maximum number of previous successful evaluation runs to fetch from GitHub Actions (default: 10)"
    )
    parser.add_argument(
        "--workflow-name",
        type=str,
        default="Run Evaluations",
        help="Name of the GitHub Actions workflow (default: 'Run Evaluations')"
    )
    
    args = parser.parse_args()
    
    # Resolve paths
    script_dir = Path(__file__).parent
    
    # Determine if we're fetching from GitHub Actions or using local directory
    if args.eval_results_dir:
        # Use local directory
        results_dir = Path(args.eval_results_dir).resolve()
        temp_dir = None
    else:
        # Fetch from GitHub Actions - load environment variables first
        load_environment_variables()
        
        if not args.owner or not args.repo:
            print("Error: --owner and --repo are required when --eval-results-dir is not provided", file=sys.stderr)
            sys.exit(1)
        
        print(f"Fetching evaluation artifacts from GitHub Actions: {args.owner}/{args.repo}")
        try:
            temp_dir = fetch_github_actions_artifacts(
                owner=args.owner,
                repo=args.repo,
                max_runs=args.max_runs,
                workflow_name=args.workflow_name
            )
            results_dir = temp_dir
        except Exception as e:
            print(f"Error fetching GitHub Actions artifacts: {e}", file=sys.stderr)
            sys.exit(1)
    
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = (script_dir.parent / "docs").resolve()
    
    try:
        aggregate_local_results(results_dir, output_dir)
    finally:
        # Clean up temporary directory if we fetched from GitHub Actions
        if temp_dir and temp_dir.exists():
            import shutil
            print(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    main()

